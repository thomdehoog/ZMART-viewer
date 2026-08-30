"""The churn ladder: one position to one hundred thousand, eight ways each.

Every decade rung is served four ways -- static linked, static baked, live
unbaked, live baked -- and each way twice: nominal grid placements and
scattered off-chunk ones. Scattered cannot be zero-copy linked, by design,
and that cell records the refusal rather than a number.

What is measured per cell: the cold open (a fresh governed reading, first
derive, first coarse piece), the churn (a real replacement committed, the
derive it costs, the tiles it re-reads, the piece latency right after), and
for the baked column the price of the bake itself. The writer's own landing
cost is measured on the small rungs, where the real writer built the run.

Pixels are one real position's store, hard-linked into place -- placement
lives in the layout, never in the pixels -- and rungs at ten thousand and
above carry a fabricated record in the manifest's own schema, which the
measurement trusts only after the viewer has folded it and found every
commit. Nothing is ever written into a hard-linked store; replacements copy.
"""

from __future__ import annotations

import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

_VIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_VIZ))
sys.path.insert(0, str(_VIZ / "measure"))

RESULTS = Path(__file__).resolve().parent / "results"
SHOTS = RESULTS / "shots"

import numpy as np  # noqa: E402

from zmart_viewer import pieces as served  # noqa: E402
from zmart_viewer.building import GovernedRun  # noqa: E402
from zmart_viewer.live import the_live_picture_declared  # noqa: E402
from zmart_viewer.pieces import link_a_finished_run, pointed_bytes_behind  # noqa: E402
from zmart_viewer.record.coordinator import LivePublisher  # noqa: E402
from zmart_viewer.record.model import CommitEvent  # noqa: E402
from zmart_viewer.record.profiles import plan_the_writing  # noqa: E402

LADDER = Path(os.environ.get("ZMART_CHURN_LADDER", "/tmp/zmart-churn"))
FRAME = 384
STEP = 320  # chunk-aligned: 320 is five whole 64-pixel chunks
STREWN = STEP // 3  # how far a scattered position may land from its grid seat
RUNGS = [
    int(n) for n in os.environ.get("ZMART_CHURN_RUNGS", "1,10,100,1000,10000,100000").split(",")
]
FABRICATED_FROM = 100  # rungs 1 and 10 go through the real writer and anchor
# the fabrication: the fabricated record is in the same schema, and the viewer
# must fold it whole before anything fabricated is measured.
CHANGES = 3

#: The data is grown on purpose: a real acquisition has moments, colours and
#: depth, and a benchmark of flat frames would flatter every path.
MOMENTS = 3
CHANNELS = ("DAPI", "GFP")
PLANES = 4


def profile_and_layouts():
    profile, _ = plan_the_writing(
        "overview", frame=FRAME, z_planes=PLANES, timepoints=MOMENTS, channels=CHANNELS
    )
    return profile


def the_specimen(moment: int) -> np.ndarray:
    """Pixels worth photographing: rings and gradients, distinct per moment,
    channel and plane, inside a 400..3800 band a window can show."""
    span = np.linspace(-1, 1, FRAME)
    across, down = np.meshgrid(span, span)
    radius = np.sqrt(across**2 + down**2)
    body = np.zeros((len(CHANNELS), PLANES, FRAME, FRAME), dtype="uint16")

    for channel in range(len(CHANNELS)):
        for plane in range(PLANES):
            rings = np.sin(radius * (8 + 4 * channel) + moment + plane / 2) ** 2
            slope = (across + 1) / 2 if channel else (down + 1) / 2
            body[channel, plane] = (400 + 3400 * (0.6 * rings + 0.4 * slope)).astype("uint16")
    return body


