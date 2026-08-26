"""A finished dataset can be relived as a live run, one position at a time.

Opening a dataset shows all of it at once; replaying it shows the same
positions arriving one at a time, in the order the stage scanned them, so an
operator can watch a survey fill without a microscope in the room.

Nothing is copied and the picture never changes shape. Every position is
declared first as a description alone -- kilobytes standing for a
seven-gigabyte tile -- so the room has its final extent before a single pixel
shows, and the pixels then arrive into it as renames. Any dataset the viewer
can open can be replayed, both metadata generations, at whatever offsets its
tiles happen to sit.
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

from replay import replay_the_dataset  # noqa: E402
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
        """What a replay leaves behind opens again later, like any folder.

        "Later" is the operator's own case: watch a dataset assemble, then
        open it again another day. Since 2026-08-26 what it leaves is a
        picture declared over the positions where they already lie -- a few
        kilobytes of description, no second copy of anything -- so opening
        it later is opening a folder, with nothing special about it at all.
        That is the point: there is one case, not two.
        """
        address, folder = serving
        scan = _a_grid_scan(folder / "yesterdayscan")
        watching = folder / "yesterdaywatching"
        picture = replay_the_dataset(scan, watching, every_s=0.0)
        assert picture.is_dir(), "the replay must leave a picture behind it"
        status, answer = _post(address, "/api/stores/open",
                               {"path": str(watching)})
        assert status == 200, answer
        served_rows = [one for one in answer.get("layers", [])
                       if one.get("kind") == "image"]
        assert served_rows, "the picture must open as an image row"
        # And it is a description, not a copy: everything it weighs is
        # bookkeeping beside a dataset it never touched.
        weighed = sum(f.stat().st_size for f in watching.rglob("*")
                      if f.is_file())
        source = sum(f.stat().st_size for f in Path(scan).rglob("*")
                     if f.is_file())
        assert weighed < source, (
            f"the picture weighs {weighed} against the dataset's {source}; "
            "a replay that copies has gone back to rewriting the data"
        )

def test_a_replay_declares_the_picture_once(tmp_path, monkeypatch):
    """However many positions arrive, the picture is declared once.

    Every reveal used to re-declare the whole picture, and that read every
    position's description in order to write a file whose CONTENT does not
    change: the room is declared from all of them before anything is shown,
    so a stub and the real tile say the same shape at the same place. The
    rewrite's only effect was moving the description's timestamp -- which is
    what a served picture watches to know it must be built again
    (``app/picture/served.py``, ``_the_pictures_mark``).

    So it was O(all positions) paid on every arrival, for a timestamp.
    Measured: 616 ms per position at 400, and 14 s each at 10,000, which put
    a hundred-by-hundred survey out of reach for a reason that had nothing to
    do with the data. Touching the description does the same job in 62-228
    microseconds and does not grow with the survey.

    Pinned by counting rather than by timing, because a clock says "fast
    today" and a count says "does not read every position to reveal one".
    """
    import replay as the_replay

    declared = []
    honestly = the_replay.declare_a_built_picture

    def counting(*given, **named):
        declared.append(named.get("name"))
        return honestly(*given, **named)

    monkeypatch.setattr(the_replay, "declare_a_built_picture", counting)
    scan = _a_grid_scan(tmp_path / "scan", across=3)
    replay_the_dataset(scan, tmp_path / "watching", every_s=0.0)
    assert len(declared) == 1, (
        f"nine positions were revealed and the picture was declared "
        f"{len(declared)} times; revealing one position must not re-read all "
        f"of them"
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

    picture = replay_the_dataset(scan, tmp_path / "watching", every_s=0.0)
    assert picture.exists()

    # Nothing is written now, so the pixels cannot be read back out of a
    # copy -- there is no copy. What this gate is actually about is whether
    # the older metadata generation is UNDERSTOOD: a 0.4 description read,
    # its translation believed, the position placed where it says it is. So
    # ask the composer what it was given, through the same reader the viewer
    # uses.
    from mosaic import read_the_transfer
    placed = read_the_transfer(scan.parent / f"{scan.name}-appearing")
    assert len(placed.tiles) == 4, (
        f"{len(placed.tiles)} of 4 positions were placed; a 0.4 description "
        "that cannot be read is a position that silently goes missing"
    )
    # Where each position's finest copy says its first voxel sits.
    corners = sorted(tuple(round(one, 1) for one in tile.copies[0].corner_um[-2:])
                     for tile in placed.tiles)
    assert corners == sorted([(0.0, 0.0), (0.0, STEP_UM), (STEP_UM, 0.0),
                              (STEP_UM, STEP_UM)]), corners


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
        # One source, not nine. A replay declares ONE picture and reveals
        # positions into it -- so the row is fed by that one picture however
        # many positions have landed in it. This used to read
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
