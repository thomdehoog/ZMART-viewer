"""Write down what a built picture is, and keep a governed one honest.

The description — axes, sizes, levels, voxel size, provenance — is an
OME-Zarr holding no pixels; ``bake`` computes coarse levels once and
keeps them as files. For a manifest-governed live run, what may be shown
is the gateway's decision (zmart_live) and the served picture is patched
per commit: the cost of a change is the change.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import contextmanager
from multiprocessing import get_context
from pathlib import Path

import numpy as np
import zarr
from zmart_live.gateway import _LiveRun
from zmart_live.model import rounded_up
from zmart_live.shardlink import how_the_array_is_stored

from .compose import (
    PIECE,
    Composer,
    Copy,
    Mosaic,
    Tile,
    _read_one_tile,
    read_the_transfer,
    the_mosaic_written_down,
)

OURS = "zmart"

_BAKE_PROCESSES = min(4, os.cpu_count() or 1)

_BAKING: tuple | None = None


def _start_baking(run: Path, piece: int) -> None:
    """Give this bake worker its own reading of the governed run."""
    global _BAKING

    governed = GovernedRun(run, piece=piece)
    composer = governed.composer()
    composer.stop_warming()
    _BAKING = (governed, composer)


def _bake_one_stripe(store: Path, level: int, rows: tuple[int, ...]) -> int:
    """One worker's share of a bake: whole rows of one level, written as files."""
    assert _BAKING is not None, "a bake worker must be started before use"
    _, composer = _BAKING
    deep = composer.grid(level)[0]
    across = composer.grid(level)[2]
    moments, channels = composer.mosaic.frame_room
    grown = (moments, channels) != (1, 1)
    written = 0
    for row in rows:
        for moment in range(moments):
            for channel in range(channels):
                frame = (str(moment), str(channel)) if grown else ()
                for plane in range(deep):
                    inside = store.joinpath(str(level), "c", *frame, str(plane), str(row))
                    for column in range(across):
                        body = composer.bytes_for(
                            level, plane, row, column, moment=moment, channel=channel
                        )
                        if body is None:
                            continue
                        inside.mkdir(parents=True, exist_ok=True)
                        (inside / str(column)).write_bytes(body)
                        written += 1
    return written


def the_scene_folder_name(name: str) -> str:
    """The scene folder's name: the given name wearing ``.zmartview.zarr`` once."""
    bare = name.removesuffix(".zarr").removesuffix(".ome").removesuffix(".zmartview")
    return f"{bare}.zmartview.zarr"


def declare_a_built_picture(
    where: str | Path,
    transfer: str | Path,
    *,
    name: str = "built",
    piece: int = PIECE,
    bake: bool = False,
    workers: int = 1,
    told=None,
) -> Path:
    """Write the description of a picture built from a transfer."""
    where, transfer = Path(where), Path(transfer).resolve()
    mosaic = read_the_transfer(transfer)
    composer = Composer(mosaic, piece=piece, workers=workers)

    store = where / the_scene_folder_name(name)
    store.mkdir(parents=True, exist_ok=True)

    for kept in sorted(store.glob("[0-9]*")):
        if kept.is_dir() and (int(kept.name) >= mosaic.levels or (kept / "c").exists()):
            shutil.rmtree(kept)

    for level in range(mosaic.levels):
        inside = store / str(level)
        inside.mkdir(exist_ok=True)
        (inside / "zarr.json").write_text(
            json.dumps(json.loads(composer.array_json(level)), indent=1), encoding="utf-8"
        )

    described = json.loads(composer.group_json())
    baked: list[int] = []
    if bake:
        try:
            baked = _bake_the_coarse_ground(store, composer, described, told=told)
        finally:
            composer.close()

    described["attributes"][OURS] = {
        "what": (
            "A picture that holds no pixels beyond its baked coarse ground. "
            "Every other piece of it is built when it is asked for, out of the "
            "tiles of the transfer named below, which are read and never "
            "changed."
        ),
        "built_from": transfer.as_posix(),
        "piece": composer.piece,
        "tiles": len(mosaic.tiles),
        "baked": baked,
    }
    (store / "zarr.json").write_text(json.dumps(described, indent=1), encoding="utf-8")

    (store / "tiles.json").write_text(json.dumps(the_mosaic_written_down(mosaic)), encoding="utf-8")

    return store


def declare_a_governed_picture(
    where: str | Path,
    run: str | Path,
    *,
    name: str = "live",
    piece: int = PIECE,
    bake: bool = False,
) -> Path:
    """Write the description of a picture built from a manifest-governed run."""

    where, run = Path(where), Path(run).resolve()
    governed = GovernedRun(run, piece=piece)
    try:
        composer = governed.composer()
        folded = governed._run._folded
        tail = governed._run._last_folded_revision
        revision = governed._run._geometry()[0].revision

        store = where / the_scene_folder_name(name)
        store.mkdir(parents=True, exist_ok=True)
        for kept in sorted(store.glob("[0-9]*")):
            if kept.is_dir() and (
                int(kept.name) >= composer.mosaic.levels or (kept / "c").exists()
            ):
                shutil.rmtree(kept)
        for level in range(composer.mosaic.levels):
            inside = store / str(level)
            inside.mkdir(exist_ok=True)
            (inside / "zarr.json").write_text(
                json.dumps(json.loads(composer.array_json(level)), indent=1), encoding="utf-8"
            )

        described = json.loads(composer.group_json())
        baked: list[int] = []
        if bake:
            with _holding_the_bake_lock(store):
                baked = _bake_the_coarse_ground(store, composer, described, governed_run=run)
        described["attributes"][OURS] = {
            "what": (
                "A picture of a live, manifest-governed run. It holds no "
                "pixels beyond its baked coarse ground, kept true per "
                "commit; every other piece is built when asked for, from "
                "the positions the run's manifest has published as of that "
                "request, and nothing else."
            ),
            "governed_from": run.as_posix(),
            "piece": composer.piece,
            "baked": baked,
        }
        (store / "zarr.json").write_text(json.dumps(described, indent=1), encoding="utf-8")
        if bake:
            governed.stamp_the_bake(store, events=folded, tail=tail, layout=revision)
        return store
    finally:
        governed.close()


