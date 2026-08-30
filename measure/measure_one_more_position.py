"""One more position onto a survey of N: the cost, measured at three rungs.

The claim under test (the operator's own): one more landing onto ten
thousand positions costs what it costs onto four hundred. Reports, per
rung: the real writer's cost, the derive with its phases, tiles read
(must be at most one), and the sweep counter that names any residual
slope. Uncommitted positions are landed; a fully committed fixture is
grown by real replacements instead, which is the same one-more-change.
"""

import json
import os
import statistics
import sys
import time
from pathlib import Path

import numpy as np

_VIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_VIZ))
sys.path.insert(0, str(_VIZ / "measure"))
sys.path.insert(0, str(_VIZ.parent))

import measure_a_governed_run_at_scale as harness  # noqa: E402

from zmart_viewer.building import GovernedRun  # noqa: E402
from zmart_viewer.live import the_live_picture_declared  # noqa: E402

harness.FIXTURES = Path(os.environ.get("ZMART_FIXTURES", str(harness.FIXTURES)))
harness.FIXTURES.mkdir(exist_ok=True)


def rung(across: int) -> dict:
    run, order = harness.the_run(across)
    # The smart-microscopy configuration the migration decision settled on:
    # the linked view is an end-of-run product, never a per-landing tax.
    object.__setattr__(run, "linked_view", "at_run_end")
    committed = {position for position, _moment in run._committed_units()}
    uncommitted = [one for one in order if one not in committed]
    held_back, bulk = uncommitted[-5:], uncommitted[:-5]
    began = time.time()

    for number, position_id in enumerate(bulk):
        harness.fast_publish(run, position_id)

        if (number + 1) % 2000 == 0:
            print(
                f"    {number + 1}/{len(bulk)} published ({time.time() - began:.0f}s)", flush=True
            )

    the_live_picture_declared(run.folder, bake=True)
    governed = GovernedRun(run.folder)
    composer = governed.composer()
    composer.stop_warming()
    governed.composer()  # warm: the steady state a watched run sits in

    body = np.full((1, harness.FRAME, harness.FRAME), 7, dtype=np.uint16)
    landings = []

    for number in range(5):
        marked = time.perf_counter()

        if number < len(held_back):
            run.write_and_publish(held_back[number], body)
        else:
            run.replace_a_position(order[number], body)
        write_ms = (time.perf_counter() - marked) * 1000
        marked = time.perf_counter()
        governed.request_catch_up()
        governed.composer()
        derive_wall_ms = (time.perf_counter() - marked) * 1000
        landings.append(
            {
                "write_ms": write_ms,
                "derive_wall_ms": derive_wall_ms,
                "derive_ms": governed.accounting.get("last_derive_ms"),
                "tiles_read": governed.accounting.get("last_tiles_read"),
                "swept": governed.accounting.get("last_snapshot_swept"),
            }
        )
    return {
        "across": across,
        "positions": across * across,
        "write_median_ms": round(statistics.median(one["write_ms"] for one in landings), 1),
        "derive_median_ms": round(statistics.median(one["derive_ms"] for one in landings), 1),
        "derive_wall_median_ms": round(
            statistics.median(one["derive_wall_ms"] for one in landings), 1
        ),
        "tiles_read_max": max(one["tiles_read"] for one in landings),
        "swept": landings[-1]["swept"],
        "phases_last": governed.accounting.get("last_phase_ms"),
    }


if __name__ == "__main__":
    for across in [int(one) for one in sys.argv[1:]] or [20]:
        print(f"== rung {across}x{across} = {across * across} positions", flush=True)
        print(json.dumps(rung(across), indent=1), flush=True)
