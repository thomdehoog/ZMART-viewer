"""The zero-copy linked row, measured after an honest re-link.

A run that has grown or replaced positions is refused by the gateway until
its link map is rewritten -- governance failing closed, by design. So this
row first pays the re-link (the finish-the-run cost, reported per rung) and
then times the gateway's byte-range answers end to end, read included.
"""

import os
import statistics
import sys
import time
from pathlib import Path

_VIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_VIZ))
sys.path.insert(0, str(_VIZ / "measure"))
sys.path.insert(0, str(_VIZ.parent))

import measure_a_governed_run_at_scale as harness  # noqa: E402
from measure_the_four_ways_of_serving import middle_of_factory, piece_path  # noqa: E402
from zmart_live.gateway import answer_from_a_live_run  # noqa: E402

harness.FIXTURES = Path(os.environ.get("ZMART_FIXTURES", str(harness.FIXTURES)))


def one_rung(across: int) -> None:
    run, order = harness.the_run(across)
    store = run.folder / "views" / "live" / "live.ome.zarr"
    middle_of = middle_of_factory(across)

    began = time.perf_counter()
    committed = frozenset(run._committed_units())
    run.write_the_link_map(committed)
    run.write_the_view()
    relink_s = time.perf_counter() - began

    laps, absent = [], 0

    for number in range(30):
        inside = piece_path(store, 0, *middle_of(order[number % len(order)]))
        marked = time.perf_counter()
        answer = answer_from_a_live_run(store / inside)

        if answer is None or not answer.allowed or answer.serving is None:
            absent += 1
            laps.append((time.perf_counter() - marked) * 1000)
            continue

        with open(answer.serving.path, "rb") as source:
            source.seek(answer.serving.offset)
            source.read(answer.serving.length)
        laps.append((time.perf_counter() - marked) * 1000)
    note = f"  [{absent} ABSENT]" if absent else ""
    print(
        f"{across * across:6} positions: re-link {relink_s:6.1f}s "
        f"({len(committed)} units) | linked serve first {laps[0]:.1f}ms, "
        f"median {statistics.median(laps):.2f}ms, max {max(laps):.1f}ms{note}",
        flush=True,
    )


if __name__ == "__main__":
    for across in [int(one) for one in sys.argv[1:]] or [20, 50, 100]:
        one_rung(across)
