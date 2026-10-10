"""A folder opened by its path keeps up with its data, and answers when it cannot.

Review S2 and N2. A folder of positions opened by its path is composed into
one picture, and that picture used to be frozen at the moment of opening:
the guide promises that new positions appear within a second, and the root
README shows ``zmart-viewer /path/to/run`` as the way to watch a run. A
plate opened the same way wrote its scene beside the plate, where the guide
promises nothing is written. A failure to write the viewer's own folder
dropped the connection, so the load window waited for ever. And stopping a
build removed whatever scene stood in its place before the build began.

Server-level, no browser: each gate asks the door what the page asks it.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from grid_scans import _a_grid_scan, _post  # noqa: E402
from test_a_transfer_is_built_into_one_picture import STEP_UM, _write_a_tile  # noqa: E402
from test_open_and_close import _store  # noqa: E402
from zmart_viewer.picture.built_picture import the_scene_folder_name  # noqa: E402
from zmart_viewer.serving.server import make_server  # noqa: E402


@pytest.fixture
def viewer(tmp_path):
    """A live viewer with nothing open, and its address."""
    server = make_server(port=0, data_dir=tmp_path, live=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server
    finally:
        server.shutdown()
        thread.join(timeout=5)


def tiles_in_the_served_picture(address: str, answer: dict) -> int:
    """How many positions the one composed picture says it is made of."""
    row = next(one for one in answer["layers"] if one.get("kind") == "image")
    source = row["sources"][0].split("|", 1)[0]
    with urllib.request.urlopen(f"{address}{source}zarr.json", timeout=10) as got:
        return json.loads(got.read())["attributes"]["zmart"]["tiles"]


def config(address: str) -> dict:
    with urllib.request.urlopen(f"{address}/api/config", timeout=10) as got:
        return json.loads(got.read())


def test_a_position_that_lands_after_opening_appears(viewer, tmp_path):
    """Opened by its path while still being written, the run keeps growing on screen."""
    address, server = viewer
    run = tmp_path / "run"
    run.mkdir()
    for number, (row, column) in enumerate([(0, 0), (0, 1), (1, 0)]):
        _write_a_tile(run / f"Tile{number}.ome.zarr", number, (row * STEP_UM, column * STEP_UM))
    status, answer = _post(address, "/api/stores/open", {"path": str(run)})
    assert status == 200, answer
    assert tiles_in_the_served_picture(address, answer) == 3

    announcements = server.RequestHandlerClass.keywords["announcements"]
    told = announcements.listen()
    _write_a_tile(run / "Tile3.ome.zarr", 3, (STEP_UM, STEP_UM))

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if tiles_in_the_served_picture(address, config(address)) == 4:
            break
        time.sleep(0.2)
    assert tiles_in_the_served_picture(address, config(address)) == 4, (
        "a position landed in the opened folder and the picture never took it in"
    )
    assert told.get(timeout=5) is not None, "the open page was never told the picture grew"
    announcements.stop_listening(told)


def test_a_viewer_on_finished_data_composes_once(tmp_path):
    """``--static`` data is not watched: the picture is composed once and left be."""
    run = tmp_path / "run"
    run.mkdir()
    for number, (row, column) in enumerate([(0, 0), (0, 1)]):
        _write_a_tile(run / f"Tile{number}.ome.zarr", number, (row * STEP_UM, column * STEP_UM))
    server = make_server(port=0, data_dir=tmp_path, live=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        address = f"http://127.0.0.1:{server.server_address[1]}"
        status, answer = _post(address, "/api/stores/open", {"path": str(run)})
        assert status == 200, answer
        _write_a_tile(run / "Tile2.ome.zarr", 2, (STEP_UM, 0.0))
        time.sleep(2.5)
        assert tiles_in_the_served_picture(address, config(address)) == 2
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_a_failure_to_write_the_viewers_own_folder_is_answered(viewer, tmp_path, monkeypatch):
    """The viewer's own folder cannot be made: a plain answer, never a dropped connection."""
    address, _ = viewer
    in_the_way = tmp_path / "not-a-folder"
    in_the_way.write_text("a file where the viewer's folder should go", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: in_the_way))
    run = tmp_path / "run"
    for name in ("pos1.ome.zarr", "pos2.ome.zarr"):
        _store(run / name, channels=1)
    status, answer = _post(address, "/api/stores/open", {"path": str(run)})
    assert status == 500, answer
    assert "could not" in answer["error"]


