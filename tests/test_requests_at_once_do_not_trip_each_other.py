"""Requests that arrive at once do not trip over each other's bookkeeping.

The server answers each request on its own thread. Three places shared
memory between those threads without guarding it (finding N4 of the review
of 10 October 2026):

- letting go of a closed store walked the remembered descriptions while other
  requests were adding to them, which raises "dictionary changed size during
  iteration";
- forgetting a built picture dropped the lock that keeps two requests from
  building it at once, so a second build could start beside the first, and the
  first then kept the forgotten picture open;
- an acquisition published from two threads could make two writers for the
  same picture, and keep only one of them.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

from test_mixed_acquisition import focused
from test_published_depth import CANVAS, composition
from zmart_viewer.opening import open_folders
from zmart_viewer.serving import picture_pieces
from zmart_viewer.views import publishing
from zmart_viewer.views.publishing import STORE, PublishedAcquisition


def test_forgetting_a_store_while_others_are_remembered():
    interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    stop = threading.Event()

    def other_requests_remember_their_stores():
        count = 0
        while not stop.is_set():
            open_folders._attrs_cache[f"elsewhere-{count}"] = ((0, 0), {})
            count += 1
            if count % 500 == 0:
                for key in [
                    key for key in list(open_folders._attrs_cache) if key.startswith("elsewhere-")
                ]:
                    open_folders._attrs_cache.pop(key, None)

    others = threading.Thread(target=other_requests_remember_their_stores)
    others.start()
    try:
        began = time.monotonic()
        while time.monotonic() - began < 1.0:
            open_folders.forget(Path("closed-store"))
    finally:
        stop.set()
        others.join()
        sys.setswitchinterval(interval)
        for key in [key for key in list(open_folders._attrs_cache) if key.startswith("elsewhere-")]:
            open_folders._attrs_cache.pop(key, None)


class ABuiltPicture:
    """Stands for whatever answers a built store; only closing it matters here."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_forgetting_a_picture_while_it_is_built(tmp_path, monkeypatch):
    store = tmp_path / "built.zmartview.zarr"
    store.mkdir()
    (store / "zarr.json").write_text(
        json.dumps({"attributes": {"zmart": {"built_from": str(tmp_path)}}}), encoding="utf-8"
    )
    first_is_building = threading.Event()
    let_the_first_finish = threading.Event()
    building = []
    at_once = []
    made = []

    def build(where, ours):
        building.append(where)
        at_once.append(len(building))
        if len(made) == 0:
            first_is_building.set()
            let_the_first_finish.wait(timeout=5)
        picture = ABuiltPicture()
        made.append(picture)
        building.remove(where)
        return picture

    monkeypatch.setattr(picture_pieces, "_the_serving_behind", build)
    answers = {}
    first = threading.Thread(
        target=lambda: answers.setdefault("first", picture_pieces.composer_for(store))
    )
    first.start()
    assert first_is_building.wait(timeout=5)

    picture_pieces.forget_composer(store)
    second = threading.Thread(
        target=lambda: answers.setdefault("second", picture_pieces.composer_for(store))
    )
    second.start()
    time.sleep(0.2)
    let_the_first_finish.set()
    first.join(timeout=5)
    second.join(timeout=5)
    try:
        assert max(at_once) == 1, "a second build started beside the first"
        assert made[0].closed, "the picture built across the forgetting was kept open"
        assert answers["second"] is made[-1] and not made[-1].closed
    finally:
        picture_pieces.forget_composer(store)


def test_one_acquisition_published_from_two_threads(tmp_path, monkeypatch):
    name = "flat.ome.zarr"
    focused(tmp_path, name, 256.75, 70, depth=1)
    view = PublishedAcquisition(tmp_path, piece=64)
    both_inside = threading.Barrier(2)
    real_read = publishing.read_one_tile
    created = []
    real_transfer = publishing.PublishedTransfer

    def read_while_the_other_is_inside(store):
        try:
            both_inside.wait(timeout=1)
        except threading.BrokenBarrierError:
            pass
        return real_read(store)

    def counted(*arguments, **named):
        made = real_transfer(*arguments, **named)
        created.append(made)
        return made

    monkeypatch.setattr(publishing, "read_one_tile", read_while_the_other_is_inside)
    monkeypatch.setattr(publishing, "PublishedTransfer", counted)
    failures = []

    def publish(revision):
        try:
            view.publish(
                tmp_path, {name: revision}, CANVAS, composition=composition([name]), bake=True
            )
        except Exception as problem:  # noqa: BLE001 -- reported below
            failures.append(problem)

    threads = [threading.Thread(target=publish, args=(revision,)) for revision in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    try:
        assert len(created) == 1, f"{len(created)} writers were made for one picture"
        assert view.outputs[STORE] is created[0]
    finally:
        view.close()
    assert all("regress" in str(problem) for problem in failures), failures
