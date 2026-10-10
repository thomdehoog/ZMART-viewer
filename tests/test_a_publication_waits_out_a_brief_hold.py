"""The end of a publication waits out a file that is held for a moment.

On Windows a file cannot be deleted while another program has it open
without permission to delete, which is how a virus scanner looks at a new
file. The last step of a publication removed ``pending.json`` with one bare
attempt, *before* the server's own record moved on. One refusal left the
revision on disk at 2 and in the server at 1, kept ``pending.json``, and held
back every piece it named until another announcement repaired it (finding S3
of the review of 10 October 2026). Retiring the previous product of a
projection had the same single attempt.

These tests hold the file for 300 ms, as a scanner does, and ask that the
publication finishes as if nothing had happened.
"""

from __future__ import annotations

import json
import os
import threading

import numpy as np
import pytest
import zarr
from test_mixed_acquisition import focused
from test_published_depth import CANVAS, composition
from test_view_sampling import write_tile
from zmart_viewer.views import projections
from zmart_viewer.views.projections import write_projection
from zmart_viewer.views.publishing import STORE, PublishedAcquisition

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics")

HOLD_S = 0.3


def held_for_a_moment(path) -> None:
    """Open ``path`` without permission to delete it, and let go a moment later."""
    handle = open(path, "rb")  # noqa: SIM115 -- closed by the timer
    threading.Timer(HOLD_S, handle.close).start()


def test_pending_is_removed_despite_a_brief_hold(tmp_path, monkeypatch):
    name = "flat.ome.zarr"
    path = focused(tmp_path, name, 256.75, 70, depth=1)
    view = PublishedAcquisition(tmp_path, piece=64)
    view.publish(tmp_path, {name: 1}, CANVAS, composition=composition([name]), bake=True)
    output = view.outputs[STORE]
    shown = output._shown

    group = zarr.open_group(str(path), mode="r+")
    for level in range(3):
        group[str(level)][:] = 2000

    held = []
    real_unlink = os.unlink

    def a_scanner_glances_once(target, *arguments, **named):
        if os.path.basename(target) == "pending.json" and not held:
            held.append(target)
            held_for_a_moment(target)
        return real_unlink(target, *arguments, **named)

    monkeypatch.setattr(os, "unlink", a_scanner_glances_once)
    view.publish(tmp_path, {name: 2}, CANVAS, composition=composition([name]), bake=True)
    monkeypatch.undo()

    assert held, "the publication never removed pending.json, so the hold was not met"
    on_disk = json.loads((shown / "publication.json").read_text(encoding="utf-8"))["revision"]
    assert (on_disk, output.revision) == (2, 2)
    assert not (shown / "pending.json").exists()
    withheld = [
        (level, row, column)
        for level in range(3)
        for row in range(4)
        for column in range(8)
        if output.being_rewritten(level, row, column)
    ]
    assert withheld == []


def test_a_projection_replaces_its_product_despite_a_brief_hold(tmp_path, monkeypatch):
    data = np.arange(2 * 2 * 3 * 8 * 8, dtype="uint16").reshape(2, 2, 3, 8, 8)
    tile = write_tile(tmp_path, "position.ome.zarr", data, x=12, z=100)
    output = tmp_path / "projections" / "max" / tile.name
    write_projection(tile.store, output, "max", revision=1, piece=4)

    held = []
    real_replace = os.replace

    def a_scanner_glances_at_the_retired_product(source, destination):
        real_replace(source, destination)
        if str(destination).endswith(".previous") and not held:
            inside = next(p for p in projections.Path(destination).rglob("*") if p.is_file())
            held.append(inside)
            held_for_a_moment(inside)

    monkeypatch.setattr(projections.os, "replace", a_scanner_glances_at_the_retired_product)
    write_projection(tile.store, output, "max", revision=2, piece=4)
    monkeypatch.undo()

    assert held, "the previous product was never retired, so the hold was not met"
    assert zarr.open_group(str(output), mode="r").attrs["zmart_projection"]["revision"] == 2
    assert [p.name for p in output.parent.iterdir()] == [output.name]
