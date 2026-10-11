"""A projected view from projections the microscope's driver already made.

A driver may save each capture's max projection itself, beside the capture,
under the capture's own store name. The view set then reads those and makes
none of its own, so the projection exists once. A flat capture is its own
projection: its folder is the projection folder.
"""

import numpy as np
import pytest
from test_view_sampling import write_tile
from zmart_viewer.views.slice_top_projection import ViewSet

CANVAS = {"x_um": [0, 16], "y_um": [0, 8]}


def a_view(tmp_path, projections):
    return ViewSet(
        tmp_path / "view",
        acquisition="a",
        projections=("max",),
        projection_folder=projections,
        projections_given=True,
        piece=4,
    )


def test_the_drivers_projection_is_shown_and_none_is_made(tmp_path):
    data, projections = tmp_path / "data", tmp_path / "projections"
    data.mkdir()
    projections.mkdir()
    write_tile(data, "p.ome.zarr", np.full((1, 1, 3, 8, 8), 50, dtype="uint16"))
    # Not the max of the stack above: the view must show what it was given.
    write_tile(projections, "p.ome.zarr", np.full((1, 1, 1, 8, 8), 90, dtype="uint16"))
    views = a_view(tmp_path, projections)
    try:
        views.publish(data, {"p.ome.zarr": 1}, CANVAS, composition={"regions": "complete", "order": ["p.ome.zarr"]})
        made = views.outputs["a_max.zmartview.zarr"].composer()
        np.testing.assert_array_equal(made.values_for(0, 0, 0, 0)[:4, :4], 90)
    finally:
        views.close()
    assert sorted(p.name for p in projections.iterdir()) == ["p.ome.zarr"], "the view set made none of its own"


def test_a_flat_capture_is_its_own_projection(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    write_tile(data, "p.ome.zarr", np.full((1, 1, 1, 8, 8), 70, dtype="uint16"))
    views = a_view(tmp_path, data)
    try:
        views.publish(data, {"p.ome.zarr": 1}, CANVAS, composition={"regions": "complete", "order": ["p.ome.zarr"]})
        made = views.outputs["a_max.zmartview.zarr"].composer()
        np.testing.assert_array_equal(made.values_for(0, 0, 0, 0)[:4, :4], 70)
    finally:
        views.close()
    assert sorted(p.name for p in data.iterdir()) == ["p.ome.zarr"]


def test_a_missing_projection_is_reported(tmp_path):
    data, projections = tmp_path / "data", tmp_path / "projections"
    data.mkdir()
    projections.mkdir()
    write_tile(data, "p.ome.zarr", np.full((1, 1, 3, 8, 8), 50, dtype="uint16"))
    views = a_view(tmp_path, projections)
    try:
        with pytest.raises(ValueError, match="p.ome.zarr"):
            views.publish(
                data, {"p.ome.zarr": 1}, CANVAS, composition={"regions": "complete", "order": ["p.ome.zarr"]}
            )
    finally:
        views.close()
