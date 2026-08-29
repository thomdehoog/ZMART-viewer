"""The arrangement of a transfer, and building pieces of its picture.

Every tile carries its own stage corner; the arrangement follows. Tiles
land at fractional offsets, so pieces are built — the tiles covering a
piece are read at the level being drawn, laid into one array, encoded —
never pointed at. Slabs are cached and tiles indexed per level, so cost
per piece stays flat with survey size. Measurements: docs/measured/.
"""

from __future__ import annotations

import json
import math
import threading
import time
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import zarr

IMAGE_SUFFIX = ".ome.zarr"

OURS = "zmart"


@dataclass
class Copy:
    """One resolution of one tile: how large it is, where it sits, and its pixels."""

    held_in: Path
    shape: tuple[int, int, int]
    chunks: tuple[int, int, int]
    dtype: str
    voxel_um: tuple[float, float, float]
    corner_um: tuple[float, float, float]
    outer_shape: tuple[int, ...] = ()
    presence: object | None = field(default=None, repr=False, compare=False)
    _opened: zarr.Array | None = field(default=None, repr=False)

    @property
    def array(self) -> zarr.Array:
        """The pixels, opened the first time anything asks for them."""
        if self._opened is None:
            self._opened = zarr.open_array(str(self.held_in), mode="r")

        return self._opened


@dataclass
class Tile:
    """One position of a transfer, with every copy of its picture it keeps."""

    name: str
    store: Path
    copies: list[Copy]
    turned: float = 0.0
    axes: tuple[str, ...] = ()
    moments: frozenset[int] | None = None

    def footprint(self, level: int, at: tuple[int, int, int]) -> tuple[int, int, int, int]:
        """The box this tile occupies across the specimen once it is turned."""
        held = self.copies[level].shape

        if not self.turned:
            return at[1], at[1] + held[1], at[2], at[2] + held[2]

        middle = (held[1] / 2, held[2] / 2)
        cos, sin = math.cos(self.turned), math.sin(self.turned)
        corners = []

        for down in (-middle[0], middle[0]):
            for across in (-middle[1], middle[1]):
                corners.append(
                    (
                        down * cos - across * sin + middle[0] + at[1],
                        down * sin + across * cos + middle[1] + at[2],
                    )
                )

        return (
            math.floor(min(one[0] for one in corners)),
            math.ceil(max(one[0] for one in corners)),
            math.floor(min(one[1] for one in corners)),
            math.ceil(max(one[1] for one in corners)),
        )

    @property
    def keeps(self) -> int:
        """How many copies of its picture this tile holds, counting full size."""
        return len(self.copies)


@dataclass
class Mosaic:
    """A whole transfer: its tiles, where each lands, and how large the picture is."""

    tiles: list[Tile]
    levels: int
    axes: tuple[str, ...]
    dtype: str
    corner_um: tuple[float, float, float] = field(default=(0.0, 0.0, 0.0))
    averaged: bool = False

    omero: dict | None = None

    _placed: dict[int, list[tuple[Tile, tuple[int, int, int]]]] = field(
        default_factory=dict, repr=False
    )
    _shape: dict[int, tuple[int, int, int]] = field(default_factory=dict, repr=False)
    _room: tuple[int, int] | None = field(default=None, repr=False)

    @property
    def frame_room(self) -> tuple[int, int]:
        """How many (moments, channels) the tiles' stores keep room for."""
        if self._room is None:
            found = (1, 1)

            for tile in self.tiles:
                for copy in tile.copies:
                    if copy.outer_shape:
                        found = the_frame_room_of(copy.outer_shape)
                        break
                else:
                    continue

                break

            self._room = found

        return self._room

    def voxel_um(self, level: int) -> tuple[float, float, float]:
        """How large one voxel of the built picture is at this resolution."""
        return self.tiles[0].copies[level].voxel_um

    def lands_at(self, tile: Tile, level: int) -> tuple[int, int, int]:
        """Where a tile's first voxel goes in this copy of the picture, in voxels."""
        copy = tile.copies[level]
        voxel = self.voxel_um(level)
        return tuple(
            math.floor((copy.corner_um[axis] - self.corner_um[axis]) / voxel[axis] + 0.5)
            for axis in range(3)
        )  # type: ignore[return-value]

    def placements(self, level: int) -> list[tuple[Tile, tuple[int, int, int]]]:
        """Every tile and where it lands at this resolution, worked out once."""
        found = self._placed.get(level)

        if found is None:
            found = [(tile, self.lands_at(tile, level)) for tile in self.tiles]
            self._placed[level] = found

        return found

    def shape(self, level: int) -> tuple[int, int, int]:
        """How large the built picture is at this resolution, in voxels."""
        found = self._shape.get(level)

        if found is None:
            placed = self.placements(level)
            found = tuple(
                max(at[axis] + tile.copies[level].shape[axis] for tile, at in placed)
                for axis in range(3)
            )
            self._shape[level] = found  # type: ignore[assignment]

        return found  # type: ignore[return-value]

    def reaching_into(
        self, level: int, low: tuple[int, int, int], high: tuple[int, int, int]
    ) -> list[tuple[Tile, tuple[int, int, int]]]:
        """Which tiles cover any part of this box, and where each of them lands."""
        return [
            (tile, at)
            for tile, at in self.placements(level)
            if all(
                max(low[axis], at[axis])
                < min(high[axis], at[axis] + tile.copies[level].shape[axis])
                for axis in range(3)
            )
        ]


