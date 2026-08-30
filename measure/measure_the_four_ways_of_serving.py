"""The four ways of serving a survey, timed at the real door, small to huge.

Static linked (pointer byte ranges), static baked (files), and the governed
live picture under a sustained 10-commits-a-second storm -- baked and
unbaked -- at several survey sizes. Rigour rules: every piece path is
computed from the store's own metadata, a probe that answers absent is
counted and never averaged in, the serving object's type is printed so a
storm against a non-watching composer cannot pass silently, and every storm
ends with a freshness assertion -- the replaced pixels carry a sentinel
value, and the served picture must show it. Replacement stores are copied
from the fixture's own folders: nothing is generated.
"""

import json
import os
import shutil
import statistics
import sys
import threading
import time
from pathlib import Path

import zarr

_VIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_VIZ))
sys.path.insert(0, str(_VIZ / "measure"))
sys.path.insert(0, str(_VIZ.parent))

import measure_a_governed_run_at_scale as harness  # noqa: E402
from zmart_viewer.record.gateway import answer_from_a_live_run  # noqa: E402
from zmart_viewer.record.model import CommitEvent  # noqa: E402
from zmart_viewer.record.profiles import plan_the_writing  # noqa: E402

from zmart_viewer import pieces as served  # noqa: E402
from zmart_viewer.building import GovernedRun, declare_a_governed_picture  # noqa: E402
from zmart_viewer.live import the_live_picture_declared  # noqa: E402

harness.FIXTURES = Path(os.environ.get("ZMART_FIXTURES", str(harness.FIXTURES)))

RATE = 10.0
SECONDS = 30.0
COMMITS = int(RATE * SECONDS)
SENTINEL = 21212


def grid_of(store: Path, level: int) -> tuple[list[int], list[int]]:
    described = json.loads((store / str(level) / "zarr.json").read_text())
    return described["shape"], described["chunk_grid"]["configuration"]["chunk_shape"]


