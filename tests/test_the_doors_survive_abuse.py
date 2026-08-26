"""Every operator door, fed exactly what nobody should ever feed it.

The load window's doors answer folder paths, names and paces that come from
an operator's hands, and one day from other people's scripts. Each gate here
abuses one door the way a slip, a typo, or a hostile caller would, and pins
the answer we can stand behind: a plain refusal in the reply, never a hang,
never a crash, and never a file written anywhere the operator did not point.

Server-level on purpose -- no browser -- so the whole battery runs in
seconds and can grow a case per incident forever.
"""

import sys
import threading
import time
from pathlib import Path

import pytest

VIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(VIZ / "app" / "picture"))
sys.path.insert(0, str(VIZ / "app" / "server"))
sys.path.insert(0, str(VIZ.parent))

from declare import the_scene_folder_name  # noqa: E402
from server import make_server  # noqa: E402
from test_a_dataset_is_relived_as_a_live_run import (  # noqa: E402
    _a_grid_scan,
    _post,
)
from test_open_and_close import _store  # noqa: E402


@pytest.fixture
def door(built_dist, tmp_path):
    """A served viewer and the folder beside it, for abusing."""
    first = tmp_path / "overview"
    first.mkdir()
    _store(first / "overview_pos001.ome.zarr", channels=1)
    server = make_server(port=0, data_dir=first, site_dir=built_dist,
                         store="overview_pos001.ome.zarr")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", tmp_path
    finally:
        server.shutdown()
        thread.join(timeout=5)


class TestTheConstructDoor:
    def test_a_name_cannot_walk_out_of_the_scene_folder(self, door):
        """A name with path steps in it must not place files anywhere.

        ``name`` becomes the scene's folder name; fed ``../escaped`` it
        would land the scene BESIDE the folder the operator chose, and a
        hostile caller could aim it anywhere the server can write. Refused
        at the door, with nothing written.
        """
        address, folder = door
        scan = _a_grid_scan(folder / "scan")
        for name in ("../escaped", "a/b", "..", "a\\b"):
            status, answer = _post(address, "/api/stores/construct",
                                   {"path": str(scan),
                                    "viewer_folder": str(scan / "scenes"),
                                    "bake": False, "name": name})
            assert status == 400, (name, status, answer)
            assert "name" in answer["error"], answer
        assert not (folder / "escaped.ome.zarr").exists()
        assert not (folder / "scan" / "escaped.ome.zarr").exists()

    def test_a_file_where_a_folder_should_be_answers_plainly(self, door):
        address, folder = door
        pretender = folder / "not-a-folder.txt"
        pretender.write_text("hello", encoding="utf-8")
        status, answer = _post(address, "/api/stores/construct",
                               {"path": str(pretender),
                                "viewer_folder": str(folder / "scenes"),
                                "bake": False})
        assert status == 404 and "folder" in answer["error"]

    def test_an_empty_dataset_reports_its_reason_through_the_status(self, door):
        """The build thread's failure must surface, not strand the poller."""
        address, folder = door
        empty = folder / "empty"
        empty.mkdir()
        status, _ = _post(address, "/api/stores/construct",
                          {"path": str(empty),
                           "viewer_folder": str(empty / "scenes"),
                           "bake": False})
        assert status == 200
        for _ in range(100):
            _, told = _post(address, "/api/stores/construct-status", {})
            if told.get("state") != "running":
                break
            time.sleep(0.1)
        assert told.get("state") == "error"
        assert "no OME-Zarr" in told.get("error", "")




class TestTheOpenDoor:
    def test_a_broken_description_is_refused_not_crashed_on(self, door):
        """A truncated zarr.json is what a copy interrupted halfway leaves."""
        address, folder = door
        broken = folder / "broken" / "hurt.ome.zarr"
        broken.mkdir(parents=True)
        (broken / "zarr.json").write_text('{"attributes": {"ome":',
                                          encoding="utf-8")
        status, answer = _post(address, "/api/stores/open",
                               {"path": str(broken)})
        assert status in (400, 404), (status, answer)
        assert answer.get("error"), "the refusal must say something"

    def test_a_half_written_scene_is_refused_plainly(self, door):
        """A scene folder without its description is a build that died."""
        address, folder = door
        half = folder / "half" / "scene.ome.zarr"
        (half / "0").mkdir(parents=True)
        status, answer = _post(address, "/api/stores/open",
                               {"path": str(half)})
        assert status in (400, 404), (status, answer)
        assert answer.get("error")

    def test_listing_a_missing_folder_answers_inside_the_reply(self, door):
        address, _ = door
        status, answer = _post(address, "/api/stores/list",
                               {"path": "/nowhere/at/all"})
        assert status in (200, 400, 404)
        if status != 200:
            assert answer.get("error")


class TestTheDoorsTogether:
    def test_stopping_when_nothing_runs_is_not_an_error(self, door):
        """A stop with nothing running is already true, not a fault."""
        address, _ = door
        for route in ("/api/stores/construct-cancel",):
            status, answer = _post(address, route, {})
            assert status == 200, (route, status, answer)
            assert answer == {"stopping": False}


    def test_a_build_stopped_mid_bake_keeps_nothing(self, door):
        """Stopping a build removes the half-made scene, whole.

        The scene's description is written after its bake, so a stopped
        build's folder holds pieces without a description -- not a scene,
        and leaving it would let it be quietly picked up later. It goes,
        and the door is immediately free for a fresh build.
        """
        address, folder = door
        scan = _a_grid_scan(folder / "big", across=8)
        status, _ = _post(address, "/api/stores/construct",
                          {"path": str(scan),
                           "viewer_folder": str(folder / "scenes"),
                           "bake": True})
        assert status == 200
        caught_running = False
        for _ in range(2000):
            _, told = _post(address, "/api/stores/construct-status", {})
            if (told.get("state") == "running"
                    and 0 < told.get("fraction", 0) < 1):
                caught_running = True
                status, answer = _post(address,
                                       "/api/stores/construct-cancel", {})
                assert status == 200 and answer == {"stopping": True}
                break
            if told.get("state") in ("done", "error"):
                break
            time.sleep(0.005)
        if not caught_running:
            pytest.skip("the bake finished before a stop could land, so "
                        "there was nothing to stop on this machine")
        for _ in range(200):
            _, told = _post(address, "/api/stores/construct-status", {})
            if told.get("state") != "running":
                break
            time.sleep(0.05)
        assert told["state"] == "cancelled", told
        assert not (folder / "scenes"
                    / the_scene_folder_name("big")).exists(), (
            "a stopped build must keep nothing -- pieces without a "
            "description are not a scene"
        )
        status, _ = _post(address, "/api/stores/construct",
                          {"path": str(scan),
                           "viewer_folder": str(folder / "scenes"),
                           "bake": False})
        assert status == 200, "a stopped build must not block the next one"
        for _ in range(300):
            _, told = _post(address, "/api/stores/construct-status", {})
            if told.get("state") != "running":
                break
            time.sleep(0.1)
        assert told["state"] == "done"
        assert (folder / "scenes" / the_scene_folder_name("big")
                / "zarr.json").is_file()

