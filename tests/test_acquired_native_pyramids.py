"""Native means must agree with composing L0 first, including sparse overlaps."""

from collections import Counter
from dataclasses import replace

import numpy as np
import pytest
import zarr
from test_acquired_composition import pixels, region, source

from zmart_viewer.compose import MEAN_REDUCTION, Composer, Mosaic


def write_pyramid(tile, values):
    for copy in tile.copies:
        zarr.open_array(str(copy.held_in), mode="r+")[:] = values
        h, w = values.shape[-2:]
        values = (
            values.reshape(*values.shape[:-2], h // 2, 2, w // 2, 2)
            .mean(axis=(-3, -1))
            .round()
            .astype("uint16")
        )


@pytest.mark.parametrize("case", ["complete", "sparse", "region_edge", "position", "transform"])
def test_native_and_fallback_match_independent_pixels_and_coverage(tmp_path, monkeypatch, case):
    a = source(tmp_path, "a.ome.zarr", 0)
    b = source(tmp_path, "b.ome.zarr", 0, x=5 if case == "position" else 4)
    fine = np.random.default_rng(73).integers(0, 4096, (2, 2, 2, 8, 8), dtype=np.uint16)
    write_pyramid(a, fine)
    # B is acquired black; its omitted chunks must still overwrite A.
    x, width = (1, 3) if case == "region_edge" else (0, 4 if case == "sparse" else 8)
    regions = {
        a.name: [region(0, 8), region(0, 8, t=1, c=1, z=1)],
        b.name: [region(x, width), region(x, width, t=1, c=1, z=1)],
    }
    if case == "transform":
        b.copies[2] = replace(b.copies[2], corner_um=(0, 1.5, 6.5))
    mosaic = Mosaic(
        [a, b], 5, ("z", "y", "x"), "uint16", averaged=True, extent_um=(2, 8, 32)
    ).with_acquired_regions(regions, order=[a.name, b.name], pyramid_reduction=MEAN_REDUCTION)
    composer = Composer(mosaic, piece=4)
    reads = Counter()
    read = composer._read_from

    def counted(copy, *args):
        reads[int(copy.held_in.name)] += 1
        return read(copy, *args)

    monkeypatch.setattr(composer, "_read_from", counted)
    try:
        # A cold L2 request must use L2 only when the whole chunk is safe.
        composer.values_for(2, 0, 0, 0)
        if case in ("complete", "sparse"):
            assert set(reads) == {2}
        else:
            assert reads[0] or reads[1]
        for t, c, z in ((0, 0, 0), (1, 1, 1), (0, 1, 0)):
            expected = np.zeros((8, 32), dtype=np.uint16)
            covered = np.zeros_like(expected, dtype=np.uint8)
            if t == c == z:
                expected[:, :8] = fine[t, c, z]
                offset = (5 if case == "position" else 4) + x
                expected[:, offset : offset + width] = 0
                covered[:, :8] = covered[:, offset : offset + width] = 1
            for level in range(5):
                image, mask = pixels(composer, level, t=t, c=c, z=z)
                np.testing.assert_array_equal(image, expected)
                np.testing.assert_array_equal(mask, covered)
                pad = ((0, expected.shape[0] % 2), (0, expected.shape[1] % 2))
                expected, covered = (
                    np.pad(expected, pad, mode="edge"),
                    np.pad(covered, pad, mode="edge"),
                )
                h, w = expected.shape
                expected = (
                    expected.reshape(h // 2, 2, w // 2, 2).mean((1, 3)).round().astype("uint16")
                )
                covered = covered.reshape(h // 2, 2, w // 2, 2).max((1, 3))
        count = sum(reads.values())
        assert composer.values_for(2, 0, 0, 1) is None
        assert sum(reads.values()) == count
    finally:
        composer.close()


def test_misaligned_source_does_not_disable_native_reads_in_other_chunks(tmp_path, monkeypatch):
    a = source(tmp_path, "a.ome.zarr", 120)
    b = source(tmp_path, "b.ome.zarr", 240, x=17)
    mosaic = Mosaic(
        [a, b], 3, ("z", "y", "x"), "uint16", averaged=True, extent_um=(2, 8, 32)
    ).with_acquired_regions(
        {a.name: [region(0, 8)], b.name: [region(0, 8)]},
        order=[a.name, b.name],
        pyramid_reduction=MEAN_REDUCTION,
    )
    composer = Composer(mosaic, piece=4)
    reads = []
    read = composer._read_from

    def counted(copy, *args):
        reads.append((copy.held_in.parent.name, int(copy.held_in.name)))
        return read(copy, *args)

    monkeypatch.setattr(composer, "_read_from", counted)
    try:
        assert composer.values_for(2, 0, 0, 0)[0, 0] == 120
        assert reads == [(a.name, 2)]
        assert composer.values_for(2, 0, 0, 1)[0, 0] == 180
        assert all(level == 0 for name, level in reads if name == b.name)
    finally:
        composer.close()


def test_mean_label_without_reducer_contract_keeps_composed_rounding(tmp_path):
    tile = source(tmp_path, "a.ome.zarr", 0)
    values = np.ones((2, 2, 2, 8, 8), dtype=np.uint16)
    values[..., ::2, ::2] = 0  # Mean .75: rounded 1, truncated 0.
    zarr.open_array(str(tile.copies[0].held_in), mode="r+")[:] = values
    base = Mosaic([tile], 3, ("z", "y", "x"), "uint16", averaged=True)
    composer = Composer(
        base.with_acquired_regions({tile.name: [region(0, 8)]}, order=[tile.name]), piece=4
    )
    try:
        np.testing.assert_array_equal(composer.values_for(1, 0, 0, 0), np.ones((4, 4)))
    finally:
        composer.close()


@pytest.mark.parametrize("bake", [False, True])
def test_declared_native_publication_rewrite_reopen_and_contract_change(
    tmp_path, monkeypatch, bake
):
    from zmart_viewer import pieces
    from zmart_viewer.published import STORE, PublishedTransfer

    tile = source(tmp_path, "a.ome.zarr", 120)
    canvas = {"x_um": [0, 64], "y_um": [0, 8]}
    view = PublishedTransfer(tmp_path / STORE, piece=4)
    composition = {"regions": "complete", "order": [tile.name], "pyramid_reduction": MEAN_REDUCTION}
    try:
        view.publish(tmp_path, {tile.name: 1}, canvas, composition=composition, bake=bake)
        assert view.composer().mosaic.pyramid_reduction == MEAN_REDUCTION
        assert view.composer().values_for(2, 0, 0, 0)[0, 0] == 120
        write_pyramid(tile, np.full((2, 2, 2, 8, 8), 80, dtype=np.uint16))
        view.publish(tmp_path, {tile.name: 2}, canvas, composition=composition, bake=bake)
        assert view.composer().values_for(2, 0, 0, 0)[0, 0] == 80
        reopened = PublishedTransfer(view._shown, piece=4)
        try:
            assert reopened.composer().mosaic.pyramid_reduction == MEAN_REDUCTION
            assert reopened.composer().values_for(2, 0, 0, 0)[0, 0] == 80
        finally:
            reopened.close()
        revision = view.revision
        assert (
            view.publish(tmp_path, {tile.name: 2}, canvas, composition=composition, bake=bake)
            == revision
        )
        invalid = {**composition, "pyramid_reduction": "mean"}
        with pytest.raises(ValueError, match="pyramid reduction"):
            view.publish(tmp_path, {tile.name: 2}, canvas, composition=invalid, bake=bake)
        # Withdrawing the guarantee restores L0-derived means, not warm native slabs.
        del composition["pyramid_reduction"]
        levels = []
        read = Composer._read_from

        def counted(self, copy, *args):
            levels.append(int(copy.held_in.name))
            return read(self, copy, *args)

        with monkeypatch.context() as patch:
            patch.setattr(Composer, "_read_from", counted)
            assert (
                view.publish(tmp_path, {tile.name: 2}, canvas, composition=composition, bake=bake)
                == revision + 1
            )
            assert view.composer().values_for(2, 0, 0, 0)[0, 0] == 80
            assert set(levels) == {0}
        zarr.open_array(str(tile.copies[0].held_in), mode="r+")[:] = 0
        view.publish(tmp_path, {tile.name: 3}, canvas, composition=composition, bake=bake)
        assert view.composer().values_for(2, 0, 0, 0) is None
        assert pieces.built_bytes_behind(view._shown, "4/c/0/0/0/0/0") is None
        assert view.composer().coverage_for(2, 0, 0, 0)[0, 0] == 1
    finally:
        pieces.forget(view._shown)
        view.close()