def test_a_relink_ask_says_where_the_view_stands(viewer, tmp_path):
    """The window suggests rebuilding a moved view where it stands; the server says where.

    Working the folder out in the page by cutting at the last ``/`` dropped
    the last letter of every Windows path (review N6).
    """
    import shutil

    from zmart_viewer.picture.built_picture import declare_a_built_picture

    address, _ = viewer
    run = tmp_path / "surveyrun"
    _store(run / "surveyrun_pos001.ome.zarr", channels=1)
    store = declare_a_built_picture(run / "views", run, name="surveyrun")
    shutil.move(str(run / "surveyrun_pos001.ome.zarr"), str(tmp_path / "moved.ome.zarr"))
    status, answer = _post(address, "/api/stores/open", {"path": str(store)})
    assert status == 409, answer
    assert answer["relink"]["parent"] == str(store.parent)


class TestStoppingABuild:
    """Review N2: a stopped build takes away what it made, and nothing else."""

    def stop_the_build(self, address):
        for _ in range(2000):
            _, told = _post(address, "/api/stores/construct-status", {})
            if told.get("state") == "running" and 0 < told.get("fraction", 0) < 1:
                assert _post(address, "/api/stores/construct-cancel", {})[1] == {"stopping": True}
                break
            if told.get("state") in ("done", "error"):
                pytest.skip("the bake finished before a stop could land on this machine")
            time.sleep(0.005)
        for _ in range(200):
            _, told = _post(address, "/api/stores/construct-status", {})
            if told.get("state") != "running":
                return told
            time.sleep(0.05)
        return told

    def test_a_scene_that_stood_before_the_build_is_kept(self, viewer, tmp_path):
        address, _ = viewer
        scan = _a_grid_scan(tmp_path / "big", across=8)
        scenes = tmp_path / "scenes"
        status, _ = _post(
            address,
            "/api/stores/construct",
            {"path": str(scan), "viewer_folder": str(scenes), "bake": False},
        )
        assert status == 200
        for _ in range(300):
            _, told = _post(address, "/api/stores/construct-status", {})
            if told.get("state") != "running":
                break
            time.sleep(0.1)
        assert told["state"] == "done", told
        scene = scenes / the_scene_folder_name("big")
        before = (scene / "zarr.json").read_bytes()

        status, _ = _post(
            address,
            "/api/stores/construct",
            {"path": str(scan), "viewer_folder": str(scenes), "bake": True},
        )
        assert status == 200
        assert self.stop_the_build(address)["state"] == "cancelled"
        assert (scene / "zarr.json").read_bytes() == before, (
            "stopping the new build removed or changed the scene that stood before it"
        )
        assert [one.name for one in scenes.iterdir()] == [scene.name], (
            "a stopped build must leave nothing of its own behind"
        )


def test_a_scene_folder_holding_a_stray_numbered_folder_is_rebuilt(tmp_path):
    """``1a`` looks like a level to a pattern, but it is not one, and must not stop a build."""
    from zmart_viewer.picture.built_picture import declare_a_built_picture

    run = tmp_path / "run"
    for number, (row, column) in enumerate([(0, 0), (0, 1)]):
        _write_a_tile(run / f"Tile{number}.ome.zarr", number, (row * STEP_UM, column * STEP_UM))
    stray = tmp_path / "scenes" / the_scene_folder_name("run") / "1a"
    stray.mkdir(parents=True)
    store = declare_a_built_picture(tmp_path / "scenes", run, name="run")
    assert (store / "zarr.json").is_file()
    assert stray.is_dir(), "a folder that is not a level is not the build's to remove"
