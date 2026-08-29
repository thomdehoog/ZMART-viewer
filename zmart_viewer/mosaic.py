"""Read what a transfer is from the transfer itself.

Every tile carries its own stage corner; the arrangement follows from
those. Tiles land at fractional offsets, so the picture is built
(composer.py) rather than pointed at, and a tile is placed to the nearest
voxel of the level being drawn — below what the objective resolves.
"""

from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import zarr

IMAGE_SUFFIX = ".ome.zarr"

OURS_IN_THE_DESCRIPTION = "zmart"


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


def _how_a_resolution_is_stored(held_in: Path) -> tuple[tuple[int, ...], tuple[int, ...], str]:
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
    ours = described.get(OURS_IN_THE_DESCRIPTION)
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
