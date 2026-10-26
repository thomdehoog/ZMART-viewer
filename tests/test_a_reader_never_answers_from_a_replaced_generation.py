"""A reader never answers from a generation a newer commit has replaced.

The server keeps one ``PublishedTransfer`` per published picture and answers
every request for a piece through its ``composer()``. Taking up a newly
committed generation is done under the reader's lock, and a request that
finds the lock taken used to answer at once from the generation it already
held, so that nobody ever waits on a publication. But the lock is also taken
by the request that is taking up the new generation. Right after a
publication the page asks for a burst of pieces at once: the first request
read the new generation, and the requests beside it were answered from the
old one.

The page cannot tell. It asks for those pieces because the revision moved,
and it keeps whatever comes back as that revision's. So a piece of the old
picture stayed on screen for good: gray where a newly acquired stack should
have covered it, or covered where the coverage had been withdrawn, every
piece reported as available and nothing pending
(`test_mixed_acquisition.py::test_browser_mixed_relative_depth`, a quarter of
its runs on a busy machine).

The held generation may still be answered from while another thread holds
the lock, but only while it is the newest one committed. Once
``publication.json`` names a newer one, the reader waits for the lock and
answers from that.
"""

from __future__ import annotations

import threading

from test_published_transfer import write_position
from zmart_viewer.views.publishing import STORE, PublishedTransfer

CANVAS = {"x_um": [0, 1024], "y_um": [0, 512]}


def _someone_holds_the_lock(reader: PublishedTransfer):
    """Hold ``reader``'s lock on another thread, as a concurrent request does."""
    holding, release = threading.Event(), threading.Event()

    def hold():
        with reader._lock:
            holding.set()
            release.wait(10)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert holding.wait(10)
    return release, holder


def _ask(reader: PublishedTransfer):
    """Ask for the composer on its own thread; the answer lands in the list."""
    answered = []
    asker = threading.Thread(target=lambda: answered.append(reader.composer()), daemon=True)
    asker.start()
    return answered, asker


def test_a_held_generation_that_is_still_current_is_answered_without_waiting(tmp_path):
    write_position(tmp_path, "a.ome.zarr", 0, 1200)
    view = PublishedTransfer(tmp_path / STORE, piece=64)
    reader = PublishedTransfer(tmp_path / STORE, piece=64)
    try:
        view.publish(tmp_path, {"a.ome.zarr": 1}, CANVAS)
        held = reader.composer()
        release, holder = _someone_holds_the_lock(reader)
        try:
            answered, asker = _ask(reader)
            asker.join(5)
            assert not asker.is_alive(), "waited although nothing newer was committed"
            assert answered == [held]
        finally:
            release.set()
            holder.join(10)
    finally:
        reader.close()
        view.close()


def test_a_held_generation_a_newer_commit_replaced_is_never_answered(tmp_path):
    write_position(tmp_path, "a.ome.zarr", 0, 1200)
    view = PublishedTransfer(tmp_path / STORE, piece=64)
    reader = PublishedTransfer(tmp_path / STORE, piece=64)
    try:
        view.publish(tmp_path, {"a.ome.zarr": 1}, CANVAS)
        replaced = reader.composer()
        write_position(tmp_path, "a.ome.zarr", 0, 2700)
        assert view.publish(tmp_path, {"a.ome.zarr": 2}, CANVAS) == 2
        # Another request is taking up the new generation just now.
        release, holder = _someone_holds_the_lock(reader)
        try:
            answered, asker = _ask(reader)
            asker.join(0.5)
            assert asker.is_alive(), (
                "answered from the replaced generation instead of waiting for the new one"
            )
        finally:
            release.set()
            holder.join(10)
        asker.join(10)
        assert not asker.is_alive()
        assert answered[0] is not replaced
        assert answered[0].values_for(0, 0, 0, 0).max() == 2700
    finally:
        reader.close()
        view.close()


def test_a_note_still_standing_after_the_commit_does_not_hold_the_old_generation(tmp_path):
    # The commit replaces publication.json and only then removes pending.json,
    # with a scanner's patience on Windows; for that moment both stand.
    write_position(tmp_path, "a.ome.zarr", 0, 1200)
    view = PublishedTransfer(tmp_path / STORE, piece=64)
    reader = PublishedTransfer(tmp_path / STORE, piece=64)
    try:
        view.publish(tmp_path, {"a.ome.zarr": 1}, CANVAS)
        replaced = reader.composer()
        write_position(tmp_path, "a.ome.zarr", 0, 2700)
        assert view.publish(tmp_path, {"a.ome.zarr": 2}, CANVAS) == 2
        (tmp_path / STORE / "pending.json").write_text('{"dirty": {}}', encoding="utf-8")
        assert reader.composer() is not replaced
        assert reader.revision == 2
    finally:
        reader.close()
        view.close()
