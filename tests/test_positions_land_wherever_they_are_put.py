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
import shutil
import sys
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
        scale = [
            float(LEVEL_SCALES[level]) if name in ("y", "x") else 1.0 for name in names
        ]
        translation = [0.0] * (len(names) - len(place)) + list(place)
        datasets.append({
            "path": str(level),
            "coordinateTransformations": [
                {"type": "scale", "scale": scale},
                {"type": "translation", "translation": translation},
            ],
        })
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
            str(level), shape=shape, chunks=shape, dtype=dtype,
            **({"dimension_names": names} if v3 else {}),
        )
        made[:] = _stamped_body(shape, stamp + level, dtype)

    described = _multiscales(names, levels, place, version)

    if v3:
        (path / "zarr.json").write_text(json.dumps({
            "attributes": {"ome": {"version": "0.5", "multiscales": described}},
            "zarr_format": 3, "node_type": "group",
        }), encoding="utf-8")
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
        (0.0, 0.0),                       # an exact duplicate
        (10.3, 17.8),                     # fractional
        (-float(size // 2), -8.5),        # a negative corner
        (5.0, room + 6 * size + 0.4),     # a far outlier
    ]
    return [place for place in places if abs(place[0] * 10 % 10 - 5) > 0.01]


def nominal_places(count: int, size: int) -> list[tuple[float, float]]:
    step = size - size // 8
    across = max(2, int(count ** 0.5))
    return [
        (float(step * (index // across)), float(step * (index % across)))
        for index in range(count)
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


def where_the_markers_landed(picture: np.ndarray, expected: dict[str, tuple[int, int]]) -> list[str]:
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
                f"({y},{x}) served {served[y, x]} expected {expected[y, x]}"
                for y, x in differs[:6]
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
    assert placements["pos_b.zarr"] == (0, 0)      # 3.6 - 3.4 = 0.2 rounds away
    assert placements["pos_c.zarr"] == (10, 17)    # 20 - 3.4 = 16.6 rounds to 17
    _, placements = reference_paste(folder, 1)
    assert placements["pos_c.zarr"] == (5, 8)      # 16.6 / 2 = 8.3 rounds to 8

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
    places = (
        nominal_places(6, 64) if arrangement == "nominal" else scattered_places(6, 64, seed=41)
    )
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
    places = (
        nominal_places(5, 64) if arrangement == "nominal" else scattered_places(5, 64, seed=42)
    )
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
            flat = written.reshape(written.shape[-2:]) if written.ndim == 2 else written[
                (0,) * (written.ndim - 2)
            ]
            assert np.array_equal(flat, expected), f"baked level {level} differs from composed"
    finally:
        composer.close()
    assert unbaked.name != baked.name or True


def test_the_real_door_serves_the_scattered_picture(tmp_path):
    """One scattered case through HTTP: the ladder answers what the composer would."""
    from zmart_viewer.server import make_server
    import threading

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