def origins_for(count: int, scattered: bool) -> dict[str, dict[str, int]]:
    """Where the positions land: a neat grid, or genuinely strewn about.

    Scattered means what an operator means by it: every position lands its
    own way off its grid seat -- overlapping some neighbours, leaving gaps by
    others, nothing tiled. The strewing is deterministic so a rung's fixture
    is reproducible, and any position that happens to land back on the chunk
    lattice is nudged off it, so the whole placement stays unlinkable.
    """
    across = max(1, int(count**0.5))
    strewn = np.random.default_rng(7).integers(-STREWN, STREWN + 1, size=(count, 2))
    named = {}

    for index in range(count):
        row, column = divmod(index, across)
        y, x = row * STEP, column * STEP

        if scattered:
            y = max(0, y + int(strewn[index, 0]))
            x = max(0, x + int(strewn[index, 1]))
            y += 7 if y % 64 == 0 else 0
            x += 13 if x % 64 == 0 else 0
        named[f"p{index:06d}"] = {"y": y, "x": x}
    return named


def the_master_store(profile) -> Path:
    """One real position, written once by the real writer, linked everywhere."""
    template = LADDER / "master"

    if not (template / "data" / "survey.ome.zarr" / "p000000" / "zarr.json").is_file():
        run = LivePublisher(
            template,
            profile,
            run_id="master",
            positions={"p000000": {"y": 0, "x": 0}},
            linked_view="at_run_end",
        )

        for moment in range(MOMENTS):
            run.write_a_position("p000000", the_specimen(moment), timepoint=moment)
    return template / "data" / "survey.ome.zarr" / "p000000"


def a_fabricated_run(folder: Path, profile, origins: dict) -> LivePublisher:
    """The rung's run: hard-linked stores, a record the viewer must fold whole."""
    master = the_master_store(profile)
    survey = folder / "data" / "survey.ome.zarr"

    if not (folder / "views" / "live" / "metadata" / "signed.json").is_file():
        run = LivePublisher(
            folder, profile, run_id=folder.name, positions=origins, linked_view="at_run_end"
        )
        began = time.time()

        pattern = json.loads((master / "zarr.json").read_text())
        scale_y, scale_x = pattern["attributes"]["ome"]["multiscales"][0]["datasets"][0][
            "coordinateTransformations"
        ][0]["scale"][-2:]

        for number, (name, origin) in enumerate(origins.items()):
            subprocess.run(
                ["cp", "-al", str(master), str(survey / name)], check=True
            ) if number else shutil.copytree(master, survey / name)

            # The pixels are the master's, but the corner is this position's
            # own: the viewer checks a store's stamped corner against the
            # layout and refuses a drifted record, exactly as it should. The
            # replacement goes through a fresh file -- writing into the
            # hard-linked one would restamp the master and every sibling.
            stamped = json.loads(json.dumps(pattern))
            about = stamped["attributes"]["ome"]["multiscales"][0]
            about["name"] = name

            for dataset in about["datasets"]:
                translation = dataset["coordinateTransformations"][1]["translation"]
                translation[-2] = origin["y"] * scale_y
                translation[-1] = origin["x"] * scale_x
            fresh = survey / name / "zarr.json.fresh"
            fresh.write_text(json.dumps(stamped))
            os.replace(fresh, survey / name / "zarr.json")

            if (number + 1) % 20000 == 0:
                print(f"      {number + 1} stores linked ({time.time() - began:.0f}s)", flush=True)
        run.write_the_layout()
        meta = folder / "views" / "live" / "metadata"
        began = time.time()

        revision = run.manifest.next_revision() - 1
        by_store: dict[str, int] = {}

        with open(meta / "events.jsonl", "a", encoding="utf-8") as history:
            for name in origins:
                for moment in range(MOMENTS):
                    revision += 1
                    kind = "position_committed" if moment == 0 else "timepoint_committed"
                    history.write(
                        json.dumps(
                            CommitEvent(
                                revision=revision,
                                event_type=kind,
                                position_id=name,
                                run_id=run.run_id,
                                acquisition_type=run.profile.acquisition_type,
                                acquisition_profile_id=run.profile.profile_id,
                                scene_layout_revision=run.layout.revision,
                                link_revision=1,
                                timepoint=moment,
                                channels=tuple(run.channels),
                                levels=tuple(range(len(run.profile.levels))),
                                pyramids_ready=True,
                                links_ready=True,
                                view_ready=True,
                                validated=True,
                            ).to_json()
                        )
                        + "\n"
                    )
                by_store[name] = revision
        signed = json.loads((meta / "signed.json").read_text())
        signed["revision"] = revision
        signed["by_store"] = by_store
        signed["layout_revision"] = run.layout.revision
        (meta / "signed.json").write_text(json.dumps(signed))
        print(f"      record fabricated ({time.time() - began:.0f}s)", flush=True)

    run = LivePublisher(
        folder, profile, run_id=folder.name, positions=origins, linked_view="at_run_end"
    )
    committed = len(set(run._committed_units()))
    assert committed == len(origins) * MOMENTS, (
        f"the viewer folded {committed} units where {len(origins) * MOMENTS} were "
        "fabricated -- the record is not spec-true, nothing here may be measured"
    )
    return run