def _the_description_of(store: Path) -> tuple[dict, str]:
    """What a store says about itself, and which generation of OME-Zarr wrote it."""
    newer = store / "zarr.json"

    if newer.is_file():
        held = json.loads(newer.read_text(encoding="utf-8"))
        attributes = held.get("attributes") or {}
        return (attributes.get("ome") or attributes), "0.5"

    older = store / ".zattrs"

    if older.is_file():
        return json.loads(older.read_text(encoding="utf-8")), "0.4"

    raise ValueError(
        f"{store} does not look like an OME-Zarr image: it holds neither a "
        "zarr.json nor a .zattrs. A picture can only be built over images, so "
        "check this is the folder the transfer wrote rather than the one above it."
    )


def _how_a_resolution_is_stored(
    held_in: Path,
) -> tuple[tuple[int, ...], tuple[int, ...], str]:
    """How large one resolution is, how it is chunked, and what a voxel holds."""
    for name in ("zarr.json", ".zarray"):
        described = held_in / name

        if described.is_file():
            held = json.loads(described.read_text(encoding="utf-8"))
            shape = tuple(int(n) for n in held["shape"])

            if held.get("zarr_format") == 2:
                chunks = tuple(int(n) for n in held["chunks"])
                # Version 2 spells the kind of number the way numpy does --
                # "<u2" -- and the rest of this works in the plain name.
                kind = np.dtype(held["dtype"]).name
            else:
                grid = (held.get("chunk_grid") or {}).get("configuration") or {}
                chunks = tuple(int(n) for n in grid["chunk_shape"])
                kind = str(held["data_type"])

            return shape, chunks, kind

    raise ValueError(
        f"{held_in} holds no array description, so how its picture is stored "
        "cannot be read. That resolution was probably never finished being "
        "written."
    )


def _read_one_tile(store: Path) -> Tile:
    """Every resolution a tile keeps, each with its own voxel size and corner."""
    described, _ = _the_description_of(store)
    multiscale = (described.get("multiscales") or [{}])[0]
    datasets = multiscale.get("datasets") or []
    axes = tuple(str(axis.get("name", "")) for axis in multiscale.get("axes") or ())

    if not datasets:
        raise ValueError(
            f"{store} says it keeps no copies of its picture, so there is nothing "
            "to build from. That is either not an OME-Zarr image or was never "
            "finished being written."
        )

    shared = [0.0, 0.0, 0.0]

    for transform in multiscale.get("coordinateTransformations") or []:
        if transform.get("type") == "translation":
            moved = [float(n) for n in transform["translation"]]
            shared = [shared[axis] + moved[-3 + axis] for axis in range(3)]

    copies = []

    for dataset in datasets:
        voxel, corner = None, list(shared)

        for transform in dataset.get("coordinateTransformations") or []:
            if transform.get("type") == "scale":
                scale = [float(n) for n in transform["scale"]]
                voxel = (scale[-3], scale[-2], scale[-1])

            if transform.get("type") == "translation":
                moved = [float(n) for n in transform["translation"]]
                corner = [corner[axis] + moved[-3 + axis] for axis in range(3)]

        if voxel is None:
            raise ValueError(
                f"{store} does not say how large a voxel of its {dataset['path']!r} "
                "copy is, so a picture built over it could not be drawn to scale."
            )

        held_in = store / str(dataset["path"])
        shape, chunks, kind = _how_a_resolution_is_stored(held_in)
        copies.append(
            Copy(
                held_in=held_in,
                shape=tuple(shape[-3:]),  # type: ignore[arg-type]
                chunks=tuple(chunks[-3:]),  # type: ignore[arg-type]
                dtype=kind,
                voxel_um=voxel,
                corner_um=(corner[0], corner[1], corner[2]),
                outer_shape=tuple(shape[:-3]),
            )
        )

    ours = described.get(OURS)
    turned = float((ours or {}).get("turned_radians") or 0.0)
    return Tile(name=store.name, store=store, copies=copies, axes=axes, turned=turned)


