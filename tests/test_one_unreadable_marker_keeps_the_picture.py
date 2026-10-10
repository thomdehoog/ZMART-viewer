"""One unreadable read of the publication marker leaves the live picture as it was.

``signed.json`` is the one small file that says what a run has published. On
Windows a read of it can fail for a moment: the writer's rename holds it, or a
virus scanner has it open. The forgiving reader turns any such failure into
"nothing published yet", which is right for a single pixel request and wrong
for anything that *remembers* the answer. A running total that took that
answer to heart emptied the picture and deleted its baked overview until the
next commit arrived (finding M1 of the review of 10 October 2026).

These tests imitate exactly that: the strict read succeeds, the forgiving one
fails. Whatever remembers the published state must keep the last state it
could prove, and answer with it until the marker reads again.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

VIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VIZ))
sys.path.insert(0, str(VIZ.parent))

from record_fixtures import some_specimen  # noqa: E402
from test_a_governed_picture_is_baked_per_commit import every_baked_file  # noqa: E402
from test_the_composer_obeys_the_manifest import PIECE, a_governed_run, the_columns_of  # noqa: E402
from zmart_viewer.live.record.live_serving import LiveRun  # noqa: E402
from zmart_viewer.live.record.manifest import CommittedState, RunManifest  # noqa: E402
from zmart_viewer.picture.built_picture import declare_a_governed_picture  # noqa: E402
from zmart_viewer.serving import picture_pieces as served  # noqa: E402


def the_marker_is_touched(run) -> None:
    """Change the marker's timestamp without a commit, so readers look again."""
    marker = next(run.folder.rglob("signed.json"))
    later = time.time_ns() + 10_000_000
    os.utime(marker, ns=(later, later))


def the_forgiving_read_fails(monkeypatch) -> None:
    """Make the forgiving read answer as it does when the file is held."""
    monkeypatch.setattr(
        RunManifest, "committed", lambda self: CommittedState(run_id=self.run_id)
    )


def test_the_published_state_survives_one_unreadable_marker(tmp_path, monkeypatch):
    run = a_governed_run(tmp_path)
    run.write_and_publish("posA", some_specimen(700))
    run.write_and_publish("posB", some_specimen(4242))
    live = LiveRun(run.folder)
    before = live.published_units()
    assert {position for position, _moment, _generation in before} == {"posA", "posB"}

    the_forgiving_read_fails(monkeypatch)
    the_marker_is_touched(run)

    assert live.published_units() == before, (
        "a marker that could not be read for a moment emptied the published state"
    )
    assert live.positions_in_commit_order() == ("posA", "posB")


def test_the_baked_overview_survives_one_unreadable_marker(tmp_path, monkeypatch):
    run = a_governed_run(tmp_path)
    run.write_and_publish("posA", some_specimen(700))
    store = declare_a_governed_picture(
        run.folder / "views" / "shown", run.folder, name="live", piece=PIECE, bake=True
    )
    run.write_and_publish("posB", some_specimen(4242))
    _, _, b_only = the_columns_of(run)
    address = f"0/c/0/0/{b_only}"
    try:
        assert served.built_bytes_behind(store, address) is not None
        baked = every_baked_file(store)
        assert baked

        the_forgiving_read_fails(monkeypatch)
        the_marker_is_touched(run)

        for _ask in range(3):
            assert served.built_bytes_behind(store, address) is not None, (
                "one unreadable marker blanked a piece that is published"
            )
        assert every_baked_file(store) == baked, (
            "one unreadable marker deleted or rewrote the baked overview"
        )
    finally:
        served.forget(store)
