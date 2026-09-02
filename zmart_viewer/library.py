"""Which folders are open, and how the stores inside them are read.

Open folders are numbered datasets; the numbers guard serving (a request
may only reach inside an open folder, and a number is never reused), and
watched folders are re-read as a run writes into them. The reading half
answers discovery, axes, channels, declared windows, voxel sizes and
written timepoints, through one reader for both zarr generations.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

_CHANNEL_COLORS = {
    "405": (0.30, 0.45, 1.00),
    "488": (0.00, 1.00, 0.40),
    "561": (1.00, 0.75, 0.10),
    "647": (1.00, 0.20, 1.00),
}
_CHANNEL_PATTERN = re.compile(r"Ch(\d{3})")

_DEFAULT_CHANNEL_TURNS = (
    (0.00, 1.00, 0.40),  # green
    (1.00, 0.20, 1.00),  # magenta
    (0.20, 0.80, 1.00),  # cyan
    (1.00, 0.75, 0.10),  # amber
)

DESCRIPTION_FILES = (".zattrs", ".zarray", ".zgroup", "zarr.json")


def channel_of(name: str) -> str | None:
    """The excitation wavelength a store's name declares, if it declares one."""
    match = _CHANNEL_PATTERN.search(name)
    return match.group(1) if match else None


def layer_names(names: list[str]) -> list[str]:
    """Short, *unique* labels for a set of stores."""
    short = [_short_name(name) for name in names]
    wanted = Counter(short)
    return [
        _with_filter(name, label) if wanted[name] > 1 else name
        for name, label in zip(short, names, strict=True)
    ]


def _short_name(store_name: str) -> str:
    stem = without_format_suffix(store_name)
    parts = [p for p in stem.split("_") if p.startswith("Tile") or p.startswith("Ch")]
    return "_".join(parts) if parts else stem


def _with_filter(short: str, store_name: str) -> str:
    """Add the filter block, abbreviated, to a label that would otherwise clash."""
    for part in without_format_suffix(store_name).split("_"):
        if part.startswith("Flt"):
            filter_name = part[3:] or "None"
            return f"{short}_{filter_name[:12]}"

    return short


def without_format_suffix(store_name: str) -> str:
    """A store's name with the format's suffixes taken off."""
    return (
        store_name.removesuffix(".zmartview.zarr").removesuffix(".ome.zarr").removesuffix(".zarr")
    )


def channel_color(name: str) -> tuple[float, float, float] | None:
    """The colour to draw a store in, or ``None`` to leave it greyscale."""
    channel = channel_of(name)
    return _CHANNEL_COLORS.get(channel) if channel else None


# -- is this folder an image at all? -------------------------------------------


def is_store(path: Path) -> bool:
    """True if ``path`` is an OME-Zarr image store (has multiscales metadata)."""
    return bool(_read_attrs_at(path).get("multiscales"))


_UNIT_SPELLINGS = {
    "um": "micrometer",
    "µm": "micrometer",  # the micro sign, U+00B5
    "μm": "micrometer",  # greek small letter mu, U+03BC -- identical to look at
    "nm": "nanometer",
    "mm": "millimeter",
    "cm": "centimeter",
    "m": "meter",
    "s": "second",
    "ms": "millisecond",
}


def _ome_holders(described: dict) -> list[dict]:
    """Every place inside a parsed description where the OME metadata could be living."""
    holders = []

    for outer in (described, described.get("attributes")):
        if not isinstance(outer, dict):
            continue

        holders.append(outer)
        nested = outer.get("ome")

        if isinstance(nested, dict):
            holders.append(nested)

    return holders


def normalise_units(raw: bytes) -> bytes:
    """A store's description with its axis units spelled the way the format asks."""
    if b"unit" not in raw:
        return raw

    try:
        described = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return raw

    if not isinstance(described, dict):
        return raw

    changed = False

    for holder in _ome_holders(described):
        for multiscale in holder.get("multiscales") or []:
            if not isinstance(multiscale, dict):
                continue

            for axis in multiscale.get("axes") or []:
                if not isinstance(axis, dict):
                    continue

                spelled = axis.get("unit")
                correct = _UNIT_SPELLINGS.get(spelled) if isinstance(spelled, str) else None

                if correct is not None and correct != spelled:
                    axis["unit"] = correct
                    changed = True

    if not changed:
        return raw

    return json.dumps(described).encode("utf-8")