def _refuse_tiles_that_disagree(tiles: list[Tile]) -> str:
    """Stop a transfer whose tiles are not all the same kind of picture."""
    first = tiles[0]
    kind = first.copies[0].dtype

    for tile in tiles[1:]:
        theirs = tile.copies[0].dtype

        if theirs != kind:
            raise ValueError(
                f"{first.store.name} holds {kind} and {tile.store.name} holds "
                f"{theirs}. A built picture is one image and is declared as one "
                "kind of number, so a tile of another would be converted into it "
                "silently — which is a black square or a field of noise, with "
                "nothing anywhere to report it.\n\n"
                "This usually means two acquisitions have been gathered into one "
                "folder. Build a picture over each of them separately."
            )

        for level, (ours, other) in enumerate(zip(first.copies, tile.copies, strict=True)):
            if ours.voxel_um != other.voxel_um:
                raise ValueError(
                    f"{first.store.name} was taken with voxels of "
                    f"{ours.voxel_um} micrometres and {tile.store.name} with "
                    f"{other.voxel_um} (copy {level}). Those are two "
                    "magnifications, which makes them two acquisitions rather "
                    "than two tiles of one picture — and every tile's place is "
                    "worked out by dividing micrometres by this, so the rest of "
                    "the run would be drawn in the wrong place."
                )

    return kind


def the_front_axes(outer_shape: tuple[int, ...]) -> tuple[str, ...]:
    """Which axes a store keeps in front of (z, y, x), from how many it keeps."""
    return ("t", "c")[2 - len(outer_shape) :]


def the_frame_room_of(outer_shape: tuple[int, ...]) -> tuple[int, int]:
    """The (moments, channels) a store's front axes keep room for."""
    named = dict(zip(the_front_axes(outer_shape), outer_shape, strict=True))
    return (int(named.get("t", 1)), int(named.get("c", 1)))


PLATE_WELL_GAP = 1.08


def _the_plate_in(folder: Path) -> tuple[Path, dict] | None:
    """The one plate this folder holds, or None; ambiguity is refused."""
    try:
        described, _ = _the_description_of(folder)
    except ValueError:
        described = {}

    if isinstance(described.get("plate"), dict):
        return folder, described["plate"]

    stores = sorted(one for one in folder.glob("*.zarr") if one.is_dir())
    plates, plain = [], []

    for store in stores:
        try:
            described, _ = _the_description_of(store)
        except ValueError:
            continue

        if isinstance(described.get("plate"), dict):
            plates.append((store, described["plate"]))
        else:
            plain.append(store)

    if not plates:
        return None

    if len(plates) > 1:
        raise ValueError(
            f"{folder} holds {len(plates)} plates, and one picture lays out "
            "one plate. Put each plate in its own folder and open them one "
            "at a time."
        )

    if plain:
        raise ValueError(
            f"{folder} holds a plate and {len(plain)} loose image(s) beside "
            "it, and there is no declared way to lay the two out together. "
            "Put the plate in its own folder."
        )

    return plates[0]


