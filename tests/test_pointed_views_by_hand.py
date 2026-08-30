"""The pointer contract, pinned with maps written by hand.

A pointed view holds no pixels; each piece is a byte range of a tile's own
file. These gates write the map themselves -- the format is the viewer's --
and hold what any map, whoever wrote it, may rely on: whole-file answers,
older map versions still read, unknown holdings refused, uncovered ground
answered plainly, growth by appended lines, and the server handing over the
exact stretch.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import numpy as np
from pointed_by_hand import a_small_scene, a_tile, decoded, the_tiles_bytes

from zmart_viewer import pieces
from zmart_viewer.server import make_server


def test_a_pointer_says_where_the_bytes_are_and_it_is_the_whole_file(tmp_path):
    view = a_small_scene(tmp_path)
    held = pieces.pointed_bytes_behind(view, "0/c/0/0/0/0/1")

    assert held is not None and held.is_the_whole_file
    assert held.path == "pos_b.zarr/0/c/0/0/0/0/0"
    assert np.all(decoded((tmp_path / held.path).read_bytes()) == 2000)


def test_a_view_written_before_this_change_is_still_read(tmp_path):
    """Version 1 never said how a tile keeps its pieces, because there was
    only one way it could -- each piece its own file. Known, not guessed."""
    view = a_small_scene(tmp_path)
    older = pieces.the_map_inside(view)
    older["version"] = 1

    for tile in older["tiles"]:
        tile.pop("held_as", None)
    pieces.rewrite_the_map_inside(view, older)
    pieces.forget_pointers(view)

    held = pieces.pointed_bytes_behind(view, "0/c/0/0/0/0/0")
    assert held is not None and held.is_the_whole_file


def test_a_way_of_holding_pieces_we_do_not_know_is_refused(tmp_path):
    """Refused rather than guessed at: the wrong stretch would be drawn as
    specimen, and nothing anywhere would raise."""
    view = a_small_scene(tmp_path)
    listed = pieces.the_map_inside(view)

    for tile in listed["tiles"]:
        tile["held_as"] = "packed-somehow"
    pieces.rewrite_the_map_inside(view, listed)
    pieces.forget_pointers(view)

    assert pieces.pointed_bytes_behind(view, "0/c/0/0/0/0/0") is None


def test_a_piece_no_tile_covers_is_answered_plainly(tmp_path):
    view = a_small_scene(tmp_path)

    assert pieces.pointed_bytes_behind(view, "0/c/0/0/0/0/2") is None
    assert pieces.built_bytes_behind(view, "0/c/0/0/0/0/2") is None


def test_the_server_hands_over_the_stretch_a_pointer_asks_for(tmp_path):
    view = a_small_scene(tmp_path)
    server = make_server(port=0, data_dir=tmp_path, store=[view.name], live=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        port = server.server_address[1]
        address = f"http://127.0.0.1:{port}/data/0/{view.name}/0/c/0/0/0/0/0"

        with urllib.request.urlopen(address, timeout=30) as answer:
            over_http = answer.read()
        assert over_http == the_tiles_bytes(tmp_path, "pos_a.zarr")

        try:
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/data/0/{view.name}/0/c/0/0/0/0/2", timeout=30
            )
            raise AssertionError("uncovered ground should answer 404, not bytes")
        except urllib.error.HTTPError as answer:
            assert answer.code == 404
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_a_growing_view_answers_each_tile_as_its_line_lands(tmp_path):
    """Growth is appended lines beside the view; a half-written line is
    simply not there yet, and the finished view reads the same way."""
    view = a_small_scene(tmp_path)
    a_tile(tmp_path / "pos_c.zarr", 3000)
    _, added = pieces.where_the_list_is(view)

    assert pieces.pointed_bytes_behind(view, "0/c/0/0/0/0/2") is None

    with open(added, "a", encoding="utf-8") as growing:
        growing.write(
            json.dumps(
                {
                    "store": "pos_c.zarr",
                    "at": [0, 0, 2],
                    "size": [1, 1, 1],
                    "from": [0, 0, 0],
                    "held_as": "file",
                }
            )
            + "\n"
        )
    held = pieces.pointed_bytes_behind(view, "0/c/0/0/0/0/2")
    assert held is not None and np.all(decoded((tmp_path / held.path).read_bytes()) == 3000)

    with open(added, "a", encoding="utf-8") as growing:
        growing.write('{"store": "pos_d.zarr", "at": [0, 0')
    assert pieces.pointed_bytes_behind(view, "0/c/0/0/0/0/2") is not None, (
        "a half-written line must not take the whole view down"
    )


def test_the_map_travels_inside_the_view(tmp_path):
    """The pointers survive the scene being moved: everything in the map is
    relative, nothing in it names an absolute place."""
    import shutil

    a_small_scene(tmp_path / "was")
    shutil.move(str(tmp_path / "was"), str(tmp_path / "is"))
    view = tmp_path / "is" / "picture.zarr"

    held = pieces.pointed_bytes_behind(view, "0/c/0/0/0/0/0")
    assert held is not None
    assert np.all(decoded((tmp_path / "is" / held.path).read_bytes()) == 1000)