def _bake_the_coarse_ground(
    store: Path, composer: Composer, described: dict, *, governed_run: Path | None = None, told=None
) -> list[int]:
    """Build the coarse ground once, into real files, and extend the pyramid."""
    coarsest = composer.mosaic.levels - 1
    pinned = sorted(composer.pinned_levels)
    datasets = described["attributes"]["ome"]["multiscales"][0]["datasets"]

    built_by_workers = False
    if governed_run is not None and _BAKE_PROCESSES > 1:
        composer.stop_warming()
        try:
            working = ProcessPoolExecutor(
                max_workers=_BAKE_PROCESSES,
                mp_context=get_context("spawn"),
                initializer=_start_baking,
                initargs=(governed_run, composer.piece),
            )
            try:
                stripes = []
                for level in pinned:
                    down = composer.grid(level)[1]
                    for worker in range(min(_BAKE_PROCESSES, down)):
                        rows = tuple(range(worker, down, _BAKE_PROCESSES))
                        if rows:
                            stripes.append(working.submit(_bake_one_stripe, store, level, rows))
                # Consumed in order; a stripe that cannot be built stops the
                # bake, exactly as the serial loop's first failure would.
                for stripe in stripes:
                    stripe.result()
            finally:
                working.shutdown(wait=True, cancel_futures=True)
            built_by_workers = True
        except BrokenProcessPool:
            print(
                "The bake's worker processes could not start (usually: "
                "the calling script runs its work at import time, and a "
                "worker re-imports it -- guard the script with "
                "'if __name__ == \"__main__\":'). Baking serially "
                "instead, which is slower and otherwise identical."
            )
    if not built_by_workers:
        moments, channels = composer.mosaic.frame_room
        grown = (moments, channels) != (1, 1)
        # One unit of progress per row of pieces per frame, counted up front
        # so the ratio is honest from the first report.
        total = (
            moments
            * channels
            * sum(composer.grid(level)[0] * composer.grid(level)[1] for level in pinned)
        )
        done = 0
        for level in pinned:
            deep, down, across = composer.grid(level)
            for moment in range(moments):
                for channel in range(channels):
                    frame = (str(moment), str(channel)) if grown else ()
                    for plane in range(deep):
                        for row in range(down):
                            done += 1
                            if told is not None:
                                told(done, total)
                            inside = store.joinpath(str(level), "c", *frame, str(plane), str(row))
                            for column in range(across):
                                body = composer.bytes_for(
                                    level, plane, row, column, moment=moment, channel=channel
                                )
                                if body is None:
                                    continue
                                inside.mkdir(parents=True, exist_ok=True)
                                (inside / str(column)).write_bytes(body)

    whole = np.asarray(zarr.open_array(str(store / str(coarsest)), mode="r"))
    room = whole.shape[:-3]
    depth, height, width = whole.shape[-3:]
    voxel = list(composer.mosaic.voxel_um(coarsest))
    level = coarsest
    while height > composer.piece or width > composer.piece:
        level += 1
        height, width = -(-height // 2), -(-width // 2)
        voxel = [voxel[0], voxel[1] * 2, voxel[2] * 2]
        evened = np.pad(
            whole,
            [(0, 0)] * (len(room) + 1)
            + [(0, height * 2 - whole.shape[-2]), (0, width * 2 - whole.shape[-1])],
            mode="edge",
        )
        whole = (
            evened.reshape(*room, depth, height, 2, width, 2)
            .mean(axis=(-3, -1))
            .round()
            .astype(composer.mosaic.dtype)
        )
        made = zarr.create_array(
            store=str(store / str(level)),
            shape=(*room, depth, height, width),
            chunks=(1,) * len(room) + (1, composer.piece, composer.piece),
            dtype=composer.mosaic.dtype,
            zarr_format=3,
            dimension_names=(["t", "c"] if room else []) + list(composer.mosaic.axes),
            overwrite=True,
        )
        made[:] = whole
        datasets.append(
            {
                "path": str(level),
                "coordinateTransformations": [
                    {"type": "scale", "scale": [1.0] * len(room) + list(voxel)},
                    {
                        "type": "translation",
                        "translation": [0.0] * len(room) + list(composer.mosaic.corner_um),
                    },
                ],
            }
        )

    return pinned + list(range(coarsest + 1, level + 1))


def main() -> None:
    import argparse

    parsed = argparse.ArgumentParser(description=__doc__)
    parsed.add_argument("transfer", type=Path)
    parsed.add_argument("where", type=Path)
    parsed.add_argument("--name", default="built")
    parsed.add_argument("--piece", type=int, default=PIECE)
    parsed.add_argument(
        "--bake",
        action="store_true",
        help="also build the coarse ground now, once, into "
        "real files, so opening never builds it again. "
        "Declaring without this removes any earlier bake.",
    )
    parsed.add_argument(
        "--workers",
        type=int,
        default=1,
        help="how many processes build while baking; one builds in place",
    )
    given = parsed.parse_args()

    store = declare_a_built_picture(
        given.where,
        given.transfer,
        name=given.name,
        piece=given.piece,
        bake=given.bake,
        workers=given.workers,
    )
    print(f"\n  declared {store}")
    print("  it holds no pixels; open the folder above it in the viewer.\n")


if __name__ == "__main__":
    main()


log = logging.getLogger("zmart-viewer.governed")

_BAKE_CATCH_UP_QUIET_S = 0.25


def _promising_its_blocks(copy: Copy) -> Copy:
    """The same copy, now promising that every block it is asked for exists."""
    stored = []

    def is_on_disk(index: tuple[int, ...]) -> bool:
        if not stored:
            stored.append(how_the_array_is_stored(copy.held_in))
        return stored[0].where_one_chunk_lives(tuple(index)) is not None

    copy.presence = is_on_disk
    return copy


def _a_committed_tile(store) -> Tile:
    """One published position, read as a tile whose ground is promised."""
    tile = _read_one_tile(store)
    for copy in tile.copies:
        _promising_its_blocks(copy)
    return tile


@contextmanager
def _holding_the_bake_lock(store: Path):
    """The whole-machine lock on one picture's baked files."""
    store.mkdir(parents=True, exist_ok=True)
    holding = open(store / ".bake.lock", "a+b")
    try:
        if os.name == "nt":  # pragma: no cover - exercised on the Windows target
            import msvcrt

            holding.seek(0)
            msvcrt.locking(holding.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                holding.seek(0)
                msvcrt.locking(holding.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(holding.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(holding.fileno(), fcntl.LOCK_UN)
    finally:
        holding.close()


def _after_a_windows_reader(operation, *paths):
    """Perform one file swap/removal after a brief Windows sharing lock."""
    deadline = time.monotonic() + 5.0
    pause = 0.002
    while True:
        try:
            return operation(*paths)
        except PermissionError as problem:
            sharing = getattr(problem, "winerror", None) in (5, 32, 33) or getattr(
                problem, "errno", None
            ) in (5, 13)
            if os.name != "nt" or not sharing or time.monotonic() >= deadline:
                raise
            time.sleep(pause)
            pause = min(pause * 2, 0.05)


def _a_tile_stamped(
    pattern: Tile,
    store: Path,
    corner_um: tuple[float, float, float],
    moments: frozenset[int] | None = None,
) -> Tile:
    """One published position's tile, stamped from the pattern instead of read."""
    copies = [
        _promising_its_blocks(
            Copy(
                held_in=store / one.held_in.name,
                shape=one.shape,
                chunks=one.chunks,
                dtype=one.dtype,
                voxel_um=one.voxel_um,
                corner_um=corner_um,
                outer_shape=one.outer_shape,
            )
        )
        for one in pattern.copies
    ]
    return Tile(
        name=store.name,
        store=store,
        copies=copies,
        axes=pattern.axes,
        turned=pattern.turned,
        moments=moments,
    )


class TheWorldFrame(Mosaic):
    """A mosaic whose geometry is the layout's, whatever tiles have arrived."""

    _origins: dict[tuple, tuple[float, float, float]] = {}
    _shapes: dict[tuple, tuple[int, int, int]] = {}

    def __init__(self, tiles, layout, profile, *, run: Path):
        named = (run, layout.run_id, layout.revision, profile.profile_id)
        remembered = TheWorldFrame._origins.get(named)
        if remembered is None:
            remembered = tuple(
                min(
                    (
                        float(placement.origin.get(axis, 0))
                        * float(profile.voxel_size.get(axis, 1.0))
                        for placement in layout.positions
                    ),
                    default=0.0,
                )
                for axis in ("z", "y", "x")
            )
            TheWorldFrame._origins[named] = remembered
        origin_um = remembered
        super().__init__(
            tiles=tiles,
            levels=len(profile.levels),
            axes=("z", "y", "x"),
            dtype=str(profile.dtype),
            corner_um=origin_um,
            averaged=True,
        )
        self._layout = layout
        self._profile = profile
        self._run_folder = run

    def voxel_um(self, level: int) -> tuple[float, float, float]:
        """From the profile, so it exists before any position does."""
        rung = self._profile.level(level)
        return tuple(
            float(self._profile.voxel_size.get(axis, 1.0)) * float(rung.downsampling.get(axis, 1))
            for axis in ("z", "y", "x")
        )  # type: ignore[return-value]

    def shape(self, level: int) -> tuple[int, int, int]:
        """The layout's extent: every planned position, arrived or not."""
        found = self._shape.get(level)
        if found is None:
            named = (
                self._run_folder,
                self._layout.run_id,
                self._layout.revision,
                self._profile.profile_id,
                level,
            )
            found = TheWorldFrame._shapes.get(named)
            if found is None:
                rung = self._profile.level(level)
                frame = self._profile.frame_shape
                reach = []
                for axis in ("z", "y", "x"):
                    down = float(rung.downsampling.get(axis, 1))
                    edge = max(
                        (
                            float(placement.origin.get(axis, 0)) + float(frame.get(axis, 1))
                            for placement in self._layout.positions
                        ),
                        default=float(frame.get(axis, 1)),
                    )
                    reach.append(rounded_up(int(edge), int(down)))
                found = tuple(reach)
                TheWorldFrame._shapes[named] = found
            self._shape[level] = found  # type: ignore[assignment]
        return found  # type: ignore[return-value]

    @property
    def frame_room(self) -> tuple[int, int]:
        """The run's (moments, channels) room, knowable before any arrival."""
        from_tiles = super().frame_room
        return (max(int(self._profile.timepoints), from_tiles[0]), len(self._profile.channels))

    @property
    def slab_depths(self) -> list[int]:
        """How many planes one file holds per level, from the profile."""
        return [
            int(self._profile.level(level).inner_chunk.get("z", 1)) for level in range(self.levels)
        ]


class GovernedRun:
    """One governed run, served as a built picture that obeys its manifest."""

    def __init__(self, folder: str | Path, piece: int = PIECE, store: str | Path | None = None):
        self.folder = Path(folder).resolve()
        self._run = _LiveRun(self.folder)
        self._piece = piece
        self._shown = Path(store).resolve() if store is not None else None
        self._baked: tuple[int, ...] | None = None
        self._bake_guard = threading.Lock()
        self._derive_guard = threading.Lock()
        self._catch_up_guard = threading.Condition()
        self._catch_up_requested = False
        self._catch_up_after = 0.0
        self._catch_up_thread: threading.Thread | None = None
        self._closing = False
        self._folded_installed = -1
        self._frame_installed: tuple[int, str] | None = None
        self._stamp_installed: dict | None = None
        self._mark: tuple[int, int, int, int] | None = None
        self._held: Composer | None = None
        self._drawing: dict[str, int] = {}
        self._tiles: dict[str, Tile] = {}
        self._pattern: Tile | None = None
        self._pattern_of: str | None = None
        self._corners: dict[str, tuple[float, float, float]] = {}
        self._corners_mark: tuple[int, str] | None = None
        self._guard = threading.Lock()
        self._bake_below: dict[int, zarr.Array] = {}
        self._bake_staging: dict[int, zarr.Array] = {}
        self._bake_recipes: dict[int, dict | None] = {}
        self.accounting = {
            "derives": 0,
            "last_derive_ms": 0.0,
            "last_tiles_read": 0,
            "last_positions": 0,
            "last_bake_arrays_opened": 0,
            "last_bake_stagings_built": 0,
            "last_bake_zarr_ops": 0,
            "last_bake_pieces_rehalved": 0,
            "last_bake_slabs_built": 0,
            "last_bake_slabs_warm": 0,
            "last_snapshot_swept": 0,
        }

    def composer(self) -> Composer:
        """The composer for the manifest's state as of now."""
        with self._derive_guard:
            return self._derive_and_install_the_composer()

    def request_catch_up(self) -> None:
        """Schedule a demand-free derive, coalescing a burst of announcements."""
        with self._catch_up_guard:
            if self._closing:
                return
            self._catch_up_requested = True
            self._catch_up_after = time.monotonic() + _BAKE_CATCH_UP_QUIET_S
            if self._catch_up_thread is not None:
                self._catch_up_guard.notify_all()
                return
            worker = threading.Thread(
                target=self._catch_up_after_announcements,
                name=f"zmart-bake-catch-up-{self.folder.name}",
                daemon=True,
            )
            self._catch_up_thread = worker
            worker.start()
            self._catch_up_guard.notify_all()

    def _catch_up_after_announcements(self) -> None:
        """Keep deriving until the last announced manifest state is installed."""
        while True:
            with self._catch_up_guard:
                while True:
                    if self._closing:
                        self._catch_up_thread = None
                        return
                    wait_for = self._catch_up_after - time.monotonic()
                    if self._catch_up_requested and wait_for <= 0:
                        self._catch_up_requested = False
                        break
                    self._catch_up_guard.wait(
                        timeout=max(0.0, wait_for) if self._catch_up_requested else None
                    )
            try:
                self.composer()
                with self._derive_guard:
                    current = self._run.manifest.fingerprint()
                    with self._guard:
                        settled = current == self._mark
            except Exception:
                log.exception("the announced bake at %s could not catch up", self.folder)
                with self._catch_up_guard:
                    if self._catch_up_requested and not self._closing:
                        continue
                    self._catch_up_thread = None
                    return
            with self._catch_up_guard:
                if self._closing:
                    self._catch_up_thread = None
                    return
                if not settled and not self._catch_up_requested:
                    self._catch_up_requested = True
                    self._catch_up_after = time.monotonic() + _BAKE_CATCH_UP_QUIET_S
                if self._catch_up_requested:
                    continue
                self._catch_up_thread = None
                return

    def _derive_and_install_the_composer(self) -> Composer:
        """Derive, patch and install one manifest state without overtaking."""
        mark = self._run.manifest.fingerprint()
        with self._guard:
            if mark == self._mark and self._held is not None:
                return self._held
            previous, before = self._held, dict(self._drawing)
            kept = dict(self._tiles)
        phases: dict[str, float] = {}
        watch = time.perf_counter
        marked = watch()
        self._run.manifest.committed_strict()
        phases["strict_read"] = (watch() - marked) * 1000
        baked_picture = self._shown is not None and bool(self._the_baked_levels())
        began = time.perf_counter()
        marked = watch()
        layout, profile = self._run._geometry()
        framed = (layout.revision, profile.profile_id)
        moved_frame = previous is not None and framed != self._frame_installed
        made, drawing, tiles = self._compose_the_snapshot(before, kept)
        phases["compose"] = (watch() - marked) * 1000
        marked = watch()
        dirtied: dict[int, set[tuple[int, int]]] | None = None
        dirty_moments: frozenset[int] | None = None
        if previous is not None and not moved_frame:
            changed_names = frozenset(
                one
                for one in before.keys() | drawing.keys()
                if before.get(one) != drawing.get(one)
                or (one in kept and one in tiles and kept[one].moments != tiles[one].moments)
            )
            stale = frozenset(
                copy.held_in
                for one in changed_names
                if one in kept and before.get(one) != drawing.get(one)
                for copy in kept[one].copies
            )
            dirtied = self._what_changed_dirtied(
                previous,
                made,
                {one: kept[one] for one in changed_names if one in kept},
                {one: tiles[one] for one in changed_names if one in drawing},
            )
            touched: set[int] = set()
            for one in changed_names:
                was = kept[one].moments if one in kept else frozenset()
                now = tiles[one].moments if one in tiles else frozenset()
                touched |= was | now if before.get(one) != drawing.get(one) else was ^ now
            dirty_moments = frozenset(touched)
            made.inherit_the_unchanged(previous, dirtied, stale=stale)
            # The piece index moves house with the slabs, patched only where
            # the change reached -- see Composer.inherit_the_index.
            made.inherit_the_index(
                previous,
                dirtied,
                changed_names,
                [(one, tiles[one]) for one in drawing if one in changed_names],
            )
        folded = self._run._folded
        phases["inherit"] = (watch() - marked) * 1000
        if baked_picture:
            current = {
                "events": folded,
                "tail": self._run._last_folded_revision,
                "layout": layout.revision,
            }
            self._keep_the_bake_true(
                made,
                None if moved_frame else dirtied,
                current,
                phases,
                moments=None if moved_frame else dirty_moments,
            )
        self.accounting["derives"] += 1
        self.accounting["last_derive_ms"] = (time.perf_counter() - began) * 1000
        self.accounting["last_positions"] = len(drawing)
        marked = watch()
        with self._guard:
            if (mark != self._mark or self._held is None) and (
                folded >= self._folded_installed or mark == self._run.manifest.fingerprint()
            ):
                stood_down = self._held
                self._mark, self._held = mark, made
                self._drawing, self._tiles = drawing, tiles
                self._folded_installed = folded
                self._frame_installed = framed
            else:
                stood_down = made
        phases["install"] = (watch() - marked) * 1000
        marked = watch()
        if stood_down is not None and stood_down is not self._held:
            stood_down.stop_warming()
        phases["stop_warming"] = (watch() - marked) * 1000
        marked = watch()
        if baked_picture:
            self._held.warm_from_the_baked(
                self._shown,
                frozenset(
                    one for one in self._the_baked_levels() if one < self._held.mosaic.levels
                ),
            )
        self._held.keep_the_coarse_levels_warm()
        phases["warm"] = (watch() - marked) * 1000
        self.accounting["last_phase_ms"] = phases
        return self._held

    def _the_baked_levels(self) -> tuple[int, ...]:
        """Which levels the declared picture keeps as baked files, if any."""
        if self._baked is None:
            held: tuple[int, ...] = ()
            described = self._shown / "zarr.json" if self._shown else None
            if described is not None and described.is_file():
                ours = (
                    json.loads(described.read_text(encoding="utf-8")).get("attributes") or {}
                ).get("zmart") or {}
                held = tuple(int(one) for one in ours.get("baked") or ())
            self._baked = held
        return self._baked

    def stamp_the_bake(
        self, store: str | Path | None = None, *, events: int, tail: int, layout: int
    ) -> None:
        """Write down exactly which manifest prefix the baked files absorbed."""
        where = Path(store).resolve() if store is not None else self._shown
        stamp = where / "baked.json"
        arriving = stamp.with_name("baked.json.stamping")
        arriving.write_text(
            json.dumps({"events": events, "tail": tail, "layout": layout}), encoding="utf-8"
        )
        _after_a_windows_reader(os.replace, arriving, stamp)

    def _the_stamp(self) -> dict | None:
        """The stamp's identity, or ``None`` when nothing can be trusted."""
        stamp = self._shown / "baked.json"
        if not stamp.is_file():
            return None
        try:
            held = json.loads(stamp.read_text(encoding="utf-8"))
            return {
                "events": int(held["events"]),
                "tail": int(held["tail"]),
                "layout": int(held["layout"]),
            }
        except (ValueError, KeyError, TypeError):
            return None

    def _keep_the_bake_true(
        self,
        made: Composer,
        dirtied: dict[int, set[tuple[int, int]]] | None,
        current: dict,
        phases: dict[str, float] | None = None,
        *,
        moments: frozenset[int] | None = None,
    ) -> None:
        """Patch the baked files the manifest's movement reached, then stamp."""
        if phases is None:
            phases = {}
        watch = time.perf_counter
        marked = watch()
        with self._bake_guard, _holding_the_bake_lock(self._shown):
            self.accounting["last_bake_arrays_opened"] = 0
            self.accounting["last_bake_stagings_built"] = 0
            self.accounting["last_bake_zarr_ops"] = 0
            self.accounting["last_bake_pieces_rehalved"] = 0
            self.accounting["last_bake_pieces_composed"] = 0
            self.accounting["last_bake_tile_reads"] = 0
            stamped = self._the_stamp()
            if stamped == current:
                self._stamp_installed = current
                phases["bake_scan"] = (watch() - marked) * 1000
                return
            if dirtied is None or stamped != self._stamp_installed:
                dirtied, moments = self._the_ground_the_bake_missed(made, current)
                if dirtied is None:
                    self._stamp_installed = current
                    phases["bake_scan"] = (watch() - marked) * 1000
                    return
            baked = self._the_baked_levels()
            phases["bake_scan"] = (watch() - marked) * 1000
            marked = watch()
            reads_before = made.tile_reads
            costs_before = dict(made.costs)
            moments_room, channels = made.mosaic.frame_room
            patched = (
                range(moments_room)
                if moments is None
                else sorted(one for one in moments if one < moments_room)
            )
            for level in sorted(one for one in baked if one < made.mosaic.levels):
                for row, column in sorted(dirtied.get(level, ())):
                    deep = made.grid(level)[0]
                    for moment in patched:
                        for channel in range(channels):
                            self.accounting["last_bake_pieces_composed"] += 1
                            for plane in range(deep):
                                self._replace_one_piece(
                                    made, level, plane, row, column, moment=moment, channel=channel
                                )
            for cost in ("read_ms", "build_ms", "encode_ms"):
                phases["bake_compose_" + cost[:-3]] = made.costs[cost] - costs_before[cost]
            self.accounting["last_bake_slabs_built"] = (
                made.costs["slabs_built"] - costs_before["slabs_built"]
            )
            self.accounting["last_bake_slabs_warm"] = (
                made.costs["slabs_warm"] - costs_before["slabs_warm"]
            )
            self.accounting["last_bake_tile_reads"] = made.tile_reads - reads_before
            phases["bake_compose"] = (watch() - marked) * 1000
            marked = watch()
            coarsest = made.mosaic.levels - 1
            reached = dirtied.get(coarsest, set())
            frames = (
                [()]
                if (moments_room, channels) == (1, 1)
                else [(moment, channel) for moment in patched for channel in range(channels)]
            )
            for level in sorted(one for one in baked if one >= made.mosaic.levels):
                reached = {(row // 2, column // 2) for row, column in reached}
                if reached:
                    self._rehalve_one_level(level, sorted(reached), frames)
            phases["bake_rehalve"] = (watch() - marked) * 1000
            marked = watch()
            self.stamp_the_bake(
                events=current["events"], tail=current["tail"], layout=current["layout"]
            )
            self._stamp_installed = current
            phases["bake_stamp"] = (watch() - marked) * 1000

    def _the_ground_the_bake_missed(
        self,
        made: Composer,
        current: dict,
    ) -> tuple[dict[int, set[tuple[int, int]]] | None, frozenset[int] | None]:
        """The footprints of every event the stamp cannot prove it absorbed."""
        events = self._run.manifest.events()
        stamped = self._the_stamp()
        everything = {
            level: {
                (row, column)
                for row in range(made.grid(level)[1])
                for column in range(made.grid(level)[2])
            }
            for level in range(made.mosaic.levels)
        }
        if stamped is None or stamped["layout"] != current["layout"]:
            return everything, None
        absorbed = stamped["events"]
        if absorbed > len(events) or absorbed < 0:
            return everything, None
        if absorbed and events[absorbed - 1].revision != stamped["tail"]:
            return everything, None
        if absorbed == len(events):
            return None, None
        touched: set[int] | None = set()
        for event in events[absorbed:]:
            if event.event_type == "position_replaced" or touched is None:
                touched = None
            else:
                touched.add(0 if event.timepoint is None else event.timepoint)
        moments = None if touched is None else frozenset(touched)
        missed = {event.position_id for event in events[absorbed:]}
        dirty: dict[int, set[tuple[int, int]]] = {}
        named = {
            tile.name.split(".")[0]: tile
            for tile in made.mosaic.tiles
            if tile.name.split(".")[0] in missed
        }
        for tile in named.values():
            for level in range(made.mosaic.levels):
                at = made.mosaic.lands_at(tile, level)
                held = tile.copies[level].shape
                reached = dirty.setdefault(level, set())
                for row in range(at[1] // self._piece, (at[1] + held[1] - 1) // self._piece + 1):
                    for column in range(
                        at[2] // self._piece, (at[2] + held[2] - 1) // self._piece + 1
                    ):
                        reached.add((row, column))
        return dirty, moments

    def _replace_one_piece(
        self,
        made: Composer,
        level: int,
        plane: int,
        row: int,
        column: int,
        *,
        moment: int = 0,
        channel: int = 0,
    ) -> None:
        """One baked chunk file made true, atomically, or removed if empty."""
        frame = (str(moment), str(channel)) if made.mosaic.frame_room != (1, 1) else ()
        inside = self._shown.joinpath(str(level), "c", *frame, str(plane), str(row))
        baked = inside / str(column)
        body = made.bytes_for(level, plane, row, column, moment=moment, channel=channel)
        if body is None:
            if baked.is_file():
                _after_a_windows_reader(os.unlink, baked)
            return
        inside.mkdir(parents=True, exist_ok=True)
        arriving = baked.with_name(f"{baked.name}.baking")
        arriving.write_bytes(body)
        _after_a_windows_reader(os.replace, arriving, baked)

    def _rehalve_one_level(
        self, level: int, pieces: list[tuple[int, int]], frames: list[tuple[int, ...]]
    ) -> None:
        """Recompute touched pieces of one extended level from the one below."""
        below = self._bake_below.get(level)
        if below is None:
            below = zarr.open_array(str(self._shown / str(level - 1)), mode="r")
            self._bake_below[level] = below
            self.accounting["last_bake_arrays_opened"] += 1
        staging = self._shown / f".patching-{level}"
        above = self._bake_staging.get(level)
        if above is None:
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir()
            shutil.copy2(self._shown / str(level) / "zarr.json", staging / "zarr.json")
            above = zarr.open_array(str(staging), mode="r+")
            self._bake_staging[level] = above
            self.accounting["last_bake_arrays_opened"] += 1
            self.accounting["last_bake_stagings_built"] += 1
        deep, height, width = above.shape[-3:]
        self.accounting["last_bake_pieces_rehalved"] += len(pieces)
        served_recipe = self._the_baked_recipe(level)
        source_recipe = self._the_baked_recipe(level - 1)
        if (
            served_recipe is not None
            and source_recipe is not None
            and served_recipe["shape"][0] == source_recipe["shape"][0]
        ):
            for row, column in pieces:
                self._rehalve_one_piece_directly(
                    level, staging, source_recipe, served_recipe, row, column
                )
        else:
            for row, column in pieces:
                top, left = row * self._piece, column * self._piece
                bottom = min(top + self._piece, height)
                right = min(left + self._piece, width)
                wanted = (bottom - top, right - left)
                for address in frames:
                    source = below[
                        (
                            *address,
                            slice(None),
                            slice(2 * top, min(2 * bottom, below.shape[-2])),
                            slice(2 * left, min(2 * right, below.shape[-1])),
                        )
                    ]
                    self.accounting["last_bake_zarr_ops"] += 1
                    evened = np.pad(
                        source,
                        (
                            (0, 0),
                            (0, 2 * wanted[0] - source.shape[-2]),
                            (0, 2 * wanted[1] - source.shape[-1]),
                        ),
                        mode="edge",
                    )
                    above[(*address, slice(None), slice(top, bottom), slice(left, right))] = (
                        evened.reshape(deep, wanted[0], 2, wanted[1], 2)
                        .mean(axis=(2, 4))
                        .round()
                        .astype(above.dtype)
                    )
                    self.accounting["last_bake_zarr_ops"] += 1
        planes = -(-deep // int(above.chunks[-3]))
        for row, column in pieces:
            for address in frames:
                parts = tuple(str(one) for one in address)
                for plane in range(planes):
                    staged = staging.joinpath("c", *parts, str(plane), str(row), str(column))
                    real = self._shown.joinpath(
                        str(level), "c", *parts, str(plane), str(row), str(column)
                    )
                    if staged.is_file():
                        real.parent.mkdir(parents=True, exist_ok=True)
                        _after_a_windows_reader(os.replace, staged, real)
                    elif real.is_file():
                        _after_a_windows_reader(os.unlink, real)

    def _the_baked_recipe(self, level: int) -> dict | None:
        """How one baked level's chunk files are encoded, or None to go general."""
        found = self._bake_recipes.get(level, False)
        if found is not False:
            return found
        recipe = None
        try:
            described = json.loads(
                (self._shown / str(level) / "zarr.json").read_text(encoding="utf-8")
            )
            codecs = described["codecs"]
            chunk = described["chunk_grid"]["configuration"]["chunk_shape"]
            if (
                len(codecs) == 2
                and codecs[0]["name"] == "bytes"
                and codecs[0]["configuration"]["endian"] == "little"
                and codecs[1]["name"] == "zstd"
                and not codecs[1]["configuration"].get("checksum")
                and len(chunk) == 3
                and chunk[0] == 1
                and chunk[1] == self._piece
                and chunk[2] == self._piece
            ):
                recipe = {
                    "shape": tuple(int(one) for one in described["shape"]),
                    "dtype": np.dtype(described["data_type"]).newbyteorder("<"),
                    "fill": described["fill_value"],
                    "zstd_level": int(codecs[1]["configuration"]["level"]),
                }
        except (OSError, KeyError, TypeError, ValueError):
            recipe = None
        self._bake_recipes[level] = recipe
        return recipe

    def _rehalve_one_piece_directly(
        self,
        level: int,
        staging: Path,
        source_recipe: dict,
        served_recipe: dict,
        row: int,
        column: int,
    ) -> None:
        """One piece of one extended level, re-halved file by file."""
        from numcodecs import Zstd

        deep, height, width = served_recipe["shape"]
        below_deep, below_height, below_width = source_recipe["shape"]
        piece = self._piece
        top, left = row * piece, column * piece
        wanted = (min(top + piece, height) - top, min(left + piece, width) - left)
        src_h = min(2 * (top + wanted[0]), below_height) - 2 * top
        src_w = min(2 * (left + wanted[1]), below_width) - 2 * left
        below_dir = self._shown / str(level - 1)
        source_dtype = source_recipe["dtype"]
        packing = Zstd(level=served_recipe["zstd_level"])
        unpacking = Zstd()
        for plane in range(deep):
            canvas = np.empty((src_h, src_w), dtype=source_dtype)
            for down in (0, 1):
                for across in (0, 1):
                    grid_row, grid_col = 2 * row + down, 2 * column + across
                    row0 = grid_row * piece
                    col0 = grid_col * piece
                    if row0 >= 2 * top + src_h or col0 >= 2 * left + src_w:
                        continue
                    rows = min(piece, 2 * top + src_h - row0)
                    cols = min(piece, 2 * left + src_w - col0)
                    held = below_dir / "c" / str(plane) / str(grid_row) / str(grid_col)
                    if held.is_file():
                        block = np.frombuffer(
                            unpacking.decode(held.read_bytes()), dtype=source_dtype
                        ).reshape(1, piece, piece)
                        part = block[0, :rows, :cols]
                    else:
                        part = np.full((rows, cols), source_recipe["fill"], source_dtype)
                    canvas[
                        row0 - 2 * top : row0 - 2 * top + rows,
                        col0 - 2 * left : col0 - 2 * left + cols,
                    ] = part
            evened = np.pad(
                canvas[None],
                ((0, 0), (0, 2 * wanted[0] - src_h), (0, 2 * wanted[1] - src_w)),
                mode="edge",
            )
            halved = (
                evened.reshape(1, wanted[0], 2, wanted[1], 2)
                .mean(axis=(2, 4))
                .round()
                .astype(served_recipe["dtype"])
            )
            buffer = np.full((1, piece, piece), served_recipe["fill"], served_recipe["dtype"])
            buffer[0, : wanted[0], : wanted[1]] = halved[0]
            if np.all(buffer == served_recipe["fill"]):
                continue
            staged = staging / "c" / str(plane) / str(row)
            staged.mkdir(parents=True, exist_ok=True)
            (staged / str(column)).write_bytes(
                packing.encode(np.ascontiguousarray(buffer).tobytes())
            )

    def _compose_the_snapshot(
        self,
        before: dict[str, int],
        kept: dict[str, Tile],
    ) -> tuple[Composer, dict[str, int], dict[str, Tile]]:
        """Derive tiles, frame and composer from the manifest's current truth."""
        published = self._run._published_units()
        order = self._run._positions_in_commit_order()
        layout, profile = self._run._geometry()

        current: dict[str, int] = {}
        for position_id, _moment, generation in published:
            if generation > current.get(position_id, -1):
                current[position_id] = generation
        gathered: dict[str, set[int]] = {}
        for position_id, moment, generation in published:
            if generation == current[position_id]:
                gathered.setdefault(position_id, set()).add(moment)
        moments_of = {position_id: frozenset(moments) for position_id, moments in gathered.items()}
        drawing = {
            position_id: current[position_id]
            for position_id in order
            if moments_of.get(position_id)
        }
        changed = [
            one
            for one, generation in drawing.items()
            if before.get(one) != generation
            or one not in kept
            or kept[one].moments != moments_of[one]
        ]
        read = 0
        fresh: dict[str, Tile] = {}
        if changed:
            corners = self._corners_of(layout, profile)
            for one in changed:
                if one not in corners:
                    raise ValueError(
                        f"the manifest publishes position {one!r}, but layout "
                        f"revision {layout.revision} does not place it. A "
                        "committed position the layout cannot place has no "
                        "corner to draw it at, so this is drift between the "
                        "run's records, refused rather than guessed around."
                    )
            if self._pattern is None or self._pattern_of != profile.profile_id:
                first = changed[0]
                self._pattern = self._the_pattern_read_and_checked(
                    first, drawing[first], corners[first]
                )
                self._pattern_of = profile.profile_id
                read = 1
            fresh = {
                one: _a_tile_stamped(
                    self._pattern,
                    self._the_store_of(one, drawing[one]),
                    corners[one],
                    moments=moments_of[one],
                )
                for one in changed
            }
        self.accounting["last_tiles_read"] = read
        tiles = {
            position_id: fresh.get(position_id) or kept[position_id] for position_id in drawing
        }
        ordered = [tiles[position_id] for position_id in drawing]
        self.accounting["last_snapshot_swept"] = 2 * len(published) + len(order) + 2 * len(drawing)
        return (
            Composer(TheWorldFrame(ordered, layout, profile, run=self.folder), piece=self._piece),
            drawing,
            tiles,
        )

    def _the_pattern_read_and_checked(
        self,
        position_id: str,
        generation: int,
        corner_um: tuple[float, float, float],
    ) -> Tile:
        """Read the one store that stands in for every other, and check it."""
        pattern = _read_one_tile(self._the_store_of(position_id, generation))
        for copy in pattern.copies:
            if copy.corner_um != corner_um:
                raise ValueError(
                    f"position {position_id!r} says its corner sits at "
                    f"{copy.corner_um} micrometres, but the layout places it "
                    f"at {corner_um}. The writer stamps a store's corner from "
                    "the layout's own placement, so a disagreement means the "
                    "run's records have drifted apart — and every other "
                    "position's corner is taken from the layout on that "
                    "store's word, so this is refused rather than drawn "
                    "somewhere quietly wrong."
                )
        return pattern

    def _corners_of(self, layout, profile) -> dict[str, tuple[float, float, float]]:
        """Where each planned position's first voxel sits, in micrometres."""
        named = (layout.revision, profile.profile_id)
        if self._corners_mark != named:
            voxel = tuple(float(profile.voxel_size.get(axis, 1.0)) for axis in ("z", "y", "x"))
            self._corners = {
                placement.position_id: tuple(
                    float(placement.origin.get(axis, 0.0)) * size
                    for axis, size in zip(("z", "y", "x"), voxel, strict=True)
                )
                for placement in layout.positions
            }
            self._corners_mark = named
        return self._corners

    def _what_changed_dirtied(
        self,
        previous: Composer,
        fresh: Composer,
        was_tiles: dict[str, Tile],
        now_tiles: dict[str, Tile],
    ) -> dict[int, set[tuple[int, int]]]:
        """Which pieces the manifest's movement reached, per level."""
        dirty: dict[int, set[tuple[int, int]]] = {}
        for composer, named in ((previous, was_tiles), (fresh, now_tiles)):
            for tile in named.values():
                for level in range(composer.mosaic.levels):
                    at = composer.mosaic.lands_at(tile, level)
                    held = tile.copies[level].shape
                    reached = dirty.setdefault(level, set())
                    for row in range(
                        at[1] // self._piece, (at[1] + held[1] - 1) // self._piece + 1
                    ):
                        for column in range(
                            at[2] // self._piece, (at[2] + held[2] - 1) // self._piece + 1
                        ):
                            reached.add((row, column))
        return dirty

    def close(self) -> None:
        """Let go of the held snapshot, closing whatever it holds open."""
        with self._catch_up_guard:
            self._closing = True
            catching_up = self._catch_up_thread
            self._catch_up_requested = False
            self._catch_up_guard.notify_all()
        if catching_up is not None and catching_up is not threading.current_thread():
            catching_up.join()
        with self._derive_guard, self._guard:
            held, self._held, self._mark = self._held, None, None
            self._drawing = {}
            self._bake_below = {}
            self._bake_staging = {}
            self._bake_recipes = {}
        if held is not None:
            held.close()

    def _the_store_of(self, position_id: str, generation: int) -> Path:
        """Where one published position's current pixels live."""
        if generation == 0:
            name = position_id
        else:
            name = f"{position_id}.generation-{generation}"
        return self.folder / "data" / "survey.ome.zarr" / name
