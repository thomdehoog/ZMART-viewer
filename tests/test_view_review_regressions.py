"""Independent regression oracles for the 0.3.0 review findings."""

import json

import numpy as np
import pytest
from test_view_sampling import INPUT_FORMATS, write_tile

from zmart_viewer import pieces
from zmart_viewer.compose import Composer, read_the_mosaic_as_written
from zmart_viewer.views import ViewSet


@pytest.mark.parametrize("direct", [False, True])
def test_equivalent_region_order_is_no_write(tmp_path, monkeypatch, direct):
    import zarr

    from zmart_viewer import published

    positions = tmp_path / "positions"
    positions.mkdir()
    write_tile(positions, "p.ome.zarr", np.zeros((1, 1, 3, 8, 8), dtype="uint16"))
    regions = [
        {
            "frame": 0,
            "channel": 0,
            "origin": {"z": 0, "y": 0, "x": x},
            "shape": {"z": 3, "y": 8, "x": 2},
        }
        for x in (0, 6)
    ]
    view = ViewSet(
        tmp_path / "view",
        acquisition="a",
        projections=("min", "max", "sum"),
        projection_folder=tmp_path / "projections",
        piece=4,
    )
    if direct:
        view.close()
        view = published.PublishedTransfer(tmp_path / "aggregate.ome.zarr", piece=4)
    composition = {"regions": {"p.ome.zarr": regions}, "order": ["p.ome.zarr"]}
    canvas = {"x_um": [0, 8], "y_um": [0, 8]}
    try:
        revision = view.publish(positions, {"p.ome.zarr": 1}, canvas, composition=composition)

        def unexpected_write(*args, **kwargs):
            pytest.fail("An equivalent announcement must not write arrays or publication metadata")

        monkeypatch.setattr(zarr.Array, "__setitem__", unexpected_write)
        monkeypatch.setattr(published, "_atomic_json", unexpected_write)
        composition["regions"]["p.ome.zarr"] = [*reversed(regions), regions[0]]
        assert (
            view.publish(positions, {"p.ome.zarr": 1}, canvas, composition=composition) == revision
        )
    finally:
        view.close()


def fresh(output):
    state = json.loads((output._shown / "publication.json").read_text())
    return Composer(read_the_mosaic_as_written(state["mosaic"]), piece=output._piece)


@pytest.mark.parametrize("different_spacing", [False, True])
@pytest.mark.parametrize("input_format", INPUT_FORMATS)
@pytest.mark.parametrize("bake", [False, True])
def test_shared_folder_publication_owners_advance_independently(
    tmp_path, different_spacing, input_format, bake
):
    import zarr

    from zmart_viewer.library import Library
    from zmart_viewer.published import PublishedFolders

    library = Library()
    published = PublishedFolders(library)
    composition = {"regions": "complete", "order": ["p.ome.zarr"]}
    canvas = {"x_um": [0, 8], "y_um": [0, 8]}
    numbers = {}
    try:
        for name in ("a", "b"):
            folder = tmp_path / name
            folder.mkdir()
            tile = write_tile(
                folder,
                "p.ome.zarr",
                np.ones((1, 1, 2, 8, 8), dtype="uint16"),
                input_format=input_format,
            )
            if name == "b" and different_spacing:
                group = zarr.open_group(str(tile.store), mode="r+")
                attrs = dict(group.attrs)
                for level in (attrs if input_format == "v2" else attrs["ome"])["multiscales"][0][
                    "datasets"
                ]:
                    scale = level["coordinateTransformations"][0]["scale"]
                    scale[2:] = [2, scale[3] * 0.5, scale[4] * 0.5]
                group.attrs.update(attrs)
            numbers[name] = published.open(
                folder,
                canvas=canvas,
                versions={"p.ome.zarr": 1},
                composition=composition,
                bake=bake,
                views={"path": str(tmp_path / "view"), "acquisition": name},
            )
        assert len(library.datasets()) == 2
        assert numbers["a"] != numbers["b"]
        cold = Library()
        cold.open(tmp_path / "view")
        assert {d.acquisition for d in cold.datasets()} == {"a", "b"}
        assert len(cold.entries()) == 4
        published.announce(
            [
                {
                    "path": str(tmp_path / "a"),
                    "source_revisions": {"p.ome.zarr": 2},
                    "composition": composition,
                }
            ]
        )
        for mode in ("slice", "top"):
            assert published.source_revision(numbers["a"], f"a_{mode}.zmartview.zarr") == 2
            assert published.source_revision(numbers["b"], f"b_{mode}.zmartview.zarr") == 1
        assert len(published.entries(library.entries())) == 4
        assert dict(published.refresh()) == {numbers["a"]: 4, numbers["b"]: 2}
        library.close(numbers["a"])
        assert published.refresh() == ((numbers["b"], 2),)
        published.announce(
            [
                {
                    "path": str(tmp_path / "b"),
                    "source_revisions": {"p.ome.zarr": 2},
                    "composition": composition,
                }
            ]
        )
        assert published.revisions() == ((numbers["b"], 4),)
    finally:
        published.close()


