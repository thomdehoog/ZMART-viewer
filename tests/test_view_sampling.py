"""Independent pixel/coverage oracles for named Slice and Top views."""

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
    halve_xy,
    read_the_mosaic_as_written,
    the_mosaic_written_down,
)


def write_tile(root, name, data, *, x=0, z=0, z_chunk=3):
    group = zarr.open_group(str(root / name), mode="w", zarr_format=3)
    datasets = []
    image = data
    for level in range(3):
        group.create_array(
            str(level), data=image, chunks=(1, 1, z_chunk, 4, 4), dimension_names=list("tczyx")
        )
        factor = 2**level
        datasets.append(
            {
                "path": str(level),
                "coordinateTransformations": [
                    {"type": "scale", "scale": [1, 1, 1, factor, factor]},
                    {
                        "type": "translation",
                        "translation": [0, 0, z, (factor - 1) / 2, x + (factor - 1) / 2],
                    },
                ],
            }
        )
        if level < 2:
            h, w = image.shape[-2:]
            image = image.reshape(*image.shape[:-2], h // 2, 2, w // 2, 2).mean((-3, -1))
            image = np.rint(image).astype(data.dtype)
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
    return _read_one_tile(root / name)


def fixture(root, reverse=False):
    tiles, regions, originals = [], {}, []
    for n, (depth, start, x) in enumerate(((1, 10, 0), (5, 10, 4), (12, 14, 8))):
        data = np.empty((2, 2, depth, 8, 8), dtype="uint16")
        mask = np.ones(data.shape, dtype=bool)
        for t in range(2):
            for c in range(2):
                for z in range(depth):
                    data[t, c, z] = 1000 * t + 100 * c + 10 * (n + 1) + z
        mask[..., 2:4] = False
        if n == 1:
            mask[:, :, 2] = False
        if n == 2:
            data[..., 6:] = 0  # Acquired black, including omitted Zarr fill chunks.
        tile = write_tile(root, f"p{n}.ome.zarr", data, x=x, z=start)
        tiles.append(tile)
        regions[tile.name] = [
            {
                "frame": t,
                "channel": c,
                "origin": {"z": z, "y": 0, "x": left},
                "shape": {"z": 1, "y": 8, "x": width},
            }
            for t in range(2)
            for c in range(2)
            for z in range(depth)
            for left, width in ((0, 2), (4, 4))
            if mask[t, c, z, 0, left]
        ]
        originals.append((data, mask, start, x))
    if reverse:
        tiles.reverse()
        originals.reverse()
    mosaic = Mosaic(
        tiles, 3, ("z", "y", "x"), "uint16", averaged=True, extent_um=(40, 8, 20)
    ).with_acquired_regions(
        regions, order=[t.name for t in tiles], pyramid_reduction=MEAN_REDUCTION
    )
    return mosaic, originals


def oracle(originals, mode, z, t, c, level):
    image = np.zeros((8, 20), dtype="uint16")
    coverage = np.zeros((8, 20), dtype=bool)
    for data, mask, start, x in originals:
        selected = z - start
        if mode == "top":
            selected = min(max(selected, 0), data.shape[2] - 1)
        if not 0 <= selected < data.shape[2]:
            continue
        acquired = mask[t, c, selected]
        target = image[:, x : x + 8]
        target[acquired] = data[t, c, selected][acquired]
        coverage[:, x : x + 8] |= acquired
    for _ in range(level):
        h, w = image.shape
        image = np.rint(image.reshape(h // 2, 2, w // 2, 2).mean((1, 3))).astype("uint16")
        coverage = coverage.reshape(h // 2, 2, w // 2, 2).any((1, 3))
    return image, coverage


@pytest.mark.parametrize("mode", ["slice", "top"])
@pytest.mark.parametrize("reverse", [False, True])
def test_every_plane_coverage_overlap_and_roundtrip(tmp_path, mode, reverse):
    mosaic, originals = fixture(tmp_path, reverse)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    mosaic = replace(mosaic, sampling=mode)
    restored = read_the_mosaic_as_written(json.loads(json.dumps(the_mosaic_written_down(mosaic))))
    assert restored.sampling == mode
    made = Composer(restored, piece=4)
    try:
        for level in range(3):
            for z in range(40):
                for t, c in ((0, 0), (1, 1)):
                    expected, mask = oracle(originals, mode, z, t, c, level)
                    for row in range((expected.shape[0] + 3) // 4):
                        for col in range((expected.shape[1] + 3) // 4):
                            h, w = (
                                min(4, expected.shape[0] - row * 4),
                                min(4, expected.shape[1] - col * 4),
                            )
                            actual = made.values_for(level, z, row, col, t, c)
                            if actual is None:
                                actual = np.zeros((4, 4), dtype="uint16")
                            at = (slice(row * 4, row * 4 + h), slice(col * 4, col * 4 + w))
                            np.testing.assert_array_equal(actual[:h, :w], expected[at])
                            np.testing.assert_array_equal(
                                made.coverage_for(level, z, row, col, t, c)[:h, :w], mask[at]
                            )
    finally:
        made.close()
    assert all(p.read_bytes() == value for p, value in before.items())


def test_float_pyramid_preserves_fractional_signal():
    data = np.array([[0.1, 0.2], [0.3, 0.4]], dtype="float32")
    np.testing.assert_allclose(halve_xy(data), [[0.25]])


def test_wide_integer_pyramid_rounding_is_exact():
    data = np.array([[2**63 + 1, 2**63 + 1], [2**63 + 1, 2**63 + 1]], dtype="uint64")
    np.testing.assert_array_equal(halve_xy(data), [[2**63 + 1]])


def test_top_warm_completes_with_run_reuse(tmp_path):
    mosaic, _ = fixture(tmp_path)
    made = Composer(replace(mosaic, sampling="top"), piece=4)
    try:
        made.warm_the_coarse_levels()
        assert made.coarse_levels_are_warm
        before = made.costs["slabs_built"]
        made.warm_the_coarse_levels()
        assert made.costs["slabs_built"] == before
        assert len(made._pinned) < sum(np.prod(made.grid(level)) for level in made.pinned_levels)
    finally:
        made.close()


@pytest.mark.parametrize("explicit", [False, True])
def test_legacy_float_rounding_contract_survives(tmp_path, explicit):
    from zmart_viewer.compose import LEGACY_MEAN_REDUCTION

    data = np.full((1, 1, 1, 8, 8), 0.25, dtype="float32")
    tile = write_tile(tmp_path, "old.ome.zarr", data)
    regions = {
        tile.name: [
            {
                "frame": 0,
                "channel": 0,
                "origin": {"z": 0, "y": 0, "x": 0},
                "shape": {"z": 1, "y": 8, "x": 8},
            }
        ]
    }
    mosaic = Mosaic([tile], 3, ("z", "y", "x"), "float32", averaged=True).with_acquired_regions(
        regions, order=[tile.name], pyramid_reduction=LEGACY_MEAN_REDUCTION if explicit else None
    )
    made = Composer(mosaic, piece=4)
    try:
        assert made.values_for(1, 0, 0, 0) is None
        made._can_read_native = lambda *args: False
        made._slabs.clear()
        made._pinned.clear()
        assert made.values_for(1, 0, 0, 0) is None
    finally:
        made.close()