def _read_the_plate(store: Path, plate: dict) -> list[Tile]:
    """Every field of every well, laid out from the plate's own indices."""
    wells = plate.get("wells") or []

    if not wells:
        raise ValueError(f"the plate at {store} declares no wells, so there is nothing to lay out.")

    row_names = [row.get("name") for row in plate.get("rows") or []]
    column_names = [column.get("name") for column in plate.get("columns") or []]
    read = []

    for well in wells:
        path = well["path"]
        row, column = well.get("rowIndex"), well.get("columnIndex")

        if row is None or column is None:
            # Some writers name only the well's path; the plate's own rows
            # and columns lists still say where "B/1" belongs.
            try:
                row_name, column_name = path.split("/")
                row = row_names.index(row_name)
                column = column_names.index(column_name)
            except ValueError:
                raise ValueError(
                    f"the well at {path} of the plate {store.name} carries "
                    "no row and column indices, and its path does not match "
                    "the plate's declared rows and columns -- there is no "
                    "way to know where it belongs."
                ) from None

        described, _ = _the_description_of(store / path)
        images = (described.get("well") or {}).get("images") or []
        fields = [_read_one_tile(store / path / image["path"]) for image in images]
        read.append((row, column, path.replace("/", ""), fields))

    sample = read[0][3][0].copies[0]
    field_h = sample.shape[-2] * sample.voxel_um[-2]
    field_w = sample.shape[-1] * sample.voxel_um[-1]
    across = math.ceil(math.sqrt(max(len(fields) for *_, fields in read)))

    def _the_well_laid_out(fields):
        corners = [tuple(float(one) for one in tile.copies[0].corner_um[-2:]) for tile in fields]

        if len(set(corners)) > 1:
            base_y = min(y for y, _ in corners)
            base_x = min(x for _, x in corners)
            offsets = [(y - base_y, x - base_x) for y, x in corners]
        else:
            offsets = [
                (down * field_h, along * field_w)
                for down, along in (divmod(number, across) for number in range(len(fields)))
            ]

        height = max(
            off_y + tile.copies[0].shape[-2] * tile.copies[0].voxel_um[-2]
            for (off_y, _), tile in zip(offsets, fields, strict=True)
        )
        width = max(
            off_x + tile.copies[0].shape[-1] * tile.copies[0].voxel_um[-1]
            for (_, off_x), tile in zip(offsets, fields, strict=True)
        )
        return offsets, height, width

    laid = [
        (row, column, well_name, fields, *_the_well_laid_out(fields))
        for row, column, well_name, fields in read
    ]
    # Wells step by the LARGEST well's extent plus a visible gap, per axis,
    # so every well fits its cell whichever way its fields were placed.
    pitch_y = max(height for *_, height, _ in laid) * PLATE_WELL_GAP
    pitch_x = max(width for *_, width in laid) * PLATE_WELL_GAP

    tiles = []

    for row, column, well_name, fields, offsets, _, _ in laid:
        for number, (tile, (off_y, off_x)) in enumerate(zip(fields, offsets, strict=True)):
            tile.name = f"{well_name}-{number}"
            move_y = row * pitch_y + off_y - tile.copies[0].corner_um[-2]
            move_x = column * pitch_x + off_x - tile.copies[0].corner_um[-1]

            for copy in tile.copies:
                z, y, x = copy.corner_um
                copy.corner_um = (z, y + move_y, x + move_x)

            tiles.append(tile)

    return tiles


