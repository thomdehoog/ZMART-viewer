"""A Viewer's own folders go when it goes — even when it goes badly.

The folders a Viewer composes into used to be removed only on a clean stop, so
a Viewer that was killed left them behind for ever, outside every cache limit.
Now each session folder is locked by the process that made it, and the next
Viewer to start reclaims whatever nobody holds a lock on. These checks make the
folders small on purpose; nothing here allocates more than a few kilobytes.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from zmart_viewer.scratch import LOCK_FILE, ScratchSession
from zmart_viewer.server import make_server


def _fill(folder: Path, size: int) -> None:
    (folder / "piece").write_bytes(b"x" * size)


def test_closing_removes_both_of_this_sessions_folders(tmp_path):
    session = ScratchSession(tmp_path)
    scenes = session.open("scenes")
    replays = session.open("replays")
    _fill(scenes, 100)
    _fill(replays, 200)
    assert scenes.is_dir() and replays.is_dir()

    session.close()

    assert not scenes.exists() and not replays.exists()


def test_an_unlocked_orphan_is_swept_and_its_bytes_are_counted_exactly(tmp_path):
    """A folder whose owner died: its lock is free, so it goes, and the tally is exact."""
    orphan = tmp_path / "scenes" / "session-dead"
    orphan.mkdir(parents=True)
    (orphan / LOCK_FILE).touch()
    _fill(orphan, 1234)
    (orphan / "more").mkdir()
    (orphan / "more" / "bytes").write_bytes(b"y" * 66)

    done = ScratchSession(tmp_path).sweep_orphans("scenes")

    assert done["removed"] == ["session-dead"]
    assert done["reclaimed_bytes"] == 1300
    assert not orphan.exists()


def test_a_folder_made_before_locks_existed_is_still_reclaimed(tmp_path):
    old = tmp_path / "replays" / "session-old"
    old.mkdir(parents=True)
    _fill(old, 10)
    done = ScratchSession(tmp_path).sweep_orphans("replays")
    assert done["removed"] == ["session-old"]


def test_a_folder_another_live_process_holds_survives(tmp_path):
    """The lock is the liveness signal: a running owner's folder is left alone."""
    keeper = subprocess.Popen(
        [sys.executable, "-c", (
            "import sys, time; from pathlib import Path\n"
            "from zmart_viewer.scratch import ScratchSession\n"
            f"s = ScratchSession(Path({str(tmp_path)!r})); made = s.open('scenes')\n"
            "print(made, flush=True)\n"
            "time.sleep(30)"
        )],
        stdout=subprocess.PIPE, text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
    )
    try:
        held = Path(keeper.stdout.readline().strip())
        assert held.is_dir()

        done = ScratchSession(tmp_path).sweep_orphans("scenes")

        assert done["kept"] == [held.name]
        assert done["removed"] == []
        assert held.is_dir()
    finally:
        keeper.kill()
        keeper.wait(timeout=10)

    # And once that process is gone, the very same folder is reclaimed.
    done = ScratchSession(tmp_path).sweep_orphans("scenes")
    assert done["removed"] == [held.name]


def test_a_symlink_or_an_escaped_candidate_is_refused_and_its_target_survives(tmp_path):
    precious = tmp_path / "precious"
    precious.mkdir()
    _fill(precious, 50)
    home = tmp_path / "scenes"
    home.mkdir()
    (home / "session-link").symlink_to(precious, target_is_directory=True)
    (home / "session-file").write_text("not a folder")

    done = ScratchSession(tmp_path).sweep_orphans("scenes")

    assert done["removed"] == []
    assert precious.is_dir() and (precious / "piece").exists()
    assert (home / "session-file").exists()


def test_this_sessions_own_folder_is_never_swept_by_itself(tmp_path):
    session = ScratchSession(tmp_path)
    mine = session.open("scenes")
    done = session.sweep_orphans("scenes")
    assert done["kept"] == [mine.name]
    assert mine.is_dir()
    session.close()


def test_the_tally_counts_both_kinds(tmp_path):
    session = ScratchSession(tmp_path)
    _fill(session.open("scenes"), 300)
    _fill(session.open("replays"), 500)
    told = session.managed_bytes()
    # The folder's own bookkeeping — the owner note — is part of what it
    # holds, so it is counted too; the lock file is empty and costs nothing.
    note = {kind: (session.open(kind) / "owner.json").stat().st_size
            for kind in ("scenes", "replays")}
    assert told["kinds"]["scenes"]["bytes"] == 300 + note["scenes"]
    assert told["kinds"]["replays"]["bytes"] == 500 + note["replays"]
    assert told["total"] == 800 + note["scenes"] + note["replays"]
    session.close()


def _serve(tmp_path, root):
    site = tmp_path / "site"
    site.mkdir(exist_ok=True)
    (site / "index.html").write_text("<!doctype html><title>page</title>", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    server = make_server(port=0, data_dir=data, site_dir=site, scratch_root=root)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_a_server_sweeps_orphans_when_it_starts_and_reports_what_it_holds(tmp_path):
    import http.client

    root = tmp_path / "root"
    orphan = root / "scenes" / "session-dead"
    orphan.mkdir(parents=True)
    _fill(orphan, 42)

    server, thread = _serve(tmp_path, root)
    try:
        for _ in range(50):
            if "swept" in server.RequestHandlerClass.keywords["scratch"]:
                break
            time.sleep(0.05)
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        conn.request("GET", "/api/scratch")
        told = json.loads(conn.getresponse().read())
        conn.close()
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert told["root"] == str(root)
    assert told["swept_at_start"]["scenes"]["removed"] == ["session-dead"]
    assert told["swept_at_start"]["scenes"]["reclaimed_bytes"] == 42
    assert not orphan.exists()


def test_a_clean_shutdown_leaves_no_session_folder_behind(tmp_path):
    root = tmp_path / "root"
    server, thread = _serve(tmp_path, root)
    handler_scratch = server.RequestHandlerClass.keywords["scratch"]
    made = handler_scratch["sessions"].open("scenes")
    assert made.is_dir()
    server.shutdown()
    thread.join(timeout=5)
    assert not made.exists()
    assert not any((root / "scenes").iterdir()) if (root / "scenes").exists() else True
