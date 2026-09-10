"""Top stands on a common floor; Slice cuts the acquired specimen volume."""

import json

import numpy as np
import pytest
import zarr
from test_view_sampling import write_tile

from zmart_viewer import pieces, published
from zmart_viewer.compose import Composer, read_the_mosaic_as_written, the_mosaic_written_down
from zmart_viewer.views import ViewSet


@pytest.mark.parametrize("bake", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_floor_and_specimen_placement_survive_reopen(tmp_path, bake, reverse):
    source = tmp_path / "positions"
    source.mkdir()
    # Distinct absolute heights, depths and deliberately irrelevant focus references.
    inputs = [("flat", 1, 14, 0, 0), ("short", 2, 10, 8, 40), ("tall", 4, 12, 16, 100)]
    for name, depth, z, x, value in inputs:
        data = np.empty((2, 2, depth, 8, 8), dtype="uint16")
        for t in range(2):
            for c in range(2):
                for p in range(depth):
                    data[t, c, p] = value + 10 * p + 200 * t + 50 * c
        store = write_tile(source, name + ".ome.zarr", data, x=x, z=z)
        group = zarr.open_group(str(store.store), mode="r+")
        group.attrs["zmart_microscopy"] = {
            "z_coordinate": {
                "acquisition_provenance": {"requested_stage_focus_z_um": z + 0.7},
            }
        }
    originals = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
    names = [name + ".ome.zarr" for name, *_ in inputs]
    if reverse:
        names.reverse()
    kwargs = dict(acquisition="test", piece=4)
    views = ViewSet(tmp_path / "view", **kwargs)
    try:
        views.publish(
            source,
            dict.fromkeys(names, 1),
            {"x_um": [0, 32], "y_um": [0, 8]},
            composition={"regions": "complete", "order": names},
            bake=bake,
        )
        views.close()
        views = ViewSet(tmp_path / "view", **kwargs)
        for mode, bottom, count in (("top", 0, 4), ("slice", 10, 6)):
            output = views.outputs[f"test_{mode}.zmartview.zarr"]
            held = output.composer()
            # Independent cold composer: a bake must not certify its own cache.
            made = Composer(
                read_the_mosaic_as_written(the_mosaic_written_down(held.mosaic)), piece=4
            )
            assert made.mosaic.corner_um[0] == bottom
            assert made.mosaic.shape(0)[0] == count
            for level in range(made.mosaic.levels):
                for plane in range(count):
                    for name, depth, z, x, value in inputs:
                        selected = min(plane, depth - 1) if mode == "top" else bottom + plane - z
                        covered = 0 <= selected < depth
                        col, px = divmod(x // 2**level, 4)
                        for t in range(2):
                            for c in range(2):
                                signal = made.values_for(level, plane, 0, col, t, c)
                                actual = 0 if signal is None else signal[0, px]
                                assert actual == (
                                    value + 10 * selected + 200 * t + 50 * c if covered else 0
                                )
                                assert (
                                    made.coverage_for(level, plane, 0, col, t, c)[0, px] == covered
                                )
                                path = f"{level}/c/{t}/{c}/{plane}/0/{col}"
                                assert pieces.built_bytes_behind(
                                    output._shown, path
                                ) == made.bytes_for(level, plane, 0, col, t, c)
            # The unused XY column remains transparent, including Top's held planes.
            assert not made.coverage_for(0, count - 1, 0, 6).any()
        assert all(p.read_bytes() == data for p, data in originals.items())
    finally:
        views.close()
        for output in views.outputs.values():
            pieces.forget(output._shown)


@pytest.mark.parametrize("bake", [False, True])
def test_adaptive_steps_sparse_coverage_and_live_extension(tmp_path, bake):
    source = tmp_path / "positions"
    source.mkdir()
    canvas = {"x_um": [0, 24], "y_um": [0, 8]}

    def write(name, depth, z, step, x, offset):
        data = np.broadcast_to(
            (np.arange(depth, dtype="uint16") * 10 + offset)[None, None, :, None, None],
            (1, 1, depth, 8, 8),
        ).copy()
        tile = write_tile(source, name, data, z=z, x=x)
        group = zarr.open_group(str(tile.store), mode="r+")
        ome = group.attrs["ome"]
        for ds in ome["multiscales"][0]["datasets"]:
            ds["coordinateTransformations"][0]["scale"][2] = step
        group.attrs["ome"] = ome

    write("short.ome.zarr", 2, 100, 2, 0, 0)
    views = ViewSet(tmp_path / "view", acquisition="a", piece=4)
    try:
        versions = {"short.ome.zarr": 1}
        for count in (0, 4, 6, 3):
            if count:
                write("adaptive.ome.zarr", count, 102, 0.5, 8, 100)
                versions["adaptive.ome.zarr"] = versions.get("adaptive.ome.zarr", 0) + 1
            composition = {"regions": "complete", "order": list(versions)}
            views.publish(source, versions, canvas, composition=composition, bake=bake)
            for mode in ("top", "slice"):
                output = views.outputs[f"a_{mode}.zmartview.zarr"]
                cold = Composer(
                    read_the_mosaic_as_written(the_mosaic_written_down(output.composer().mosaic)),
                    piece=4,
                )
                assert cold.mosaic.voxel_um(0)[0] == (1 if mode == "top" else (0.5 if count else 2))
                if mode == "top":
                    assert cold.mosaic.shape(0)[0] == max(2, count)
                    for plane in range(max(2, count)):
                        values = cold.values_for(0, plane, 0, 0)
                        assert (0 if values is None else values[0, 0]) == min(plane, 1) * 10
                        if count:
                            assert (
                                cold.values_for(0, plane, 0, 2)[0, 0]
                                == 100 + min(plane, count - 1) * 10
                            )
                else:
                    assert cold.mosaic.corner_um[0] == 100
                    # An acquired black first plane is opaque; empty XY is not.
                    assert cold.coverage_for(0, 0, 0, 0)[0, 0] == 1
                    assert not cold.coverage_for(0, 0, 0, 4).any()
                    if count:
                        assert (
                            cold.values_for(0, 2, 0, 0)[0, 0] == 10
                        )  # specimen 101, nearest native plane
                        assert cold.coverage_for(0, 2, 0, 2)[0, 0] == 0
                        assert cold.values_for(0, 4, 0, 2)[0, 0] == 100  # specimen 102
                for level in range(cold.mosaic.levels):
                    for plane in range(cold.mosaic.shape(level)[0]):
                        for col in range(cold.grid(level)[2]):
                            assert pieces.built_bytes_behind(
                                output._shown, f"{level}/c/{plane}/0/{col}"
                            ) == cold.bytes_for(level, plane, 0, col)
            # Idle announce must neither migrate twice nor recompute anything.
            revision = views.revision
            views.publish(source, versions, canvas, composition=composition, bake=bake)
            assert views.revision == revision
        # Native plane zero and two acquired, middle missing: mapped Slice
        # coverage must exclude that middle plane, never infer acquisition from fill.
        composition["regions"] = {
            "short.ome.zarr": [
                {
                    "frame": 0,
                    "channel": 0,
                    "origin": {"z": 0, "y": 0, "x": 0},
                    "shape": {"z": 2, "y": 8, "x": 8},
                }
            ],
            "adaptive.ome.zarr": [
                {
                    "frame": 0,
                    "channel": 0,
                    "origin": {"z": z, "y": 0, "x": 0},
                    "shape": {"z": 1, "y": 8, "x": 8},
                }
                for z in (0, 2)
            ],
        }
        views.publish(source, versions, canvas, composition=composition, bake=bake)
        for mode, plane in (("top", 1), ("slice", 5)):
            made = views.outputs[f"a_{mode}.zmartview.zarr"].composer()
            assert not made.coverage_for(0, plane, 0, 2).any()
            assert made.coverage_for(0, plane + 1, 0, 2).all()
    finally:
        views.close()
        for output in views.outputs.values():
            pieces.forget(output._shown)


def test_fractional_absolute_heights_use_one_stable_nearest_plane_grid(tmp_path):
    source = tmp_path / "positions"
    source.mkdir()
    for name, z, x in (("a", 62.79, 0), ("b", 64.26, 8)):
        write_tile(source, name + ".ome.zarr", np.ones((1, 1, 1, 8, 8), dtype="uint16"), x=x, z=z)
    views = ViewSet(tmp_path / "view", acquisition="a", piece=4)
    try:
        views.publish(
            source,
            {"a.ome.zarr": 1, "b.ome.zarr": 1},
            {"x_um": [0, 16], "y_um": [0, 8]},
            composition={"regions": "complete", "order": ["a.ome.zarr", "b.ome.zarr"]},
            bake=False,
        )
        made = views.outputs["a_slice.zmartview.zarr"].composer()
        assert made.mosaic.corner_um[0] == 63
        assert [tile.copies[0].corner_um[0] for tile in made.mosaic.tiles] == [63, 64]
        assert made.coverage_for(0, 0, 0, 0)[0, 0] == 1
        assert made.coverage_for(0, 0, 0, 2)[0, 0] == 0
        assert made.coverage_for(0, 1, 0, 2)[0, 0] == 1
    finally:
        views.close()


def test_old_placement_is_rebuilt_once_without_rewriting_originals(tmp_path, monkeypatch):
    source = tmp_path / "positions"
    source.mkdir()
    write_tile(source, "a.ome.zarr", np.full((1, 1, 3, 8, 8), 17, dtype="uint16"), z=50)
    canvas = {"x_um": [0, 8], "y_um": [0, 8]}
    composition = {"regions": "complete", "order": ["a.ome.zarr"]}
    versions = {"a.ome.zarr": 1}
    place = published._place_depth
    with monkeypatch.context() as old:
        old.setattr(
            published, "_place_depth", lambda tiles, **kw: place(tiles, **{**kw, "mode": None})
        )
        views = ViewSet(tmp_path / "view", acquisition="a", piece=4)
        views.publish(source, versions, canvas, composition=composition)
        views.close()
    for path in (tmp_path / "view").glob("*/publication.json"):
        state = json.loads(path.read_text())
        state["composition"].pop("depth_placement")
        path.write_text(json.dumps(state))
    views = ViewSet(tmp_path / "view", acquisition="a", piece=4)
    try:
        before = views.revision
        views.publish(source, versions, canvas, composition=composition)
        assert views.revision > before
        assert views.outputs["a_top.zmartview.zarr"].composer().mosaic.corner_um[0] == 0
        assert views.outputs["a_slice.zmartview.zarr"].composer().mosaic.corner_um[0] == 50
        before = views.revision
        views.publish(source, versions, canvas, composition=composition)
        assert views.revision == before
    finally:
        views.close()