def read_the_transfer(folder: str | Path) -> Mosaic:
    """Open a transfer and work out how its tiles fit together."""
    folder = Path(folder)
    plate = _the_plate_in(folder)

    if plate is not None:
        tiles = _read_the_plate(*plate)
    else:
        stores = sorted(
            one
            for one in folder.glob("*.zarr")
            if one.is_dir() and not one.name.endswith(".zmartview.zarr")
        )

        if not stores:
            raise ValueError(
                f"{folder} holds no OME-Zarr images, so there is nothing to "
                "build a picture from. A transfer is a container of one image "
                "per tile; this is probably the folder above it, or a single "
                "tile rather than the set."
            )

        with ThreadPoolExecutor(max_workers=min(32, (len(stores) + 3) // 4 or 1)) as pool:
            tiles = list(pool.map(_read_one_tile, stores))

    keeps = {tile.keeps for tile in tiles}

    if len(keeps) != 1:
        raise ValueError(
            f"the tiles in {folder} disagree about how many copies of their picture "
            f"they keep — {sorted(keeps)}. The built picture offers the copies every "
            "tile has, so a run written two different ways has to be looked at as "
            "two runs."
        )

    axes = tiles[0].axes

    for tile in tiles[1:]:
        others = tile.axes

        if others != axes:
            raise ValueError(
                f"{tiles[0].store.name} stores its picture as {', '.join(axes)} "
                f"and {tile.store.name} as {', '.join(others)}. The same bytes "
                "read under two different meanings is a specimen that looks "
                "strange for no reason anybody can point at, so this is refused "
                "rather than drawn."
            )

    front = tuple(axes[:-3])

    if tuple(axes[-3:]) != ("z", "y", "x") or front != ("t", "c")[2 - len(front) :]:
        raise ValueError(
            f"{tiles[0].store} stores its picture as {', '.join(axes)}. This "
            "builds over tiles of three spatial axes — depth, height and "
            "width — optionally behind a channel axis or a (t, c) pair. "
            "Anything else has no agreed meaning to draw."
        )

    rooms = {the_frame_room_of(tile.copies[0].outer_shape) for tile in tiles}

    if len(rooms) > 1:
        raise ValueError(
            f"the tiles of {folder} keep different (t, c) room: "
            f"{sorted(rooms)}. One picture has one room, so a tile with more "
            "moments or channels than the declaration would quietly lose "
            "them. This is refused rather than drawn half-true."
        )

    kind = _refuse_tiles_that_disagree(tiles)

    corner = tuple(min(tile.copies[0].corner_um[axis] for tile in tiles) for axis in range(3))
    said = None

    for tile in tiles:
        try:
            described, _ = _the_description_of(tile.store)
        except ValueError:
            continue

        if isinstance(described.get("omero"), dict):
            said = described["omero"]
            break

    return Mosaic(
        tiles=tiles,
        levels=tiles[0].keeps,
        axes=tuple(axes[-3:]),  # type: ignore[arg-type]
        dtype=kind,
        corner_um=corner,  # type: ignore[arg-type]
        omero=said,
    )


def the_mosaic_written_down(mosaic: Mosaic) -> dict:
    """The whole geometry of a picture, as one plain description."""
    return {
        "levels": mosaic.levels,
        "axes": list(mosaic.axes),
        "dtype": mosaic.dtype,
        "corner_um": list(mosaic.corner_um),
        # Only where the tiles said something. A picture whose tiles named no
        # channels writes no key, exactly as it did before this was carried.
        **({"omero": mosaic.omero} if mosaic.omero else {}),
        "tiles": [
            {
                "name": tile.name,
                "store": tile.store.as_posix(),
                "turned": tile.turned,
                "axes": list(tile.axes),
                "copies": [
                    {
                        "held_in": copy.held_in.as_posix(),
                        "shape": list(copy.shape),
                        "chunks": list(copy.chunks),
                        "dtype": copy.dtype,
                        "voxel_um": list(copy.voxel_um),
                        "corner_um": list(copy.corner_um),
                        **({"outer_shape": list(copy.outer_shape)} if copy.outer_shape else {}),
                    }
                    for copy in tile.copies
                ],
            }
            for tile in mosaic.tiles
        ],
    }


def read_the_mosaic_as_written(held: dict) -> Mosaic:
    """The mosaic back from its written-down geometry, touching no tile."""
    tiles = [
        Tile(
            name=one["name"],
            store=Path(one["store"]),
            turned=float(one.get("turned", 0.0)),
            axes=tuple(one["axes"]),
            copies=[
                Copy(
                    held_in=Path(copy["held_in"]),
                    shape=tuple(copy["shape"]),
                    chunks=tuple(copy["chunks"]),
                    dtype=copy["dtype"],
                    voxel_um=tuple(copy["voxel_um"]),
                    corner_um=tuple(copy["corner_um"]),
                    outer_shape=tuple(copy.get("outer_shape", ())),
                )
                for copy in one["copies"]
            ],
        )
        for one in held["tiles"]
    ]
    return Mosaic(
        tiles=tiles,
        levels=int(held["levels"]),
        axes=tuple(held["axes"]),
        dtype=held["dtype"],
        corner_um=tuple(held["corner_um"]),
        omero=held.get("omero"),
    )


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
        self,
        copy,
        low: tuple[int, int, int],
        high: tuple[int, int, int],
        outer: tuple[int, ...],
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
        self,
        level: int,
        plane: int,
        row: int,
        column: int,
        moment: int = 0,
        channel: int = 0,
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
        self,
        level: int,
        plane: int,
        row: int,
        column: int,
        moment: int = 0,
        channel: int = 0,
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
        self,
        level: int,
        plane: int,
        row: int,
        column: int,
        moment: int = 0,
        channel: int = 0,
    ):
        """Build a slab here, or hand it to a worker process when they exist."""
        if self._workers == 1:
            return self._build_slab(level, plane, row, column, moment, channel)

        with self._pool_guard:
            if self._pool is None:
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
        self,
        level: int,
        plane: int,
        row: int,
        column: int,
        moment: int = 0,
        channel: int = 0,
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
        self,
        level: int,
        plane: int,
        row: int,
        column: int,
        moment: int = 0,
        channel: int = 0,
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
                        {
                            "type": "scale",
                            "scale": ([1.0, 1.0] if grown else []) + list(voxel),
                        },
                        {
                            "type": "translation",
                            "translation": ([0.0, 0.0] if grown else []) + at,
                        },
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
                "chunk_key_encoding": {
                    "name": "default",
                    "configuration": {"separator": "/"},
                },
                "fill_value": 0,
                "codecs": CODECS,
                "attributes": {},
                "dimension_names": (["t", "c"] if grown else []) + list(self.mosaic.axes),
            }
        ).encode()
