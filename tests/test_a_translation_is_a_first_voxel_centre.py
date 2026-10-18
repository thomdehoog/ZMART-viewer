"""A translation names the centre of the first voxel, at every level.

OME-NGFF RFC-5 (accepted 2025-12-01): "The pixel/voxel center is the origin of
the continuous coordinate system". A store's translation is therefore the
centre of its first voxel, and a level made by averaging 2x2 voxels has its
first voxel centred at ``first_centre + 0.5 * (2**level - 1) * base``. Depth is
a plane's own height and stays as it is.

A canvas, on the other hand, is a rectangle on the specimen: its bounds are
edges. The first voxel of a picture baked onto a canvas is centred half a
voxel inside its low edge, and a tile whose first voxel is centred on that
grid lands on it as it is, without being moved half a voxel either way.
"""

import json
import math

import numpy as np
import pytest
import zarr
from zmart_viewer.picture.arrangement import read_one_tile
from zmart_viewer.views.projections import write_projection
from zmart_viewer.views.publishing import STORE, PublishedTransfer, validated_composition


def a_centred_store(folder, name, *, first_y, first_x, voxel, side=8, levels=3, value=900):
    """A position store written the way the writers now write: first-voxel centres."""
    group = zarr.open_group(str(folder / name), mode="w", zarr_format=3)
    datasets = []
    for level in range(levels):
        factor = 2**level
        group.create_array(
            str(level),
            data=np.full((1, 1, 1, side // factor, side // factor), value, dtype="uint16"),
            chunks=(1, 1, 1, 4, 4),
        )
        shift = 0.5 * (factor - 1) * voxel
        datasets.append(
            {
                "path": str(level),
                "coordinateTransformations": [
                    {"type": "scale", "scale": [1, 1, 1, voxel * factor, voxel * factor]},
                    {
                        "type": "translation",
                        "translation": [0, 0, 0, first_y + shift, first_x + shift],
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
    return folder / name


def the_translations_of(store):
    """Every level's (scale, translation) as the store describes them."""
    held = json.loads((store / "zarr.json").read_text(encoding="utf-8"))
    found = []
    for dataset in held["attributes"]["ome"]["multiscales"][0]["datasets"]:
        transforms = {one["type"]: one for one in dataset["coordinateTransformations"]}
        found.append((transforms["scale"]["scale"], transforms["translation"]["translation"]))
    return found


@pytest.mark.parametrize("voxel", [1.0, 2.5, 0.3])
@pytest.mark.parametrize("low", [(0.0, 0.0), (-128.0, 17.5)])
@pytest.mark.parametrize("k", [(0, 0), (3, 5)])
def test_a_tile_centred_on_the_canvas_grid_lands_on_it_unmoved(tmp_path, voxel, low, k):
    """No half-voxel rounding: the grid is ``low + voxel/2 + k*voxel``."""
    low_y, low_x = low
    first_y = low_y + voxel / 2 + k[0] * voxel
    first_x = low_x + voxel / 2 + k[1] * voxel
    a_centred_store(tmp_path, "a.ome.zarr", first_y=first_y, first_x=first_x, voxel=voxel)
    written = read_one_tile(tmp_path / "a.ome.zarr")
    canvas = {"y_um": [low_y, low_y + 16 * voxel], "x_um": [low_x, low_x + 16 * voxel]}
    view = PublishedTransfer(tmp_path / STORE, piece=4)
    try:
        view.publish(
            tmp_path,
            {"a.ome.zarr": 1},
            canvas,
            composition={"regions": "complete", "order": ["a.ome.zarr"]},
        )
        mosaic = view.composer().mosaic
        assert mosaic.corner_um[1] == pytest.approx(low_y + voxel / 2, abs=1e-9)
        assert mosaic.corner_um[2] == pytest.approx(low_x + voxel / 2, abs=1e-9)
        tile = mosaic.tiles[0]
        for placed, read in zip(tile.copies, written.copies, strict=True):
            for axis in (1, 2):
                assert placed.corner_um[axis] == pytest.approx(read.corner_um[axis], abs=1e-9), (
                    "a tile already on the canvas grid was moved"
                )
        assert mosaic.lands_at(tile, 0) == (0, k[0], k[1])
    finally:
        view.close()


@pytest.mark.parametrize("composed", [True, False])
@pytest.mark.parametrize("voxel", [1.0, 0.1, 0.3])
def test_tiles_touching_the_canvas_edges_are_accepted(tmp_path, voxel, composed):
    """A tile filling the canvas edge to edge is inside it, not half a voxel out."""
    low = 0.7
    a_centred_store(
        tmp_path, "a.ome.zarr", first_y=low + voxel / 2, first_x=low + voxel / 2, voxel=voxel
    )
    canvas = {"y_um": [low, low + 8 * voxel], "x_um": [low, low + 8 * voxel]}
    view = PublishedTransfer(tmp_path / STORE, piece=4)
    try:
        view.publish(
            tmp_path,
            {"a.ome.zarr": 1},
            canvas,
            composition={"regions": "complete", "order": ["a.ome.zarr"]} if composed else None,
        )
        assert view.composer().mosaic.shape(0)[1:] == (8, 8)
    finally:
        view.close()


def test_a_tile_a_voxel_beyond_the_canvas_is_refused(tmp_path):
    a_centred_store(tmp_path, "a.ome.zarr", first_y=0.5, first_x=1.5, voxel=1.0)
    view = PublishedTransfer(tmp_path / STORE, piece=4)
    try:
        with pytest.raises(ValueError, match="outside the declared specimen canvas"):
            view.publish(
                tmp_path,
                {"a.ome.zarr": 1},
                {"y_um": [0, 8], "x_um": [0, 8]},
            )
    finally:
        view.close()


@pytest.mark.parametrize("composed", [True, False])
def test_a_published_pictures_levels_are_first_voxel_centres(tmp_path, composed):
    """Every declared level, the baked extra ones included, at ``low + voxel_l/2``."""
    voxel, low = 2.5, (-40.0, 10.0)
    names = []
    for column in range(4):
        name = f"p{column}.ome.zarr"
        a_centred_store(
            tmp_path,
            name,
            first_y=low[0] + voxel / 2,
            first_x=low[1] + voxel / 2 + 8 * column * voxel,
            voxel=voxel,
            levels=2,
        )
        names.append(name)
    canvas = {"y_um": [low[0], low[0] + 8 * voxel], "x_um": [low[1], low[1] + 32 * voxel]}
    view = PublishedTransfer(tmp_path / STORE, piece=4)
    try:
        view.publish(
            tmp_path,
            dict.fromkeys(names, 1),
            canvas,
            composition={"regions": "complete", "order": names} if composed else None,
        )
        levels = the_translations_of(tmp_path / STORE)
        assert len(levels) >= 4, "the picture must reach past the stores' own two levels"
        for scale, translation in levels:
            assert translation[-3] == 0
            assert translation[-2] == pytest.approx(low[0] + scale[-2] / 2, abs=1e-9)
            assert translation[-1] == pytest.approx(low[1] + scale[-1] / 2, abs=1e-9)
    finally:
        view.close()


def test_the_governed_pictures_levels_are_first_voxel_centres(tmp_path):
    """The live picture, its baked extra levels included, at ``centre_0 + 0.5(2^l-1)base``."""
    from record_fixtures import some_specimen
    from test_the_composer_obeys_the_manifest import PIECE, a_governed_run
    from zmart_viewer.picture.built_picture import declare_a_governed_picture

    run = a_governed_run(tmp_path / "run")
    run.write_and_publish("posA", some_specimen(700))
    run.write_and_publish("posB", some_specimen(900))
    base = float(run.profile.voxel_size["x"])
    store = declare_a_governed_picture(
        tmp_path / "views", run.folder, name="live", piece=PIECE, bake=True
    )
    levels = the_translations_of(store)
    assert len(levels) > len(run.profile.levels), "the bake must add a level of its own"
    for level, (scale, translation) in enumerate(levels):
        centre = base / 2 + 0.5 * (2**level - 1) * base
        assert scale[-1] == base * 2**level
        assert translation == pytest.approx([0.0, centre, centre], abs=1e-9), f"level {level}"


def test_a_live_position_store_declares_first_voxel_centres(tmp_path):
    """The live writer: per level ``origin*voxel + voxel_l/2`` in y and x, depth as it was."""
    from record_fixtures import some_specimen
    from test_the_composer_obeys_the_manifest import a_governed_run

    run = a_governed_run(tmp_path / "run")
    run.write_and_publish("posA", some_specimen(700))
    run.write_and_publish("posB", some_specimen(900))
    voxel = {axis: float(run.profile.voxel_size.get(axis, 1.0)) for axis in "zyx"}
    for placement in run.layout.positions:
        origin = {axis: float(placement.origin.get(axis, 0)) for axis in "zyx"}
        for scale, translation in the_translations_of(run.position_store(placement.position_id)):
            expected = [
                origin["z"] * voxel["z"],
                origin["y"] * voxel["y"] + scale[-2] / 2,
                origin["x"] * voxel["x"] + scale[-1] / 2,
            ]
            assert translation[-3:] == pytest.approx(expected, abs=1e-9)
            assert translation[:-3] == [0.0] * (len(translation) - 3)


def test_the_scene_places_first_voxel_centres(tmp_path):
    """The run's scene agrees with its stores: pixel index 0 maps to the voxel centre."""
    from test_the_composer_obeys_the_manifest import a_governed_run
    from zmart_viewer.live.record.scene import build_the_scene

    run = a_governed_run(tmp_path / "run")
    scene = build_the_scene(run.profile, run.layout)
    voxel = {axis: float(run.profile.voxel_size.get(axis, 1.0)) for axis in "zyx"}
    origins = {p.position_id: p.origin for p in run.layout.positions}
    for image in scene.images:
        if image.placement is None:
            continue
        origin = origins.get(image.image_id, {})
        at = image.placement.applied_to({})
        assert at["z"] == float(origin.get("z", 0)) * voxel["z"]
        for axis in "yx":
            assert at[axis] == pytest.approx(
                float(origin.get(axis, 0)) * voxel[axis] + voxel[axis] / 2
            ), image.image_id


def test_a_request_naming_an_xy_origin_is_refused():
    """There is one convention; a request cannot choose another."""
    with pytest.raises(ValueError, match="xy_origin"):
        validated_composition(
            {"a.ome.zarr": 1},
            {"y_um": [0, 8], "x_um": [0, 8]},
            {"regions": "complete", "order": ["a.ome.zarr"], "xy_origin": "corner"},
            bake=True,
        )


def as_an_older_viewer_recorded_it(state):
    """The publication state an older viewer wrote for a corner-convention request.

    It kept the canvas's low edge as the mosaic corner and each copy's corner
    as its stores said it, the same at every level, and recorded the choice.
    """
    old = json.loads(json.dumps(state))
    mosaic = old["mosaic"]
    base = mosaic["tiles"][0]["copies"][0]["voxel_um"]
    mosaic["corner_um"] = [
        mosaic["corner_um"][0],
        mosaic["corner_um"][1] - base[1] / 2,
        mosaic["corner_um"][2] - base[2] / 2,
    ]
    for tile in mosaic["tiles"]:
        for copy in tile["copies"]:
            voxel = copy["voxel_um"]
            copy["corner_um"] = [
                copy["corner_um"][0],
                copy["corner_um"][1] - voxel[1] / 2,
                copy["corner_um"][2] - voxel[2] / 2,
            ]
    mosaic["xy_origin"] = "corner"
    old["composition"]["xy_origin"] = "corner"
    return old


def test_an_older_corner_view_opens_as_it_was_and_refuses_new_publishing(tmp_path):
    """Old views already describe centres; they open unchanged and are not written to."""
    a_centred_store(tmp_path, "a.ome.zarr", first_y=0.5, first_x=0.5, voxel=1.0)
    canvas = {"y_um": [0, 8], "x_um": [0, 16]}
    composition = {"regions": "complete", "order": ["a.ome.zarr"]}
    view = PublishedTransfer(tmp_path / STORE, piece=4)
    try:
        view.publish(tmp_path, {"a.ome.zarr": 1}, canvas, composition=composition)
        made = view.composer()
        fresh_corner = made.mosaic.corner_um
        fresh_copies = [copy.corner_um for copy in made.mosaic.tiles[0].copies]
        fresh_description = json.loads(made.group_json())
    finally:
        view.close()

    publication = tmp_path / STORE / "publication.json"
    state = json.loads(publication.read_text(encoding="utf-8"))
    publication.write_text(json.dumps(as_an_older_viewer_recorded_it(state)), encoding="utf-8")

    reopened = PublishedTransfer(tmp_path / STORE, piece=4)
    try:
        made = reopened.composer()
        assert made.mosaic.corner_um == pytest.approx(fresh_corner)
        for copy, corner in zip(made.mosaic.tiles[0].copies, fresh_copies, strict=True):
            assert copy.corner_um == pytest.approx(corner)
        assert json.loads(made.group_json()) == fresh_description
        with pytest.raises(ValueError, match="publish into a new view"):
            reopened.publish(tmp_path, {"a.ome.zarr": 2}, canvas, composition=composition)
    finally:
        reopened.close()


def test_a_projection_records_no_convention_and_replaces_an_older_corner_one(tmp_path):
    """A projection made under the corner request is not taken for one made now."""
    source = a_centred_store(tmp_path, "a.ome.zarr", first_y=0.5, first_x=8.5, voxel=1.0)
    destination = tmp_path / "projections" / "a.ome.zarr"
    write_projection(source, destination, "max")
    held = zarr.open_group(str(destination), mode="r+")
    recorded = dict(held.attrs["zmart_projection"])
    held.attrs["zmart_projection"] = {**recorded, "recipe": 1, "xy_origin": "corner"}

    write_projection(source, destination, "max")
    recipe = zarr.open_group(str(destination), mode="r").attrs["zmart_projection"]
    assert "xy_origin" not in recipe
    for level, (scale, translation) in enumerate(the_translations_of(destination)):
        assert scale[-1] == 2**level
        assert translation[-2:] == [scale[-2] / 2, 8 + scale[-1] / 2]
        assert math.isclose(translation[-2], 0.5 * 2**level)