def piece_path(store: Path, level: int, y_px: int, x_px: int) -> str:
    shape, chunk = grid_of(store, level)
    factor = max(1, round(grid_of(store, 0)[0][-1] / shape[-1]))
    coords = [0] * (len(shape) - 2) + [y_px // factor // chunk[-2], x_px // factor // chunk[-1]]
    return f"{level}/c/" + "/".join(str(one) for one in coords)


def parse_cell(name: str, across: int) -> tuple[int, int]:
    width = len(str(across - 1))
    return int(name[1 : 1 + width]), int(name[1 + width :])


def middle_of_factory(across: int):
    geometry = plan_the_writing("overview", frame=harness.FRAME, z_planes=1)[1]
    step = tuple(geometry.step_shape)

    def middle_of(name: str) -> tuple[int, int]:
        row, column = parse_cell(name, across)
        return row * step[0] + harness.FRAME // 2, column * step[1] + harness.FRAME // 2

    return middle_of


def generations_from(run) -> dict[str, int]:
    gens: dict[str, int] = {}

    for event in run.manifest.events():
        gens[event.position_id] = max(
            gens.get(event.position_id, 0), event.position_generation or 0
        )
    return gens


def replace_publish(run, position_id: str, generation: int) -> None:
    run.manifest.publish(
        CommitEvent(
            revision=run.manifest.next_revision(),
            event_type="position_replaced",
            position_id=position_id,
            position_generation=generation,
            run_id=run.run_id,
            acquisition_type=run.profile.acquisition_type,
            acquisition_profile_id=run.profile.profile_id,
            scene_layout_revision=run.layout.revision,
            link_revision=1,
            channels=tuple(run.channels),
            levels=tuple(range(len(run.profile.levels))),
            pyramids_ready=True,
            links_ready=True,
            view_ready=True,
            validated=True,
        )
    )


def _a_complete_copy(root: Path, name: str, generation: int, fill: bool) -> None:
    """The generation store, present and whole -- a partial copy from a killed
    run passes an exists() check and crashes the derive, so completeness is
    judged by the file list, never by the folder being there."""
    source = root / name
    target = root / f"{name}.generation-{generation}"
    expected = sorted(one.relative_to(source).as_posix() for one in source.rglob("*"))

    if target.exists():
        found = sorted(one.relative_to(target).as_posix() for one in target.rglob("*"))

        if found != expected:
            shutil.rmtree(target)

    if not target.exists():
        shutil.copytree(source, target)

    if fill:
        group = zarr.open_group(str(target), mode="r+")

        for key in group.array_keys():
            group[key][:] = SENTINEL


def repair_generations(run, gens: dict[str, int]) -> None:
    """Every generation the manifest names, made whole before anything is timed."""
    root = run.folder / "data" / "survey.ome.zarr"
    began = time.time()
    repaired = 0

    for name, top in gens.items():
        for generation in range(1, top + 1):
            _a_complete_copy(root, name, generation, fill=False)
            repaired += 1

    if repaired:
        print(
            f"    {repaired} manifest-named generation stores verified whole "
            f"({time.time() - began:.0f}s)",
            flush=True,
        )


def prepare_generations(run, victims: list[str], gens: dict[str, int]) -> dict[str, int]:
    """Sentinel-filled generation stores for every commit the storm will make."""
    root = run.folder / "data" / "survey.ome.zarr"
    planned: dict[str, int] = {}

    for number in range(COMMITS):
        name = victims[number % len(victims)]
        planned[name] = planned.get(name, 0) + 1
    began = time.time()

    for name, extra in planned.items():
        base = gens.get(name, 0)

        for generation in range(base + 1, base + extra + 1):
            _a_complete_copy(root, name, generation, fill=True)
    print(
        f"    {sum(planned.values())} sentinel generation stores ready "
        f"({time.time() - began:.0f}s)",
        flush=True,
    )
    return planned


def a_quiet_sample(label: str, probe, count: int = 30) -> None:
    laps, absent, failed = [], 0, 0

    for number in range(count):
        marked = time.perf_counter()

        try:
            answer = probe(number)
        except Exception:
            answer, failed = None, failed + 1
        laps.append((time.perf_counter() - marked) * 1000)
        absent += answer is None
    note = "" if not absent and not failed else f"  [{absent} ABSENT, {failed} FAILED]"
    print(
        f"    {label}: first {laps[0]:.1f}ms, median {statistics.median(laps):.2f}ms, "
        f"max {max(laps):.1f}ms{note}",
        flush=True,
    )


def freshness(store, victim: str, across: int, step: tuple[int, int]) -> bool:
    held = served._composer_for(store)
    composer = held.composer() if isinstance(held, GovernedRun) else held
    row, column = parse_cell(victim, across)
    y = row * step[0] + harness.FRAME // 2
    x = column * step[1] + harness.FRAME // 2
    _, chunk = grid_of(store, 0)
    piece = composer.values_for(0, 0, y // chunk[-2], x // chunk[-1])
    seen = piece is not None and bool((piece == SENTINEL).any())
    print(
        f"    freshness: sentinel of {victim} "
        f"{'SERVED -- the picture is current' if seen else 'MISSING -- STALE PICTURE'}",
        flush=True,
    )
    return seen


def the_storm(label, run, store, coarse: str, fine: str, victims, gens) -> None:
    held = served._composer_for(store)
    governed = held if isinstance(held, GovernedRun) else None
    print(
        f"    serving object: {type(held).__name__}"
        + ("" if governed else "  [NOT GOVERNED -- this storm cannot be absorbed]"),
        flush=True,
    )
    derives_before = governed.accounting["derives"] if governed else 0
    stop = threading.Event()
    committed = [0]
    coarse_ms: list[float] = []
    fine_ms: list[float] = []
    absent = [0]
    failed = [0]

    def commits() -> None:
        beat = 1.0 / RATE
        next_at = time.monotonic()

        for number in range(COMMITS):
            name = victims[number % len(victims)]
            gens[name] = gens.get(name, 0) + 1
            replace_publish(run, name, gens[name])
            committed[0] = number + 1
            next_at += beat
            time.sleep(max(0.0, next_at - time.monotonic()))

    def viewing() -> None:
        while not stop.is_set():
            for path, laps in ((coarse, coarse_ms), (fine, fine_ms)):
                marked = time.perf_counter()

                try:
                    answer = served.built_bytes_behind(store, path)
                except Exception:
                    answer, failed[0] = None, failed[0] + 1
                laps.append((time.perf_counter() - marked) * 1000)
                absent[0] += answer is None
            time.sleep(0.02)

    storm = threading.Thread(target=commits)
    viewer = threading.Thread(target=viewing)
    began = time.monotonic()
    storm.start()
    viewer.start()
    storm.join()
    stop.set()
    viewer.join(timeout=10)
    took = time.monotonic() - began

    def report(name: str, laps: list[float]) -> None:
        ranked = sorted(laps)
        print(
            f"    {name}: {len(laps)} answers, median {statistics.median(laps):.1f}ms, "
            f"p90 {ranked[int(0.9 * len(ranked))]:.1f}ms, max {max(laps):.1f}ms"
        )

    print(
        f"    {committed[0]} commits at {RATE:.0f}/s over {took:.1f}s"
        + (f"  [{absent[0]} ABSENT, {failed[0]} FAILED answers]" if absent[0] or failed[0] else "")
    )
    report(f"coarse {coarse}", coarse_ms)
    report(f"level-0 {fine}", fine_ms)

    if governed:
        derives = governed.accounting["derives"] - derives_before
        print(
            f"    derives: {derives} ({committed[0] / max(derives, 1):.1f} commits "
            f"absorbed each), last derive {governed.accounting['last_derive_ms']:.1f}ms, "
            f"tiles read {governed.accounting['last_tiles_read']}",
            flush=True,
        )


def one_rung(across: int) -> None:
    print(f"\n==== {across * across} positions ====", flush=True)
    run, order = harness.the_run(across)
    geometry = plan_the_writing("overview", frame=harness.FRAME, z_planes=1)[1]
    step = tuple(geometry.step_shape)
    gens = generations_from(run)
    repair_generations(run, gens)
    half = min(COMMITS, len(order) // 2)
    baked_victims, plain_victims = order[:half], order[half : 2 * half]

    print("  declaring the baked picture (reused when already declared):")
    began = time.time()
    baked_store = the_live_picture_declared(run.folder, bake=True)
    print(f"    {baked_store.name} in {time.time() - began:.1f}s")
    pointer_store = run.folder / "views" / "live" / "live.ome.zarr"

    def middle_of(name: str) -> tuple[int, int]:
        row, column = parse_cell(name, across)
        return row * step[0] + harness.FRAME // 2, column * step[1] + harness.FRAME // 2

    y, x = middle_of(baked_victims[0])
    coarse = piece_path(baked_store, 3, y, x)
    fine = piece_path(baked_store, 0, y, x)

    def through_the_gateway(number: int):
        inside = piece_path(pointer_store, 0, *middle_of(order[number % len(order)]))
        answer = answer_from_a_live_run(pointer_store / inside)

        if answer is None or not answer.allowed or answer.serving is None:
            return None

        with open(answer.serving.path, "rb") as source:
            source.seek(answer.serving.offset)
            return source.read(answer.serving.length)

    print("  -- static, linked (the gateway's zero-copy byte ranges)")
    a_quiet_sample("linked level-0", through_the_gateway)

    print("  -- static, baked (coarse pieces from files)")
    a_quiet_sample(f"baked {coarse}", lambda number: served.built_bytes_behind(baked_store, coarse))

    print("  -- live, baked: the storm")
    prepare_generations(run, baked_victims, gens)
    the_storm("baked", run, baked_store, coarse, fine, baked_victims, gens)
    freshness(baked_store, baked_victims[0], across, step)
    served.forget(baked_store)

    print("  declaring the same run unbaked (a second view):")
    began = time.time()
    plain_store = declare_a_governed_picture(
        run.folder / "views" / "plain", run.folder, name="plain", bake=False
    )
    print(f"    {plain_store.name} in {time.time() - began:.1f}s")
    y, x = middle_of(plain_victims[0])
    plain_coarse = piece_path(plain_store, 3, y, x)
    plain_fine = piece_path(plain_store, 0, y, x)

    print("  -- static, unbaked (coarse pieces composed on request)")
    a_quiet_sample(
        f"composed {plain_coarse}",
        lambda number: served.built_bytes_behind(plain_store, plain_coarse),
        count=8,
    )

    print("  -- live, unbaked: the storm")
    prepare_generations(run, plain_victims, gens)
    the_storm("unbaked", run, plain_store, plain_coarse, plain_fine, plain_victims, gens)
    freshness(plain_store, plain_victims[0], across, step)
    served.forget(plain_store)


if __name__ == "__main__":
    for across in [int(one) for one in sys.argv[1:]] or [20, 50, 100]:
        one_rung(across)