def a_written_run(folder: Path, profile, origins: dict) -> tuple[LivePublisher, float | None]:
    """The rung's run through the real writer, and one real landing's cost."""
    run = LivePublisher(
        folder, profile, run_id=folder.name, positions=origins, linked_view="at_run_end"
    )
    landing_s = None

    if not set(run._committed_units()):
        for name in origins:
            for moment in range(MOMENTS):
                marked = time.perf_counter()
                run.write_and_publish(name, the_specimen(moment), timepoint=moment)
                landing_s = time.perf_counter() - marked
    return run, landing_s


def churn(run: LivePublisher, governed: GovernedRun, store: Path, coarse: str) -> dict:
    """CHANGES real replacements: what each costs the reader, measured after.

    Two pieces are served after every replacement: the coarse one an overview
    shows, and the full-resolution piece directly under the replaced position,
    which is what an operator zoomed in on it would be looking at. Both are
    read before the replacement too, and each replacement's pixels are a ramp
    shifted by its generation -- so a serve that hands back the bytes from
    before is stale, and a stale serve invalidates the whole cell loudly.
    The ramp stays inside the display band: churned positions photograph.
    """
    survey = run.folder / "data" / "survey.ome.zarr"
    names = sorted(run.positions)[:CHANGES]
    laps = []

    zeroth = json.loads((store / "0" / "zarr.json").read_text())
    chunk = zeroth["chunk_grid"]["configuration"]["chunk_shape"]

    def right_under(name: str) -> str:
        origin = run.positions[name]
        return f"0/c/0/0/0/{origin['y'] // chunk[-2]}/{origin['x'] // chunk[-1]}"

    for name in names:
        fine = right_under(name)
        before_coarse = served.built_bytes_behind(store, coarse)
        before_fine = served.built_bytes_behind(store, fine)
        generation = run.generations.get(name, 0) + 1
        target = survey / f"{name}.generation-{generation}"

        if not target.exists():
            shutil.copytree(survey / name, target)
            import zarr

            group = zarr.open_group(str(target), mode="r+")

            for key in group.array_keys():
                array = group[key]
                width = array.shape[-1]
                ramp = (
                    600 + 137 * generation + np.arange(width) * 3000 // max(1, width - 1)
                ) % 3800
                array[:] = np.broadcast_to(ramp.astype("uint16"), array.shape)
        # One replacement is ONE commit: the record advances every visible
        # moment of the position to the new generation by itself.
        run.manifest.publish(
            CommitEvent(
                revision=run.manifest.next_revision(),
                event_type="position_replaced",
                position_id=name,
                position_generation=generation,
                run_id=run.run_id,
                acquisition_type=run.profile.acquisition_type,
                acquisition_profile_id=run.profile.profile_id,
                scene_layout_revision=run.layout.revision,
                link_revision=1,
                timepoint=0,
                channels=tuple(run.channels),
                levels=tuple(range(len(run.profile.levels))),
                pyramids_ready=True,
                links_ready=True,
                view_ready=True,
                validated=True,
            )
        )
        run.generations[name] = generation
        marked = time.perf_counter()
        governed.composer()
        derive_ms = (time.perf_counter() - marked) * 1000
        marked = time.perf_counter()
        answer = served.built_bytes_behind(store, coarse)
        serve_ms = (time.perf_counter() - marked) * 1000
        marked = time.perf_counter()
        sharp = served.built_bytes_behind(store, fine)
        fine_ms = (time.perf_counter() - marked) * 1000
        laps.append(
            {
                "derive_ms": derive_ms,
                "tiles_read": governed.accounting["last_tiles_read"],
                "swept": governed.accounting["last_snapshot_swept"],
                "serve_ms": serve_ms,
                "fine_ms": fine_ms,
                "absent": answer is None or sharp is None,
                "stale": (answer is not None and answer == before_coarse)
                or (sharp is not None and sharp == before_fine),
            }
        )
    stale = sum(one["stale"] for one in laps)
    assert stale == 0, (
        f"{stale} serve(s) handed back the bytes from before the replacement -- "
        "the churn numbers would be measuring a stale cache, nothing here counts"
    )
    return {
        "derive_median_ms": round(statistics.median(one["derive_ms"] for one in laps), 1),
        "tiles_read_max": max(one["tiles_read"] for one in laps),
        "swept": laps[-1]["swept"],
        "serve_median_ms": round(statistics.median(one["serve_ms"] for one in laps), 1),
        "fine_serve_median_ms": round(statistics.median(one["fine_ms"] for one in laps), 1),
        "absent_answers": sum(one["absent"] for one in laps),
    }


