"""Acquisition facts, not fill values, decide which source owns each pixel."""

import json
from dataclasses import replace

import numpy as np
import pytest
import zarr

from zmart_viewer.compose import (
    MEAN_REDUCTION,
    Composer,
    Mosaic,
    _read_one_tile,
    read_the_mosaic_as_written,
    the_mosaic_written_down,
)


def region(x, width, *, y=0, height=8, z=0, depth=1, t=0, c=0):
    return {
        "frame": t,
        "channel": c,
        "origin": {"z": z, "y": y, "x": x},
        "shape": {"z": depth, "y": height, "x": width},
    }


def source(folder, name, value, *, x=0):
    group = zarr.open_group(str(folder / name), mode="w", zarr_format=3)
    datasets = []
    for level in range(3):
        side = 8 // 2**level
        group.create_array(
            str(level),
            data=np.full((2, 2, 2, side, side), value, dtype="uint16"),
            chunks=(1, 1, 1, 4, 4),
        )
        datasets.append(
            {
                "path": str(level),
                "coordinateTransformations": [
                    {"type": "scale", "scale": [1, 1, 1, 2**level, 2**level]},
                    {
                        "type": "translation",
                        "translation": [0, 0, 0, (2**level - 1) / 2, x + (2**level - 1) / 2],
                    },
                ],
            }
        )
    group.attrs["ome"] = {
        "version": "0.5",
        "multiscales": [
            {
                "type": "mean",
                "axes": [
                    {
                        "name": a,
                        "type": "time" if a == "t" else "channel" if a == "c" else "space",
                        **({"unit": "micrometer"} if a in "zyx" else {}),
                    }
                    for a in "tczyx"
                ],
                "datasets": datasets,
            }
        ],
    }
    return _read_one_tile(folder / name)


