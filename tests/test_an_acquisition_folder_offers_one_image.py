"""A folder holding a view and its positions is offered as one image.

An acquisition that keeps its positions inside the view's own folder must
open as the one picture the view describes -- never as a heap of position
stores beside it -- while every position stays a complete image of its own
that the view's pointers hand over byte for byte.
"""

from __future__ import annotations

import numpy as np
from pointed_by_hand import a_pointed_view, a_tile, decoded

from zmart_viewer.library import Library
from zmart_viewer.pieces import pointed_bytes_behind


def _an_acquisition_folder(folder):
    view = a_pointed_view(
        folder / "picture.zarr",
        [("picture.zarr/tiles/pos_a.zarr", (0, 0)), ("picture.zarr/tiles/pos_b.zarr", (0, 1))],
        canvas_chunks=(1, 2),
    )
    tiles = view / "tiles"
    tiles.mkdir()
    a_tile(tiles / "pos_a.zarr", 1000)
    a_tile(tiles / "pos_b.zarr", 2000)
    return view


def test_the_viewer_is_offered_one_image_rather_than_every_position(tmp_path):
    _an_acquisition_folder(tmp_path)
    library = Library()
    library.open(tmp_path)
    offered = [store for dataset in library.datasets() for store in dataset.stores]

    assert len(offered) == 1, f"one acquisition folder must open as one picture, not as {offered}"


def test_the_view_hands_over_the_positions_own_bytes(tmp_path):
    view = _an_acquisition_folder(tmp_path)
    held = pointed_bytes_behind(view, "0/c/0/0/0/0/1")

    assert held is not None and "tiles/pos_b" in held.path
    assert np.all(decoded((tmp_path / held.path).read_bytes()) == 2000)


def test_every_position_is_still_a_complete_image_on_its_own(tmp_path):
    import zarr

    view = _an_acquisition_folder(tmp_path)
    alone = zarr.open_array(str(view / "tiles" / "pos_a.zarr" / "0"), mode="r")

    assert alone.shape == (1, 1, 1, 64, 64)
    assert int(alone[0, 0, 0, 0, 0]) == 1000
