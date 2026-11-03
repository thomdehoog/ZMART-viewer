"""Show an OME-TIFF file as if it were an OME-Zarr image, without converting it.

Neuroglancer, which draws the pictures in the viewer, only reads OME-Zarr. Many
microscopes and programs write OME-TIFF instead. This module lets the viewer
show an OME-TIFF file as it is: the file is never copied, changed or converted.

How it works, in two parts:

1. **A description, written once.** When an OME-TIFF is opened, the viewer
   writes a small OME-Zarr *description* into its own session folder (never
   beside your data). It says how large the image is, which channels it has,
   how large a pixel is in micrometres, and which smaller copies of the image
   (the pyramid levels) exist. It holds no pixels, so it is only a few
   kilobytes.
2. **Pixels, read when asked for.** When Neuroglancer asks for one piece of the
   picture, the server reads just that region from the TIFF file, at the right
   pyramid level, and sends it. Nothing else of the file is read, so a very
   large file opens as easily as a small one.

What works well, and what does not yet:

- A **tiled, pyramidal** OME-TIFF (as Bio-Formats, QuPath and ``tifffile``
  write them) is the best case. Each piece is one small region of one tile, and
  zooming out reads the file's own smaller copies.
- A TIFF **without a pyramid** opens too, but zooming far out on a large image
  is slow, because every zoomed-out piece has to be read at full size.
- TIFF stores each plane separately, so every piece is one plane deep. Flat
  images feel the same as OME-Zarr; 3D volume views read more pieces.
- This prototype opens the **first image** in the file. Files holding several
  positions as separate images show only the first one.
- RGB colour images (brightfield, histology) are shown as three channels.
"""

from __future__ import annotations

import json
import logging
import threading
import xml.etree.ElementTree as ElementTree
from collections import OrderedDict
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

#: The file endings an OME-TIFF is recognised by.
OME_TIFF_ENDINGS = (".ome.tif", ".ome.tiff", ".ome.btf", ".ome.tf2", ".ome.tf8")

#: The key inside the description that marks it as standing in for a TIFF.
TIFF_FROM = "tiff_from"

#: The namespace of the note ZMART leaves inside each OME-TIFF position it
#: writes. Other programs skip a note they do not know, so the file stays an
#: ordinary OME-TIFF for Fiji, napari or QuPath. The note holds what OME-XML has
#: no place for: ``zmart_microscopy`` (how the planes were taken, as JSON),
#: ``omero`` (each channel's name, colour and the brightness window it first
#: opens with, as JSON, as an OME-Zarr position says it),
#: ``pyramid`` (``mean`` when each smaller copy averages 2 x 2 pixels of the one
#: before) and ``position_origin`` (``corner``: the stage position is that of
#: the image's corner, not its middle).
ZMART_NOTE = "zmart/position"

#: How many OME-TIFF files a folder's picture keeps open at the same time.
#: Each open file costs about 30 kB of memory and one of the computer's open
#: files, and Windows in particular allows only a few thousand of those. The
#: files used least recently are closed first, and reopening one costs a few
#: milliseconds. Set ZMART_TIFFS_KEPT_OPEN to change it.
TIFFS_KEPT_OPEN = 256

#: The size of a piece when the TIFF itself is not stored in tiles.
PIECE = 512

#: How hard each piece is compressed before it is sent to the browser. Level 1
#: is about seven times faster than zstd's usual level and makes pieces only a
#: little larger (two thirds of the raw size, against just over half). On the
#: viewer's own computer, speed matters far more than size.
_ZSTD_LEVEL = 1

#: The kinds of pixel Neuroglancer can draw.
_DRAWABLE = ("uint8", "uint16", "uint32", "int8", "int16", "int32", "float32")

#: Neuroglancer's own default colours, used when the file names none.
_COLOURS = ("FFFFFF", "00FF00", "FF00FF", "00FFFF", "FFFF00", "FF0000", "0000FF")

#: How OME-XML spells length units, turned into what OME-Zarr expects.
_UNITS = {
    "µm": "micrometer",
    "um": "micrometer",
    "nm": "nanometer",
    "mm": "millimeter",
    "cm": "centimeter",
    "m": "meter",
    "Å": "angstrom",
}


class CannotShow(ValueError):
    """A file this module understands, but cannot show; the message says why."""


class NotPlaced(CannotShow):
    """An OME-TIFF that does not say where the stage stood, so it cannot be a position."""


def is_an_ome_tiff(path: str | Path) -> bool:
    """True when ``path`` is a file whose name says it is an OME-TIFF."""
    path = Path(path)
    return path.is_file() and path.name.lower().endswith(OME_TIFF_ENDINGS)


def the_bare_name(path: Path) -> str:
    """The file's name without its OME-TIFF ending: ``cells.ome.tif`` gives ``cells``."""
    name = path.name

    for ending in OME_TIFF_ENDINGS:
        if name.lower().endswith(ending):
            return name[: -len(ending)]

    return path.stem