def voxel_size(store: Path) -> tuple[float, ...]:
    """How large one voxel is at full resolution, as the store itself declares it."""
    described = (_read_attrs_at(store).get("multiscales") or [{}])[0]
    levels = described.get("datasets") or [{}]

    for transform in levels[0].get("coordinateTransformations") or []:
        if isinstance(transform, dict) and transform.get("type") == "scale":
            found = transform.get("scale")

            if not isinstance(found, list):
                continue

            names = axis_names(store)

            if len(names) == len(found):
                found = [
                    value
                    for name, value in zip(names, found, strict=True)
                    if name in ("z", "y", "x")
                ]

            return tuple(round(float(value), 6) for value in found)

    return ()


def declared_channels(store: Path) -> list[str] | None:
    """The channels a store names inside itself, or ``None`` if it holds one image."""
    if "c" not in axis_names(store):
        return None

    return [str(channel["name"]) for channel in channels(store)]


_attrs_cache: dict[str, tuple[int, dict]] = {}


def _description_file(path: Path) -> Path | None:
    """Where this store keeps its description, whichever version wrote it."""
    for name in (".zattrs", "zarr.json"):
        candidate = path / name

        if candidate.exists():
            return candidate

    return None


def _read_attrs_at(path: Path) -> dict:
    """The OME-Zarr description at ``path``, or an empty one if unreadable."""
    key = str(path)
    described = _description_file(path)

    if described is None:
        _attrs_cache.pop(key, None)
        return {}

    try:
        stamp = described.stat().st_mtime_ns
    except OSError:
        _attrs_cache.pop(key, None)
        return {}

    remembered = _attrs_cache.get(key)

    if remembered is not None and remembered[0] == stamp:
        return remembered[1]

    try:
        attrs = json.loads(described.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        attrs = {}

    if not isinstance(attrs, dict):
        attrs = {}

    if described.name == "zarr.json":
        # Everything else in a version 3 file describes the group rather than the
        # image, so the store's own attributes are all we want from it.
        attrs = attrs.get("attributes") if isinstance(attrs.get("attributes"), dict) else {}
    # Flattened by merging each place the metadata could be, the innermost last so
    # that a store nesting it under "ome" wins over anything left at the top.
    flat: dict = {}

    for holder in _ome_holders(attrs):
        flat.update(holder)

    attrs = flat
    _attrs_cache[key] = (stamp, attrs)
    return attrs


_array_cache: dict[str, tuple[int, dict]] = {}


def _read_array_description(level: Path) -> dict:
    """How the array at ``level`` is laid out on disk, whichever zarr version wrote it."""
    key = str(level)

    for name in (".zarray", "zarr.json"):
        found = level / name

        try:
            stamp = found.stat().st_mtime_ns
        except OSError:
            continue

        remembered = _array_cache.get(key)

        if remembered is not None and remembered[0] == stamp:
            return remembered[1]

        try:
            described = json.loads(found.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            described = None

        answer = _array_layout(described) if isinstance(described, dict) else {}
        _array_cache[key] = (stamp, answer)
        return answer

    _array_cache.pop(key, None)
    return {}


def _array_layout(described: dict) -> dict:
    """Read one array description into the shape :func:`_read_array_description` promises."""
    shape = _numbers(described.get("shape"))

    if described.get("zarr_format") == 3 or "chunk_grid" in described:
        return {"shape": shape, **_version_3_layout(described)}

    return {"shape": shape, **_version_2_layout(described)}


def _version_2_layout(described: dict) -> dict:
    """Version 2, which names its pieces plainly: ``3.0.0`` or, in folders, ``3/0/0``."""
    separator = described.get("dimension_separator")
    return {
        "pieces": _numbers(described.get("chunks")),
        "prefix": "",
        # A version 2 array that says nothing files its pieces side by side in one
        # folder, which is the layout `docs/how_it_works/DATA_LAYOUT.md` asks writers to avoid.
        "separator": separator if separator in ("/", ".") else ".",
    }


def _version_3_layout(described: dict) -> dict:
    """Version 3, which has two ways of naming a piece and a default for each."""
    grid = described.get("chunk_grid")
    settings = grid.get("configuration") if isinstance(grid, dict) else None
    encoding = described.get("chunk_key_encoding")
    encoding = encoding if isinstance(encoding, dict) else {}
    named = encoding.get("configuration")
    named = named if isinstance(named, dict) else {}
    prefix, fallback = ("", ".") if encoding.get("name") == "v2" else ("c", "/")
    separator = named.get("separator")
    return {
        "pieces": _numbers(settings.get("chunk_shape") if isinstance(settings, dict) else None),
        "prefix": prefix,
        "separator": separator if separator in ("/", ".") else fallback,
    }


def _numbers(value: object) -> list[int]:
    """A list of whole numbers out of a description, or nothing if it is not one."""
    if not isinstance(value, list):
        return []

    try:
        return [int(number) for number in value]
    except (TypeError, ValueError):
        return []


def zarr_scheme(store: Path) -> str:
    """Which of the engine's zarr readers should be asked for this store."""
    return "zarr3" if (store / "zarr.json").exists() else "zarr2"


def axis_names(store: Path) -> list[str]:
    """The axes this store declares, in order — for example ``[t, c, z, y, x]``."""
    axes = (_read_attrs_at(store).get("multiscales") or [{}])[0].get("axes") or []
    return [axis.get("name", "") for axis in axes if isinstance(axis, dict)]


def channels(store: Path) -> list[dict]:
    """Describe each channel inside this store: its name and its colour."""
    attrs = _read_attrs_at(store)
    names = axis_names(store)
    described = attrs.get("omero", {}).get("channels")
    described = described if isinstance(described, list) else []

    if not described:
        # No ``omero`` block does not mean no channels. A run that has not
        # decided its display window yet writes none, because a strict reader
        # refuses a channel entry without a complete window -- but it still
        # knows what its channels are called and which colour each is meant to
        # be, and it says so under ``zmart``. Read that, so an unresolved
        # three-colour acquisition shows three named rows rather than one.
        described = _channels_the_acquisition_named(attrs)

    count = _channel_count(store, names, len(described))

    if count is None:
        # No channel axis at all: the store is one image, so it is one layer.
        return [{"name": _short_name(store.name), "color": None}]

    return described_channels(described, count)


def _channels_the_acquisition_named(attrs: dict) -> list[dict]:
    """The acquisition's own channel list, kept under ``zmart`` when no ``omero`` block can be written.

    The entries come from ``zmart-acquisition.json`` by way of the store's
    ``zmart`` attributes: a label, a colour, and the numeric range the camera
    can produce. They are handed back in the shape an ``omero`` entry has, so
    the rest of this module reads them the same way -- with one deliberate
    difference. No ``start`` or ``end`` is ever filled in here, because none was
    decided; the Viewer measures a window from the pixels instead and says so.
    """
    ours = attrs.get("zmart")
    listed = ours.get("channels") if isinstance(ours, dict) else None

    if not isinstance(listed, list) or not listed:
        return []

    out = []

    for entry in listed:
        if not isinstance(entry, dict):
            return []

        shown: dict = {"label": entry.get("label")}
        colour = entry.get("color")

        if isinstance(colour, str):
            shown["color"] = colour

        declared_range = entry.get("range")

        if isinstance(declared_range, dict):
            shown["window"] = {"min": declared_range.get("min"), "max": declared_range.get("max")}

        out.append(shown)

    return out


def described_channels(described: list[dict], count: int) -> list[dict]:
    """Turn a description's channel entries into what a layer list shows."""
    out = []

    for index in range(count):
        entry = described[index] if index < len(described) else {}
        entry = entry if isinstance(entry, dict) else {}
        label = entry.get("label") or entry.get("name") or f"channel {index + 1}"
        out.append(
            {
                "name": str(label),
                "color": _hex_to_rgb(entry.get("color")),
                "window": _the_window_asked_for(entry),
                "range": _the_range_declared_by(entry),
                # Which channels the run had switched on. Absent means on: a run
                # that never mentioned a channel has not switched it off.
                "active": entry.get("active") is not False,
            }
        )

    if count > 1:
        if all(channel["color"] in (None, (1.0, 1.0, 1.0)) for channel in out):
            for channel in out:
                channel["color"] = None

        turn = 0

        for channel in out:
            if channel["color"] is None:
                channel["color"] = _DEFAULT_CHANNEL_TURNS[turn % len(_DEFAULT_CHANNEL_TURNS)]
                turn += 1

    return out


def _the_range_declared_by(channel: dict) -> dict | None:
    """The whole range this channel's numbers live in, if the run says."""
    window = channel.get("window")

    if not isinstance(window, dict):
        return None

    low, high = window.get("min"), window.get("max")

    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return None

    if high <= low:
        return None

    return {"low": float(low), "high": float(high)}


def _the_window_asked_for(channel: dict) -> dict | None:
    """The brightness a run asked this channel to be shown between, if it said."""
    window = channel.get("window")

    if not isinstance(window, dict):
        return None

    low, high = window.get("start"), window.get("end")

    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return None

    if high <= low:
        return None

    if low == window.get("min") and high == window.get("max"):
        return None

    return {"low": float(low), "high": float(high)}


def _channel_count(store: Path, names: list[str], described: int) -> int | None:
    """How many channels the store's own array says it has."""
    if "c" not in names:
        return None

    datasets = (_read_attrs_at(store).get("multiscales") or [{}])[0].get("datasets") or []

    if not datasets:
        return described or None

    shape = _read_array_description(store / str(datasets[0].get("path"))).get("shape") or []
    index = names.index("c")
    return int(shape[index]) if index < len(shape) else (described or None)


def label_images(store: Path) -> list[str]:
    """The segmentation masks stored alongside this image, if any."""
    folder = store / "labels"
    listed = _read_attrs_at(folder).get("labels")
    names = [name for name in listed if isinstance(name, str)] if isinstance(listed, list) else []

    if not names:
        # A labels folder with no list in it is still worth looking inside, since
        # the list is optional and a mask that is present should be shown.
        try:
            names = sorted(child.name for child in folder.iterdir() if is_store(child))
        except OSError:
            return []

    return [name for name in names if is_store(folder / name)]


_SCAN_LIMIT = 20_000


class _TooManyToCount:
    """Stands for "this folder holds more pieces than it is sensible to look through"."""


_TOO_MANY = _TooManyToCount()

# Remembering the answer alongside the folder's own modification time, so asking
# again costs one cheap look unless something has actually been written since.
_frame_counts: dict[str, tuple[int, int | None | _TooManyToCount]] = {}

_MTIME_STILL_MOVING_NS = 100_000_000


def forget(store: Path) -> None:
    """Let go of everything remembered about one store, because it has been closed."""
    under = str(store)
    inside = under + os.sep

    for remembered in (_attrs_cache, _array_cache, _frame_counts):
        for key in [key for key in remembered if key == under or key.startswith(inside)]:
            del remembered[key]


def written_timepoints(store: Path) -> int | None:
    """How far into a timelapse the images on disk reach, so the slider can stop there."""
    names = axis_names(store)

    if "t" not in names or names.index("t") != 0:
        return None

    datasets = (_read_attrs_at(store).get("multiscales") or [{}])[0].get("datasets") or []

    if not datasets:
        return None

    level = _the_copy_that_holds_the_picture(store, datasets)
    watched = _moments_folder(level)

    try:
        stamp = watched.stat().st_mtime_ns
    except OSError:
        return None

    remembered = _frame_counts.get(str(watched))

    if remembered is not None and (remembered[0] == stamp or remembered[1] is _TOO_MANY):
        return None if remembered[1] is _TOO_MANY else remembered[1]

    answer = _count_frames(level)

    if answer is _TOO_MANY or time.time_ns() - stamp > _MTIME_STILL_MOVING_NS:
        _frame_counts[str(watched)] = (stamp, answer)

    return None if answer is _TOO_MANY else answer


def _the_copy_that_holds_the_picture(store: Path, datasets: list[dict]) -> Path:
    """Which copy of the image to count the moments from."""
    copies = [store / str(entry.get("path")) for entry in datasets]

    for level in copies:
        holder = _moments_folder(level)

        try:
            if any(entry.name not in DESCRIPTION_FILES for entry in holder.iterdir()):
                return level
        except OSError:
            continue

    return copies[0]


def _moments_folder(level: Path) -> Path:
    """The folder that gains an entry as each moment of a timelapse is written."""
    described = _read_array_description(level)

    if described.get("prefix") and described.get("separator") == "/":
        return level / str(described["prefix"])

    return level


def _count_frames(level: Path) -> int | None | _TooManyToCount:
    """One past the furthest moment that holds an image, found by reading the folder."""
    described = _read_array_description(level)

    if not described:
        return None

    if (described.get("pieces") or [])[:1] != [1]:
        return None

    prefix, separator = described["prefix"], described["separator"]

    if separator == "/":
        furthest = _furthest_moment_among_folders(level / prefix if prefix else level)
    else:
        furthest = _furthest_moment_among_files(level, f"{prefix}{separator}" if prefix else "")

    if not isinstance(furthest, int):
        return furthest  # Nothing there yet, or more pieces than are worth reading.

    return furthest + 1


def _furthest_moment_among_folders(folder: Path) -> int | None:
    """The highest-numbered folder in ``folder``, each of which is one moment."""
    furthest = None

    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.name.isdigit():
                    moment = int(entry.name)
                    furthest = moment if furthest is None else max(furthest, moment)
    except OSError:
        return None

    return furthest


def _furthest_moment_among_files(folder: Path, prefix: str) -> int | None | _TooManyToCount:
    """The furthest moment named by the files in ``folder``, or nothing if there are too many."""
    furthest = None
    seen = 0

    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                name = entry.name

                if name.startswith("."):
                    # ``.zarray`` and its like describe the image rather than
                    # holding any of it, so they are not moments.
                    continue

                if prefix:
                    if not name.startswith(prefix):
                        continue

                    name = name[len(prefix) :]

                head, _, rest = name.partition(".")

                if rest and head.isdigit():
                    moment = int(head)
                    furthest = moment if furthest is None else max(furthest, moment)

                seen += 1

                if seen > _SCAN_LIMIT:
                    return _TOO_MANY
    except OSError:
        return None

    return furthest


def _hex_to_rgb(value: object) -> tuple[float, float, float] | None:
    """Turn an ``omero`` colour like ``"00FF66"`` into fractions of red/green/blue."""
    if not isinstance(value, str):
        return None

    text = value.strip().lstrip("#")

    if len(text) != 6:
        return None

    try:
        return tuple(int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return None


def select_tiles(names: list[str], tiles: list[int] | None) -> list[str]:
    """Keep only the named tiles, or everything when ``tiles`` is ``None``."""
    if tiles is None:
        return names

    wanted = {f"Tile{n}" for n in tiles}
    return [
        name
        for name in names
        if any(part in wanted for part in without_format_suffix(name).split("_"))
    ]


def prefer_filter(names: list[str], wanted: str | None) -> list[str]:
    """Keep one store per tile and channel, preferring a filter by name."""
    if wanted is None:
        return names

    chosen: dict[tuple[str | None, str | None], str] = {}

    for name in names:
        key = (_tile_of(name), channel_of(name))
        current = chosen.get(key)

        if current is None or (
            wanted.lower() in _filter_of(name).lower()
            and wanted.lower() not in _filter_of(current).lower()
        ):
            chosen[key] = name

    return [name for name in names if name in set(chosen.values())]


def _tile_of(name: str) -> str | None:
    for part in without_format_suffix(name).split("_"):
        if part.startswith("Tile"):
            return part

    return None


def _filter_of(name: str) -> str:
    for part in without_format_suffix(name).split("_"):
        if part.startswith("Flt"):
            return part[3:]

    return ""


def discover(path: str | Path) -> tuple[Path, list[str]]:
    """Return ``(parent_directory, store_names)`` for whatever ``path`` names."""
    path = Path(path).resolve()

    if is_store(path):
        return path.parent, [path.name]

    names = sorted(child.name for child in path.iterdir() if child.is_dir() and is_store(child))
    return path, names


_DESCRIPTION_FILES = (".zattrs", "zarr.json")


def _described_at(folder: Path) -> str:
    """When this folder's description was last written, as a short mark."""
    for name in _DESCRIPTION_FILES:
        try:
            found = (folder / name).stat()
        except OSError:
            continue

        return f"{found.st_mtime_ns}:{found.st_size}"

    return "-"


def _named_for(path: Path, parent: Path) -> str:
    """What to call the dataset a load produced."""
    del parent  # kept in the signature: what was chosen is the whole answer here

    if path.name.endswith(".zmartview.zarr"):
        return path.name

    return without_format_suffix(path.name)


def _acquisition_type_in(store_name: str) -> str:
    """The kind of scan a store's name says it is — ``targetscan``, ``overview``."""
    stem = without_format_suffix(store_name)
    front, _, _position = stem.partition("_")
    return front or stem


Acquisition = tuple[tuple[float, ...], tuple[str, ...] | None]


def _acquisition_of(root: Path, name: str) -> Acquisition:
    """What kind of acquisition one store says it is, read from the store itself."""
    declared = declared_channels(root / name)
    return voxel_size(root / name), tuple(declared) if declared is not None else None


def _same_acquisition(one: Acquisition | None, other: Acquisition | None) -> bool:
    """Whether two stores can be pieces of the same acquisition."""
    if one is None or other is None:
        return True

    size_here, channels_here = one
    size_there, channels_there = other

    if size_here and size_there and size_here != size_there:
        return False

    if channels_here is not None and channels_there is not None:
        if channels_here != channels_there:
            return False

    return True


def _kind_of_acquisition(root: Path, names: list[str]) -> Acquisition | None:
    """What kind of acquisition a dataset is, from the first of its stores that says."""
    for name in names:
        found = _acquisition_of(root, name)

        if found[0] or found[1] is not None:
            return found

    return None


# -- one load, one acquisition -------------------------------------------------


@dataclass
class Dataset:
    """One load: one acquisition, however many stores it was written as."""

    number: int
    root: Path
    name: str
    stores: list[str]
    channels: list[str]
    live: bool
    # Whether to keep looking in the folder for stores that appear after it was
    # opened. See the note on ``open``.
    watch: bool = field(default=True)
    acquisition: Acquisition | None = field(default=None)
    # The folders outside this one that its own stores link into, found when each
    # store is placed. See :func:`_borrowed_folders`.
    borrows: list[Path] = field(default_factory=list)


def _is_a_pyramid_level(folder: Path) -> bool:
    """Whether a folder is one level of an image, rather than any other folder."""
    try:
        if not folder.is_dir():
            return False

        return (folder / "zarr.json").exists() or (folder / ".zarray").exists()
    except OSError:
        return False


def _borrowed_folders(root: Path, names: Iterable[str]) -> list[Path]:
    """The folders outside this one that its own stores link into."""
    borrowed: dict[Path, None] = {}

    for name in names:
        store = root / name

        try:
            inside = list(os.scandir(store))
        except OSError:
            continue

        for entry in inside:
            here = Path(entry.path)

            try:
                target = here.resolve()
            except OSError:
                continue

            if target == here or target == root or root in target.parents:
                continue

            if _is_a_pyramid_level(target):
                borrowed[target] = None

    return list(borrowed)


def _one_acquisition_only(root: Path, names: list[str]) -> None:
    """Refuse a load that spans more than one acquisition, saying what it found."""
    families: dict[tuple, list[str]] = {}

    for name in names:
        size = voxel_size(root / name)

        if size:
            families.setdefault(size, []).append(name)

    if len(families) > 1:
        described = "\n".join(
            "  voxel " + " x ".join(f"{value:g}" for value in size) + " um: " + ", ".join(found)
            for size, found in sorted(families.items())
        )
        raise ValueError(
            f"{root} holds more than one acquisition — open one of them instead:\n{described}"
        )

    declared: dict[tuple, list[str]] = {}

    for name in names:
        found = declared_channels(root / name)

        if found is not None:
            declared.setdefault(tuple(found), []).append(name)

    if len(declared) > 1:
        described = "\n".join(
            "  channels " + ", ".join(names_of) + ": " + ", ".join(found)
            for names_of, found in sorted(declared.items())
        )
        raise ValueError(
            f"{root} holds stores declaring different channels, so they are not one "
            f"acquisition — open one of them instead:\n{described}"
        )


def _channels_of(root: Path, names: list[str]) -> list[str]:
    """The channels a dataset presents, in the order the panel should show them."""
    for name in names:
        found = declared_channels(root / name)

        if found is not None:
            return found

    seen: list[str] = []

    for name in sorted(names):
        wavelength = channel_of(name)

        if wavelength and f"Ch{wavelength}" not in seen:
            seen.append(f"Ch{wavelength}")

    return seen


class Library:
    """The datasets the viewer has open, and where they live on disk."""

    def __init__(self) -> None:
        self._datasets: dict[int, Dataset] = {}
        # Counts up forever rather than filling gaps, so a number never refers to
        # two different folders over the life of a session.
        self._next = 0
        self._lock = threading.RLock()
        self._let_go: set[tuple[Path, str]] = set()

    # -- opening and closing ---------------------------------------------

    def open(
        self,
        path: str | Path,
        *,
        names: list[str] | None = None,
        watch: bool | None = None,
        name: str | None = None,
    ) -> int:
        """Open a folder and return the number it will be addressed by."""
        path = Path(path).expanduser()

        if not path.exists():
            raise FileNotFoundError(f"there is no folder at {path}")

        if not path.is_dir():
            raise ValueError(f"{path} is a file, not a folder of images")

        parent, found = discover(path)
        chosen = names if names is not None else found

        if not chosen:
            raise ValueError(
                f"no OME-Zarr image was found in {path} — if the images are one "
                "level down, choose the folder that contains them"
            )

        root = parent.resolve()
        _one_acquisition_only(root, list(chosen))
        watched = watch if watch is not None else (names is None)

        with self._lock:
            self._let_go.difference_update((root, store) for store in chosen)
            number = self._next
            self._next += 1
            self._datasets[number] = Dataset(
                number=number,
                root=root,
                name=name or _named_for(path, parent),
                stores=list(chosen),
                channels=_channels_of(root, list(chosen)),
                live=bool(watched),
                watch=bool(watched),
                acquisition=_kind_of_acquisition(root, list(chosen)),
                borrows=_borrowed_folders(root, chosen),
            )

        return number

    def dataset(self, number: int) -> Dataset | None:
        """The dataset a load produced, or ``None`` if it has since been closed."""
        with self._lock:
            return self._datasets.get(number)

    def datasets(self) -> list[Dataset]:
        """Every open dataset, oldest first, which is the order the panel shows."""
        with self._lock:
            return [self._datasets[number] for number in sorted(self._datasets)]

    def close(self, number: int) -> bool:
        """Stop serving a folder. Returns whether it was open at all."""
        with self._lock:
            return self._close(number)

    def _close(self, number: int) -> bool:
        """Stop serving a dataset, with the lock already held."""
        closed = self._datasets.pop(number, None)

        if closed is None:
            return False
        # Remembered, so that a folder still being watched does not find these again
        # a moment later and put them back on screen. See the note in ``__init__``.
        self._let_go.update((closed.root, name) for name in closed.stores)
        return True

    def close_group(self, group: str, *, folder: int | None = None) -> list[tuple[int, Path, str]]:
        """Close a dataset by the name the panel shows it under."""
        closed: list[tuple[int, Path, str]] = []

        with self._lock:
            for number, dataset in list(self._datasets.items()):
                if folder is not None and number != folder:
                    continue

                if dataset.name != group:
                    continue

                closed += [(number, dataset.root, store) for store in dataset.stores]
                self._close(number)

        return closed

    def entries(self) -> list[tuple[int, Path, str]]:
        """Every open image as ``(folder number, folder, store name)``."""
        with self._lock:
            watched: list[Path] = []

            for dataset in self.datasets():
                if dataset.watch and dataset.root not in watched:
                    watched.append(dataset.root)

        for root in watched:
            self._look_again(root)

        with self._lock:
            return [
                (dataset.number, dataset.root, name)
                for dataset in self.datasets()
                for name in dataset.stores
            ]

    def _look_again(self, root: Path) -> None:
        """Look in a watched folder and place whatever has appeared in it."""
        try:
            _, found = discover(root)
        except OSError:
            # A folder that has become unreadable mid-run is not a reason to lose
            # what is already on screen.
            return

        with self._lock:
            spoken_for = self._spoken_for(root)

        unknown = [name for name in found if name not in spoken_for]

        if not unknown:
            return

        kinds = {name: _acquisition_of(root, name) for name in unknown}

        with self._lock:
            spoken_for = self._spoken_for(root)

            for name in unknown:
                if name in spoken_for:
                    continue

                self._place(root, name, kinds[name])
                spoken_for.add(name)

    def _spoken_for(self, root: Path) -> set[str]:
        """The stores in a folder that are already accounted for. With the lock held."""
        spoken = {name for folder, name in self._let_go if folder == root}

        for dataset in self._datasets.values():
            if dataset.root == root:
                spoken.update(dataset.stores)

        return spoken

    def _place(self, root: Path, name: str, kind: Acquisition) -> None:
        """Put a store that has just appeared where it belongs. With the lock held."""
        for number in sorted(self._datasets):
            dataset = self._datasets[number]

            if dataset.root != root or not dataset.watch:
                continue

            if _same_acquisition(dataset.acquisition, kind):
                dataset.stores.append(name)
                # A position that arrives during a run brings its own links: each
                # block of a growing linked picture points at the source itself.
                for folder in _borrowed_folders(root, [name]):
                    if folder not in dataset.borrows:
                        dataset.borrows.append(folder)

                if dataset.acquisition is None and (kind[0] or kind[1] is not None):
                    dataset.acquisition = kind

                return

        number = self._next
        self._next += 1
        self._datasets[number] = Dataset(
            number=number,
            root=root,
            name=self._heading_for(root, name, number),
            stores=[name],
            channels=_channels_of(root, [name]),
            live=True,
            watch=True,
            acquisition=kind,
            borrows=_borrowed_folders(root, [name]),
        )

    def _heading_for(self, root: Path, store_name: str, number: int) -> str:
        """What to call a dataset the viewer opened on its own. With the lock held."""
        taken = {dataset.name for dataset in self._datasets.values() if dataset.root == root}
        kind = _acquisition_type_in(store_name)
        whole = without_format_suffix(store_name)

        for candidate in (kind, whole, f"{kind} ({number})", f"{whole} ({number})"):
            if candidate not in taken:
                return candidate

        return f"{whole} ({number})"

    # -- noticing a change without reading anything ------------------------

    def revision(
        self,
        *,
        excluding: set[int] | frozenset[int] | Callable[[], frozenset[int]] = frozenset(),
    ) -> str:
        """A short summary of the open folders that changes when their contents do."""
        excluded = excluding() if callable(excluding) else excluding

        with self._lock:
            open_now = [
                (dataset.number, dataset.root, list(dataset.stores))
                for dataset in self.datasets()
                if dataset.number not in excluded
            ]

        marks = []

        for number, root, names in open_now:
            try:
                with os.scandir(root) as listing:
                    beside = sorted(
                        (
                            entry.name,
                            entry.stat().st_mtime_ns,
                            _described_at(Path(entry.path)),
                        )
                        for entry in listing
                        if entry.is_dir()
                    )
            except OSError:
                # A folder that cannot be read right now (a share hiccuping) should
                # not read as "everything changed" and trigger a needless rebuild.
                marks.append(f"{number}:?")
                continue

            marks.append(
                f"{number}:"
                + ",".join(f"{name}@{when}/{described}" for name, when, described in beside)
            )

            for name in names:
                try:
                    marks.append(str(_moments_folder(root / name / "0").stat().st_mtime_ns))
                except OSError:
                    marks.append("?")

        return hashlib.blake2b("|".join(marks).encode("utf-8"), digest_size=16).hexdigest()

    # -- the two questions the server asks ---------------------------------

    def is_empty(self) -> bool:
        """Whether nothing at all is open, which the server reports as an empty viewer."""
        with self._lock:
            return not any(dataset.stores for dataset in self._datasets.values())

    def resolve(self, relative: str) -> Path | None:
        """Turn ``<number>/<store>/<chunk…>`` into a file, or ``None`` if not allowed."""
        number, _, rest = relative.partition("/")

        if not number.isdigit() or not rest:
            return None

        with self._lock:
            found = self._datasets.get(int(number))
            borrows = list(found.borrows) if found else []

        if found is None:
            return None

        target = (found.root / rest).resolve()

        for root in (found.root, *borrows):
            root = root.resolve()

            if target == root or root in target.parents:
                return target

        return None