def a_cell(folder: Path, profile, origins: dict, *, bake: bool, browser, label: str) -> dict:
    count = len(origins)
    build = a_fabricated_run if count >= FABRICATED_FROM else a_written_run
    made = build(folder, profile, origins)
    run, landing_s = made if isinstance(made, tuple) else (made, None)

    marked = time.perf_counter()
    store = the_live_picture_declared(run.folder, bake=bake)
    declared_s = time.perf_counter() - marked

    marked = time.perf_counter()
    governed = GovernedRun(run.folder)
    composer = governed.composer()
    composer.stop_warming()
    cold_derive_s = time.perf_counter() - marked

    levels = sorted(int(one.name) for one in store.iterdir() if one.name.isdigit())
    coarse_level = min(3, levels[-1])
    shape = json.loads((store / str(coarse_level) / "zarr.json").read_text())["shape"]
    coarse = f"{coarse_level}/c/" + "/".join("0" for _ in shape)
    marked = time.perf_counter()
    served.built_bytes_behind(store, coarse)
    first_piece_s = time.perf_counter() - marked

    linked = None

    if not bake:
        try:
            marked = time.perf_counter()
            view = link_a_finished_run(run.folder)
            link_s = time.perf_counter() - marked
            marked = time.perf_counter()
            held = pointed_bytes_behind(view, "0/c/0/0/0/0/0")
            linked = {
                "link_s": round(link_s, 1),
                "first_pointed_ms": round((time.perf_counter() - marked) * 1000, 1),
                "answered": held is not None,
            }
        except ValueError as refusal:
            linked = {"refused": str(refusal).split(",")[0][:80]}

    # The photograph comes before the churn, so it shows the specimen as it
    # landed rather than whatever the replacements painted over it.
    look = the_physical_look(browser, store, label)

    cell = {
        "declared_s": round(declared_s, 2),
        "cold_derive_s": round(cold_derive_s, 3),
        "first_piece_s": round(first_piece_s, 3),
        "disk": the_footprint(run.folder, store),
        "look": look,
        "churn": churn(run, governed, store, coarse),
    }

    if landing_s is not None:
        cell["real_landing_s"] = round(landing_s, 2)

    if linked is not None:
        cell["linked"] = linked
    governed.close()
    served.forget(store)
    return cell


def resident_mb() -> float:
    line = next(one for one in Path("/proc/self/status").read_text().splitlines() if "VmRSS" in one)
    return round(int(line.split()[1]) / 1024, 1)


