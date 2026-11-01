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
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

#: The file endings an OME-TIFF is recognised by.
OME_TIFF_ENDINGS = (".ome.tif", ".ome.tiff", ".ome.btf", ".ome.tf2", ".ome.tf8")

#: The key inside the description that marks it as standing in for a TIFF.
TIFF_FROM = "tiff_from"

#: The size of a piece when the TIFF itself is not stored in tiles.
PIECE = 512

#: The pieces are compressed the same way as the viewer's other built pictures.
_ZSTD_LEVEL = 0

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

    Reading is guarded by a lock, because the server answers several requests
    at the same time and a TIFF file is read through a single file handle.
    """

    def __init__(self, path: str | Path) -> None:
        import tifffile  # deferred: only needed when an OME-TIFF is opened
        import zarr

        self.path = Path(path)
        self._lock = threading.Lock()
        self._file = tifffile.TiffFile(self.path)

        try:
            series = self._file.series[0]
            self.axes_in_file = series.axes
            self.dtype = np.dtype(series.dtype)
            self._arrays = [
                zarr.open_array(series.aszarr(level=level), mode="r")
                for level in range(len(series.levels))
            ]
            self._order = _the_order_of(series.axes)
            self.shapes = [self._five_axes(array.shape) for array in self._arrays]
            self.pieces = [_the_piece_of(level) for level in series.levels]
            self.metadata = _what_the_ome_xml_says(self._file.ome_metadata, self.shapes[0])
        except Exception:
            self._file.close()
            raise

        if self.dtype.name not in _DRAWABLE:
            self._file.close()
            raise ValueError(
                f"{self.path.name} holds pixels of type {self.dtype}, which the viewer "
                "cannot draw. It draws whole numbers of 8, 16 or 32 bits and 32-bit "
                "decimal numbers."
            )

    # -- what the picture is ---------------------------------------------------

    @property
    def levels(self) -> int:
        """How many copies of the picture the file holds, counting full size."""
        return len(self._arrays)

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

        with self._lock:
            region = np.asarray(self._arrays[level][where])

        if self._order:
            # Y and X are the only axes left; the file may hold them as X, Y.
            region = region.transpose(self._order)

        piece = np.zeros((down, across), dtype=self.dtype)
        piece[: bottom - top, : right - left] = region
        return piece

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

    def group_json(self) -> dict:
        """The picture described as an ordinary OME-Zarr 0.5 multiscale image."""
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

        axes = [
            {"name": "t", "type": "time", "unit": said["time_unit"]},
            {"name": "c", "type": "channel"},
            {"name": "z", "type": "space", "unit": said["units"][0]},
            {"name": "y", "type": "space", "unit": said["units"][1]},
            {"name": "x", "type": "space", "unit": said["units"][2]},
        ]
        return {
            "zarr_format": 3,
            "node_type": "group",
            "attributes": {
                "ome": {
                    "version": "0.5",
                    "multiscales": [
                        {
                            "name": the_bare_name(self.path),
                            "axes": axes,
                            "datasets": datasets,
                        }
                    ],
                    "omero": self._omero(),
                },
            },
        }

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

    def _omero(self) -> dict:
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
                    "window": {"min": 0.0, "max": highest, **self._a_first_window(index)},
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
    except ValueError:
        raise
    except Exception as why:
        raise ValueError(f"{tiff.name} could not be read as an OME-TIFF: {why}") from why

    try:
        # Read one piece now, so a file the viewer cannot decode is refused at
        # the door with a reason, instead of opening as an empty black picture.
        picture.values_for(picture.levels - 1, 0, 0, 0, 0, 0)
    except Exception as why:
        picture.close()
        hint = (
            " Its pixels are compressed in a way that needs the 'imagecodecs' "
            "package: run  pip install imagecodecs"
            if "imagecodecs" in str(why)
            else ""
        )
        raise ValueError(f"the pixels in {tiff.name} could not be read ({why}).{hint}") from why

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

    raise ValueError(f"the image's axes {axes!r} do not include both Y and X")


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

    for channel in (one for one in pixels if one.tag.endswith("}Channel")):
        said["channel_names"].append(channel.get("Name") or f"Channel {len(said['channel_names'])}")
        said["channel_colours"].append(_the_colour_of(channel.get("Color")))

    _, channels, _, _, _ = shape

    if said["channel_names"] and len(said["channel_names"]) != channels:
        # An RGB image lists one channel holding three samples; name them plainly.
        said["channel_names"] = ["Red", "Green", "Blue"][:channels] if channels == 3 else []
        said["channel_colours"] = ["FF0000", "00FF00", "0000FF"][:channels] if channels == 3 else []

    return said


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