def pixels(composer, level, *, t=0, c=0, z=0):
    _, h, w = composer.mosaic.shape(level)
    result = np.zeros((h, w), dtype="uint16")
    mask = np.zeros((h, w), dtype="uint8")
    for row in range((h + 3) // 4):
        for col in range((w + 3) // 4):
            height, width = min(4, h - row * 4), min(4, w - col * 4)
            at = (slice(row * 4, row * 4 + height), slice(col * 4, col * 4 + width))
            values = composer.values_for(level, z, row, col, moment=t, channel=c)
            if values is not None:
                result[at] = values[:height, :width]
            mask[at] = composer.coverage_for(level, z, row, col, moment=t, channel=c)[
                :height, :width
            ]
    return result, mask


def test_sparse_pixels_coverage_order_and_snapshot_roundtrip(tmp_path):
    a, b = source(tmp_path, "a.ome.zarr", 120), source(tmp_path, "b.ome.zarr", 0, x=1)
    base = Mosaic([a, b], 3, ("z", "y", "x"), "uint16", averaged=True, extent_um=(2, 8, 16))
    originals = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    regions = {a.name: [region(0, 4)], b.name: [region(0, 2), region(6, 2)]}
    snapshot = base.with_acquired_regions(regions, order=[a.name, b.name])
    # Changes to the caller's inputs cannot change the published picture.
    regions[b.name][0]["shape"]["x"] = 8
    restored = read_the_mosaic_as_written(json.loads(json.dumps(the_mosaic_written_down(snapshot))))
    expected = np.zeros((8, 16), dtype="uint16")
    expected[:, :4] = 120
    expected[:, 1:3] = 0
    covered = np.zeros((8, 16), dtype="uint8")
    covered[:, :4] = 1
    covered[:, 7:9] = 1
    for mosaic in (snapshot, restored):
        composer = Composer(mosaic, piece=4)
        try:
            image, mask = pixels(composer, 0)
            np.testing.assert_array_equal(image, expected)
            np.testing.assert_array_equal(mask, covered)
            # Entirely empty source chunks are not read, even if they contain nonzero fill.
            before = composer.tile_reads
            assert composer.values_for(0, 0, 0, 3) is None
            assert composer.tile_reads == before
            assert not pixels(composer, 0, c=1)[1].any()
            assert not pixels(composer, 0, t=1)[1].any()
            assert not pixels(composer, 0, z=1)[1].any()
            coarse, coarse_mask = expected, covered
            for level in (1, 2):
                coarse = (
                    coarse.reshape(coarse.shape[0] // 2, 2, coarse.shape[1] // 2, 2)
                    .mean((1, 3))
                    .round()
                    .astype("uint16")
                )
                coarse_mask = coarse_mask.reshape(
                    coarse_mask.shape[0] // 2, 2, coarse_mask.shape[1] // 2, 2
                ).max((1, 3))
                image, mask = pixels(composer, level)
                np.testing.assert_array_equal(image, coarse)
                np.testing.assert_array_equal(mask, coarse_mask)
        finally:
            composer.close()
    raised = snapshot.with_acquired_regions(
        {a.name: [region(0, 4)], b.name: [region(0, 2), region(6, 2)]}, order=[b.name, a.name]
    )
    composer = Composer(raised, piece=4)
    try:
        assert pixels(composer, 0)[0][0, 1] == 120
        assert pixels(composer, 1)[0][0, 0] == 120
    finally:
        composer.close()
    assert all(p.read_bytes() == body for p, body in originals.items())
    assert {p for p in tmp_path.rglob("*") if p.is_file()} == set(originals)


def test_gap_filled_by_black_in_only_one_czt_plane(tmp_path):
    tile = source(tmp_path, "a.ome.zarr", 0)
    # Zarr omits fill-only chunks: these zeros are still acquired when declared.
    assert not list(tile.store.glob("*/c/**/*"))
    assert all(copy.presence is None for copy in tile.copies)
    base = Mosaic([tile], 3, ("z", "y", "x"), "uint16", averaged=True)
    for regions, expected in (([], 0), ([region(3, 2, t=1, c=1, z=1)], 16)):
        composer = Composer(
            base.with_acquired_regions({tile.name: regions}, order=[tile.name]), piece=4
        )
        try:
            image, mask = pixels(composer, 0, t=1, c=1, z=1)
            assert not image.any()
            assert mask.sum() == expected
            assert not pixels(composer, 0)[1].any()
        finally:
            composer.close()


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "unknown",
        "duplicate_order",
        "outside",
        "negative",
        "fractional",
        "bad_channel",
        "null",
    ],
)
def test_unknown_or_invalid_acquired_ground_is_refused(tmp_path, change):
    tile = source(tmp_path, "a.ome.zarr", 0)
    base = Mosaic([tile], 3, ("z", "y", "x"), "uint16", averaged=True)
    records, order = {tile.name: [region(0, 2)]}, [tile.name]
    if change == "missing":
        records = {}
    if change == "unknown":
        records["unknown"] = []
    if change == "duplicate_order":
        order *= 2
    if change == "outside":
        records[tile.name] = [region(7, 2)]
    if change == "negative":
        records[tile.name] = [region(-1, 2)]
    if change == "fractional":
        records[tile.name] = [region(0.5, 2)]
    if change == "bad_channel":
        records[tile.name] = [region(0, 2, c=2)]
    if change == "null":
        records[tile.name] = None
    with pytest.raises(ValueError):
        base.with_acquired_regions(records, order=order)


def test_non_grid_aligned_geometry_is_refused_not_rounded(tmp_path):
    tile = source(tmp_path, "a.ome.zarr", 120, x=0.25)
    base = Mosaic([tile], 3, ("z", "y", "x"), "uint16", averaged=True)
    with pytest.raises(ValueError, match="aligned"):
        base.with_acquired_regions({tile.name: [region(0, 8)]}, order=[tile.name])


def test_committed_moments_survive_worker_snapshot(tmp_path):
    tile = replace(source(tmp_path, "a.ome.zarr", 0), moments=frozenset([1]))
    base = Mosaic([tile], 3, ("z", "y", "x"), "uint16", averaged=True)
    restored = read_the_mosaic_as_written(the_mosaic_written_down(base))
    assert restored.tiles[0].moments == frozenset([1])


def test_empty_chunk_inside_source_bounds_reads_no_pixels(tmp_path):
    tile = source(tmp_path, "a.ome.zarr", 999)
    base = Mosaic([tile], 3, ("z", "y", "x"), "uint16", averaged=True)
    composer = Composer(
        base.with_acquired_regions({tile.name: [region(0, 1)]}, order=[tile.name]), piece=4
    )
    try:
        assert composer.values_for(0, 0, 0, 1) is None
        assert not composer.coverage_for(0, 0, 0, 1).any()
        assert composer.tile_reads == 0
        assert pixels(composer, 0)[0][0, 0] == 999
        assert pixels(composer, 0)[0][0, 1] == 0
    finally:
        composer.close()


@pytest.mark.parametrize("corruption", ["outside", "missing"])
def test_invalid_coverage_snapshot_is_refused_on_reopen(tmp_path, corruption):
    a, b = source(tmp_path, "a.ome.zarr", 10), source(tmp_path, "b.ome.zarr", 20)
    base = Mosaic([a, b], 3, ("z", "y", "x"), "uint16", averaged=True)
    snapshot = base.with_acquired_regions(
        {a.name: [region(0, 8)], b.name: []}, order=[a.name, b.name]
    )
    written = the_mosaic_written_down(snapshot)
    if corruption == "outside":
        written["tiles"][0]["acquired_regions"][0]["shape"]["x"] = 9
    else:
        del written["tiles"][1]["acquired_regions"]
    with pytest.raises(ValueError):
        read_the_mosaic_as_written(written)


@pytest.mark.parametrize("x,width", [(3, 3), (0, 4)])
def test_worker_builds_the_same_sparse_czt_pixels(tmp_path, x, width):
    tile = source(tmp_path, "a.ome.zarr", 840)
    base = Mosaic([tile], 3, ("z", "y", "x"), "uint16", averaged=True)
    snapshot = base.with_acquired_regions(
        {tile.name: [region(x, width, t=1, c=1, z=1)]},
        order=[tile.name],
        pyramid_reduction=MEAN_REDUCTION,
    )
    alone, worker = Composer(snapshot, piece=4), Composer(snapshot, piece=4, workers=2)
    try:
        for level in range(3):
            np.testing.assert_array_equal(
                pixels(worker, level, t=1, c=1, z=1)[0], pixels(alone, level, t=1, c=1, z=1)[0]
            )
            assert not pixels(worker, level)[0].any()
    finally:
        alone.close()
        worker.close()


def test_odd_canvas_edges_match_the_shared_mean_reduction(tmp_path):
    tile = source(tmp_path, "a.ome.zarr", 100)
    base = Mosaic([tile], 3, ("z", "y", "x"), "uint16", averaged=True, extent_um=(2, 7, 7))
    with pytest.raises(ValueError, match="aggregate canvas"):
        base.with_acquired_regions({tile.name: [region(0, 8)]}, order=[tile.name])
    snapshot = base.with_acquired_regions(
        {tile.name: [region(6, 1, y=6, height=1)]}, order=[tile.name]
    )
    expected = np.zeros((7, 7), dtype="uint16")
    expected[-1, -1] = 100
    mask = np.zeros((7, 7), dtype="uint8")
    mask[-1, -1] = 1
    composer = Composer(snapshot, piece=4)
    try:
        for level in range(3):
            actual, covered = pixels(composer, level)
            np.testing.assert_array_equal(actual, expected)
            np.testing.assert_array_equal(covered, mask)
            h, w = expected.shape
            padded = np.pad(expected, ((0, h % 2), (0, w % 2)), mode="edge")
            expected = (
                padded.reshape((h + 1) // 2, 2, (w + 1) // 2, 2)
                .mean((1, 3))
                .round()
                .astype("uint16")
            )
            padded = np.pad(mask, ((0, h % 2), (0, w % 2)))
            mask = padded.reshape((h + 1) // 2, 2, (w + 1) // 2, 2).max((1, 3))
    finally:
        composer.close()


def test_existing_chunk_baker_preserves_sparse_composition_without_l0_copy(tmp_path):
    from zmart_viewer.published import PublishedTransfer

    a, b = (
        source(tmp_path / "originals", "a.ome.zarr", 120),
        source(tmp_path / "originals", "b.ome.zarr", 0, x=1),
    )
    base = Mosaic([a, b], 3, ("z", "y", "x"), "uint16", averaged=True, extent_um=(2, 8, 16))
    snapshot = base.with_acquired_regions(
        {a.name: [region(0, 4, t=1, c=1, z=1)], b.name: [region(0, 2, t=1, c=1, z=1)]},
        order=[a.name, b.name],
    )
    composer = Composer(snapshot, piece=4)
    view = PublishedTransfer(tmp_path / "derived", piece=4)
    try:
        view._shown.mkdir()
        levels = view._declare_levels(composer, json.loads(composer.group_json()))
        assert levels == [2]
        for t in range(2):
            for c in range(2):
                for z in range(2):
                    view._replace_one_piece(composer, 2, z, 0, 0, moment=t, channel=c)
        baked = zarr.open_array(str(view._shown / "2"), mode="r")[:]
        expected = np.zeros_like(baked)
        # Each 4x4 block has two columns of 120 and two overwritten by acquired black.
        expected[1, 1, 1, :, 0] = 60
        np.testing.assert_array_equal(baked, expected)
        assert not (view._shown / "0" / "c").exists()
        assert not (view._shown / "2" / "c" / "0").exists()
    finally:
        composer.close()
        view.close()