@pytest.mark.parametrize("bake", [False, True])
@pytest.mark.parametrize("value", [0.25, 0.75])
def test_default_float_views_preserve_fractional_means(tmp_path, bake, value):
    positions = tmp_path / "positions"
    positions.mkdir()
    # The input pyramids round floats. Without a certified reducer, they must
    # not be substituted for a fractional reduction of the original pixels.
    names = [f"p{i}.ome.zarr" for i in range(4)]
    for i, name in enumerate(names):
        write_tile(positions, name, np.full((2, 2, 3, 32, 32), value, dtype="float32"), x=32 * i)
    view = ViewSet(tmp_path / "view", acquisition="a", piece=4)
    try:
        view.publish(
            positions,
            dict.fromkeys(names, 1),
            {"x_um": [0, 128], "y_um": [0, 32]},
            composition={"regions": "complete", "order": names},
            bake=bake,
        )
        for output in view.outputs.values():
            made = fresh(output)
            try:
                for level in range(made.mosaic.levels):
                    for t in range(2):
                        for c in range(2):
                            values = made.values_for(level, 0, 0, 0, t, c)
                            assert values is not None
                            assert values[0, 0] == value
                            if bake and level >= 2:
                                assert (output._shown / f"{level}/c/{t}/{c}/0/0/0").is_file()
                            assert pieces.built_bytes_behind(
                                output._shown, f"{level}/c/{t}/{c}/0/0/0"
                            ) == made.bytes_for(level, 0, 0, 0, t, c)
            finally:
                made.close()
    finally:
        view.close()
        for output in view.outputs.values():
            pieces.forget(output._shown)


@pytest.mark.parametrize("method", ["min", "max", "sum"])
@pytest.mark.parametrize("reopen", [False, True])
@pytest.mark.parametrize("retire", [False, True])
def test_projection_only_rejects_stale_original_revision(tmp_path, method, reopen, retire):
    positions = tmp_path / "positions"
    positions.mkdir()
    options = dict(
        acquisition="a",
        modes=(),
        projections=(method,),
        projection_folder=tmp_path / "projections",
        piece=4,
    )
    view = ViewSet(tmp_path / "view", **options)
    canvas = {"x_um": [0, 8], "y_um": [0, 8]}
    composition = {"regions": "complete", "order": ["p.ome.zarr"]}
    try:
        for revision, value in ((1, 3), (2, 7)):
            write_tile(positions, "p.ome.zarr", np.full((1, 1, 2, 8, 8), value, dtype="uint16"))
            view.publish(positions, {"p.ome.zarr": revision}, canvas, composition=composition)
        if retire:
            write_tile(positions, "other.ome.zarr", np.ones((1, 1, 2, 8, 8), dtype="uint16"))
            view.publish(
                positions,
                {"other.ome.zarr": 1},
                canvas,
                composition={"regions": "complete", "order": ["other.ome.zarr"]},
            )
        if reopen:
            view.close()
            view = ViewSet(tmp_path / "view", **options)
        revision = view.revision
        with pytest.raises(ValueError, match="regress"):
            view.publish(positions, {"p.ome.zarr": 1}, canvas, composition=composition)
        assert view.revision == revision
        if not retire:
            output = next(iter(view.outputs.values()))
            assert output.composer().values_for(0, 0, 0, 0)[0, 0] == (14 if method == "sum" else 7)
    finally:
        view.close()


@pytest.mark.parametrize("outer", [(1, 1), (2, 2)])
def test_shrink_never_serves_retired_chunk_addresses(tmp_path, outer):
    positions = tmp_path / "positions"
    positions.mkdir()
    view = ViewSet(tmp_path / "view", acquisition="a", piece=4)
    canvas = {"x_um": [0, 8], "y_um": [0, 8]}
    composition = {"regions": "complete", "order": ["p.ome.zarr"]}
    try:
        write_tile(positions, "p.ome.zarr", np.full((*outer, 3, 8, 8), 111, dtype="uint16"))
        view.publish(positions, {"p.ome.zarr": 1}, canvas, composition=composition)
        write_tile(positions, "p.ome.zarr", np.full((outer[0], 1, 1, 8, 8), 222, dtype="uint16"))
        view.publish(positions, {"p.ome.zarr": 2}, canvas, composition=composition)
        for output in view.outputs.values():
            prefix = "0/0/" if outer[0] > 1 else ""
            assert pieces.built_bytes_behind(output._shown, f"2/c/{prefix}1/0/0") is None
            if outer[1] > 1:
                assert pieces.built_bytes_behind(output._shown, "2/c/0/1/0/0/0") is None
    finally:
        view.close()
        for output in view.outputs.values():
            pieces.forget(output._shown)
