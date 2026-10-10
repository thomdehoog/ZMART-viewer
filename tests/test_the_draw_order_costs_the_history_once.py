"""Reading the draw order costs one pass over the history, not one per position.

Every publication asks for the positions in the order they first arrived.
That order was built by checking each event against a growing list, so the
cost grew with the square of the number of positions: 89 ms at 5,000 and
363 ms at 10,000, on every publish (finding N5 of the review of 10 October
2026). Twenty thousand positions in one pass take a few milliseconds.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

from zmart_viewer.live.record.publisher import LivePublisher

POSITIONS = 20_000


def a_writer_with_history(position_ids):
    events = [SimpleNamespace(position_id=one) for one in position_ids]
    return SimpleNamespace(manifest=SimpleNamespace(events=lambda: events))


def test_the_order_is_first_arrival_with_repeats_kept_in_place():
    writer = a_writer_with_history(["b", "a", "b", "c", "a"])

    assert LivePublisher._positions_in_commit_order(writer) == ["b", "a", "c"]


def test_twenty_thousand_positions_are_ordered_in_one_pass():
    writer = a_writer_with_history([f"p{index:05d}" for index in range(POSITIONS)])

    began = time.perf_counter()
    ordered = LivePublisher._positions_in_commit_order(writer)
    took = time.perf_counter() - began

    assert len(ordered) == POSITIONS
    assert took < 0.25, f"ordering {POSITIONS} positions took {took * 1000:.0f} ms"
