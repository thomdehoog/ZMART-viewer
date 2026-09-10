import json

import numpy as np
import pytest
import zarr
from test_view_sampling import fixture, oracle, write_tile

from zmart_viewer import pieces
from zmart_viewer.compose import MEAN_REDUCTION
from zmart_viewer.published import PublishedTransfer
from zmart_viewer.views import ViewSet


@pytest.mark.parametrize("bake", [False, True])
def test_later_acquisition_fills_only_its_sparse_gap(tmp_path, bake):
    source = tmp_path / "positions"
    source.mkdir()
    name = "p.ome.zarr"
    view = ViewSet(tmp_path / "view", acquisition="a", piece=4)
    data = np.zeros((1, 1, 2, 8, 8), dtype="uint16")
    mask = np.zeros((8, 16), dtype=bool)
    regions = []
    try:
        for revision, columns in enumerate(((0, 3), (2,)), 1):
            for x in columns:
                regions.append(
                    {
                        "frame": 0,
                        "channel": 0,
                        "origin": {"z": 0, "y": 0, "x": x},
                        "shape": {"z": 2, "y": 2, "x": 1},
                    }
                )
                mask[:2, x] = True
            if revision == 2:
                data[..., :2, 2] = 64
            write_tile(source, name, data)
            view.publish(
                source,
                {name: revision},
                {"x_um": [0, 16], "y_um": [0, 8]},
                composition={
                    "regions": {name: regions},
                    "order": [name],
                    "pyramid_reduction": MEAN_REDUCTION,
                },
                bake=bake,
            )
            expected = np.zeros((8, 16), dtype="uint16")
            expected[:, :8] = data[0, 0, 0]
            covered = mask.copy()
            for level in range(3):
                for output in view.outputs.values():
                    made = output.composer()
                    for y in range(made.grid(level)[1]):
                        for x in range(made.grid(level)[2]):
                            area = (slice(y * 4, (y + 1) * 4), slice(x * 4, (x + 1) * 4))
                            signal = made.values_for(level, 0, y, x)
                            if signal is None:
                                signal = np.zeros((4, 4), dtype="uint16")
                            h, w = expected[area].shape
                            np.testing.assert_array_equal(signal[:h, :w], expected[area])
                            np.testing.assert_array_equal(
                                made.coverage_for(level, 0, y, x)[:h, :w], covered[area]
                            )
                            assert pieces.built_bytes_behind(
                                output._shown, f"{level}/c/0/{y}/{x}"
                            ) == made.bytes_for(level, 0, y, x)
                h, w = expected.shape
                expected = np.rint(expected.reshape(h // 2, 2, w // 2, 2).mean((1, 3))).astype(
                    "uint16"
                )
                covered = covered.reshape(h // 2, 2, w // 2, 2).any((1, 3))
    finally:
        view.close()
        for output in view.outputs.values():
            pieces.forget(output._shown)


def test_failed_geometry_change_can_recover_to_last_committed_shape(tmp_path, monkeypatch):
    source = tmp_path / "positions"
    source.mkdir()
    name = "p.ome.zarr"
    view = ViewSet(tmp_path / "view", acquisition="a", piece=4)
    canvas = {"x_um": [0, 8], "y_um": [0, 8]}
    composition = {"regions": "complete", "order": [name], "pyramid_reduction": MEAN_REDUCTION}
    declare = PublishedTransfer._declare_levels

    def fail(output, *args, **kwargs):
        declare(output, *args, **kwargs)
        raise OSError("Interrupted geometry declaration")

    try:
        write_tile(source, name, np.ones((1, 1, 3, 8, 8), dtype="uint16"))
        view.publish(source, {name: 1}, canvas, composition=composition)
        write_tile(source, name, np.ones((2, 1, 3, 8, 8), dtype="uint16"))
        monkeypatch.setattr(PublishedTransfer, "_declare_levels", fail)
        with pytest.raises(OSError):
            view.publish(source, {name: 2}, canvas, composition=composition)
        monkeypatch.setattr(PublishedTransfer, "_declare_levels", declare)
        write_tile(source, name, np.full((1, 1, 3, 8, 8), 7, dtype="uint16"))
        view.publish(source, {name: 3}, canvas, composition=composition)
        for output in view.outputs.values():
            made = output.composer()
            assert not (output._shown / "pending.json").exists()
            for level in range(made.mosaic.levels):
                assert json.loads(
                    (output._shown / str(level) / "zarr.json").read_text()
                ) == json.loads(made.array_json(level))
                assert pieces.built_bytes_behind(
                    output._shown, f"{level}/c/0/0/0"
                ) == made.bytes_for(level, 0, 0, 0)
    finally:
        view.close()
        for output in view.outputs.values():
            pieces.forget(output._shown)


@pytest.mark.parametrize("bake", [False, True])
@pytest.mark.parametrize("outer", [(2, 1), (1, 2), (2, 2)])
def test_saved_views_preserve_chunks_through_frame_growth_and_shrink(tmp_path, bake, outer):
    source = tmp_path / "positions"
    source.mkdir()
    name = "p.ome.zarr"
    composition = {"regions": "complete", "order": [name], "pyramid_reduction": MEAN_REDUCTION}
    canvas = {"x_um": [0, 8], "y_um": [0, 8]}
    view = ViewSet(tmp_path / "view", acquisition="a", piece=4)
    try:
        for revision, (frames, channels) in enumerate(((1, 1), outer, (1, 1)), 1):
            if revision > 1:
                view.close()
                # A saved view owns its chunk layout; callers need not remember it.
                view = ViewSet(tmp_path / "view", acquisition="a")
            data = (
                np.arange(frames * channels * 3 * 8 * 8, dtype="uint16").reshape(
                    frames, channels, 3, 8, 8
                )
                + revision
            )
            write_tile(source, name, data)
            view.publish(source, {name: revision}, canvas, composition=composition, bake=bake)
            for output in view.outputs.values():
                made = output.composer()
                assert output._piece == 4
                assert made.mosaic.frame_room == (frames, channels)
                for level in range(made.mosaic.levels):
                    metadata = json.loads((output._shown / str(level) / "zarr.json").read_text())
                    assert metadata == json.loads(made.array_json(level))
                    for t in range(frames):
                        for c in range(channels):
                            prefix = f"{t}/{c}/" if (frames, channels) != (1, 1) else ""
                            for z in range(made.grid(level)[0]):
                                for y in range(made.grid(level)[1]):
                                    for x in range(made.grid(level)[2]):
                                        route = f"{level}/c/{prefix}{z}/{y}/{x}"
                                        assert pieces.built_bytes_behind(
                                            output._shown, route
                                        ) == made.bytes_for(level, z, y, x, t, c)
    finally:
        view.close()
        for output in view.outputs.values():
            pieces.forget(output._shown)


@pytest.mark.parametrize("bake", [False, True])
def test_named_views_reopen_pixels_coverage_and_idle(tmp_path, bake):
    source = tmp_path / "positions"
    source.mkdir()
    mosaic, originals = fixture(source)
    names = [t.name for t in mosaic.tiles]
    composition = {
        "regions": {t.name: [r.as_written() for r in t.acquired_regions] for t in mosaic.tiles},
        "order": names,
        "z_references": {name: 0 for name in names},
        "pyramid_reduction": MEAN_REDUCTION,
    }
    views = ViewSet(
        tmp_path / "view",
        acquisition="overview",
        projections=("min", "max", "sum"),
        projection_folder=tmp_path / "projections",
        piece=4,
    )
    versions = dict.fromkeys(names, 1)
    canvas = {"x_um": [0, 20], "y_um": [0, 8]}
    try:
        views.publish(source, versions, canvas, composition=composition, bake=bake)
        assert len(views.sources) == 5
        initial = views.revision
        stamps = {p: p.stat().st_mtime_ns for p in (tmp_path / "view").rglob("*") if p.is_file()}
        views.publish(source, versions, canvas, composition=composition, bake=bake)
        assert views.revision == initial
        assert stamps == {
            p: p.stat().st_mtime_ns for p in (tmp_path / "view").rglob("*") if p.is_file()
        }
        for mode in ("slice", "top"):
            output = views.outputs[f"overview_{mode}.zmartview.zarr"]
            output.close()
            made = output.composer()
            # The singleton's display placement is zero, not its acquired height.
            placed = [
                (data, mask, 0 if data.shape[2] == 1 else z, x) for data, mask, z, x in originals
            ]
            for z in (0, 2, 10, 12, 15, 24, 25):
                for level in range(3):
                    expected, coverage = oracle(placed, mode, z, 1, 1, level)
                    actual = made.values_for(level, z, 0, 0, 1, 1)
                    if actual is None:
                        actual = np.zeros((4, 4))
                    h, w = min(4, expected.shape[0]), min(4, expected.shape[1])
                    np.testing.assert_array_equal(actual[:h, :w], expected[:h, :w])
                    np.testing.assert_array_equal(
                        made.coverage_for(level, z, 0, 0, 1, 1)[:h, :w], coverage[:h, :w]
                    )
                    # Exercise the real served bake address, including Top aliases.
                    served = pieces.built_bytes_behind(output._shown, f"{level}/c/1/1/{z}/0/0")
                    assert served == made.bytes_for(level, z, 0, 0, 1, 1)
            attrs = json.loads((output._shown / "zarr.json").read_text())
            assert attrs["attributes"]["zmart"]["view"]["type"] == mode
    finally:
        views.close()
        for name in views.sources:
            pieces.forget(views.folder / name)


def test_failed_projection_keeps_previous_fine_and_coarse_pixels(tmp_path):
    source = tmp_path / "positions"
    source.mkdir()
    names = ["a.ome.zarr", "b.ome.zarr"]
    for i, name in enumerate(names):
        write_tile(source, name, np.full((1, 1, 2, 8, 8), 3, dtype="uint32"), x=i * 8)
    views = ViewSet(
        tmp_path / "view",
        acquisition="targets",
        modes=(),
        projections=("sum",),
        projection_folder=tmp_path / "projections",
        piece=4,
    )
    composition = {"regions": "complete", "order": names, "pyramid_reduction": MEAN_REDUCTION}
    canvas = {"x_um": [0, 16], "y_um": [0, 8]}
    try:
        views.publish(source, dict.fromkeys(names, 1), canvas, composition=composition)
        output = next(iter(views.outputs.values()))
        old_paths = [tile.store for tile in output.composer().mosaic.tiles]
        old = [output.composer().values_for(level, 0, 0, 0).copy() for level in range(3)]
        zarr.open_group(str(source / names[0]), mode="r+")["0"][:] = 5
        zarr.open_group(str(source / names[1]), mode="r+")["0"][:] = 2**32 - 1
        with pytest.raises(OverflowError):
            views.publish(source, dict.fromkeys(names, 2), canvas, composition=composition)
        assert views.revision == 1
        output.close()
        for level, expected in enumerate(old):
            np.testing.assert_array_equal(output.composer().values_for(level, 0, 0, 0), expected)
        assert [tile.store for tile in output.composer().mosaic.tiles] == old_paths
        zarr.open_group(str(source / names[1]), mode="r+")["0"][:] = 7
        views.publish(source, dict.fromkeys(names, 2), canvas, composition=composition)
        assert views.revision == 2
        assert output.composer().values_for(0, 0, 0, 0)[0, 0] == 10
    finally:
        views.close()


def test_idle_view_publish_reads_no_original_metadata(tmp_path, monkeypatch):
    source = tmp_path / "positions"
    source.mkdir()
    name = "position.ome.zarr"
    write_tile(source, name, np.ones((1, 1, 2, 8, 8), dtype="uint16"))
    views = ViewSet(
        tmp_path / "view",
        acquisition="a",
        projections=("min", "max", "sum"),
        projection_folder=tmp_path / "projections",
        piece=4,
    )
    composition = {"regions": "complete", "order": [name], "pyramid_reduction": MEAN_REDUCTION}
    canvas = {"x_um": [0, 8], "y_um": [0, 8]}
    try:
        views.publish(source, {name: 1}, canvas, composition=composition)
        from pathlib import Path

        read = Path.read_text

        def watched(path, *args, **kwargs):
            assert source not in path.parents, f"Idle publication read original metadata: {path}"
            return read(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", watched)
        views.publish(source, {name: 1}, canvas, composition=composition)
        with pytest.raises(ValueError, match="source folder"):
            views.publish(tmp_path, {name: 1}, canvas, composition=composition)
    finally:
        views.close()


@pytest.mark.parametrize("modes,methods", [(("slice",), ()), ((), ("sum",))])
def test_reopen_preserves_original_folder_identity(tmp_path, modes, methods):
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        write_tile(tmp_path / name, "p.ome.zarr", np.ones((1, 1, 2, 8, 8), dtype="uint16"))
    options = dict(
        acquisition="a",
        modes=modes,
        projections=methods,
        projection_folder=tmp_path / "projections",
        piece=4,
    )
    view = ViewSet(tmp_path / "view", **options)
    composition = {"regions": "complete", "order": ["p.ome.zarr"]}
    canvas = {"x_um": [0, 8], "y_um": [0, 8]}
    view.publish(tmp_path / "a", {"p.ome.zarr": 1}, canvas, composition=composition)
    view.close()
    view = ViewSet(tmp_path / "view", **options)
    try:
        with pytest.raises(ValueError, match="source folder"):
            view.publish(tmp_path / "b", {"p.ome.zarr": 1}, canvas, composition=composition)
    finally:
        view.close()
