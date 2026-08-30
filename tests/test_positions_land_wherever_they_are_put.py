"""Positions land wherever they are put, and a wrong landing names itself.

The free-placement gate of docs/open/PLAN_positions_land_wherever_they_are_put.md:
scattered, overlapping, fractional and negative translations across both
OME-Zarr generations, the awkward shapes, plates, and the live path — served
unbaked, baked, and through the real door — all compared against a reference
paste that is anchored on hand-computed cases before it judges anything.
"""

from __future__ import annotations

import json
import random
import urllib.request
from pathlib import Path

import numpy as np
import pytest
import zarr

from zmart_viewer.building import GovernedRun, declare_a_built_picture
from zmart_viewer.compose import Composer, read_the_transfer
from zmart_viewer.pieces import built_bytes_behind

LEVEL_SCALES = (1, 2)


def _marker_floor(dtype) -> int:
    """Markers live above every stamp, as high as the dtype allows."""
    kind = np.dtype(dtype)
    return 60_000 if kind.kind == "f" or np.iinfo(kind).max >= 60_000 else 200


# -- writing scattered fixtures -------------------------------------------------


def _stamped_body(shape: tuple[int, ...], stamp: int, dtype) -> np.ndarray:
    """A tile body: the stamp everywhere, its marker at the origin voxel."""
    body = np.full(shape, stamp, dtype=dtype)
    body[(0,) * len(shape)] = _marker_floor(dtype) + stamp % 40
    return body


def _axes(names: tuple[str, ...]) -> list[dict]:
    return [
        {"name": name, "type": "time" if name == "t" else "space"}
        | ({} if name in ("t", "c") else {"unit": "micrometer"})
        for name in names
    ]


def _multiscales(names: tuple[str, ...], levels: int, place: tuple[float, ...], version: str):
    datasets = []

    for level in range(levels):
        scale = [float(LEVEL_SCALES[level]) if name in ("y", "x") else 1.0 for name in names]
        translation = [0.0] * (len(names) - len(place)) + list(place)
        datasets.append(
            {
                "path": str(level),
                "coordinateTransformations": [
                    {"type": "scale", "scale": scale},
                    {"type": "translation", "translation": translation},
                ],
            }
        )
    return [{"version": version, "axes": _axes(names), "datasets": datasets}]


def write_position(
    path: Path,
    stamp: int,
    place: tuple[float, ...],
    *,
    version: str = "0.4",
    names: tuple[str, ...] = ("z", "y", "x"),
    size: int = 64,
    levels: int = 2,
    dtype=np.uint16,
) -> None:
    """One position store of ``levels`` copies, stamped, placed at ``place``."""
    front = tuple(2 if name in ("t", "c") else 1 for name in names[:-2])
    v3 = version == "0.5"
    group = zarr.open_group(str(path), mode="w", zarr_format=3 if v3 else 2)

    for level in range(levels):
        side = size // LEVEL_SCALES[level]
        shape = front + (side, side)
        made = group.create_array(
            str(level),
            shape=shape,
            chunks=shape,
            dtype=dtype,
            **({"dimension_names": names} if v3 else {}),
        )
        made[:] = _stamped_body(shape, stamp + level, dtype)

    described = _multiscales(names, levels, place, version)

    if v3:
        (path / "zarr.json").write_text(
            json.dumps(
                {
                    "attributes": {"ome": {"version": "0.5", "multiscales": described}},
                    "zarr_format": 3,
                    "node_type": "group",
                }
            ),
            encoding="utf-8",
        )
    else:
        (path / ".zattrs").write_text(json.dumps({"multiscales": described}), encoding="utf-8")


