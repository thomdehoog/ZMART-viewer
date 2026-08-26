"""A finished dataset can be relived as a live run, one position at a time.

The load window's "other" tab offers Replay next to Open. Opening shows the
whole dataset at once; replaying hands the same positions to the very doorway
the microscope uses -- the live writer, its manifest, its announcements -- and
the picture assembles on screen tile by tile. Nothing about the live path is
faked, which is the point: a replay is a dress rehearsal for smart microscopy
that any operator can run on data they already have, with no microscope in
the room.

The dataset must be one whose tiles sit on a regular grid the live writer can
reproduce. Overlapping is fine -- real stage scans overlap -- as long as the
overlap is even; a transfer whose tiles sit at irregular offsets is refused
in plain words. A timelapse replays the way it was acquired: every position
of the first moment, then every position of the next, sweep after sweep, so
the time slider grows on screen exactly as it would during the experiment.
"""

import json
import sys
import threading
import urllib.request
import time
from pathlib import Path

import numpy as np
import pytest
import zarr

VIZ = Path(__file__).resolve().parents[1]
# The backend goes in FRONT of the building folder: both hold a server.py,
# and the one with make_server -- the one every other browser test uses --
# is the backend's.
sys.path.insert(0, str(VIZ / "app" / "picture"))
sys.path.insert(0, str(VIZ / "app" / "server"))
sys.path.insert(0, str(VIZ.parent))

from replay import plan_a_replay, replay_the_dataset  # noqa: E402
from server import make_server  # noqa: E402

# One replay tile: two planes deep, one camera frame square. 384 is the same
# frame the live fixtures elsewhere use -- small enough to write quickly, large
# enough that the planner can give it a real pyramid.
FRAME = 384
PLANES = 2
# How far the stage steps between tiles, in micrometres at one micrometre a
# voxel. 320 leaves 64 pixels of overlap, which is the overlap the live
# planner itself chooses for this frame -- a replay has to reproduce the
# dataset's own spacing, so the fixture uses a spacing that can be reproduced.
STEP_UM = 320.0