def browser_rss_mb() -> float:
    """What the browser's processes hold, summed -- the other half of the memory
    story: the benchmark's own RSS says nothing about what viewing costs."""
    listed = subprocess.run(["ps", "-eo", "rss,comm"], capture_output=True, text=True).stdout
    return round(
        sum(
            int(one.split(None, 1)[0])
            for one in listed.splitlines()[1:]
            if "chrom" in one or "headless" in one
        )
        / 1024,
        1,
    )


def the_footprint(folder: Path, store: Path) -> dict:
    """What this rung costs on disk: the served view and the record itself.

    The pixels are not counted -- they are hard-linked from one master and any
    du over them would charge the same bytes to every rung.
    """
    meta = folder / "views" / "live" / "metadata"
    events = meta / "events.jsonl"
    view_kb = int(
        subprocess.run(["du", "-sk", str(store)], capture_output=True, text=True).stdout.split()[0]
    )
    return {
        "view_mb": round(view_kb / 1024, 1),
        "record_kb": round(
            (events.stat().st_size + (meta / "signed.json").stat().st_size) / 1024, 1
        ),
        "events": sum(1 for _ in open(events, encoding="utf-8")),
    }


def the_physical_look(browser, store: Path, label: str) -> dict:
    """Open the picture in a real browser, wait, photograph it, then feel it.

    After the photograph the view is kept moving for a few seconds while
    frames are counted: the middle frame gap is the rate the viewer holds,
    the worst gap is the pause an operator would feel, and the request count
    is the traffic this picture costs the server. Snappiness, measured.
    """
    import threading

    from watching import COUNT_FRAMES, EVERY_SOURCE_RESOLVED, KEEP_MOVING, SAMPLE_SECONDS

    from zmart_viewer.server import make_server

    server = make_server(
        port=0, data_dir=store.parent, store=[store.name], live=False, window=(0, 4095)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = browser.new_page(viewport={"width": 1100, "height": 850})
    asked = {"requests": 0}
    page.on("request", lambda _: asked.update(requests=asked["requests"] + 1))

    try:
        opening = time.perf_counter()
        page.goto(f"http://127.0.0.1:{server.server_address[1]}", wait_until="domcontentloaded")
        page.wait_for_function("() => window.zmartViewer !== undefined", timeout=120_000)
        page.wait_for_function("() => window.zmartSourcesWaiting() === 0", timeout=600_000)
        page.wait_for_function(EVERY_SOURCE_RESOLVED, timeout=600_000)
        resolved_s = time.perf_counter() - opening
        from pixels import colour_spread, fraction_lit, image_middle

        lit = 0.0

        for _ in range(120):
            lit = fraction_lit(page)

            if lit > 0.02:
                break
            page.wait_for_timeout(500)
        first_picture_s = time.perf_counter() - opening

        # "Sources resolved and something lit" is not "picture finished": at ten
        # positions the first look caught a mosaic photographed while still
        # refining -- one channel, coarse level. And one quiet half-second is
        # not "finished" either: composed pieces arrive with lulls longer than
        # that, which once passed for settled and failed the channel guard on a
        # perfectly healthy viewer. Settled means the canvas has not changed
        # for two whole seconds AND both channels are in it; how long that
        # takes is itself the number an operator feels as time-to-sharp.
        settled, quiet, before = False, 0, image_middle(page)

        def both_channels(view) -> bool:
            greenish = (view[:, :, 1].astype(int) - view[:, :, 0] > 30).mean()
            magentaish = (view[:, :, 0].astype(int) - view[:, :, 1] > 30).mean()
            return bool(greenish > 0.01 and magentaish > 0.01)

        for _ in range(480):
            page.wait_for_timeout(500)
            now = image_middle(page)
            quiet = quiet + 1 if np.array_equal(now, before) else 0
            before = now

            if quiet >= 4 and both_channels(now):
                settled = True
                break
        settled_s = time.perf_counter() - opening
        shot = SHOTS / f"{label}.png"
        page.screenshot(path=str(shot))
        # A photograph that cannot lie: a lit canvas that is one flat colour --
        # blank white, blank anything -- is not the specimen, and must not pass.
        settled_view = image_middle(page)
        variety = colour_spread(settled_view)
        greenish = float((settled_view[:, :, 1].astype(int) - settled_view[:, :, 0] > 30).mean())
        magentaish = float((settled_view[:, :, 0].astype(int) - settled_view[:, :, 1] > 30).mean())

        if settled:
            # A settled claim must be a real specimen: flat or one-channel here
            # means the wait was fooled, and the cell may not pass.
            assert variety["distinct"] > 50, (
                f"the canvas settled but flat ({variety['distinct']} distinct colours) -- "
                f"whatever {label} is showing, it is not the specimen"
            )
            assert both_channels(settled_view), (
                f"the settled picture of {label} is missing a channel: "
                f"{greenish:.3f} green, {magentaish:.3f} magenta"
            )
        # An unsettled deadline is a result, not a failure: at some scales the
        # unbaked overview simply is not a first screen, and the row says so --
        # settled false, the fractions reached, and the photograph of that state.

        asked_opening = asked["requests"]
        page.evaluate(KEEP_MOVING)
        page.evaluate(COUNT_FRAMES)
        page.evaluate("() => { window.__drawn = 0; window.__at = []; }")
        page.wait_for_timeout(int(SAMPLE_SECONDS * 1000))
        at = [float(n) for n in page.evaluate("() => window.__at")]
        page.evaluate("() => clearInterval(window.__nudge)")
        gaps = sorted(later - earlier for earlier, later in zip(at, at[1:]))

        return {
            "resolved_s": round(resolved_s, 2),
            "first_picture_s": round(first_picture_s, 2),
            "settled_s": round(settled_s, 2),
            "settled": settled,
            "green": round(greenish, 3),
            "magenta": round(magentaish, 3),
            "lit": round(lit, 3),
            "distinct_colours": variety["distinct"],
            "frame_ms": round(gaps[len(gaps) // 2], 1) if gaps else None,
            "worst_pause_ms": round(gaps[-1], 1) if gaps else None,
            "asked_opening": asked_opening,
            "asked_moving": asked["requests"] - asked_opening,
            "browser_rss_mb": browser_rss_mb(),
            "shot": str(shot.relative_to(RESULTS.parent)),
        }
    finally:
        page.close()
        server.shutdown()
        thread.join(timeout=5)


def main() -> None:
    LADDER.mkdir(exist_ok=True)
    SHOTS.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(_VIZ / "tests"))
    profile = profile_and_layouts()

    from watching import a_browser, say_what_is_drawing

    started, browser = a_browser()
    say_what_is_drawing(browser)
    record = open(RESULTS / "ladder.jsonl", "a", encoding="utf-8")

    try:
        for count in RUNGS:
            print(f"\n==== {count} position(s)", flush=True)

            for scattered in (True, False):
                for bake in (False, True):
                    placement = "scattered" if scattered else "nominal"
                    # Every cell owns its folder: a shared one would hand the
                    # baked cell the unbaked cell's churned pixels and its
                    # grown record, and the photograph would show the churn.
                    label = f"n{count}_{placement}_{'baked' if bake else 'unbaked'}"
                    folder = LADDER / label
                    print(f"  -- {placement}, {'baked' if bake else 'unbaked'}", flush=True)
                    origins = origins_for(count, scattered)

                    try:
                        cell = a_cell(
                            folder, profile, origins, bake=bake, browser=browser, label=label
                        )
                        cell["rss_mb"] = resident_mb()
                    except Exception as wrong:
                        # A failed cell is a result too: recorded loudly, and the
                        # night's remaining cells still get measured.
                        import traceback

                        traceback.print_exc()
                        cell = {"failed": f"{type(wrong).__name__}: {wrong}"[:300]}
                    record.write(
                        json.dumps(
                            {"positions": count, "placement": placement, "baked": bake, **cell}
                        )
                        + "\n"
                    )
                    record.flush()
                    print("    " + json.dumps(cell), flush=True)
    finally:
        record.close()
        browser.close()
        started.stop()


if __name__ == "__main__":
    main()
