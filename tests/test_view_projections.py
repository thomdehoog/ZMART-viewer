import numpy as np
import pytest
import zarr
from test_view_sampling import write_tile

from zmart_viewer.contrast import measure
from zmart_viewer.library import _read_attrs_at
from zmart_viewer.projections import projection_dtype, reduce_z, write_projection


def test_sum_auto_contrast_is_not_limited_to_sixteen_bits(tmp_path):
    data = np.broadcast_to(
        np.arange(8, dtype="uint16")[None, None, None, None, :] * 1000 + 30000, (1, 1, 3, 8, 8)
    ).copy()
    tile = write_tile(tmp_path, "p.ome.zarr", data)
    projected = write_projection(tile.store, tmp_path / "sum.ome.zarr", "sum")
    window = measure(projected)["window"]
    assert 65535 < window[0] < window[1] < 120000


@pytest.mark.parametrize("method", ["min", "max", "sum"])
def test_acquired_black_missing_and_reduction(method):
    values = np.array([[[0, 10, 99]], [[7, 20, 99]]], dtype="uint16")
    mask = np.array([[[1, 0, 0]], [[1, 1, 0]]], dtype=bool)
    image, coverage = reduce_z(values, mask, method)
    np.testing.assert_array_equal(image, [[0 if method == "min" else 7, 20, 0]])
    np.testing.assert_array_equal(coverage, [[1, 1, 0]])


@pytest.mark.parametrize("method", ["min", "max", "sum"])
def test_saved_projection_czt_levels_originals_and_idle(tmp_path, method):
    data = np.arange(2 * 2 * 3 * 8 * 8, dtype="uint16").reshape(2, 2, 3, 8, 8)
    tile = write_tile(tmp_path, "position.ome.zarr", data, x=12, z=100)
    original = {p: p.read_bytes() for p in tile.store.rglob("*") if p.is_file()}
    output = tmp_path / "projections" / method / tile.name
    write_projection(tile.store, output, method, revision=1, piece=4)
    group = zarr.open_group(str(output), mode="r")
    expected = getattr(np, method)(data, axis=2, keepdims=True)
    for level in range(3):
        np.testing.assert_array_equal(group[str(level)][:], expected)
        h, w = expected.shape[-2:]
        expected = np.rint(
            expected.reshape(*expected.shape[:-2], h // 2, 2, w // 2, 2).mean((-3, -1))
        )
    stamps = {p: p.stat().st_mtime_ns for p in output.rglob("*") if p.is_file()}
    write_projection(tile.store, output, method, revision=1, piece=4)
    assert stamps == {p: p.stat().st_mtime_ns for p in output.rglob("*") if p.is_file()}
    assert all(p.read_bytes() == value for p, value in original.items())
    assert _read_attrs_at(output)["multiscales"][0]["axes"][2]["name"] == "z"


def test_sum_overflow_is_not_saturated():
    with pytest.raises(OverflowError):
        reduce_z(np.full((2, 1, 1), 2**32 - 1, dtype="uint32"), np.ones((2, 1, 1), bool), "sum")


def test_sum_fractional_and_high_values():
    value, _ = reduce_z(np.full((3, 1, 1), 0.25, dtype="float32"), np.ones((3, 1, 1), bool), "sum")
    np.testing.assert_array_equal(value, [[0.75]])
    value, _ = reduce_z(np.full((3, 1, 1), 65535, dtype="uint16"), np.ones((3, 1, 1), bool), "sum")
    assert value.dtype == np.dtype("uint32")
    assert value.item() == 196605


@pytest.mark.parametrize("method", ["min", "max", "sum"])
def test_float16_promotes_to_renderable_float32(method):
    assert projection_dtype("float16", method) == np.dtype("float32")


def test_projection_preserves_time_calibration_and_refuses_wrong_units(tmp_path):
    tile = write_tile(tmp_path, "p.ome.zarr", np.ones((2, 1, 3, 8, 8), dtype="uint16"))
    group = zarr.open_group(str(tile.store), mode="r+")
    metadata = dict(group.attrs)
    scales = metadata["ome"]["multiscales"][0]
    scales["axes"][0]["unit"] = "millisecond"
    for dataset in scales["datasets"]:
        dataset["coordinateTransformations"][0]["scale"][0] = 1500
        dataset["coordinateTransformations"][1]["translation"][0] = 200
    group.attrs.update(metadata)
    out = write_projection(tile.store, tmp_path / "p_min.ome.zarr", "min")
    saved = _read_attrs_at(out)["multiscales"][0]
    assert saved["axes"][0]["unit"] == "millisecond"
    assert saved["datasets"][0]["coordinateTransformations"][0]["scale"][0] == 1500
    assert saved["datasets"][0]["coordinateTransformations"][1]["translation"][0] == 200
    scales["axes"][-1]["unit"] = "nanometer"
    group.attrs.update(metadata)
    with pytest.raises(ValueError, match="micrometres"):
        write_projection(tile.store, tmp_path / "p_max.ome.zarr", "max")


def test_output_never_replaces_originals_or_unowned_data(tmp_path):
    source = tmp_path / "originals.ome.zarr"
    source.mkdir()
    tile = write_tile(source, "position.ome.zarr", np.zeros((1, 1, 1, 8, 8), dtype="uint16"))
    marker = source / "keep.txt"
    marker.write_text("original data")
    for destination in (source, tile.store, tile.store / "nested.ome.zarr"):
        with pytest.raises(ValueError, match="separate"):
            write_projection(tile.store, destination, "min")
    unrelated = tmp_path / "unrelated.ome.zarr"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_text("unrelated data")
    with pytest.raises((ValueError, FileNotFoundError)):
        write_projection(tile.store, unrelated, "min")
    assert marker.read_text() == "original data"
    assert (unrelated / "keep.txt").read_text() == "unrelated data"