def _write_a_grid_tile(store: Path, number: int, at_um: tuple[float, float],
                       *, across: int | None = None) -> None:
    """One position of a raw grid scan, bright enough to tell apart.

    ``across`` makes the frame a rectangle rather than a square, which is the
    one shape the replay still refuses -- and so is what the gate about
    refusals reaching the operator now uses.
    """
    store.mkdir(parents=True)
    picture = np.full((PLANES, FRAME, across or FRAME), 1500 + number * 800,
                      "uint16")
    datasets = []
    for level in range(2):
        shrink = 2 ** level
        wide = (across or FRAME) // shrink
        array = zarr.create_array(
            store=str(store / str(level)),
            shape=(PLANES, FRAME // shrink, wide),
            chunks=(PLANES, FRAME // shrink, wide),
            dtype="uint16", zarr_format=3, dimension_names=["z", "y", "x"],
            overwrite=True,
        )
        array[:] = picture[:, ::shrink, ::shrink]
        datasets.append({
            "path": str(level),
            "coordinateTransformations": [
                {"type": "scale", "scale": [1.0, 1.0 * shrink, 1.0 * shrink]},
                {"type": "translation", "translation": [0.0, at_um[0], at_um[1]]},
            ],
        })
    (store / "zarr.json").write_text(json.dumps({
        "attributes": {"ome": {
            "version": "0.5",
            "multiscales": [{
                "name": store.name, "type": "nearest",
                "axes": [{"name": one, "type": "space", "unit": "micrometer"}
                         for one in ("z", "y", "x")],
                "datasets": datasets,
            }],
        }},
        "zarr_format": 3, "node_type": "group",
    }), encoding="utf-8")


def _a_grid_scan(folder: Path, *, across: int = 2) -> Path:
    """A raw dataset of ``across``-squared positions on a regular grid."""
    folder.mkdir(parents=True)
    number = 0
    for row in range(across):
        for column in range(across):
            _write_a_grid_tile(folder / f"pos{number:02d}.ome.zarr", number,
                               (row * STEP_UM, column * STEP_UM))
            number += 1
    return folder


class TestPlanningAReplay:
    """The plan maps the dataset onto the live writer's grid, or says why not."""

    def test_a_grid_scan_is_placed_where_its_own_grid_puts_it(self, tmp_path):
        plan = plan_a_replay(_a_grid_scan(tmp_path / "scan"))
        assert plan.total == 4
        step = int(STEP_UM)
        # On the grid it was imaged on -- not because a grid is imposed on it,
        # nothing is snapped any more, but because that is where its tiles are.
        assert {(where["y"], where["x"]) for where in plan.positions.values()} == {
            (0, 0), (0, step), (step, 0), (step, step)
        }
        # The plan reproduces the dataset's own spacing, or it is no replay.
        assert plan.geometry.step_shape == (int(STEP_UM), int(STEP_UM))

    def test_positions_off_any_grid_are_planned_where_they_sit(self, tmp_path):
        """A run whose positions sit at awkward places still plans.

        The whole worth of a replay is that nothing about the live path is
        faked: the positions go through the writer a real acquisition uses.
        A dataset that could only be rehearsed if it happened to sit on a
        regular grid would leave the awkward runs -- which is most data from
        anywhere else -- with no way to exercise the live path at all.

        These three sit at places no grid would put them: an extra 17 um
        along the row, and a third that is off in both directions at once by
        a fraction of a micrometre.
        """
        folder = tmp_path / "awkward"
        folder.mkdir()
        _write_a_grid_tile(folder / "pos00.ome.zarr", 0, (0.0, 0.0))
        _write_a_grid_tile(folder / "pos01.ome.zarr", 1, (0.0, STEP_UM + 17.0))
        _write_a_grid_tile(folder / "pos02.ome.zarr", 2,
                           (9.5, STEP_UM * 2 + 3.25))
        plan = plan_a_replay(folder)
        assert plan.total == 3, (
            "every position of an awkward run has to be planned, not just "
            "the ones that happen to land on a grid")

    def test_a_rectangular_frame_is_replayed_like_a_square_one(self, tmp_path):
        """A camera whose frame is not square still rehearses.

        Plenty are not -- a light sheet's frame is routinely longer one way
        than the other -- and the live geometry has always allowed it: a
        profile keeps a frame_shape of two numbers with its own overlap along
        each. Only this planner refused, because it asked for ONE number and
        would rather refuse than guess which.

        So the rectangle is what the gate uses: a frame half as wide as it is
        tall, stepped by its own width across and its own height down.
        """
        folder = tmp_path / "oblong"
        folder.mkdir()
        wide = FRAME // 2
        for number, at in enumerate(((0.0, 0.0), (0.0, wide * 1.0),
                                     (FRAME * 1.0, 0.0), (FRAME * 1.0, wide * 1.0))):
            _write_a_grid_tile(folder / f"pos{number:02d}.ome.zarr", number, at,
                               across=wide)
        plan = plan_a_replay(folder)
        assert plan.total == 4
        assert plan.geometry.frame_shape == (FRAME, wide), (
            f"the frame must be kept as it was recorded; got "
            f"{plan.geometry.frame_shape}")
        assert {(where["y"], where["x"]) for where in plan.positions.values()} == {
            (0, 0), (0, wide), (FRAME, 0), (FRAME, wide)
        }

    def test_uneven_spacing_is_replayed_where_it_sits(self, tmp_path):
        """Positions that are not evenly spaced are no longer refused.

        They used to be, and the refusal said so plainly: a replay could only
        place tiles on the writer's grid, so a run whose neighbours sat 17
        micrometres further apart than the rest had nowhere to go. A position
        now carries its own place, so the run is replayed where it was imaged.

        Kept rather than deleted with the refusal, as the record of the day
        that limit lifted -- and because "this is not refused" is worth
        holding in place as firmly as the refusal ever was.
        """
        folder = tmp_path / "uneven"
        folder.mkdir()
        _write_a_grid_tile(folder / "pos00.ome.zarr", 0, (0.0, 0.0))
        _write_a_grid_tile(folder / "pos01.ome.zarr", 1, (0.0, STEP_UM))
        _write_a_grid_tile(folder / "pos02.ome.zarr", 2, (0.0, STEP_UM * 2 + 17.0))
        plan = plan_a_replay(folder)
        assert plan.total == 3
        across = sorted(where["x"] for where in plan.positions.values())
        assert across == [0, int(STEP_UM), int(STEP_UM * 2 + 17.0)], (
            f"each position is planned where its own description puts it; got {across}")

    def test_the_replay_publishes_one_position_at_a_time(self, tmp_path):
        """The library-level heart: every beat lands through the live writer."""
        scan = _a_grid_scan(tmp_path / "scan")
        beats = []
        view = replay_the_dataset(
            scan, tmp_path / "run", every_s=0.0,
            told=lambda done, total: beats.append((done, total)),
        )
        # The first report is (0, total): it says how far there is to go
        # before the first position starts writing, so a progress display
        # has something to show -- and a Stop pressed during that first,
        # sometimes long, write is honoured (the operator's ask, 2026-08-23).
        assert beats == [(0, 4), (1, 4), (2, 4), (3, 4), (4, 4)]
        assert view.name.endswith(".ome.zarr") and view.exists(), (
            "the replay must leave a live view that any viewer can open"
        )
        # And the pixels, falsified rather than trusted: every published
        # position must hold exactly the source tile's own values. A replay
        # that mapped a position onto the wrong cell, or fed one tile's
        # pixels to another's name, would pass every count above and still
        # be showing the operator a lie.
        survey = tmp_path / "run" / "data" / "survey.ome.zarr"
        for position in ("pos00", "pos01", "pos02", "pos03"):
            published = np.asarray(
                zarr.open_group(str(survey / position), mode="r")["0"]
            ).squeeze()
            source = np.asarray(zarr.open_array(
                str(scan / f"{position}.ome.zarr" / "0"), mode="r")).squeeze()
            assert np.array_equal(published, source), (
                f"{position} was published with pixels that are not its own"
            )


MOMENTS = 2
CHANNELS = 2


def _write_a_timelapse_tile(store: Path, number: int,
                            at_um: tuple[float, float]) -> None:
    """One position of a timelapse scan: two moments, two channels.

    Every (moment, channel) frame carries its own value, so a replay that
    fed one moment's pixels to another's slot would be caught by a single
    comparison rather than slip by as "some bright square landed".
    """
    store.mkdir(parents=True)
    picture = np.empty((MOMENTS, CHANNELS, PLANES, FRAME, FRAME), "uint16")
    for moment in range(MOMENTS):
        for channel in range(CHANNELS):
            picture[moment, channel] = (1000 + number * 100
                                        + moment * 3000 + channel * 400)
    datasets = []
    for level in range(2):
        shrink = 2 ** level
        array = zarr.create_array(
            store=str(store / str(level)),
            shape=(MOMENTS, CHANNELS, PLANES, FRAME // shrink,
                   FRAME // shrink),
            chunks=(1, 1, PLANES, FRAME // shrink, FRAME // shrink),
            dtype="uint16", zarr_format=3,
            dimension_names=["t", "c", "z", "y", "x"], overwrite=True,
        )
        array[:] = picture[..., ::shrink, ::shrink]
        datasets.append({
            "path": str(level),
            "coordinateTransformations": [
                {"type": "scale",
                 "scale": [1.0, 1.0, 1.0, 1.0 * shrink, 1.0 * shrink]},
                {"type": "translation",
                 "translation": [0.0, 0.0, 0.0, at_um[0], at_um[1]]},
            ],
        })
    (store / "zarr.json").write_text(json.dumps({
        "attributes": {"ome": {
            "version": "0.5",
            "multiscales": [{
                "name": store.name, "type": "nearest",
                "axes": [{"name": "t", "type": "time", "unit": "second"},
                         {"name": "c", "type": "channel"}]
                + [{"name": one, "type": "space", "unit": "micrometer"}
                   for one in ("z", "y", "x")],
                "datasets": datasets,
            }],
        }},
        "zarr_format": 3, "node_type": "group",
    }), encoding="utf-8")


def _a_timelapse_scan(folder: Path, *, across: int = 2) -> Path:
    """A raw timelapse of ``across``-squared positions on a regular grid."""
    folder.mkdir(parents=True)
    number = 0
    for row in range(across):
        for column in range(across):
            _write_a_timelapse_tile(folder / f"pos{number:02d}.ome.zarr",
                                    number, (row * STEP_UM, column * STEP_UM))
            number += 1
    return folder


class TestReplayingATimelapse:
    """A timelapse replays sweep by sweep, every frame with its own pixels."""

    def test_the_plan_sweeps_the_grid_once_per_moment(self, tmp_path):
        plan = plan_a_replay(_a_timelapse_scan(tmp_path / "scan"))
        assert plan.total == 4 * MOMENTS
        assert plan.profile.timepoints == MOMENTS
        moments = [beat[-1] for beat in plan.beats]
        assert moments == [0] * 4 + [1] * 4, (
            "a timelapse replays the way it was acquired: every position of "
            "one moment, then every position of the next"
        )
        # Within each sweep the positions land in scan order, and both
        # sweeps walk the same path -- a stage revisits its grid the same
        # way each time.
        names = [beat[0] for beat in plan.beats]
        assert names[:4] == names[4:]

    def test_every_moment_lands_with_its_own_pixels(self, tmp_path):
        scan = _a_timelapse_scan(tmp_path / "scan")
        beats = []
        view = replay_the_dataset(
            scan, tmp_path / "run", every_s=0.0,
            told=lambda done, total: beats.append((done, total)),
        )
        assert beats[-1] == (4 * MOMENTS, 4 * MOMENTS)
        assert view.exists()
        survey = tmp_path / "run" / "data" / "survey.ome.zarr"
        for position in ("pos00", "pos01", "pos02", "pos03"):
            published = np.asarray(
                zarr.open_group(str(survey / position), mode="r")["0"])
            source = np.asarray(zarr.open_array(
                str(scan / f"{position}.ome.zarr" / "0"), mode="r"))
            assert published.shape == source.shape, (
                f"{position} was published into a different (t, c) room "
                f"than the source keeps: {published.shape} against "
                f"{source.shape}"
            )
            assert np.array_equal(published, source), (
                f"{position} was published with pixels that are not its own "
                "-- a moment or channel landed in the wrong slot"
            )


    def test_a_watcher_sees_the_slider_grow_and_follow(
            self, browser, built_dist, tmp_path):
        """On a held page, each sweep offers its moment and the view follows.

        This is the whole point of replaying a timelapse: the time slider
        must offer moment 0 alone while the first sweep lands, grow when
        the second sweep begins, and the view -- left standing on the
        front -- must move to the new moment on its own, exactly as it
        would during the experiment.
        """
        first = tmp_path / "overview"
        first.mkdir()
        from test_open_and_close import _store
        _store(first / "overview_pos001.ome.zarr", channels=1)
        _a_timelapse_scan(tmp_path / "sweeps")
        server = make_server(port=0, data_dir=first, site_dir=built_dist,
                             store="overview_pos001.ome.zarr")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        page = browser.new_page(viewport={"width": 1300, "height": 1000})
        try:
            page.goto(f"http://127.0.0.1:{server.server_address[1]}",
                      wait_until="domcontentloaded")
            page.wait_for_function("() => window.zmartConfig !== undefined",
                                   timeout=30_000)
            # The replay runs beside the viewer, not inside it: a thread
            # here stands in for the script an operator would run, writing
            # the sweeps and saying so after each one. The page is opened on
            # the run WHILE it is being written, which is the case this gate
            # exists for.
            run = tmp_path / "sweeps-run"
            replaying = threading.Thread(
                target=replay_the_dataset,
                args=(tmp_path / "sweeps", run),
                kwargs={"every_s": 0.4,
                        "announce": _telling(
                            f"http://127.0.0.1:{server.server_address[1]}")},
                daemon=True)
            replaying.start()
            for _ in range(300):
                if (run / "views" / "live").is_dir():
                    break
                time.sleep(0.1)
            _open_the_run(page, run)
            page.wait_for_function(
                "() => window.zmartConfig.groups.includes('sweeps-run')",
                timeout=30_000)
            slider = "document.querySelector('input[aria-label=\"t position\"]')"
            # While the first sweep lands, only moment 0 is on offer: the
            # committed ranges stop at 1, and there is nothing to slide yet
            # (a slider already reaching moment 1 would mean the replay is
            # being served as a finished folder, whole room and all).
            page.wait_for_function(f"""() => {{
                const rows = (window.zmartConfig?.layers || []).filter(
                    one => one.group === 'sweeps-run');
                if (!rows.length) return false;
                const ranges = rows[0].committedTimeRanges || [];
                const held = {slider};
                return ranges.length === 1 && ranges[0].start === 0
                    && ranges[0].stop === 1
                    && (!held || held.max === '0');
            }}""", timeout=30_000)
            # The second sweep begins: the slider grows...
            page.wait_for_function(f"() => {slider}?.max === '1'",
                                   timeout=60_000)
            # ...and the watcher, standing on the front, is carried onto
            # the moment that just landed.
            page.wait_for_function("""() => {
                const p = window.zmartViewer.navigationState.position;
                const names = p.coordinateSpace.value.names;
                const i = names.indexOf('t');
                if (i < 0) return false;
                const lower = p.coordinateSpace.value.bounds.lowerBounds[i];
                return Math.abs(p.value[i] - Math.ceil(lower) - 1) <= 0.5;
            }""", timeout=60_000)
            replaying.join(timeout=120)
            assert not replaying.is_alive(), "the replay never finished"
            # Every sweep of every position landed. Counted from the run's
            # own record rather than from a progress report -- there is no
            # longer a door to ask, and the manifest is the better witness:
            # it is what the viewer itself believes.
            history = (run / "views" / "live" / "metadata" / "events.jsonl")
            published = [line for line in
                         history.read_text(encoding="utf-8").splitlines()
                         if line.strip()]
            assert len(published) == 4 * MOMENTS, len(published)
            page.screenshot(path=str(tmp_path / "a_timelapse_replay.png"))
        finally:
            page.close()
            server.shutdown()
            thread.join(timeout=5)


def _post(address: str, route: str, payload: dict) -> tuple[int, dict]:
    """One JSON request straight to the server, no browser in between."""
    import urllib.error
    import urllib.request

    asked = urllib.request.Request(
        f"{address}{route}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(asked, timeout=180) as answer:
            return answer.status, json.loads(answer.read() or b"{}")
    except urllib.error.HTTPError as refusal:
        return refusal.code, json.loads(refusal.read() or b"{}")


@pytest.fixture
def serving(built_dist, tmp_path):
    """A running server on one ordinary acquisition, and the folder beside it."""
    first = tmp_path / "overview"
    first.mkdir()
    from test_open_and_close import _store
    _store(first / "overview_pos001.ome.zarr", channels=1)
    server = make_server(port=0, data_dir=first, site_dir=built_dist,
                         store="overview_pos001.ome.zarr")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", tmp_path
    finally:
        server.shutdown()
        thread.join(timeout=5)


class TestTheReplayRoutes:
    """The server's own rules for the door, asked without a browser."""

    def test_a_finished_replay_opens_again_later(self, serving):
        """The run a replay wrote is a real run, and the plain door opens it.

        "Later" is the operator's own case: watch an experiment while it
        runs, then open it again another day. The run's root holds ``data``
        beside ``views`` and no image directly inside, so the plain door
        once answered "no OME-Zarr image was found" (workstation,
        2026-08-19). It is opened with its served view named, so the live
        registry binds it and the time slider offers exactly the moments
        that landed -- and a run still being written opens exactly the same
        way, which is the whole point of there being one case and not two.
        """
        address, folder = serving
        scan = _a_grid_scan(folder / "yesterdayscan")
        # Written by the replay tool, which is where reliving a dataset lives
        # since 2026-08-26. The viewer never wrote this and does not need to
        # have: what it is handed is a folder, the same as any other.
        view = replay_the_dataset(scan, folder / "yesterdayrun", every_s=0.0)
        kept = Path(view).parents[2]
        assert kept.is_dir(), "the replay must leave a run behind it"
        status, answer = _post(address, "/api/stores/open",
                               {"path": str(kept)})
        assert status == 200, answer
        served_rows = [one for one in answer.get("layers", [])
                       if one.get("kind") == "image"
                       and one.get("group") == kept.name]
        assert served_rows, (
            "the reopened run must serve its live view as its own group"
        )

def test_a_04_dataset_replays_the_same(tmp_path):
    """The older metadata generation replays too, pixels pinned.

    Everything the viewer WRITES is 0.5; this pins the reading side --
    positions somebody converted years ago should relive exactly like
    fresh ones. The fixture mirrors the grid scan above in 0.4 form:
    flat .zattrs beside zarr v2 arrays.
    """
    scan = tmp_path / "older"
    scan.mkdir()
    for number in range(4):
        row, column = divmod(number, 2)
        store = scan / f"pos{number:02d}.ome.zarr"
        store.mkdir()
        group = zarr.open_group(str(store), mode="w", zarr_format=2)
        data = np.full((PLANES, FRAME, FRAME), 1500 + number * 800, "uint16")
        group.create_array("0", shape=data.shape, chunks=data.shape,
                           dtype="uint16")[:] = data
        (store / ".zattrs").write_text(json.dumps({"multiscales": [{
            "version": "0.4",
            "axes": [{"name": one, "type": "space", "unit": "micrometer"}
                     for one in ("z", "y", "x")],
            "datasets": [{"path": "0", "coordinateTransformations": [
                {"type": "scale", "scale": [1.0, 1.0, 1.0]},
                {"type": "translation",
                 "translation": [0.0, row * STEP_UM, column * STEP_UM]},
            ]}],
        }]}), encoding="utf-8")

    view = replay_the_dataset(scan, tmp_path / "run", every_s=0.0)
    assert view.exists()
    survey = tmp_path / "run" / "data" / "survey.ome.zarr"
    for number in range(4):
        published = np.asarray(zarr.open_group(
            str(survey / f"pos{number:02d}"), mode="r")["0"]).squeeze()
        assert published.max() == 1500 + number * 800, (
            f"pos{number:02d} relived with pixels that are not its own"
        )


def _relive(page, folder: Path, name: str) -> None:
    """Relive the dataset, then open the run it left, the way an operator does.

    The viewer stopped being able to write on 2026-08-26. A replay is a script
    run beside it now, not a tab inside it, so this is the two-step an operator
    actually performs: relive the dataset, then open the run -- which by then
    is an ordinary folder, opened through the ordinary door.
    """
    run = folder / f"{name}-run"
    # A run can only be lived once -- its record only moves forward -- so
    # opening the same one a second time reopens what is there rather than
    # writing it again. That IS the case the callers here are testing.
    if not run.exists():
        replay_the_dataset(folder / name, run, every_s=0.0)
    _open_the_run(page, run)
    page.wait_for_timeout(4000)
    # The way back to the whole picture, pressed the same way both times: what
    # is compared below is what was drawn, not where the camera happened to be.
    page.get_by_role("button", name="Overview", exact=True).click()


def _open_the_run(page, run: Path) -> None:
    """Open a run through the load window, the way an operator opens anything.

    A run still being written and a run that finished open by the same steps
    and through the same door, which is the point: the viewer is handed a
    folder and never asks which of the two it is.
    """
    page.get_by_label("open images").click()
    window = page.get_by_role("dialog", name="load data")
    window.wait_for(timeout=10_000)
    box = page.get_by_label("folder path")
    box.fill(str(run.parent))
    box.press("Enter")
    window.get_by_label(run.name, exact=True).wait_for(timeout=15_000)
    window.get_by_label(run.name, exact=True).click()
    page.get_by_label(f"open {run.name}").click()


def _telling(address: str):
    """A callback that tells the viewer at ``address`` that something landed.

    This is the microscope's half of the bargain, and a replay run beside the
    viewer keeps it the same way a microscope would: write the files, then say
    so. Best effort -- a viewer that has closed must not stop the run.
    """
    def tell() -> None:
        try:
            urllib.request.urlopen(urllib.request.Request(
                address.rstrip("/") + "/api/announce",
                data=json.dumps({"wrote_image_in_place": True}).encode(),
                headers={"Content-Type": "application/json"}), timeout=5).close()
        except OSError:
            pass
    return tell


def _what_is_shown(page) -> dict:
    """The picture as an operator sees it: how much is lit, and in what colour."""
    from pixels import fraction_lit
    return {
        "lit": round(fraction_lit(page), 3),
        "colours": page.evaluate(
            "() => (window.zmartConfig?.layers || []).map((one) => one.color)"),
        "sources": page.evaluate(
            "() => (window.zmartConfig?.layers || [])"
            ".map((one) => (one.sources || []).length)"),
    }


def test_closing_one_acquisition_does_not_spoil_the_next(
        browser, built_dist, tmp_path):
    """Close one dataset, open another, and the second one is drawn in full.

    Clearing the screen and loading the next thing is the ordinary rhythm of a
    session at the microscope, and it has to leave the viewer as good as it
    found it. It did not: after a close, whatever was opened next came up with
    most of its tiles missing, and only reloading the page put it right -- so
    what was wrong was what the page had kept, not the data. The server sends
    the same description and the browser fetches the same pieces either way
    (measured 2026-08-21).

    Two different datasets rather than the same one twice, because that is the
    case an operator meets: the second is a different size, so drawing it as
    though it were the first is visible.
    """
    first = tmp_path / "overview"
    first.mkdir()
    from test_open_and_close import _store
    _store(first / "overview_pos001.ome.zarr", channels=1)
    _a_grid_scan(tmp_path / "one", across=2)
    _a_grid_scan(tmp_path / "two", across=3)
    server = make_server(port=0, data_dir=first, site_dir=built_dist,
                         store="overview_pos001.ome.zarr")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = browser.new_page(viewport={"width": 1300, "height": 1000})
    try:
        page.goto(f"http://127.0.0.1:{server.server_address[1]}",
                  wait_until="domcontentloaded")
        page.wait_for_function("() => window.zmartConfig !== undefined", timeout=30_000)
        page.get_by_title("Hide this acquisition").first.click()
        page.wait_for_timeout(500)

        _relive(page, tmp_path, "one")
        page.wait_for_function(
            "() => (window.zmartConfig.groups || []).some((g) => g.includes('one'))",
            timeout=30_000)
        page.wait_for_timeout(6000)
        assert _what_is_shown(page)["lit"] > 0.05, "the first dataset never drew"

        group = page.evaluate(
            "() => (window.zmartConfig.groups || []).find((g) => g.includes('one'))")
        # Marked so that the engine after the close can be told from the one
        # before it: closing builds a new one, which is the fix for this.
        page.evaluate("() => { window.zmartViewer.zmartMark = 'before'; }")
        page.locator(f'[aria-label="close {group}"]').click()
        page.wait_for_timeout(2500)
        assert page.evaluate("() => window.zmartViewer?.zmartMark") != "before", (
            "closing an acquisition must build the engine again: the one that "
            "drew the closed dataset is still here"
        )

        _relive(page, tmp_path, "two")
        page.wait_for_function(
            "() => (window.zmartConfig.groups || []).some((g) => g.includes('two'))",
            timeout=30_000)
        page.wait_for_timeout(6000)
        after = _what_is_shown(page)
        drawn = page.evaluate(
            "() => (window.zmartScene || []).filter("
            "  (s) => s.type === 'image' && (s.name || '').includes('two'))"
            ".map((s) => (s.source || []).length)")
        # One source, not nine. A replay goes through the live writer, which
        # publishes into ONE growing picture -- so the row is fed by that one
        # picture however many positions have landed in it. This used to read
        # nine, back when a replay handed the engine a source per position;
        # what the gate is really about is the line below, that the second
        # dataset draws at all after the first was closed.
        assert drawn and all(count == 1 for count in drawn), (
            f"the second dataset should arrive as one live picture: {drawn}")
        assert after["lit"] > 0.05, (
            "after closing the first dataset, the second one drew almost "
            f"nothing: {after}"
        )
    finally:
        page.close()
        server.shutdown()
        thread.join(timeout=5)


def test_opening_it_again_after_closing_it_shows_the_same_picture(
        browser, built_dist, tmp_path):
    """Close an acquisition, open it again, and it comes back as it was.

    Closing is how an operator clears the screen mid-session, and opening the
    same folder again is what they do next. The second showing has to be the
    first one: the same tiles lit, in the same colour.

    It was not. The second open drew half the tiles, in a different colour,
    and only reloading the page put it right -- so whatever was wrong lived in
    what the page had kept, not in the data (2026-08-21). A gate here because
    nothing else exercises the same folder twice in one session: every other
    test opens once and closes at the end.
    """
    first = tmp_path / "overview"
    first.mkdir()
    from test_open_and_close import _store
    _store(first / "overview_pos001.ome.zarr", channels=1)
    _a_grid_scan(tmp_path / "twice", across=2)
    server = make_server(port=0, data_dir=first, site_dir=built_dist,
                         store="overview_pos001.ome.zarr")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = browser.new_page(viewport={"width": 1300, "height": 1000})
    try:
        page.goto(f"http://127.0.0.1:{server.server_address[1]}",
                  wait_until="domcontentloaded")
        page.wait_for_function("() => window.zmartConfig !== undefined", timeout=30_000)
        # Nothing of the starting acquisition in the measurement: what is
        # counted below has to be the reopened dataset's own pixels.
        page.get_by_title("Hide this acquisition").first.click()
        page.wait_for_timeout(500)

        _relive(page, tmp_path, "twice")
        page.wait_for_function(
            "() => (window.zmartConfig.groups || []).some((g) => g.includes('twice'))",
            timeout=30_000)
        page.wait_for_timeout(6000)
        once = _what_is_shown(page)
        assert once["lit"] > 0.05, f"nothing was drawn the first time: {once}"

        group = page.evaluate(
            "() => (window.zmartConfig.groups || []).find((g) => g.includes('twice'))")
        page.locator(f'[aria-label="close {group}"]').click()
        page.wait_for_timeout(2500)
        assert _what_is_shown(page)["lit"] < 0.02, "closing it left it on screen"

        _relive(page, tmp_path, "twice")
        page.wait_for_function(
            "() => (window.zmartConfig.groups || []).some((g) => g.includes('twice'))",
            timeout=30_000)
        page.wait_for_timeout(6000)
        again = _what_is_shown(page)

        assert again["colours"] == once["colours"], (
            "the same data came back in a different colour: "
            f"{once['colours']} first, {again['colours']} second"
        )
        assert again["sources"] == once["sources"], (
            f"a different number of tiles came back: {once} then {again}")
        assert abs(again["lit"] - once["lit"]) < 0.05, (
            "the second showing is not the first: "
            f"{once['lit']:.1%} of the view lit, then {again['lit']:.1%}"
        )
    finally:
        page.close()
        server.shutdown()
        thread.join(timeout=5)


def _a_replay_photographed(browser, built_dist, tmp_path, *, bake):
    """Relive one scan, open it baked or not, and photograph what it draws."""
    import numpy as np
    from PIL import Image

    first = tmp_path / "overview"
    first.mkdir(parents=True)
    from test_open_and_close import _store
    _store(first / "overview_pos001.ome.zarr", channels=1)
    _a_grid_scan(tmp_path / "scan")

    server = make_server(port=0, data_dir=first, site_dir=built_dist,
                         store="overview_pos001.ome.zarr")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = browser.new_page(viewport={"width": 1300, "height": 1000})
    try:
        page.goto(f"http://127.0.0.1:{server.server_address[1]}",
                  wait_until="domcontentloaded")
        page.wait_for_function("() => window.zmartConfig !== undefined", timeout=30_000)
        # The run is written beside the viewer, then opened. The bake is an
        # OPEN-time ask, not a writing one -- it decides whether the picture's
        # pieces are written now or composed when somebody looks -- so it goes
        # on the open, exactly where the server reads it.
        run = tmp_path / "scan-run"
        replay_the_dataset(tmp_path / "scan", run, every_s=0.0)
        # The bake is remembered beside the run, so it is asked for once here
        # and honoured on every later binding. Done before the window opens
        # the run, because the window has no bake control for a run that
        # already carries its own picture.
        page.evaluate("""async ([where, bake]) => {
          const r = await fetch('/api/stores/open', {method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({path: where, bake})});
          await r.json();
          await fetch('/api/stores/close', {method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({path: where})});
        }""", [str(run), bake])
        # And now opened the way an operator opens it -- through the window,
        # which is also what puts the camera where the picture is. Opened by
        # a bare request instead, the view stayed at the depth the previous
        # acquisition left it at and drew nothing (2026-08-26).
        _open_the_run(page, run)
        page.wait_for_function(
            "() => (window.zmartConfig.groups || []).some((g) => g.includes('scan'))",
            timeout=30_000)
        # Only the run is wanted in the photograph, and the hide has to come
        # after the reload -- a reloaded page starts showing everything again.
        # By name, not by position: after a reload the run may be listed
        # first, and hiding "the first one" then hid the very thing being
        # photographed (2026-08-26).
        page.get_by_label("toggle group overview").click()
        page.wait_for_timeout(500)
        page.wait_for_timeout(7000)
        page.get_by_role("button", name="Overview", exact=True).click()
        page.wait_for_timeout(3000)
        canvas = page.locator("canvas").first.bounding_box()
        shot = page.screenshot(clip={
            "x": canvas["x"] + canvas["width"] * 0.15,
            "y": canvas["y"] + canvas["height"] * 0.15,
            "width": canvas["width"] * 0.7, "height": canvas["height"] * 0.7})
        # Kept beside the run: when these two disagree, the difference is
        # something to look at, not a number to argue with.
        (tmp_path / f"photographed-{'baked' if bake else 'plain'}.png"
         ).write_bytes(shot)
        import io
        return np.asarray(
            Image.open(io.BytesIO(shot)).convert("L"), dtype=float)
    finally:
        page.close()
        server.shutdown()
        thread.join(timeout=5)


def test_a_live_run_draws_the_same_picture_baked_or_not(browser, built_dist,
                                                        tmp_path):
    """The live view is unbaked unless asked, and both roads show one picture.

    Baking on the fly is what lets a real acquisition scale: the pieces are
    written as the run goes, so a cold open reads files and each commit
    patches only its own footprint. It also costs real time up front, and an
    operator watching a rehearsal does not want to pay it -- so the viewer
    serves a live run unbaked and the bake is asked for, the same way it is
    asked for on the door that builds a view over raw positions.

    What must not differ is the picture. A bake is a promise about speed and
    about nothing else, so the two are photographed and compared rather than
    reasoned about: the ways a bake can quietly go wrong -- a level built
    from the wrong copy, a piece written with fill where a tile reaches --
    all look perfectly plausible alone and only show up side by side.
    """
    import numpy as np

    plain = _a_replay_photographed(browser, built_dist, tmp_path / "plain",
                                   bake=False)
    baked = _a_replay_photographed(browser, built_dist, tmp_path / "baked",
                                   bake=True)
    assert plain.shape == baked.shape, (
        f"the two pictures are different sizes: {plain.shape} against {baked.shape}")
    assert float(plain.max()) > 40, "the unbaked replay drew nothing at all"
    assert float(baked.max()) > 40, "the baked replay drew nothing at all"
    apart = float(np.abs(plain - baked).mean())
    assert apart < 2.0, (
        f"baked and unbaked differ by {apart:.2f} levels per pixel on average "
        "-- the bake must change how fast the picture arrives and nothing "
        "about what it shows")