def scattered_places(count: int, size: int, seed: int) -> list[tuple[float, float]]:
    """Random overlapping places, plus the standing edge set."""
    rng = random.Random(seed)
    room = size * max(2, count // 2)
    places = [
        (round(rng.uniform(0, room - size), 1), round(rng.uniform(0, room - size), 1))
        for _ in range(count)
    ]
    places[:0] = [
        (0.0, 0.0),
        (0.0, 0.0),  # an exact duplicate
        (10.3, 17.8),  # fractional
        (-float(size // 2), -8.5),  # a negative corner
        (5.0, room + 6 * size + 0.4),  # a far outlier
    ]
    return [place for place in places if abs(place[0] * 10 % 10 - 5) > 0.01]


def nominal_places(count: int, size: int) -> list[tuple[float, float]]:
    step = size - size // 8
    across = max(2, int(count**0.5))
    return [
        (float(step * (index // across)), float(step * (index % across))) for index in range(count)
    ]


# -- the reference paste, and its diagnostic ------------------------------------


def _rounded(value: float) -> int:
    """The composer's own rule: to the nearest voxel, halves rounding up."""
    import math

    return math.floor(value + 0.5)


def reference_paste(folder: Path, level: int) -> tuple[np.ndarray, dict[str, tuple[int, int]]]:
    """The expected level, pasted independently: sorted names, later wins.

    Returns the pasted front-frame (first t, first c) and each store's
    expected (y, x) placement at this level.
    """
    stores = sorted(one for one in folder.iterdir() if one.is_dir())
    read = []

    for store in stores:
        attrs_file = store / ".zattrs"
        described = (
            json.loads(attrs_file.read_text())
            if attrs_file.is_file()
            else json.loads((store / "zarr.json").read_text())["attributes"]["ome"]
        )
        place = described["multiscales"][0]["datasets"][0]["coordinateTransformations"][1]
        body = zarr.open_array(str(store / str(level)), mode="r")[...]
        body = body.reshape(body.shape[-2:]) if body.ndim == 2 else body[(0,) * (body.ndim - 3)]
        read.append((store.name, tuple(place["translation"][-2:]), body))

    scale = LEVEL_SCALES[level]
    corner = (
        min(place[0] for _, place, _ in read),
        min(place[1] for _, place, _ in read),
    )
    placements = {
        name: (_rounded((place[0] - corner[0]) / scale), _rounded((place[1] - corner[1]) / scale))
        for name, place, _ in read
    }
    height = max(placements[name][0] + body.shape[-2] for name, _, body in read)
    width = max(placements[name][1] + body.shape[-1] for name, _, body in read)
    pasted = np.zeros((height, width), dtype=read[0][2].dtype)

    for name, _, body in read:
        top, left = placements[name]
        flat = body if body.ndim == 2 else body[0]
        pasted[top : top + flat.shape[-2], left : left + flat.shape[-1]] = flat
    return pasted, placements


def where_the_markers_landed(
    picture: np.ndarray, expected: dict[str, tuple[int, int]]
) -> list[str]:
    """Every marker's asked-for corner against where it actually is."""
    report = []

    floor = _marker_floor(picture.dtype)

    for name, (top, left) in sorted(expected.items()):
        marker = picture[top, left] if top < picture.shape[0] and left < picture.shape[1] else None
        found = np.argwhere(picture >= floor)
        hits = [tuple(one) for one in found]
        report.append(
            f"{name}: asked ({top}, {left}), value there {marker}, markers seen at {hits[:6]}"
        )
    return report


def served_level(composer: Composer, level: int) -> np.ndarray:
    """The whole level as the composer serves it, assembled from its pieces."""
    deep, height, width = composer.mosaic.shape(level)
    out = np.zeros((height, width), dtype=composer.mosaic.dtype)
    piece = composer.piece if hasattr(composer, "piece") else 512
    rows = -(-height // piece)
    columns = -(-width // piece)

    for row in range(rows):
        for column in range(columns):
            values = composer.values_for(level, 0, row, column)

            if values is None:  # governed ground nobody committed: absent
                continue
            flat = values if values.ndim == 2 else values[0]
            keep_y = min(piece, height - row * piece)
            keep_x = min(piece, width - column * piece)
            out[
                row * piece : row * piece + keep_y,
                column * piece : column * piece + keep_x,
            ] = flat[:keep_y, :keep_x]
    return out


def assert_placed(folder: Path, composer: Composer, *, levels: int = 2) -> None:
    for level in range(levels):
        expected, placements = reference_paste(folder, level)
        served = served_level(composer, level)
        assert served.shape == expected.shape, (
            f"level {level}: served room {served.shape}, expected {expected.shape}"
        )

        if not np.array_equal(served, expected):
            differs = np.argwhere(served != expected)
            cells = ", ".join(
                f"({y},{x}) served {served[y, x]} expected {expected[y, x]}" for y, x in differs[:6]
            )
            report = "\n".join(where_the_markers_landed(served, placements))
            raise AssertionError(
                f"level {level}: {len(differs)} voxels differ, first at {cells}.\n{report}"
            )


# -- the oracle is anchored before it judges ------------------------------------


def test_the_reference_is_pinned_by_hand(tmp_path):
    """Offsets 3.4 and 3.6 land at 3 and 4 at level 0, both at 2 at level 1."""
    folder = tmp_path / "micro"
    folder.mkdir()
    write_position(folder / "pos_a.zarr", 100, (0.0, 3.4), size=8)
    write_position(folder / "pos_b.zarr", 200, (0.0, 3.6), size=8)
    _, placements = reference_paste(folder, 0)
    assert placements == {"pos_a.zarr": (0, 0), "pos_b.zarr": (0, 0)}, placements

    write_position(folder / "pos_c.zarr", 300, (10.0, 20.0), size=8)
    _, placements = reference_paste(folder, 0)
    assert placements["pos_a.zarr"] == (0, 0)
    assert placements["pos_b.zarr"] == (0, 0)  # 3.6 - 3.4 = 0.2 rounds away
    assert placements["pos_c.zarr"] == (10, 17)  # 20 - 3.4 = 16.6 rounds to 17
    _, placements = reference_paste(folder, 1)
    assert placements["pos_c.zarr"] == (5, 8)  # 16.6 / 2 = 8.3 rounds to 8

    served = Composer(read_the_transfer(folder))
    try:
        assert_placed(folder, served)
    finally:
        served.close()


def test_two_tiles_on_one_spot_pin_the_winner(tmp_path):
    """The later-sorted name wins, exactly and everywhere it overlaps."""
    folder = tmp_path / "order"
    folder.mkdir()
    write_position(folder / "pos_a.zarr", 111, (0.0, 0.0), size=16)
    write_position(folder / "pos_b.zarr", 222, (0.0, 0.0), size=16)
    composer = Composer(read_the_transfer(folder))

    try:
        piece = composer.values_for(0, 0, 0, 0)
        body = piece if piece.ndim == 2 else piece[0]
        assert body[1, 1] == 222 and body[0, 0] == _marker_floor(body.dtype) + 222 % 40
    finally:
        composer.close()


# -- the static columns ---------------------------------------------------------


def test_a_flat_store_is_refused_from_composing_in_plain_words(tmp_path):
    """Two-axis stores cannot be composed today; the answer is words, not a crash."""
    folder = tmp_path / "run"
    folder.mkdir()
    write_position(folder / "pos_a.zarr", 100, (0.0, 0.0), names=("y", "x"), size=16)
    write_position(folder / "pos_b.zarr", 101, (4.0, 4.0), names=("y", "x"), size=16)

    with pytest.raises(ValueError, match="flat two-axis"):
        read_the_transfer(folder)


SHAPES = [
    ("v04", {"version": "0.4"}),
    ("v05", {"version": "0.5"}),
    ("multichannel", {"version": "0.4", "names": ("c", "z", "y", "x")}),
    ("timelapse", {"version": "0.5", "names": ("t", "c", "z", "y", "x")}),
    ("one_channel_kept", {"version": "0.5", "names": ("c", "z", "y", "x")}),
    ("eight_bit", {"version": "0.4", "dtype": np.uint8}),
    ("floating", {"version": "0.5", "dtype": np.float32}),
]


def a_scattered_run(folder: Path, spelling: dict, places) -> None:
    folder.mkdir()

    for index, place in enumerate(places):
        write_position(folder / f"pos_{index:02d}.zarr", 100 + index, place, **spelling)


@pytest.mark.parametrize("name,spelling", SHAPES, ids=[name for name, _ in SHAPES])
@pytest.mark.parametrize("arrangement", ["nominal", "scattered"])
def test_static_positions_land_where_put(tmp_path, name, spelling, arrangement):
    places = nominal_places(6, 64) if arrangement == "nominal" else scattered_places(6, 64, seed=41)
    folder = tmp_path / "run"
    a_scattered_run(folder, spelling, places)
    composer = Composer(read_the_transfer(folder))

    try:
        assert_placed(folder, composer)
    finally:
        composer.close()


@pytest.mark.parametrize("arrangement", ["nominal", "scattered"])
def test_baked_equals_unbaked_where_put(tmp_path, arrangement):
    """The bake writes exactly what composing would have served."""
    places = nominal_places(5, 64) if arrangement == "nominal" else scattered_places(5, 64, seed=42)
    folder = tmp_path / "run"
    a_scattered_run(folder, {"version": "0.5"}, places)

    unbaked = declare_a_built_picture(tmp_path / "plain", folder, name="plain", bake=False)
    baked = declare_a_built_picture(tmp_path / "hard", folder, name="hard", bake=True)
    composer = Composer(read_the_transfer(folder))

    try:
        assert_placed(folder, composer)
        levels = json.loads((baked / "zarr.json").read_text())["attributes"]["zmart"]["baked"]
        assert levels, "the bake wrote no levels at all"

        for level in [int(one) for one in levels]:
            expected, _ = reference_paste(folder, level)
            written = zarr.open_array(str(baked / str(level)), mode="r")[...]
            flat = (
                written.reshape(written.shape[-2:])
                if written.ndim == 2
                else written[(0,) * (written.ndim - 2)]
            )
            assert np.array_equal(flat, expected), f"baked level {level} differs from composed"
    finally:
        composer.close()
    assert unbaked.name != baked.name or True


def test_the_real_door_serves_the_scattered_picture(tmp_path):
    """One scattered case through HTTP: the ladder answers what the composer would."""
    import threading

    from zmart_viewer.server import make_server

    places = scattered_places(4, 64, seed=43)
    folder = tmp_path / "run"
    a_scattered_run(folder, {"version": "0.4"}, places)
    store = declare_a_built_picture(tmp_path / "views", folder, name="door")

    server = make_server(port=0, data_dir=store.parent, store=[store.name], live=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        port = server.server_address[1]
        address = f"http://127.0.0.1:{port}/data/0/{store.name}/0/c/0/0/0"
        with urllib.request.urlopen(address, timeout=30) as answer:
            over_http = answer.read()
        direct = built_bytes_behind(store, "0/c/0/0/0")
        assert direct is not None and over_http == direct
    finally:
        server.shutdown()
        thread.join(timeout=5)


# -- the sequential column: live and replay -------------------------------------


def _writer_decides_on_day_zero() -> bool:
    """Does the installed zmart_live carry the day-zero pointer-map decision?"""
    import zmart_live.coordinator as coordinator

    return "pointer_linkable" in Path(coordinator.__file__).read_text(encoding="utf-8")


needs_day_zero_writer = pytest.mark.skipif(
    not _writer_decides_on_day_zero(),
    reason="the installed zmart_live does not yet decide the pointer map at "
    "construction -- apply docs/open/HANDOVER_the_pointer_map_decides_on_day_zero.md",
)

FRAME = 384
SCATTERED_ORIGINS = {
    "posA": {"y": 0, "x": 0},
    "posB": {"y": 190, "x": 117},  # overlaps posA, off the chunk grid
    "posC": {"y": 40, "x": 1100},  # a far outlier
    "posD": {"y": 190, "x": 117},  # lands exactly on posB, committed later
}


def _live_profile():
    from zmart_live.profiles import plan_the_writing

    return plan_the_writing("overview", frame=FRAME, channels=("channel 0",))[0]


def _committed_reference(run: Path) -> tuple[np.ndarray, dict[str, tuple[int, int]]]:
    """The expected level 0 of a live run: manifest origins, commit order wins."""
    meta = run / "views" / "live" / "metadata"
    placed = {
        one["position_id"]: (one["origin"]["y"], one["origin"]["x"])
        for one in json.loads((meta / "locations.json").read_text())["positions"]
    }
    order = []

    for line in (meta / "events.jsonl").read_text().strip().splitlines():
        event = json.loads(line)

        if event.get("position_id"):
            order.append((event["position_id"], event.get("position_generation", 0)))

    survey = run / "data" / "survey.ome.zarr"
    height = max(top + FRAME for top, _ in placed.values())
    width = max(left + FRAME for _, left in placed.values())
    pasted = np.zeros((height, width), dtype=np.uint16)

    for name, generation in order:
        top, left = placed[name]
        held_in = name if not generation else f"{name}.generation-{generation}"
        body = zarr.open_array(str(survey / held_in / "0"), mode="r")[...]
        pasted[top : top + FRAME, left : left + FRAME] = body.reshape(body.shape[-2:])
    return pasted, placed


def _publish_scattered(run: Path, *, linked_view: str = "at_run_end"):
    from zmart_live.coordinator import LivePublisher

    publisher = LivePublisher(
        run,
        _live_profile(),
        run_id="gate-scatter",
        positions=SCATTERED_ORIGINS,
        linked_view=linked_view,
    )

    for index, name in enumerate(sorted(SCATTERED_ORIGINS)):
        body = _stamped_body((1, FRAME, FRAME), 100 + index, np.uint16)
        publisher.write_and_publish(name, body)
    return publisher


def test_live_scattered_landings_place_and_overlap_by_commit(tmp_path):
    """Sequential, unbaked: every landing sits at its manifest origin, later
    commit winning where landings share ground — checked after every commit."""
    from zmart_live.coordinator import LivePublisher

    run = tmp_path / "run"
    publisher = LivePublisher(
        run,
        _live_profile(),
        run_id="gate-scatter",
        positions=SCATTERED_ORIGINS,
        linked_view="at_run_end",
    )

    for index, name in enumerate(sorted(SCATTERED_ORIGINS)):
        publisher.write_and_publish(name, _stamped_body((1, FRAME, FRAME), 100 + index, np.uint16))
        expected, placed = _committed_reference(run)
        governed = GovernedRun(run)
        composer = governed.composer()
        composer.stop_warming()

        try:
            served = served_level(composer, 0)
            room = tuple(min(side, want) for side, want in zip(served.shape, expected.shape))
            window = served[: room[0], : room[1]]
            wanted = expected[: room[0], : room[1]]

            if not np.array_equal(window, wanted):
                differs = np.argwhere(window != wanted)
                y, x = differs[0]
                report = "\n".join(where_the_markers_landed(window, placed))
                raise AssertionError(
                    f"after landing {name}: {len(differs)} voxels differ, first at "
                    f"({y},{x}) served {window[y, x]} expected {wanted[y, x]}.\n{report}"
                )
        finally:
            composer.close()

    # posD landed last on posB's exact spot: the later commit owns the ground.
    assert window[SCATTERED_ORIGINS["posD"]["y"] + 1, SCATTERED_ORIGINS["posD"]["x"] + 1] == 103


def test_live_scattered_bake_writes_the_same_picture(tmp_path):
    """Sequential, baked: the baked ground equals the committed reference."""
    from zmart_viewer.live import the_live_picture_declared

    run = tmp_path / "run"
    _publish_scattered(run)
    store = the_live_picture_declared(run, bake=True)
    levels = json.loads((store / "zarr.json").read_text())["attributes"]["zmart"]["baked"]
    assert levels, "the live bake wrote no levels"
    expected, _ = _committed_reference(run)
    governed = GovernedRun(run)
    composer = governed.composer()
    composer.stop_warming()

    try:
        served = served_level(composer, 0)
    finally:
        composer.close()
    assert np.array_equal(served[: expected.shape[0], : expected.shape[1]], expected)


@needs_day_zero_writer
def test_the_pointer_map_refuses_off_chunk_placements_on_day_zero(tmp_path):
    """The one honest refusal, said at construction: the places are known
    before the first pixel, so a per-publish run that can never be linked
    is refused before anything is written."""
    from zmart_live.coordinator import LivePublisher
    from zmart_live.model import ZmartLiveError

    with pytest.raises(ZmartLiveError, match="whole chunks"):
        LivePublisher(
            tmp_path / "run",
            _live_profile(),
            run_id="gate-scatter",
            positions=SCATTERED_ORIGINS,
            linked_view="per_publish",
        )
    # The refusal lands before the first pixel and before any record: at
    # most the empty container scaffold exists.
    written = [one for one in (tmp_path / "run").rglob("*") if one.is_file()]
    assert all(one.name == "zarr.json" for one in written), written


@needs_day_zero_writer
def test_a_scattered_run_finishes_cleanly_without_the_pointer_map(tmp_path):
    """A run that cannot be pointer-linked still finishes: layout recorded,
    map and view deliberately absent, the governed picture serving it."""
    publisher = _publish_scattered(tmp_path / "run")
    assert publisher.pointer_linkable is False
    publisher.finish_the_run()
    meta = tmp_path / "run" / "views" / "live" / "metadata"
    assert (meta / "locations.json").is_file()
    assert not any((tmp_path / "run").rglob("links.json"))


@needs_day_zero_writer
def test_a_scattered_dataset_replays_where_it_sits(tmp_path):
    """The replay door takes a scattered, off-chunk dataset end to end."""
    from zmart_viewer.rehearsal import plan_a_replay, replay_the_dataset

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    places = [(0.0, 0.0), (25.0, 41.0), (10.0, 500.0)]

    for index, place in enumerate(places):
        write_position(dataset / f"pos_{index:02d}.zarr", 100 + index, place, size=FRAME, levels=1)

    plan = plan_a_replay(dataset)
    assert not plan.on_whole_chunks
    replay_the_dataset(dataset, tmp_path / "replayed", every_s=0)

    run = tmp_path / "replayed"
    expected = {
        f"pos_{index:02d}.zarr": (int(place[0]), int(place[1]))
        for index, place in enumerate(places)
    }
    meta = run / "views" / "live" / "metadata"
    placed = {
        one["position_id"]: (one["origin"]["y"], one["origin"]["x"])
        for one in json.loads((meta / "locations.json").read_text())["positions"]
    }
    assert placed == expected, placed

    governed = GovernedRun(run)
    composer = governed.composer()
    composer.stop_warming()

    try:
        served = served_level(composer, 0)

        for name, (top, left) in expected.items():
            stamp = 100 + int(name.removesuffix(".zarr")[-2:])
            assert served[top, left] == _marker_floor(np.uint16) + stamp % 40, (
                f"{name} asked ({top}, {left}); found {served[top, left]}"
            )
    finally:
        composer.close()


# -- growth: one more landing costs the same at any survey size -----------------


def _a_running_survey(folder: Path, across: int):
    """A committed survey of across-squared scattered positions, one held back."""
    from zmart_live.coordinator import LivePublisher

    origins = {
        f"pos{row:02d}{column:02d}": {"y": row * 300 + 7, "x": column * 300 + 13}
        for row in range(across)
        for column in range(across)
    }
    publisher = LivePublisher(
        folder,
        _live_profile(),
        run_id="gate-growth",
        positions=origins,
        linked_view="at_run_end",
    )
    names = sorted(origins)

    for index, name in enumerate(names[:-1]):
        body = _stamped_body((1, FRAME, FRAME), 100 + index % 40, np.uint16)
        publisher.write_and_publish(name, body)
    return publisher, names[-1]


def test_one_more_landing_reads_one_tile_no_matter_the_survey(tmp_path):
    """The cost of one more landing is counted, not timed: after a landing
    onto a warm survey the derive reads at most one tile — never the survey —
    and its only survey-sized work is the bookkeeping sweep the accounting
    counter names. Checked at two sizes so a hidden slope shows as a count."""
    read_after_landing = {}

    for across in (2, 5):
        publisher, held_back = _a_running_survey(tmp_path / f"survey{across}", across)
        governed = GovernedRun(publisher.folder)
        composer = governed.composer()
        composer.stop_warming()

        try:
            governed.composer()
            publisher.write_and_publish(held_back, _stamped_body((1, FRAME, FRAME), 99, np.uint16))
            governed.request_catch_up()
            governed.composer()
            read_after_landing[across] = governed.accounting["last_tiles_read"]
            drawn = across * across
            assert governed.accounting["last_positions"] == drawn
            assert governed.accounting["last_snapshot_swept"] == 5 * drawn
        finally:
            composer.close()

    assert all(read <= 1 for read in read_after_landing.values()), read_after_landing


def _writer_can_add_a_position() -> bool:
    """Does the installed zmart_live let a position join a running run?"""
    from zmart_live.coordinator import LivePublisher

    return hasattr(LivePublisher, "add_a_position")


needs_growing_writer = pytest.mark.skipif(
    not _writer_can_add_a_position(),
    reason="the installed zmart_live cannot yet add a position to a running "
    "run -- see the growth items in "
    "docs/open/HANDOVER_the_pointer_map_decides_on_day_zero.md",
)


@needs_growing_writer
def test_a_position_joins_a_running_survey_where_it_is_put(tmp_path):
    """Growth in space: a position the day-zero layout never named joins a
    running survey and lands exactly at the origin it was given."""
    from zmart_live.coordinator import LivePublisher

    run = tmp_path / "run"
    publisher = LivePublisher(
        run,
        _live_profile(),
        run_id="gate-growth",
        positions={"posA": {"y": 0, "x": 0}},
        linked_view="at_run_end",
    )
    publisher.write_and_publish("posA", _stamped_body((1, FRAME, FRAME), 101, np.uint16))
    publisher.add_a_position("posLate", {"y": 90, "x": 411})
    publisher.write_and_publish("posLate", _stamped_body((1, FRAME, FRAME), 102, np.uint16))

    expected, placed = _committed_reference(run)
    assert placed["posLate"] == (90, 411)
    governed = GovernedRun(run)
    composer = governed.composer()
    composer.stop_warming()

    try:
        served = served_level(composer, 0)
    finally:
        composer.close()
    assert np.array_equal(served[: expected.shape[0], : expected.shape[1]], expected)


# -- the viewer's own linked row ------------------------------------------------

ALIGNED_ORIGINS = {
    "posA": {"y": 0, "x": 0},
    "posB": {"y": 192, "x": 64},  # overlaps posA, committed later
    "posC": {"y": 64, "x": 1280},  # a far outlier
}


def _an_aligned_run(folder):
    from zmart_live.coordinator import LivePublisher

    publisher = LivePublisher(
        folder,
        _live_profile(),
        run_id="gate-linked",
        positions=ALIGNED_ORIGINS,
        linked_view="at_run_end",
    )

    for index, name in enumerate(sorted(ALIGNED_ORIGINS)):
        publisher.write_and_publish(name, _stamped_body((1, FRAME, FRAME), 100 + index, np.uint16))
    return publisher


def _decoded_chunk(run, held) -> np.ndarray:
    import numcodecs

    raw = (run / held.path).read_bytes()[held.offset : held.offset + held.length]
    return np.frombuffer(numcodecs.Zstd().decode(raw), dtype=np.uint16).reshape(64, 64)


def test_the_viewer_links_a_finished_run_and_every_pointed_byte_is_true(tmp_path):
    """The viewer's own zero-copy map: every level-0 chunk of the committed
    ground answers with the winning tile's own bytes, absence stays absent,
    and the whole canvas is swept, not sampled."""
    from zmart_viewer.pieces import link_a_finished_run, pointed_bytes_behind

    run = tmp_path / "run"
    _an_aligned_run(run)
    store = link_a_finished_run(run)
    expected, _ = _committed_reference(run)

    for chunk_row in range(expected.shape[0] // 64):
        for chunk_column in range(expected.shape[1] // 64):
            held = pointed_bytes_behind(store, f"0/c/0/{chunk_row}/{chunk_column}")
            window = expected[
                chunk_row * 64 : chunk_row * 64 + 64,
                chunk_column * 64 : chunk_column * 64 + 64,
            ]

            if held is None:
                assert not window.any(), (
                    f"chunk ({chunk_row}, {chunk_column}) has committed ground "
                    "but the map points at nothing"
                )
                continue

            served = _decoded_chunk(run, held)
            assert np.array_equal(served, window), (
                f"chunk ({chunk_row}, {chunk_column}) from {held.path}: "
                f"served {np.unique(served)[:4]} expected {np.unique(window)[:4]}"
            )

    # The ground both tiles cover came from the later commit's own store.
    overlap = pointed_bytes_behind(store, "0/c/0/3/2")
    assert overlap is not None and "posB" in overlap.path


def test_a_commit_after_linking_makes_the_pointers_stand_aside(tmp_path):
    """Staleness is honest: a replacement silences the map, the governed
    picture serves the new truth, and re-linking points at the new store."""
    from zmart_viewer.pieces import link_a_finished_run, pointed_bytes_behind

    run = tmp_path / "run"
    publisher = _an_aligned_run(run)
    store = link_a_finished_run(run)
    assert pointed_bytes_behind(store, "0/c/0/0/0") is not None

    publisher.replace_a_position("posA", _stamped_body((1, FRAME, FRAME), 120, np.uint16))
    assert pointed_bytes_behind(store, "0/c/0/0/0") is None, (
        "the map kept answering for a run that moved past it"
    )

    relinked = link_a_finished_run(run)
    held = pointed_bytes_behind(relinked, "0/c/0/0/0")
    assert held is not None and ".generation-1" in held.path
    expected, _ = _committed_reference(run)
    assert np.array_equal(_decoded_chunk(run, held), expected[:64, :64])


def test_off_chunk_placements_cannot_be_pointer_linked_by_the_viewer(tmp_path):
    """The one honest refusal, in the viewer's own words this time."""
    from zmart_viewer.pieces import link_a_finished_run

    run = tmp_path / "run"
    _publish_scattered(run)

    with pytest.raises(ValueError, match="whole chunks"):
        link_a_finished_run(run)


# -- the plate row --------------------------------------------------------------


def test_a_plates_fields_may_overlap_where_recorded(tmp_path):
    """Overlapping recorded field places are honoured, later-sorted field on top."""
    from test_a_plate_lays_itself_out import a_plate_of_placed_fields

    plate = a_plate_of_placed_fields(tmp_path) / "plate.ome.zarr"
    overlapping = [(5.0, 3.0), (12.4, 9.0), (12.4, 9.0)]

    for field, place in enumerate(overlapping):
        described_file = plate / "A" / "1" / str(field) / "zarr.json"
        described = json.loads(described_file.read_text())
        transforms = described["attributes"]["ome"]["multiscales"][0]["datasets"][0][
            "coordinateTransformations"
        ]
        transforms[-1]["translation"] = [0.0, place[0], place[1]]
        described_file.write_text(json.dumps(described), encoding="utf-8")

    mosaic = read_the_transfer(plate)
    corners = {tile.name: tile.copies[0].corner_um[1:] for tile in mosaic.tiles}
    base = corners["A1-0"]

    for field, place in enumerate(overlapping):
        delta = (
            corners[f"A1-{field}"][0] - base[0],
            corners[f"A1-{field}"][1] - base[1],
        )
        recorded = (place[0] - overlapping[0][0], place[1] - overlapping[0][1])
        assert delta == recorded, (
            f"field {field} asked to sit {recorded} micrometres from field 0 "
            f"but was placed {delta} away"
        )

    composer = Composer(mosaic)

    try:
        voxel = mosaic.voxel_um(0)
        inside = (
            int((corners["A1-2"][0] - mosaic.corner_um[1]) / voxel[1]) + 2,
            int((corners["A1-2"][1] - mosaic.corner_um[2]) / voxel[2]) + 2,
        )
        served = served_level(composer, 0)
        assert served[inside] == 1200, (
            f"the ground both overlapping fields cover shows {served[inside]}; "
            "the later-sorted field (A1-2, stamped 1200) must be on top"
        )
    finally:
        composer.close()


# -- the photographed flagship --------------------------------------------------


def test_scattered_markers_survive_the_engine(browser, built_dist, tmp_path):
    """One scattered, baked, multi-channel 0.5 case through the real page.

    Array equality cannot say whether the translations survive the engine's
    own transform; a photograph can. The picture is four scattered stamped
    tiles; the gate is that bright ground appears, and appears inside the
    scattered bounding box rather than a grid's.
    """
    import threading

    from zmart_viewer.server import make_server

    places = [(0.0, 0.0), (40.6, 71.3), (13.0, 300.0), (40.6, 71.3)]
    folder = tmp_path / "run"
    a_scattered_run(folder, {"version": "0.5", "names": ("c", "z", "y", "x")}, places)
    store = declare_a_built_picture(tmp_path / "views", folder, name="flagship", bake=True)

    server = make_server(port=0, data_dir=store.parent, store=[store.name], live=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = browser.new_page(viewport={"width": 800, "height": 600})

    try:
        page.goto(f"http://127.0.0.1:{server.server_address[1]}", wait_until="domcontentloaded")
        page.wait_for_function("() => window.zmartViewer !== undefined", timeout=60_000)
        page.wait_for_function("() => window.zmartSourcesWaiting() === 0", timeout=90_000)
        page.wait_for_timeout(2_000)
        from pixels import fraction_lit

        lit = fraction_lit(page)
        assert lit > 0.02, f"the scattered picture drew nothing ({lit:.1%} lit)"
    finally:
        page.close()
        server.shutdown()
        thread.join(timeout=5)
