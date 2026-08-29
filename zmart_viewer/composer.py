"""Build a piece of the picture out of whichever tiles reach into it.

Nothing is stored: the tiles covering a requested piece are read at the
level being drawn, laid into one array, encoded, and handed back. Whole
slabs are kept so the next requests in depth are already answered, and
tiles are indexed per level, so cost per piece stays flat with survey
size. Measurements: docs/measured/.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import zarr

from .mosaic import Mosaic, the_frame_room_of, the_front_axes

PIECE = 512

SLABS_WEIGH_AT_MOST = 256 * 1024 * 1024

BLOCKS_WEIGH_AT_MOST = 1024 * 1024 * 1024

CODECS = [
    {"name": "bytes", "configuration": {"endian": "little"}},
    {"name": "zstd", "configuration": {"level": 0, "checksum": False}},
]

PINNED_SHARE = 0.01


def the_piece_address(inside: str) -> tuple[int, int, int, int, int, int] | None:
    """Read one piece address, or ``None`` when the path does not name one."""
    parts = inside.strip("/").split("/")
    if len(parts) not in (5, 7) or parts[1] != "c":
        return None
    if not all(one.isdecimal() for one in (parts[0], *parts[2:])):
        return None
    level = int(parts[0])
    tail = [int(one) for one in parts[2:]]
    if len(tail) == 3:
        return (level, 0, 0, *tail)
    return (level, *tail)


def _tile_has_the_frame(tile, level: int, moment: int, channel: int) -> bool:
    """Whether one tile holds anything at all for this (moment, channel)."""
    if tile.moments is not None and moment not in tile.moments:
        return False
    moments, channels = the_frame_room_of(tile.copies[level].outer_shape)
    return moment < moments and channel < channels


class MissingCommittedGround(RuntimeError):
    """A chunk the manifest promised is not on disk, and nothing may stand in."""


_WORKING_ON: Composer | None = None


def _start_working(written: dict, piece: int, budget: int) -> None:
    """Give this worker process its own composer, built from the ledger."""
    global _WORKING_ON
    from .mosaic import read_the_mosaic_as_written

    _WORKING_ON = Composer(
        read_the_mosaic_as_written(written),
        piece=piece,
        weighing_at_most=budget,
        blocks_weighing_at_most=budget,
        pinning=False,
    )


def _build_in_worker(
    level: int, plane: int, row: int, column: int, moment: int = 0, channel: int = 0
):
    return _WORKING_ON._slab_for(level, plane, row, column, moment, channel)


class Composer:
    """Answers for a picture that is never stored, out of the tiles that are."""

    def __init__(
        self,
        mosaic: Mosaic,
        piece: int = PIECE,
        weighing_at_most: int = SLABS_WEIGH_AT_MOST,
        blocks_weighing_at_most: int = BLOCKS_WEIGH_AT_MOST,
        workers: int = 1,
        pinning: bool = True,
    ) -> None:
        self.mosaic = mosaic
        self.piece = piece
        self._weighing_at_most = weighing_at_most
        self._workers = max(1, int(workers or 1))
        self._pinning = pinning
        self._pool: ProcessPoolExecutor | None = None
        self._pool_guard = threading.Lock()
        self.tile_reads = 0
        self.costs = {
            "read_ms": 0.0,
            "build_ms": 0.0,
            "encode_ms": 0.0,
            "slabs_built": 0,
            "slabs_warm": 0,
            "encodes": 0,
        }

        self._blocks: OrderedDict[tuple, np.ndarray] = OrderedDict()
        self._blocks_weigh = 0
        self._blocks_weighing_at_most = blocks_weighing_at_most
        self._block_guard = threading.Lock()

        self._slabs: OrderedDict[tuple[int, int, int, int], np.ndarray] = OrderedDict()
        self._weighs = 0
        self._guard = threading.Lock()

        self._pinned: dict[tuple[int, int, int, int], np.ndarray] = {}

        # How many requests are being answered right now, so the warmer can step
        # aside: it builds only while nobody is waiting on an answer.
        self._answering = 0
        self._warmer: threading.Thread | None = None
        self._stop_warming = threading.Event()
        # Which tiles fall in each piece, per resolution, built on first use.
        self._indexed: dict[int, dict[tuple[int, int], list]] = {}
        self._indexing = threading.Lock()
        self._pinned_levels: frozenset[int] | None = None
        self._warm_store: tuple[Path, frozenset[int]] | None = None
        self._blocks_prefilled = False

        self._encoders = threading.local()

        _ = zarr.create_array(
            store=zarr.storage.MemoryStore(),
            shape=(1, piece, piece),
            chunks=(1, piece, piece),
            dtype=mosaic.dtype,
            zarr_format=3,
            dimension_names=list(mosaic.axes),
            overwrite=True,
        )
        written = [dict(one) for one in _.metadata.to_dict()["codecs"]]
        if written != CODECS:
            raise RuntimeError(
                f"a piece would be encoded as {written} but the picture is declared "
                f"as {CODECS}. The browser would be handed bytes it cannot read, and "
                "nothing would report it — the window would simply be black."
            )

    def inherit_the_unchanged(
        self,
        donor: Composer,
        dirty: dict[int, set[tuple[int, int]]],
        stale: frozenset = frozenset(),
    ) -> None:
        """Carry a predecessor's warmth forward, minus what a commit touched."""
        with donor._block_guard:
            held_blocks = list(donor._blocks.items())
        with self._block_guard:
            for key, block in held_blocks:
                if key[0] in stale or key in self._blocks:
                    continue
                self._blocks[key] = block
                self._blocks_weigh += block.nbytes
            while len(self._blocks) > 1 and self._blocks_weigh > self._blocks_weighing_at_most:
                _, dropped = self._blocks.popitem(last=False)
                self._blocks_weigh -= dropped.nbytes

        with donor._guard:
            held_slabs = list(donor._slabs.items())
            held_pinned = list(donor._pinned.items())
        with self._guard:
            for key, slab in held_slabs:
                level, row, column = key[2], key[4], key[5]
                if (row, column) in dirty.get(level, ()) or key in self._slabs:
                    continue
                self._slabs[key] = slab
                self._weighs += slab.nbytes
            while self._slabs and self._weighs > self._weighing_at_most:
                _, dropped = self._slabs.popitem(last=False)
                self._weighs -= dropped.nbytes
            for key, slab in held_pinned:
                level, row, column = key[2], key[4], key[5]
                if (row, column) in dirty.get(level, ()):
                    continue
                self._pinned.setdefault(key, slab)

    def inherit_the_index(
        self,
        donor: Composer,
        dirty: dict[int, set[tuple[int, int]]],
        changed: frozenset[str],
        now_drawn: list[tuple[str, object]],
    ) -> None:
        """Carry the piece index forward, rebuilding only the dirtied pieces."""
        with donor._indexing:
            copied = {level: dict(index) for level, index in donor._indexed.items()}
        for level, index in copied.items():
            fresh = []
            for name, tile in now_drawn:
                at = self.mosaic.lands_at(tile, level)
                held = tile.copies[level].shape
                covered = {
                    (row, column)
                    for row in range(at[1] // self.piece, (at[1] + held[1] - 1) // self.piece + 1)
                    for column in range(
                        at[2] // self.piece, (at[2] + held[2] - 1) // self.piece + 1
                    )
                }
                fresh.append((name, tile, at, covered))
            for target in dirty.get(level, ()):
                rebuilt = []
                swapped: set[str] = set()
                for tile, at in index.get(target, ()):
                    name = tile.name.split(".")[0]
                    if name not in changed:
                        rebuilt.append((tile, at))
                        continue
                    for fresh_name, fresh_tile, fresh_at, covered in fresh:
                        if fresh_name == name and target in covered:
                            rebuilt.append((fresh_tile, fresh_at))
                            swapped.add(name)
                            break
                for fresh_name, fresh_tile, fresh_at, covered in fresh:
                    if fresh_name not in swapped and target in covered:
                        rebuilt.append((fresh_tile, fresh_at))
                if rebuilt:
                    index[target] = rebuilt
                else:
                    index.pop(target, None)
        with self._indexing:
            for level, index in copied.items():
                self._indexed.setdefault(level, index)

    # -- what the picture is -------------------------------------------------

    def grid(self, level: int) -> tuple[int, int, int]:
        """How many pieces deep, tall and wide the picture is at this resolution."""
        depth, height, width = self.mosaic.shape(level)
        return depth, -(-height // self.piece), -(-width // self.piece)

    def slab_depth(self, level: int) -> int:
        """How many planes one of the tiles' files holds at this resolution."""
        declared = getattr(self.mosaic, "slab_depths", None)
        if declared is not None:
            return int(declared[level])
        return int(self.mosaic.tiles[0].copies[level].chunks[0])

    # -- building ------------------------------------------------------------

    def _tiles_in_each_piece(self, level: int) -> dict[tuple[int, int], list]:
        """Which tiles fall in each piece of the picture, worked out once."""
        found = self._indexed.get(level)
        if found is not None:
            return found
        with self._indexing:
            found = self._indexed.get(level)
            if found is not None:
                return found
            index: dict[tuple[int, int], list] = {}
            for tile, at in self.mosaic.placements(level):
                held = tile.copies[level].shape
                for row in range(at[1] // self.piece, (at[1] + held[1] - 1) // self.piece + 1):
                    for column in range(
                        at[2] // self.piece, (at[2] + held[2] - 1) // self.piece + 1
                    ):
                        index.setdefault((row, column), []).append((tile, at))
            self._indexed[level] = index
            return index

    def _a_block_of(self, copy, at: tuple[int, int, int], outer: tuple[int, ...]) -> np.ndarray:
        """One whole stored block of a tile, decoded, kept for whoever needs it next."""
        key = (copy.held_in, outer, at)
        with self._block_guard:
            found = self._blocks.get(key)
            if found is not None:
                self._blocks.move_to_end(key)
                return found

        size = copy.chunks
        if copy.presence is not None and not copy.presence(outer + at):
            raise MissingCommittedGround(
                f"{copy.held_in} was published, but its block {outer + at} is "
                "not on disk. Refusing to invent fill for committed ground."
            )
        # Indexing the leading axes with plain integers collapses them, so
        # what comes back is three-dimensional either way.
        held = np.asarray(
            copy.array[
                outer
                + (
                    slice(at[0] * size[0], (at[0] + 1) * size[0]),
                    slice(at[1] * size[1], (at[1] + 1) * size[1]),
                    slice(at[2] * size[2], (at[2] + 1) * size[2]),
                )
            ]
        )

        with self._block_guard:
            if key not in self._blocks:
                self._blocks_weigh += held.nbytes
            self._blocks[key] = held
            self._blocks.move_to_end(key)
            while len(self._blocks) > 1 and self._blocks_weigh > self._blocks_weighing_at_most:
                _, dropped = self._blocks.popitem(last=False)
                self._blocks_weigh -= dropped.nbytes
        return held

    def _read_from(
        self, copy, low: tuple[int, int, int], high: tuple[int, int, int], outer: tuple[int, ...]
    ) -> np.ndarray:
        """A rectangle of one tile, assembled out of whichever blocks hold it."""
        self.tile_reads += 1
        reading_began = time.perf_counter()
        size = copy.chunks
        out = np.empty(tuple(high[axis] - low[axis] for axis in range(3)), copy.dtype)
        for bz in range(low[0] // size[0], (high[0] - 1) // size[0] + 1):
            for by in range(low[1] // size[1], (high[1] - 1) // size[1] + 1):
                for bx in range(low[2] // size[2], (high[2] - 1) // size[2] + 1):
                    block = self._a_block_of(copy, (bz, by, bx), outer)
                    began = (bz * size[0], by * size[1], bx * size[2])
                    from_ = tuple(max(low[axis], began[axis]) for axis in range(3))
                    to = tuple(
                        min(high[axis], began[axis] + block.shape[axis]) for axis in range(3)
                    )
                    if any(from_[axis] >= to[axis] for axis in range(3)):
                        continue
                    out[
                        from_[0] - low[0] : to[0] - low[0],
                        from_[1] - low[1] : to[1] - low[1],
                        from_[2] - low[2] : to[2] - low[2],
                    ] = block[
                        from_[0] - began[0] : to[0] - began[0],
                        from_[1] - began[1] : to[1] - began[1],
                        from_[2] - began[2] : to[2] - began[2],
                    ]
        self.costs["read_ms"] += (time.perf_counter() - reading_began) * 1000
        return out

    def _build_slab(
        self, level: int, plane: int, row: int, column: int, moment: int = 0, channel: int = 0
    ) -> np.ndarray:
        """Lay the tiles into one column of ground, for every plane of one file."""
        building_began = time.perf_counter()
        depth = self.slab_depth(level)
        low_z = (plane // depth) * depth
        deep, height, width = self.mosaic.shape(level)
        high_z = min(low_z + depth, deep)

        top, left = row * self.piece, column * self.piece
        bottom = min(top + self.piece, height)
        right = min(left + self.piece, width)

        slab = np.zeros((high_z - low_z, self.piece, self.piece), self.mosaic.dtype)
        for tile, at in self._tiles_in_each_piece(level).get((row, column), ()):
            if not _tile_has_the_frame(tile, level, moment, channel):
                continue
            copy = tile.copies[level]
            outer = tuple(
                moment if name == "t" else channel for name in the_front_axes(copy.outer_shape)
            )
            held = copy.shape
            if not (max(low_z, at[0]) < min(high_z, at[0] + held[0])):
                continue
            from_z, to_z = max(low_z, at[0]), min(high_z, at[0] + held[0])
            from_y, to_y = max(top, at[1]), min(bottom, at[1] + held[1])
            from_x, to_x = max(left, at[2]), min(right, at[2] + held[2])
            slab[
                from_z - low_z : to_z - low_z,
                from_y - top : to_y - top,
                from_x - left : to_x - left,
            ] = self._read_from(
                copy,
                (from_z - at[0], from_y - at[1], from_x - at[2]),
                (to_z - at[0], to_y - at[1], to_x - at[2]),
                outer,
            )
        self.costs["build_ms"] += (time.perf_counter() - building_began) * 1000
        self.costs["slabs_built"] += 1
        return slab

    def _slab_for(
        self, level: int, plane: int, row: int, column: int, moment: int = 0, channel: int = 0
    ) -> np.ndarray:
        """The slab holding this plane, built if it is not already to hand."""
        depth = self.slab_depth(level)
        key = (moment, channel, level, (plane // depth) * depth, row, column)
        pinned = self._pinning and level in self.pinned_levels
        with self._guard:
            found = self._pinned.get(key)
            if found is not None:
                self.costs["slabs_warm"] += 1
                return found
            found = self._slabs.get(key)
            if found is not None:
                self._slabs.move_to_end(key)
                self.costs["slabs_warm"] += 1
                return found
        built = self._built_wherever(level, plane, row, column, moment, channel)
        with self._guard:
            if pinned:
                self._pinned.setdefault(key, built)
                return self._pinned[key]
            if key not in self._slabs:
                self._weighs += built.nbytes
            self._slabs[key] = built
            self._slabs.move_to_end(key)
            while len(self._slabs) > 1 and self._weighs > self._weighing_at_most:
                _, dropped = self._slabs.popitem(last=False)
                self._weighs -= dropped.nbytes
        return built

    @property
    def pinned_levels(self) -> frozenset[int]:
        """The levels warmed ahead of asking and never let go."""
        if self._pinned_levels is not None:
            return self._pinned_levels
        full = 1
        for side in self.mosaic.shape(0):
            full *= side
        pinned = {self.mosaic.levels - 1}
        for level in range(self.mosaic.levels):
            voxels = 1
            for side in self.mosaic.shape(level):
                voxels *= side
            if voxels <= PINNED_SHARE * full:
                pinned.add(level)
        self._pinned_levels = frozenset(pinned)
        return self._pinned_levels

    @property
    def coarse_levels_are_warm(self) -> bool:
        """Whether every slab of every pinned level has been built and kept."""
        if self._warm_store is not None and not self._blocks_prefilled:
            return False
        wanted = 0
        for level in self.pinned_levels:
            deep, down, across = self.grid(level)
            slabs_deep = -(-self.mosaic.shape(level)[0] // self.slab_depth(level))
            wanted += slabs_deep * down * across
        with self._guard:
            return len(self._pinned) >= wanted

    def warm_the_coarse_levels(self, stop: threading.Event | None = None) -> None:
        """Build every slab of every pinned level, coarsest first."""
        for level in sorted(self.pinned_levels, reverse=True):
            baked = self._the_baked_level(level)
            depth = self.slab_depth(level)
            deep, down, across = self.grid(level)
            planes = self.mosaic.shape(level)[0]
            for low_z in range(0, planes, depth):
                for row in range(down):
                    for column in range(across):
                        if stop is not None and stop.is_set():
                            return
                        while self._answering:
                            time.sleep(0.005)
                        if baked is None:
                            self._slab_for(level, low_z, row, column)
                        else:
                            self._a_slab_read_back(baked, level, low_z, row, column)
        if self._warm_store is not None:
            for level in sorted(self.pinned_levels, reverse=True):
                for tile, _ in self.mosaic.placements(level):
                    copy = tile.copies[level]
                    blocks = [
                        -(-size // chunk)
                        for size, chunk in zip(copy.shape[-3:], copy.chunks, strict=True)
                    ]
                    for z in range(blocks[0]):
                        for y in range(blocks[1]):
                            for x in range(blocks[2]):
                                if stop is not None and stop.is_set():
                                    return
                                while self._answering:
                                    time.sleep(0.005)
                                try:
                                    self._a_block_of(copy, (z, y, x))
                                except Exception:
                                    continue
        self._blocks_prefilled = True

    def warm_from_the_baked(self, store: Path, levels: frozenset[int]) -> None:
        """Let the warm read these levels' slabs out of this baked folder."""
        self._warm_store = (Path(store), frozenset(levels))

    def _the_baked_level(self, level: int):
        """The baked array for one level, opened once per warm, or ``None``."""
        if self._warm_store is None or level not in self._warm_store[1]:
            return None
        try:
            return zarr.open_array(str(self._warm_store[0] / str(level)), mode="r")
        except Exception:
            # A bake that is missing or unreadable is not an error here --
            # the warm simply composes, exactly as an unbaked picture does.
            return None

    def _a_slab_read_back(self, baked, level: int, low_z: int, row: int, column: int) -> None:
        """One slab lifted from the baked files, installed as if built."""
        depth = self.slab_depth(level)
        key = (0, 0, level, low_z, row, column)
        with self._guard:
            if key in self._pinned or key in self._slabs:
                return
        deep, height, width = self.mosaic.shape(level)
        high_z = min(low_z + depth, deep)
        top, left = row * self.piece, column * self.piece
        lifted = np.asarray(
            baked[
                (0,) * (baked.ndim - 3)
                + (
                    slice(low_z, high_z),
                    slice(top, min(top + self.piece, height)),
                    slice(left, min(left + self.piece, width)),
                )
            ]
        )
        slab = np.zeros((high_z - low_z, self.piece, self.piece), self.mosaic.dtype)
        slab[:, : lifted.shape[1], : lifted.shape[2]] = lifted
        with self._guard:
            if self._pinning and level in self.pinned_levels:
                self._pinned.setdefault(key, slab)
            elif key not in self._slabs:
                self._slabs[key] = slab
                self._weighs += slab.nbytes

    @property
    def working_alone(self) -> bool:
        """Whether every slab is built in this process, the measured default."""
        return self._workers == 1

    def _built_wherever(
        self, level: int, plane: int, row: int, column: int, moment: int = 0, channel: int = 0
    ):
        """Build a slab here, or hand it to a worker process when they exist."""
        if self._workers == 1:
            return self._build_slab(level, plane, row, column, moment, channel)
        with self._pool_guard:
            if self._pool is None:
                from .mosaic import the_mosaic_written_down

                budget = SLABS_WEIGH_AT_MOST // self._workers
                self._pool = ProcessPoolExecutor(
                    max_workers=self._workers,
                    initializer=_start_working,
                    initargs=(
                        the_mosaic_written_down(self.mosaic),
                        self.piece,
                        max(budget, 64 * 1024 * 1024),
                    ),
                )
            pool = self._pool
        return pool.submit(_build_in_worker, level, plane, row, column, moment, channel).result()

    def close(self) -> None:
        """Stop the warmer and let the worker processes go."""
        self.stop_warming()
        with self._pool_guard:
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)

    def keep_the_coarse_levels_warm(self) -> None:
        """Run the warm pass in the background, once, letting requests through."""
        if self._warmer is not None and self._warmer.is_alive():
            return
        self._stop_warming.clear()
        self._warmer = threading.Thread(
            target=self.warm_the_coarse_levels,
            args=(self._stop_warming,),
            name="warm-the-coarse-levels",
            daemon=True,
        )
        self._warmer.start()

    def stop_warming(self) -> None:
        """Tell a running warm pass to stop after the slab it is on."""
        self._stop_warming.set()

    def _my_encoder(self):
        """This thread's own little array to encode a piece through."""
        held = getattr(self._encoders, "array", None)
        if held is None:
            held = zarr.create_array(
                store=zarr.storage.MemoryStore(),
                shape=(1, self.piece, self.piece),
                chunks=(1, self.piece, self.piece),
                dtype=self.mosaic.dtype,
                zarr_format=3,
                dimension_names=list(self.mosaic.axes),
                overwrite=True,
            )
            self._encoders.array = held
        return held

    def values_for(
        self, level: int, plane: int, row: int, column: int, moment: int = 0, channel: int = 0
    ):
        """One piece of the picture as its numbers, for measuring, not serving."""
        with self._guard:
            self._answering += 1
        try:
            covering = self._tiles_in_each_piece(level).get((row, column), ())
            if not any(_tile_has_the_frame(tile, level, moment, channel) for tile, _ in covering):
                return None
            slab = self._slab_for(level, plane, row, column, moment, channel)
            depth = self.slab_depth(level)
            piece = slab[plane - (plane // depth) * depth]
            return piece if piece.any() else None
        finally:
            with self._guard:
                self._answering -= 1

    def bytes_for(
        self, level: int, plane: int, row: int, column: int, moment: int = 0, channel: int = 0
    ) -> bytes | None:
        """One piece of the picture, encoded exactly as its description promises."""
        with self._guard:
            self._answering += 1
        try:
            covering = self._tiles_in_each_piece(level).get((row, column), ())
            if not any(_tile_has_the_frame(tile, level, moment, channel) for tile, _ in covering):
                return None
            slab = self._slab_for(level, plane, row, column, moment, channel)
            depth = self.slab_depth(level)
            piece = slab[plane - (plane // depth) * depth]
            if not piece.any():
                return None
            encoding_began = time.perf_counter()
            encoder = self._my_encoder()
            encoder[0] = piece
            body = bytes(encoder.store._store_dict["c/0/0/0"].to_bytes())
            self.costs["encode_ms"] += (time.perf_counter() - encoding_began) * 1000
            self.costs["encodes"] += 1
            return body
        finally:
            with self._guard:
                self._answering -= 1

    # -- what the picture says about itself ----------------------------------

    def group_json(self) -> bytes:
        """The built picture described as an ordinary OME-Zarr multiscale image."""
        base = self.mosaic.voxel_um(0)
        grown = self.mosaic.frame_room != (1, 1)
        datasets = []
        for level in range(self.mosaic.levels):
            voxel = self.mosaic.voxel_um(level)
            at = list(self.mosaic.corner_um)
            if self.mosaic.averaged:
                at = [at[axis] + (voxel[axis] - base[axis]) / 2 for axis in range(3)]
            datasets.append(
                {
                    "path": str(level),
                    "coordinateTransformations": [
                        {"type": "scale", "scale": ([1.0, 1.0] if grown else []) + list(voxel)},
                        {"type": "translation", "translation": ([0.0, 0.0] if grown else []) + at},
                    ],
                }
            )
        axes = (
            [
                {"name": "t", "type": "time", "unit": "second"},
                {"name": "c", "type": "channel"},
            ]
            if grown
            else []
        ) + [
            {"name": "z", "type": "space", "unit": "micrometer"},
            {"name": "y", "type": "space", "unit": "micrometer"},
            {"name": "x", "type": "space", "unit": "micrometer"},
        ]
        return json.dumps(
            {
                "attributes": {
                    "ome": {
                        "version": "0.5",
                        "multiscales": [
                            {
                                "name": "built",
                                "type": "mean" if self.mosaic.averaged else "nearest",
                                "axes": axes,
                                "datasets": datasets,
                            }
                        ],
                        **({"omero": self.mosaic.omero} if self.mosaic.omero else {}),
                    }
                },
                "zarr_format": 3,
                "node_type": "group",
            }
        ).encode()

    def array_json(self, level: int) -> bytes:
        """One resolution of the picture, in pieces of :data:`PIECE`."""
        depth, height, width = self.mosaic.shape(level)
        moments, channels = self.mosaic.frame_room
        grown = (moments, channels) != (1, 1)
        return json.dumps(
            {
                "zarr_format": 3,
                "node_type": "array",
                "shape": ([moments, channels] if grown else []) + [depth, height, width],
                "data_type": self.mosaic.dtype,
                "chunk_grid": {
                    "name": "regular",
                    "configuration": {
                        "chunk_shape": ([1, 1] if grown else []) + [1, self.piece, self.piece]
                    },
                },
                "chunk_key_encoding": {"name": "default", "configuration": {"separator": "/"}},
                "fill_value": 0,
                "codecs": CODECS,
                "attributes": {},
                "dimension_names": (["t", "c"] if grown else []) + list(self.mosaic.axes),
            }
        ).encode()
