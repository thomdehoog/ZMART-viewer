"""A built picture reads each axis's unit and places the tile in micrometres.

A built picture is always written in micrometres. It read a tile's voxel
size and position as numbers, without the unit beside them, so a tile
declared in nanometres was drawn a thousand times too large, and its scale
bar was wrong by the same factor (finding S9 of the review of 10 October
2026). Known units are now converted; a unit the viewer does not know is
refused with a message that names it, rather than guessed.
"""

from __future__ import annotations

import numpy as np
import pytest
import zarr
from test_view_sampling import write_tile
from zmart_viewer.picture.arrangement import read_one_tile


def declared_in(store, unit: str | None, *, voxel: float, corner: float) -> None:
    """Rewrite the tile's description: every spatial axis in ``unit``."""
    group = zarr.open_group(str(store), mode="r+")
    ome = dict(group.attrs["ome"])
    multiscale = dict(ome["multiscales"][0])
    multiscale["axes"] = [
        {**axis, **({"unit": unit} if unit else {})} if axis["name"] in "zyx" else axis
        for axis in multiscale["axes"]
    ]
    for axis in multiscale["axes"]:
        if axis["name"] in "zyx" and unit is None:
            axis.pop("unit", None)
    multiscale["datasets"] = [
        {
            "path": dataset["path"],
            "coordinateTransformations": [
                {"type": "scale", "scale": [1, 1, voxel, voxel * 2**level, voxel * 2**level]},
                {"type": "translation", "translation": [0, 0, corner, corner, corner]},
            ],
        }
        for level, dataset in enumerate(multiscale["datasets"])
    ]
    ome["multiscales"] = [multiscale]
    group.attrs["ome"] = ome


@pytest.fixture
def tile(tmp_path):
    data = np.zeros((1, 1, 3, 8, 8), dtype="uint16")
    return write_tile(tmp_path, "position.ome.zarr", data).store


@pytest.mark.parametrize(
    "unit, voxel, corner, voxel_um, corner_um",
    [
        ("micrometer", 0.5, 12.0, 0.5, 12.0),
        (None, 0.5, 12.0, 0.5, 12.0),
        ("nanometer", 500.0, 12_000.0, 0.5, 12.0),
        ("millimeter", 0.0005, 0.012, 0.5, 12.0),
        ("um", 0.5, 12.0, 0.5, 12.0),
    ],
)
def test_the_tile_is_placed_in_micrometres(tile, unit, voxel, corner, voxel_um, corner_um):
    declared_in(tile, unit, voxel=voxel, corner=corner)

    finest = read_one_tile(tile).copies[0]

    assert finest.voxel_um == pytest.approx((voxel_um, voxel_um, voxel_um))
    assert finest.corner_um == pytest.approx((corner_um, corner_um, corner_um))


def test_an_unknown_unit_is_refused_by_name(tile):
    declared_in(tile, "furlong", voxel=1.0, corner=0.0)

    with pytest.raises(ValueError, match="furlong"):
        read_one_tile(tile)