class TiffPicture:
    """One OME-TIFF file, opened once and read piece by piece.

    The picture is presented with the five axes OME-Zarr uses, in this order:
    time, channel, Z, Y, X. Axes the file does not have are given a size of one.

    The server answers several requests at the same time, so several pieces
    may be read at once. ``tifffile`` takes care of that itself: it lets only
    one request at a time move through the file, and decodes the pieces side
    by side. That was checked with sixteen readers at once.
    """

    def __init__(self, path: str | Path) -> None:
        import tifffile  # deferred: only needed when an OME-TIFF is opened

        self.path = Path(path)
        self._lock = threading.Lock()
        self._file = tifffile.TiffFile(self.path)

        try:
            series = self._file.series[0]
            self.axes_in_file = series.axes
            self.dtype = np.dtype(series.dtype)
            self._series = series
            # The general reader of each level, made only when first needed:
            # a tiled file is read tile by tile and never needs one, and making
            # them costs more than opening the file itself.
            self._arrays: list = [None] * len(series.levels)
            self._order = _the_order_of(series.axes)
            self.shapes = [self._five_axes(level.shape) for level in series.levels]
            self.pieces = [_the_piece_of(level) for level in series.levels]
            self._metadata = None
            self._levels = list(series.levels)
            self._direct = [_can_be_read_tile_by_tile(level, series.axes) for level in self._levels]
            # Several pieces may be read at once; this makes tifffile take turns
            # on the file itself while the decoding runs side by side.
            self._file.filehandle.set_lock(True)
        except Exception:
            self._file.close()
            raise

        if self.dtype.name not in _DRAWABLE:
            self._file.close()
            raise CannotShow(
                f"{self.path.name} holds pixels of type {self.dtype}, which the viewer "
                "cannot draw. It draws whole numbers of 8, 16 or 32 bits and 32-bit "
                "decimal numbers."
            )

    # -- what the picture is ---------------------------------------------------

    @property
    def levels(self) -> int:
        """How many copies of the picture the file holds, counting full size."""
        return len(self._levels)

    @property
    def metadata(self) -> dict:
        """What the file's OME-XML says: pixel size, channels, stage position."""
        if self._metadata is None:
            self._metadata = _what_the_ome_xml_says(self._file.ome_metadata, self.shapes[0])

        return self._metadata

    def _general_reader(self, level: int):
        """tifffile's general reader for one level, which reads any layout."""
        import zarr

        with self._lock:
            if self._arrays[level] is None:
                self._arrays[level] = zarr.open_array(self._series.aszarr(level=level), mode="r")

            return self._arrays[level]

    def _five_axes(self, shape: tuple[int, ...]) -> tuple[int, int, int, int, int]:
        """A shape in the file's own axis order, given as (time, channel, Z, Y, X)."""
        sizes = dict(zip(self.axes_in_file, shape, strict=True))

        if "S" in sizes:
            sizes["C"] = sizes.pop("S")

        return tuple(sizes.get(axis, 1) for axis in "TCZYX")  # type: ignore[return-value]

    def grid(self, level: int) -> tuple[int, int]:
        """How many pieces down and across one plane of this level is cut into."""
        _, _, _, height, width = self.shapes[level]
        down, across = self.pieces[level]
        return -(-height // down), -(-width // across)

    # -- reading pixels -------------------------------------------------------

    def values_for(
        self, level: int, moment: int, channel: int, plane: int, row: int, column: int
    ) -> np.ndarray | None:
        """One piece of one plane as numbers, padded to the full piece size.

        Pieces at the right and bottom edges of the picture are partly outside
        it; the part outside is filled with zeros, which is what OME-Zarr expects.
        Returns ``None`` when the address lies outside the picture.
        """
        if not 0 <= level < self.levels:
            return None

        moments, channels, depth, height, width = self.shapes[level]
        down, across = self.pieces[level]
        rows, columns = self.grid(level)

        if not (
            0 <= moment < moments
            and 0 <= channel < channels
            and 0 <= plane < depth
            and 0 <= row < rows
            and 0 <= column < columns
        ):
            return None

        top, left = row * down, column * across
        bottom, right = min(height, top + down), min(width, left + across)
        wanted = {"T": moment, "C": channel, "Z": plane}

        if "S" in self.axes_in_file:
            wanted["S"] = wanted.pop("C")

        where = tuple(
            slice(top, bottom)
            if axis == "Y"
            else slice(left, right)
            if axis == "X"
            else wanted.get(axis, 0)
            for axis in self.axes_in_file
        )

        if self._direct[level]:
            return self._one_tile(level, wanted, row, column, bottom - top, right - left)

        region = np.asarray(self._general_reader(level)[where])

        if self._order:
            # Y and X are the only axes left; the file may hold them as X, Y.
            region = region.transpose(self._order)

        piece = np.zeros((down, across), dtype=self.dtype)
        piece[: bottom - top, : right - left] = region
        return piece

    def _one_tile(self, level: int, wanted: dict, row: int, column: int, rows: int, columns: int):
        """One piece read as the TIFF's own tile: the quickest way, used when the file is tiled.

        A piece and a tile are then the same region, so the tile's bytes are
        read from the file and decoded, and nothing else is touched.
        """
        stored = self._levels[level]
        key = stored.keyframe
        leading = [axis for axis in self.axes_in_file if axis not in "YXS"]
        sizes = dict(zip(self.axes_in_file, stored.shape, strict=True))
        plane = (
            int(
                np.ravel_multi_index(
                    tuple(wanted.get(axis, 0) for axis in leading),
                    tuple(sizes[axis] for axis in leading),
                )
            )
            if leading
            else 0
        )
        page = stored.pages[plane]
        across = -(-key.imagewidth // key.tilewidth)
        index = row * across + column
        piece = np.zeros(self.pieces[level], dtype=self.dtype)

        if page is None or not page.databytecounts[index]:
            return piece  # A tile the writer left out holds nothing but zeros.

        handle = self._file.filehandle
        with handle.lock:
            handle.seek(page.dataoffsets[index])
            raw = handle.read(page.databytecounts[index])

        decoded, _, _ = key.decode(raw, index)
        sample = wanted.get("S", 0)
        piece[:rows, :columns] = decoded[0, :rows, :columns, sample]
        return piece

    def block(self, level: int, moment: int, channel: int, planes, rows, columns) -> np.ndarray:
        """A block of one level as numbers, shaped (planes, rows, columns).

        This is how the picture built over a folder of positions reads a TIFF.
        It asks for one stored piece at a time, so the quick tile-by-tile
        route is used whenever the file is tiled. The block is cut off at the
        picture's edge, as an OME-Zarr array would be.
        """
        _, _, depth, height, width = self.shapes[level]
        planes = range(*planes.indices(depth))
        top, bottom, _ = rows.indices(height)
        left, right, _ = columns.indices(width)
        down, across = self.pieces[level]
        lined_up = (
            top % down == 0
            and left % across == 0
            and bottom - top <= down
            and right - left <= across
        )
        out = np.zeros((len(planes), max(0, bottom - top), max(0, right - left)), self.dtype)

        for at, plane in enumerate(planes):
            if lined_up:
                piece = self.values_for(level, moment, channel, plane, top // down, left // across)
                out[at] = piece[: bottom - top, : right - left]
            else:
                out[at] = self._region(level, moment, channel, plane, top, bottom, left, right)

        return out

    def _region(self, level, moment, channel, plane, top, bottom, left, right) -> np.ndarray:
        """Any rectangle of one plane, read through tifffile's general reader."""
        wanted = {"T": moment, "C": channel, "Z": plane}

        if "S" in self.axes_in_file:
            wanted["S"] = wanted.pop("C")

        where = tuple(
            slice(top, bottom)
            if axis == "Y"
            else slice(left, right)
            if axis == "X"
            else wanted.get(axis, 0)
            for axis in self.axes_in_file
        )
        region = np.asarray(self._general_reader(level)[where])
        return region.transpose(self._order) if self._order else region

    def bytes_for(
        self, level: int, moment: int, channel: int, plane: int, row: int, column: int
    ) -> bytes | None:
        """One piece, encoded exactly as the description promises Neuroglancer."""
        piece = self.values_for(level, moment, channel, plane, row, column)

        if piece is None:
            return None

        return _encode(piece)

    def a_sample(self, channel: int = 0) -> np.ndarray | None:
        """Some pixels from the middle of the smallest copy, for setting brightness."""
        level = self.levels - 1
        _, channels, depth, _, _ = self.shapes[level]

        if not 0 <= channel < channels:
            return None

        rows, columns = self.grid(level)
        return self.values_for(level, 0, channel, depth // 2, rows // 2, columns // 2)

    def _a_first_window(self, channel: int) -> dict:
        """A starting brightness range for one channel, from a sample of its pixels.

        Without this, a 16-bit camera image that only uses values up to a few
        thousand would open almost black. The darkest and brightest 0.1 % of the
        sample are left out, so a few hot pixels do not set the range. The
        operator can change it afterwards, as for any other picture.
        """
        try:
            sample = self.a_sample(channel)
        except Exception:
            log.warning("no brightness sample could be read from %s", self.path, exc_info=True)
            sample = None

        if sample is None or not sample.size:
            return {
                "start": 0.0,
                "end": float(np.iinfo(self.dtype).max) if self.dtype.kind in "ui" else 1.0,
            }

        low, high = np.percentile(sample.astype(np.float64), [0.1, 99.9])

        if high <= low:
            high = low + 1.0

        return {"start": float(low), "end": float(high)}

    def close(self) -> None:
        """Let go of the file, so it can be moved or deleted again."""
        with self._lock:
            self._file.close()

    # -- the description written for Neuroglancer -------------------------------

    def group_json(self, *, measured: bool = True) -> dict:
        """The picture described as an ordinary OME-Zarr 0.5 multiscale image.

        With ``measured`` left on, a few pixels of every channel are read to
        set a starting brightness.
        """
        said = self.metadata
        _, _, _, base_height, base_width = self.shapes[0]
        datasets = []

        for level, (_, _, _, height, width) in enumerate(self.shapes):
            # The smaller copies keep every Z plane; only Y and X shrink.
            down = base_height / height
            across = base_width / width
            datasets.append(
                {
                    "path": str(level),
                    "coordinateTransformations": [
                        {
                            "type": "scale",
                            "scale": [
                                said["time_step"],
                                1.0,
                                said["voxel"][0],
                                said["voxel"][1] * down,
                                said["voxel"][2] * across,
                            ],
                        },
                        {
                            "type": "translation",
                            "translation": [
                                0.0,
                                0.0,
                                0.0,
                                said["voxel"][1] * (down - 1) / 2,
                                said["voxel"][2] * (across - 1) / 2,
                            ],
                        },
                    ],
                }
            )

        multiscale = {"name": the_bare_name(self.path), "axes": self._axes(), "datasets": datasets}

        return {
            "zarr_format": 3,
            "node_type": "group",
            "attributes": {
                "ome": {
                    "version": "0.5",
                    "multiscales": [multiscale],
                    "omero": self._omero(measured=measured),
                },
            },
        }

    def as_a_position(self) -> dict:
        """The picture described as one position of a run, ready to be composed.

        The description is laid out exactly as ZMART lays out its OME-Zarr
        positions, so the viewer composes both formats the same way: every
        smaller copy starts at the same corner, the stage position, and spans a
        whole number of full-size pixels when it was made by halving. What
        ZMART's note in the file says (:data:`ZMART_NOTE`) is carried along.

        Raises :class:`CannotShow` when the file does not say where the stage
        stood: placed at zero, it would lie on top of the other positions and
        look plausible while being wrong.
        """
        said = self.metadata

        if said["stage_um"] is None:
            raise NotPlaced(
                f"{self.path.name} does not say where the stage stood, so it cannot be "
                "placed among the other positions. Open it on its own to look at it."
            )

        per_unit = [_MICROMETRES_IN.get(unit, 1.0) for unit in said["units"]]
        corner = [said["stage_um"][axis] / per_unit[axis] for axis in range(3)]
        _, _, _, base_height, base_width = self.shapes[0]
        datasets = []

        for level, (_, _, _, height, width) in enumerate(self.shapes):
            down, across = _the_step(base_height, height), _the_step(base_width, width)
            datasets.append(
                {
                    "path": str(level),
                    "coordinateTransformations": [
                        {
                            "type": "scale",
                            "scale": [
                                said["time_step"],
                                1.0,
                                said["voxel"][0],
                                said["voxel"][1] * down,
                                said["voxel"][2] * across,
                            ],
                        },
                        {"type": "translation", "translation": [0.0, 0.0, *corner]},
                    ],
                }
            )

        multiscale = {"name": the_bare_name(self.path), "axes": self._axes(), "datasets": datasets}
        note = said["zmart"]

        if note.get("pyramid") == "mean":
            multiscale["type"] = "mean"

        described = {"multiscales": [multiscale], "omero": self._omero(measured=False)}

        try:
            # The channels as ZMART wrote them, brightness window included:
            # without it a run opens stretched over the camera's whole range,
            # nearly black.
            if note.get("omero"):
                described["omero"] = json.loads(note["omero"])
            if note.get("zmart_microscopy"):
                described["zmart_microscopy"] = json.loads(note["zmart_microscopy"])
        except ValueError:
            log.warning("ZMART's note in %s could not be read; it is left out", self.path.name)

        return described

    def _axes(self) -> list[dict]:
        """The five axes, named and with their units, as OME-Zarr lists them."""
        units = self.metadata["units"]
        return [
            {"name": "t", "type": "time", "unit": self.metadata["time_unit"]},
            {"name": "c", "type": "channel"},
            {"name": "z", "type": "space", "unit": units[0]},
            {"name": "y", "type": "space", "unit": units[1]},
            {"name": "x", "type": "space", "unit": units[2]},
        ]

    def array_json(self, level: int) -> dict:
        """One pyramid level, cut into pieces of one plane each."""
        down, across = self.pieces[level]
        return {
            "zarr_format": 3,
            "node_type": "array",
            "shape": list(self.shapes[level]),
            "data_type": self.dtype.name,
            "chunk_grid": {
                "name": "regular",
                "configuration": {"chunk_shape": [1, 1, 1, down, across]},
            },
            "chunk_key_encoding": {"name": "default", "configuration": {"separator": "/"}},
            "fill_value": 0,
            "codecs": _codecs_for(self.dtype),
            "attributes": {},
            "dimension_names": ["t", "c", "z", "y", "x"],
        }

    def _omero(self, *, measured: bool = True) -> dict:
        """The channels' names and colours, and a first guess at their brightness."""
        _, channels, _, _, _ = self.shapes[0]
        names = self.metadata["channel_names"]
        colours = self.metadata["channel_colours"]

        if self.dtype.kind in "ui":
            highest = float(np.iinfo(self.dtype).max)
        else:
            highest = 1.0

        return {
            "channels": [
                {
                    "label": names[index] if index < len(names) else f"Channel {index}",
                    "color": colours[index]
                    if index < len(colours) and colours[index]
                    else _COLOURS[index % len(_COLOURS)]
                    if channels > 1
                    else "FFFFFF",
                    "active": True,
                    "window": {
                        "min": 0.0,
                        "max": highest,
                        **(
                            self._a_first_window(index)
                            if measured
                            else {"start": 0.0, "end": highest}
                        ),
                    },
                }
                for index in range(channels)
            ]
        }


def declare_a_tiff_picture(where: str | Path, tiff: str | Path) -> Path:
    """Write the description that lets the viewer show this OME-TIFF.

    ``where`` is the viewer's own folder for this session; the description is
    written there as ``<name>.zmartview.zarr``, and nothing is written beside
    the TIFF. Returns the folder written. Raises ``ValueError`` when the file
    cannot be shown, with the reason in plain words.
    """
    tiff = Path(tiff).resolve()

    try:
        picture = TiffPicture(tiff)
    except CannotShow:
        raise
    except Exception as why:
        # Most often the file is damaged, was not fully copied, or is not
        # really a TIFF although its name says so.
        raise CannotShow(
            f"{tiff.name} could not be read as an OME-TIFF. The file may be damaged, "
            f"incompletely copied, or not a TIFF at all. (The reader said: {why})"
        ) from why

    try:
        # Read one piece now, so a file the viewer cannot decode is refused at
        # the door with a reason, instead of opening as an empty black picture.
        picture.values_for(picture.levels - 1, 0, 0, 0, 0, 0)
    except Exception as why:
        picture.close()
        if isinstance(why, ImportError) or "imagecodecs" in str(why):
            # LZW, JPEG, JPEG 2000 and zstd are decoded by an extra package.
            raise CannotShow(
                f"{tiff.name} is compressed in a way that needs one more package to "
                "read. Install it with  pip install imagecodecs  and open the file "
                f"again. (The reader said: {why})"
            ) from why
        raise CannotShow(
            f"the pixels in {tiff.name} could not be read. (The reader said: {why})"
        ) from why

    try:
        store = Path(where) / f"{the_bare_name(tiff)}.zmartview.zarr"
        store.mkdir(parents=True, exist_ok=True)

        for level in range(picture.levels):
            inside = store / str(level)
            inside.mkdir(exist_ok=True)
            (inside / "zarr.json").write_text(
                json.dumps(picture.array_json(level), indent=1), encoding="utf-8"
            )

        described = picture.group_json()
        described["attributes"]["zmart"] = {
            "what": (
                "A description of an OME-TIFF file, holding no pixels. Every piece "
                "of the picture is read from the file named below when it is asked "
                "for. The file is only read, never changed."
            ),
            TIFF_FROM: tiff.as_posix(),
            "levels": picture.levels,
        }
        (store / "zarr.json").write_text(json.dumps(described, indent=1), encoding="utf-8")
    finally:
        picture.close()

    return store


# -- helpers ------------------------------------------------------------------


def _the_order_of(axes: str) -> tuple[int, int] | None:
    """How to turn a plane read from the file into (Y, X), or None if it already is."""
    flat = [axis for axis in axes if axis in "YX"]

    if flat == ["Y", "X"]:
        return None

    if flat == ["X", "Y"]:
        return (1, 0)

    raise CannotShow(f"the image's axes {axes!r} do not include both Y and X")


def _can_be_read_tile_by_tile(level, axes: str) -> bool:
    """Whether one level's pieces can be read straight from its tiles.

    That needs a tiled level whose tiles are one plane deep, with all colour
    samples of a pixel stored together, and one stored page per plane. Any
    other layout is read through ``tifffile``'s general reader instead, which
    is slower but handles everything.
    """
    key = level.keyframe
    leading = [size for axis, size in zip(axes, level.shape, strict=True) if axis not in "YXS"]
    return bool(
        key.is_tiled
        and getattr(key, "tiledepth", 1) == 1
        and (key.samplesperpixel == 1 or key.planarconfig == 1)
        and axes.replace("S", "").endswith("YX")
        and len(level.pages) == int(np.prod(leading))
    )


def _the_piece_of(level) -> tuple[int, int]:
    """The piece size for one level: the TIFF's own tile, or :data:`PIECE` if untiled."""
    page = level.keyframe

    if page.is_tiled and page.tilelength and page.tilewidth:
        return int(page.tilelength), int(page.tilewidth)

    return min(PIECE, int(page.imagelength)), min(PIECE, int(page.imagewidth))


def _what_the_ome_xml_says(xml: str | None, shape) -> dict:
    """Pixel size, units, time step and channel names, read from the OME-XML.

    Anything the file leaves out gets a plain default: one micrometre per
    pixel, one second per time point, and numbered channels.
    """
    said = {
        "voxel": [1.0, 1.0, 1.0],
        "units": ["micrometer", "micrometer", "micrometer"],
        "time_step": 1.0,
        "time_unit": "second",
        "channel_names": [],
        "channel_colours": [],
        # Where the stage stood for this image, in micrometres (Z, Y, X), when
        # the file says so. It places the image among the others in a folder.
        "stage_um": None,
        # What ZMART's own note in the file says, when it wrote the file.
        "zmart": {},
    }

    if not xml:
        return said

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        log.warning("the OME-XML in this file could not be read; using plain defaults")
        return said

    pixels = next((one for one in root.iter() if one.tag.endswith("}Pixels")), None)

    if pixels is None:
        return said

    for index, axis in enumerate("ZYX"):
        size = pixels.get(f"PhysicalSize{axis}")
        unit = pixels.get(f"PhysicalSize{axis}Unit", "µm")

        try:
            if size is not None and float(size) > 0:
                said["voxel"][index] = float(size)
        except ValueError:
            pass

        said["units"][index] = _UNITS.get(unit, "micrometer")

    step = pixels.get("TimeIncrement")

    try:
        if step is not None and float(step) > 0:
            said["time_step"] = float(step)
            said["time_unit"] = {"ms": "millisecond", "min": "minute", "h": "hour"}.get(
                pixels.get("TimeIncrementUnit", "s"), "second"
            )
    except ValueError:
        pass

    said["stage_um"] = _where_the_stage_stood(root, pixels)
    said["zmart"] = _the_zmart_note_in(root)

    for channel in (one for one in pixels if one.tag.endswith("}Channel")):
        said["channel_names"].append(channel.get("Name") or f"Channel {len(said['channel_names'])}")
        said["channel_colours"].append(_the_colour_of(channel.get("Color")))

    _, channels, _, _, _ = shape

    if said["channel_names"] and len(said["channel_names"]) != channels:
        # An RGB image lists one channel holding three samples; name them plainly.
        said["channel_names"] = ["Red", "Green", "Blue"][:channels] if channels == 3 else []
        said["channel_colours"] = ["FF0000", "00FF00", "0000FF"][:channels] if channels == 3 else []

    return said


def _the_zmart_note_in(root) -> dict:
    """The entries of ZMART's note (:data:`ZMART_NOTE`), or nothing when there is none."""
    for note in root.iter():
        if note.tag.endswith("}MapAnnotation") and note.get("Namespace") == ZMART_NOTE:
            return {
                entry.get("K"): entry.text or ""
                for entry in note.iter()
                if entry.tag.endswith("}M") and entry.get("K")
            }

    return {}


def _the_step(full: int, smaller: int) -> float:
    """How many full-size pixels one pixel of a smaller copy spans, along one axis.

    A copy made by halving has ``full // 2`` (or, rounding up, ``full // 2 + 1``)
    pixels, so the plain ratio is a little off for odd sizes. The whole number
    is used whenever the size fits it, which keeps every copy exactly on the
    full-size grid.
    """
    whole = round(full / smaller)

    if whole >= 1 and smaller in (full // whole, -(-full // whole)):
        return float(whole)

    return full / smaller


def _the_colour_of(written: str | None) -> str | None:
    """OME-XML writes a colour as one signed number (red, green, blue, alpha); give RRGGBB."""
    if written is None:
        return None

    try:
        value = int(written) & 0xFFFFFFFF
    except ValueError:
        return None

    if value in (0xFFFFFFFF, 0):
        return None  # The default "white" or "unset"; let the viewer choose.

    return f"{value >> 24 & 0xFF:02X}{value >> 16 & 0xFF:02X}{value >> 8 & 0xFF:02X}"


def _codecs_for(dtype: np.dtype) -> list[dict]:
    """How a piece is written: raw little-endian numbers, then zstd compression."""
    if dtype.itemsize == 1:
        first = {"name": "bytes"}
    else:
        first = {"name": "bytes", "configuration": {"endian": "little"}}

    return [first, {"name": "zstd", "configuration": {"level": _ZSTD_LEVEL, "checksum": False}}]


def _encode(piece: np.ndarray) -> bytes:
    """A piece as little-endian bytes, compressed with zstd."""
    from numcodecs import Zstd

    raw = np.ascontiguousarray(piece, dtype=piece.dtype.newbyteorder("<")).tobytes()
    return bytes(Zstd(level=_ZSTD_LEVEL).encode(raw))


_MICROMETRES_IN = {
    "micrometer": 1.0,
    "nanometer": 0.001,
    "millimeter": 1000.0,
    "centimeter": 10000.0,
    "meter": 1e6,
    "angstrom": 1e-4,
}

_UNIT_IN_MICROMETRES = {"µm": 1.0, "um": 1.0, "nm": 0.001, "mm": 1000.0, "cm": 1e4, "m": 1e6}


def _where_the_stage_stood(root, pixels) -> tuple[float, float, float] | None:
    """The stage position of an image, in micrometres (Z, Y, X), or None if unknown.

    OME-XML can say this in two places: on each plane (``Plane PositionX``),
    which most acquisition software fills in, or once for the image
    (``StageLabel X``). The first plane is used, because all planes of a
    position share the same X and Y.
    """
    image = next((one for one in root.iter() if one.tag.endswith("}Image")), None)
    plane = next((one for one in pixels if one.tag.endswith("}Plane")), None)
    label = (
        next((one for one in image if one.tag.endswith("}StageLabel")), None)
        if image is not None
        else None
    )

    for element, prefix in ((plane, "Position"), (label, "")):
        if element is None:
            continue

        found = []

        for axis in "ZYX":
            value = element.get(f"{prefix}{axis}")
            unit = element.get(f"{prefix}{axis}Unit", "µm")

            try:
                found.append(float(value) * _UNIT_IN_MICROMETRES.get(unit, 1.0))
            except (TypeError, ValueError):
                found.append(None)

        if found[1] is not None and found[2] is not None:
            return (found[0] or 0.0, found[1], found[2])

    return None


# -- OME-TIFF positions ---------------------------------------------------------
#
# A run saved as OME-TIFF holds one file per position. The viewer composes such
# a folder exactly as it composes a folder of OME-Zarr positions: the composer
# asks each position for its description (:func:`describe_a_tiff_position`) and
# for its pixels (:func:`pixels_of_a_tiff_position`), and never needs to know
# which format answered. Nothing is written beside the files.

_kept_open: OrderedDict = OrderedDict()
_kept_open_guard = threading.Lock()
_described: dict[Path, tuple[tuple[int, int], dict, list]] = {}


def _the_stamp_of(tiff: Path) -> tuple[int, int]:
    """When the file was last written, and its size: a replaced file differs in one."""
    stat = tiff.stat()
    return stat.st_mtime_ns, stat.st_size


class _HeldOpen:
    """One TIFF kept open, and how many readers are using it right now."""

    def __init__(self, picture: TiffPicture, stamp: tuple[int, int]) -> None:
        self.picture = picture
        self.stamp = stamp
        self.readers = 0
        self.let_go = False


def _borrow(tiff: Path) -> _HeldOpen:
    """The TIFF, opened if need be; the least recently used ones are closed.

    A file another reader is still using is never closed under it: it is
    marked, and closed by the last reader to give it back. A file that was
    replaced since it was opened (a position captured again during a run) is
    opened afresh, so its new pixels are shown rather than the old ones.
    """
    import os

    most = int(os.environ.get("ZMART_TIFFS_KEPT_OPEN") or TIFFS_KEPT_OPEN)
    stamp = _the_stamp_of(tiff)

    with _kept_open_guard:
        held = _kept_open.get(tiff)

        if held is not None and held.stamp == stamp:
            _kept_open.move_to_end(tiff)
            held.readers += 1
            return held

    opened = _HeldOpen(TiffPicture(tiff), stamp)
    closing = []

    with _kept_open_guard:
        held = _kept_open.get(tiff)

        if held is not None and held.stamp == stamp:
            closing.append(opened)  # Another reader opened the same file meanwhile.
        else:
            if held is not None:  # The file was replaced since it was opened.
                _let_go(_kept_open.pop(tiff), closing)
            _kept_open[tiff] = held = opened

        held.readers += 1

        while len(_kept_open) > max(1, most):
            _let_go(_kept_open.popitem(last=False)[1], closing)

    for one in closing:
        one.picture.close()

    return held


def _let_go(held: _HeldOpen, closing: list) -> None:
    """Mark a file to be closed: now when nobody reads it, else by its last reader."""
    held.let_go = True

    if held.readers == 0:
        closing.append(held)


def _give_back(held: _HeldOpen) -> None:
    with _kept_open_guard:
        held.readers -= 1
        closing = held.let_go and held.readers == 0

    if closing:
        held.picture.close()


def describe_a_tiff_position(tiff: str | Path) -> tuple[dict, list[tuple]]:
    """What one OME-TIFF position says about itself, read once while the file is unchanged.

    Returns two things. First, its description in the form an OME-Zarr
    position's attributes take (``multiscales``, ``omero`` and, when ZMART
    wrote the file, ``zmart_microscopy``). Second, for each level, its shape,
    the shape of one piece and the kind of pixel. Raises :class:`CannotShow`
    when the file cannot be read or does not say where the stage stood.
    """
    tiff = Path(tiff)
    stamp = _the_stamp_of(tiff)

    with _kept_open_guard:
        known = _described.get(tiff)

    if known is not None and known[0] == stamp:
        return known[1], known[2]

    try:
        held = _borrow(tiff)
    except CannotShow:
        raise
    except Exception as why:
        raise CannotShow(
            f"{tiff.name} could not be read as an OME-TIFF. The file may be damaged, "
            f"incompletely copied, or not a TIFF at all. (The reader said: {why})"
        ) from why

    try:
        picture = held.picture
        described = picture.as_a_position()
        levels = [
            (picture.shapes[level], (1, 1, 1, *picture.pieces[level]), picture.dtype.name)
            for level in range(picture.levels)
        ]
    finally:
        _give_back(held)

    with _kept_open_guard:
        _described[tiff] = (stamp, described, levels)

    return described, levels


class TiffPosition:
    """One level of one TIFF position, read the way the composer reads an array.

    The composer asks for ``array[moment, channel, planes, rows, columns]``;
    this answers by borrowing the TIFF from the files kept open.
    """

    def __init__(self, tiff: Path, level: int, shape: tuple[int, ...]) -> None:
        self.tiff = tiff
        self.level = level
        self.shape = shape

    def __getitem__(self, where):
        moment, channel, planes, rows, columns = where
        held = _borrow(self.tiff)

        try:
            return held.picture.block(self.level, moment, channel, planes, rows, columns)
        finally:
            _give_back(held)


def pixels_of_a_tiff_position(held_in: Path) -> TiffPosition | None:
    """The pixels behind one level of a position, when that position is an OME-TIFF.

    ``held_in`` names a level the way it would inside an OME-Zarr position:
    ``<position>/<level>``. Returns ``None`` when the position is not an
    OME-TIFF, which is then read as an OME-Zarr image.
    """
    tiff = Path(held_in).parent

    if not tiff.name.lower().endswith(OME_TIFF_ENDINGS):
        return None

    level = int(Path(held_in).name)
    _, levels = describe_a_tiff_position(tiff)
    return TiffPosition(tiff, level, levels[level][0])


def ome_tiffs_in(folder: Path) -> list[Path]:
    """The OME-TIFF files directly inside a folder, in name order."""
    try:
        return sorted(
            one
            for one in folder.iterdir()
            if one.name.lower().endswith(OME_TIFF_ENDINGS) and one.is_file()
        )
    except OSError:
        return []


def declare_a_folder_of_tiffs(where: str | Path, folder: str | Path, *, bake: bool = True) -> Path:
    """Write the picture built over a folder of OME-TIFF positions, and return it.

    Each file becomes one position, placed where its OME-XML says the stage
    stood, and the folder is composed exactly as a folder of OME-Zarr
    positions is. With ``bake`` (the default), the zoomed-out levels of that
    picture are built once and kept as files in ``where``, so an overview of
    thousands of positions reads a few files instead of every TIFF. Raises
    :class:`CannotShow` when a file cannot be read or does not say where the
    stage stood, naming the files.
    """
    from concurrent.futures import ThreadPoolExecutor

    from zmart_viewer.picture.built_picture import declare_a_built_picture  # deferred

    folder = Path(folder).resolve()
    tiffs = ome_tiffs_in(folder)

    if not tiffs:
        raise CannotShow(f"there are no OME-TIFF files in {folder}")

    def unplaced(tiff: Path) -> bool:
        # Described here once, and remembered: composing the folder then reads
        # the description again without opening the file a second time.
        try:
            describe_a_tiff_position(tiff)
        except NotPlaced:
            return True
        return False

    with ThreadPoolExecutor(max_workers=min(32, len(tiffs))) as pool:
        missing = [tiff.name for tiff, lost in zip(tiffs, pool.map(unplaced, tiffs)) if lost]

    if missing:
        # Without a stage position, every file would land on the same spot and
        # the picture would look plausible while being wrong. Better to say so.
        shown = ", ".join(missing[:3]) + (
            f" and {len(missing) - 3} more" if len(missing) > 3 else ""
        )
        raise CannotShow(
            f"{len(missing)} of the {len(tiffs)} OME-TIFF files in {folder.name} do not say "
            f"where the stage stood ({shown}), so they cannot be put together into one "
            "picture. Open a single file to look at it on its own. Software that writes "
            "one file per plane or channel, as LAS X does, keeps the stage position in "
            "its own records instead; the ZMART interface turns such captures into "
            "positions for that reason."
        )

    try:
        return declare_a_built_picture(where, folder, name=folder.name, bake=bake)
    except ValueError as why:
        raise CannotShow(
            f"the OME-TIFF files in {folder.name} could not be put together into one picture: {why}"
        ) from why
