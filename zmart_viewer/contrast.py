"""Work out the display window a store first opens with, when it declares none.

Measured from the smallest written copy and cached per store.
docs/how_it_works/ARCHITECTURE.md §2 records why this work belongs in the
engine and what would delete this module.
"""

from __future__ import annotations

import threading
from pathlib import Path

from .library import (
    DESCRIPTION_FILES,
    _moments_folder,
    _read_attrs_at,
    channel_color,
    channels,
    zarr_scheme,
)

LOW_PERCENTILE = 1.0
HIGH_PERCENTILE = 99.0

VOLUME_LOW_PERCENTILE = 99.0
VOLUME_HIGH_PERCENTILE = 99.99
HISTOGRAM_BINS = 64
HISTOGRAM_LOW_PERCENTILE = 0.1
HISTOGRAM_HIGH_PERCENTILE = 99.9


def _omero_window(attrs: dict, channel: int | None = None) -> tuple[float, float] | None:
    """The display window the store asks for, if it declares one."""
    declared = attrs.get("omero", {}).get("channels") or []

    if channel is not None:
        wanted = [declared[channel]] if channel < len(declared) else []
    else:
        wanted = declared

    for entry in wanted:
        window = (entry or {}).get("window") or {}

        if "start" in window and "end" in window:
            start, end = float(window["start"]), float(window["end"])

            if end > start:
                return start, end

    return None


def _level_paths(attrs: dict) -> list[str]:
    """Every copy of the image the store keeps, from the largest to the smallest."""
    datasets = (attrs.get("multiscales") or [{}])[0].get("datasets") or []
    return [str(entry["path"]) for entry in datasets if entry.get("path") is not None]


def _coarsest_level_path(attrs: dict) -> str | None:
    levels = _level_paths(attrs)
    return levels[-1] if levels else None


def _level_holds_pixels(level: Path) -> bool:
    """Has anything actually been written into this copy of the image yet?"""
    holder = _moments_folder(level)

    try:
        return any(entry.name not in DESCRIPTION_FILES for entry in holder.iterdir())
    except OSError:
        return False


_SAMPLE_PLANES = 4
_SAMPLE_SIDE = 1024


def _sample(array, held: dict[int, int] | None = None) -> object:
    """Read at most a few million voxels from anywhere in ``array``."""
    import numpy as np

    held = held or {}
    shape = tuple(int(n) for n in array.shape)

    if not shape:
        return np.asarray(array[...])

    budget = _SAMPLE_PLANES * _SAMPLE_SIDE * _SAMPLE_SIDE
    total = 1

    for axis, extent in enumerate(shape):
        total *= 1 if axis in held else max(int(extent), 1)

    if total <= budget:
        whole = tuple(held.get(axis, slice(None)) for axis in range(len(shape)))
        return np.asarray(array[whole])

    # Crop the image plane (the last two axes) to a square about the middle.
    plane: list[slice] = []

    for extent in shape[-2:]:
        if extent <= _SAMPLE_SIDE:
            plane.append(slice(None))
        else:
            start = (extent - _SAMPLE_SIDE) // 2
            plane.append(slice(start, start + _SAMPLE_SIDE))

    leading = shape[: len(shape) - len(plane)]

    if not leading:
        return np.asarray(array[tuple(plane)])

    free = [axis for axis in range(len(leading)) if axis not in held]

    if not free:
        index = tuple([*(held[axis] for axis in range(len(leading))), *plane])

        try:
            return np.asarray(array[index]).ravel()
        except (OSError, ValueError, IndexError):
            return np.asarray([])

    spread_axis = free[-1]
    depth = leading[spread_axis]
    positions = sorted(
        {int(i * (depth - 1) / max(_SAMPLE_PLANES - 1, 1)) for i in range(_SAMPLE_PLANES)}
    )
    pieces = []

    for position in positions:
        where: list[int] = []

        for axis, extent in enumerate(leading):
            if axis == spread_axis:
                where.append(position)
            elif axis in held:
                where.append(held[axis])
            else:
                where.append(max(0, extent // 2))

        try:
            pieces.append(np.asarray(array[tuple([*where, *plane])]).ravel())
        except (OSError, ValueError, IndexError):
            continue

    if not pieces:
        return np.asarray([])

    return np.concatenate(pieces)


def _samples(store: Path, *, channel: int | None = None):
    """Read a store's description and take one bounded look at its pixels."""
    import numpy as np
    import zarr

    try:
        attrs = _read_attrs_at(store)
        levels = _level_paths(attrs)

        if not levels:
            return None

        held = _hold_the_channel(attrs, channel)
        group = zarr.open_group(str(store), mode="r")
    except (OSError, KeyError, ValueError, MemoryError):
        return None

    for position, level in enumerate(reversed(levels)):
        if not _level_holds_pixels(store / level):
            continue

        try:
            array = group[level]
            data = _sample(array, held)
        except (OSError, KeyError, ValueError, IndexError, MemoryError):
            continue

        values = np.asarray(data, dtype=np.float64).ravel()
        values = values[np.isfinite(values)]
        fill = getattr(array, "fill_value", None)

        if fill is not None and np.isfinite(float(fill)):
            imaged = values[values != float(fill)]

            if imaged.size:
                values = imaged

        if values.size == 0:
            continue

        return attrs, values, position == 0

    for member in _the_members_behind(store):
        followed = _samples(member, channel=channel)

        if followed is not None:
            return attrs, followed[1], False

    composed = _a_built_pictures_sample(store, channel=channel)

    if composed is not None:
        values = np.asarray(composed, dtype=np.float64).ravel()
        values = values[np.isfinite(values)]
        imaged = values[values != 0.0]

        if imaged.size:
            values = imaged

        if values.size:
            return attrs, values, False

    return None


def _readability_problem(store: Path) -> str | None:
    """Why this is not a readable image, or ``None`` for a valid empty one.

    Pixel absence is ordinary at the start of a live run.  Description or
    array failure is not: reporting both as "waiting" leaves a corrupt store
    waiting forever and hides the only fact that can help the operator.
    """
    import zarr

    try:
        attrs = _read_attrs_at(store)
        levels = _level_paths(attrs)
    except (OSError, KeyError, UnicodeDecodeError, ValueError) as exc:
        return f"the image description cannot be read ({exc})"
    if not levels:
        return "the image description names no pixel levels"
    try:
        group = zarr.open_group(str(store), mode="r")
        for level in levels:
            group[level]
    except (OSError, KeyError, UnicodeDecodeError, ValueError, MemoryError) as exc:
        return f"the image arrays cannot be read ({exc})"
    return None


def _a_built_pictures_values(store: str | Path, level: int, box, channel: int | None):
    """A built picture's pixels inside the share of it on screen, or None."""
    from .pieces import the_values_inside

    return the_values_inside(Path(store), level, box, channel=channel or 0)


def _a_built_pictures_sample(store: str | Path, channel: int | None):
    """The composer's own sample of a built picture, or None outside one."""
    from .pieces import a_sample_behind

    return a_sample_behind(Path(store), channel=channel or 0)


def _the_members_behind(store: str | Path) -> list[Path]:
    """The image stores a linked view answers from, in the contract layout."""
    import json

    collection = Path(store).parent.parent.parent / "data" / "survey.ome.zarr"

    try:
        described = json.loads((collection / "zarr.json").read_text())
        members = described["attributes"]["zmart"]["members"]
    except (OSError, KeyError, ValueError):
        return []

    return [collection / member for member in members]


def camera_range(store: str | Path, declared: dict | None = None) -> tuple[float, float] | None:
    """The whole range this channel's numbers live in, or None where nothing says."""
    import json

    import numpy as np

    if declared:
        return float(declared["low"]), float(declared["high"])

    store = Path(store)

    try:
        level = _coarsest_level_path(_read_attrs_at(store))
    except (OSError, KeyError, ValueError):
        return None

    if level is None:
        return None

    for name in (".zarray", "zarr.json"):
        described = store / level / name

        try:
            said = json.loads(described.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        declared = said.get("dtype") or said.get("data_type")

        if not declared:
            continue

        try:
            kind = np.dtype(declared)
        except TypeError:
            return None

        if kind.kind == "u":
            return 0.0, float(np.iinfo(kind).max)

        if kind.kind == "i":
            return float(np.iinfo(kind).min), float(np.iinfo(kind).max)

        return None

    return None


def coarsest_level_is_written(store: str | Path) -> bool:
    """Has the smallest, whole-field copy of this image been written yet?"""
    store = Path(store)

    try:
        level = _coarsest_level_path(_read_attrs_at(store))
    except (OSError, KeyError, ValueError):
        return False

    return False if level is None else _level_holds_pixels(store / level)


def _hold_the_channel(attrs: dict, channel: int | None) -> dict[int, int]:
    """Which axis to pin, and where, so that only one channel is looked at."""
    if channel is None:
        return {}

    axes = [
        axis.get("name", "") for axis in (attrs.get("multiscales") or [{}])[0].get("axes") or []
    ]
    return {axes.index("c"): channel} if "c" in axes else {}


def measure(store: str | Path, *, channel: int | None = None, bins: int = HISTOGRAM_BINS) -> dict:
    """Everything the panel needs to know about one channel's brightness."""
    store = Path(store)
    read = _samples(store, channel=channel)

    if read is None:
        problem = _readability_problem(store)
        return {
            "window": None,
            "volumeWindow": None,
            "histogram": None,
            "settled": False,
            "measurementState": "unreadable" if problem is not None else "waiting",
            "measurementError": problem,
        }

    attrs, values, settled = read
    declared = _omero_window(attrs, channel)
    return {
        # A window the store itself declares is honoured for the plane view: the
        # microscope software that wrote it knew what it was showing.
        "window": declared if declared is not None else _window(values, volumetric=False),
        "volumeWindow": _window(values, volumetric=True),
        "histogram": _histogram(values, bins=bins),
        "settled": settled,
        "measurementState": "settled" if settled else "provisional",
        "measurementError": None,
    }


ENOUGH_TO_MEASURE = 4096
# And the most any one press will read. Percentiles do not get better with
# millions of samples, and this is a button an operator holds down.
AS_MUCH_AS_NEEDED = 262_144


def _the_box_on(array, axes: list[str], box, channel: int | None):
    """Where a share of the picture falls on one copy of it, as slices."""
    import numpy as np

    shape = tuple(int(n) for n in array.shape)
    (top, left), (bottom, right) = box
    taken: list = [slice(None)] * len(shape)

    for name, low, high in (("y", top, bottom), ("x", left, right)):
        if name not in axes:
            continue

        at = axes.index(name)
        length = shape[at]
        first = max(0, min(length, int(np.floor(low * length))))
        last = max(0, min(length, int(np.ceil(high * length))))

        if last <= first:
            return None

        taken[at] = slice(first, last)

    if channel is not None and "c" in axes:
        taken[axes.index("c")] = slice(channel, channel + 1)

    return taken


def _how_many(shape, taken) -> int:
    """How many numbers a read of these slices would hand back."""
    total = 1

    for length, part in zip(shape, taken):
        total *= len(range(*part.indices(int(length))))

    return total


def _thinned(shape, taken, most: int):
    """The same slices, stepped so they hand back no more than ``most``."""
    held = _how_many(shape, taken)

    if held <= most:
        return taken

    spread = (
        sum(1 for length, part in zip(shape, taken) if len(range(*part.indices(int(length)))) > 1)
        or 1
    )
    step = max(2, int(round((held / most) ** (1.0 / spread))))
    return [
        slice(part.start, part.stop, (part.step or 1) * step)
        if len(range(*part.indices(int(length)))) > 1
        else part
        for length, part in zip(shape, taken)
    ]


def measure_here(
    store: str | Path,
    *,
    channel: int | None = None,
    box=((0.0, 0.0), (1.0, 1.0)),
    bins: int = HISTOGRAM_BINS,
) -> dict | None:
    """The brightness of the part of a picture an operator is looking at."""
    import numpy as np
    import zarr

    store = Path(store)
    attrs = _read_attrs_at(store)
    levels = _level_paths(attrs)

    if not levels:
        return None

    axes = [
        axis.get("name", "") for axis in (attrs.get("multiscales") or [{}])[0].get("axes") or []
    ]

    try:
        group = zarr.open_group(str(store), mode="r")
    except (OSError, KeyError, ValueError):
        return None

    chosen = None

    for at, level in reversed(list(enumerate(levels))):
        try:
            array = group[level]
        except (OSError, KeyError, ValueError):
            continue

        held = _the_box_on(array, axes, box, channel)

        if held is None:
            return None

        if not _level_holds_pixels(store / level):
            made = _a_built_pictures_values(store, at, box, channel)

            if made is None:
                continue

            chosen = (None, made)
        else:
            chosen = (array, held)

        if _how_many(array.shape, held) >= ENOUGH_TO_MEASURE:
            break

    if chosen is None:
        return None

    array, taken = chosen

    if array is None:
        values = np.asarray(taken, dtype=np.float64).ravel()
    else:
        taken = _thinned(array.shape, taken, AS_MUCH_AS_NEEDED)

        try:
            values = np.asarray(array[tuple(taken)], dtype=np.float64).ravel()
        except (OSError, ValueError, IndexError, MemoryError):
            return None

    values = values[np.isfinite(values)]
    # Ground nobody imaged reads back as the fill value, and it is not part of
    # the specimen. Counted in, it wins the vote wherever a picture has gaps.
    fill = getattr(array, "fill_value", 0)

    if fill is not None and np.isfinite(float(fill)):
        values = values[values != float(fill)]

    if values.size == 0:
        return None

    return {
        "window": _window(values, volumetric=False),
        "volumeWindow": _window(values, volumetric=True),
        "histogram": _histogram(values, bins=bins),
    }


def _window(values, *, volumetric: bool) -> tuple[float, float]:
    """The intensity window for one view, from samples already in hand."""
    import numpy as np

    low_pct = VOLUME_LOW_PERCENTILE if volumetric else LOW_PERCENTILE
    high_pct = VOLUME_HIGH_PERCENTILE if volumetric else HIGH_PERCENTILE
    low, high = (float(v) for v in np.percentile(values, [low_pct, high_pct]))

    if high <= low:
        return low, low + 1.0

    return low, high


def _histogram(values, *, bins: int) -> dict:
    """The shape of the brightness distribution, from samples already in hand."""
    import numpy as np

    # All four percentiles in one call. Each one otherwise re-sorts the whole
    # sample, so asking separately does the same expensive work four times.
    plot_low, plot_high, auto_low, auto_high = (
        float(v)
        for v in np.percentile(
            values,
            [
                HISTOGRAM_LOW_PERCENTILE,
                HISTOGRAM_HIGH_PERCENTILE,
                LOW_PERCENTILE,
                HIGH_PERCENTILE,
            ],
        )
    )

    if plot_high <= plot_low:
        plot_high = plot_low + 1.0

    if auto_high <= auto_low:
        auto_high = auto_low + 1.0

    counts, _ = np.histogram(
        np.clip(values, plot_low, plot_high), bins=bins, range=(plot_low, plot_high)
    )
    return {
        "low": plot_low,
        "high": plot_high,
        "counts": [int(value) for value in counts],
        "autoWindow": {"low": auto_low, "high": auto_high},
    }


def display_window(
    store: str | Path, *, volumetric: bool = False, channel: int | None = None
) -> tuple[float, float] | None:
    """Return the display window, or ``None`` until one can be known honestly."""
    store = Path(store)

    if not volumetric:
        declared = _omero_window(_read_attrs_at(store), channel)

        if declared is not None:
            return declared

    read = _samples(store, channel=channel)

    if read is None:
        return None

    return _window(read[1], volumetric=volumetric)


def intensity_histogram(
    store: str | Path, *, channel: int | None = None, bins: int = HISTOGRAM_BINS
) -> dict | None:
    """Return a compact histogram measured from the smallest copy of the image."""
    if bins < 2:
        raise ValueError("a histogram needs at least two bins")

    read = _samples(Path(store), channel=channel)

    if read is None:
        return None

    return _histogram(read[1], bins=bins)


class Measurements:
    """The display description of every open store, measured once and kept.

    A store measured before its whole-field copy existed is remembered as
    provisional and measured again once the copy is written. ``fixed_window``
    (the --range flag) skips measuring and pins every window.
    """

    def __init__(self, fixed_window: tuple[float, float] | None = None):
        self._fixed = fixed_window
        self._measured: dict[str, dict] = {}
        self._provisional: set[str] = set()
        self._measuring = threading.Lock()

    def forget(self, closed) -> None:
        """Drop the measurements of stores that have just been closed."""
        for number, _, name in closed:
            stem = f"{number}/{name}"

            for key in [k for k in self._measured if k == stem or k.startswith(f"{stem}/c")]:
                self._measured.pop(key, None)
                self._provisional.discard(key)

    def describe(
        self,
        root_number: int,
        root: Path,
        name: str,
        label: str,
        coloured: bool,
        channel: int | None = None,
        declared_range: dict | None = None,
    ) -> dict:
        key = f"{root_number}/{name}" if channel is None else f"{root_number}/{name}/c{channel}"
        remembered = self._measured.get(key)

        if remembered is not None and (
            key not in self._provisional or not self._worth_measuring_again(root / name)
        ):
            return {**remembered, "name": label}

        with self._measuring:
            remembered = self._measured.get(key)

            if remembered is not None and key not in self._provisional:
                return {**remembered, "name": label}

            return self._measure(
                key, root_number, root, name, label, coloured, channel, declared_range
            )

    def _worth_measuring_again(self, store: Path) -> bool:
        """Has the store gained the whole-field copy it was missing?"""
        try:
            return coarsest_level_is_written(store)
        except OSError:
            return False

    @staticmethod
    def _window_asked_for(store: Path, channel: int | None) -> dict | None:
        """The brightness this channel's run asked for, or None if it said none."""
        try:
            described = channels(store)
        except Exception:
            return None

        at = 0 if channel is None else int(channel)

        if at >= len(described):
            return None
        return described[at].get("window")

    def _measure(
        self, key, root_number, root, name, label, coloured, channel=None, declared_range=None
    ) -> dict:
        """Read one store's pixels and work out how it should first be shown."""
        if self._fixed is not None:
            fixed_settled = coarsest_level_is_written(root / name)
            found = {
                "window": self._fixed,
                "volumeWindow": self._fixed,
                "histogram": intensity_histogram(root / name, channel=channel),
                "settled": fixed_settled,
                "measurementState": "declared",
                "measurementError": None,
            }
        else:
            found = measure(root / name, channel=channel)

        flat, volume = found["window"], found["volumeWindow"]
        asked_for = self._window_asked_for(root / name, channel)

        if asked_for is not None:
            flat = volume = (asked_for["low"], asked_for["high"])
        measurement_state = (
            "declared" if asked_for is not None else found.get("measurementState", "waiting")
        )

        color = channel_color(name) if coloured else None
        described = {
            "sources": [f"/data/{root_number}/{name}/|{zarr_scheme(root / name)}:"],
            "window": None if flat is None else {"low": flat[0], "high": flat[1]},
            "volumeWindow": (
                None if volume is None else {"low": volume[0], "high": volume[1]}
            ),
            "color": list(color) if color else None,
            "histogram": found["histogram"],
            "settled": bool(found.get("settled")),
            "measurementState": measurement_state,
            "measurementError": found.get("measurementError"),
        }
        held = camera_range(root / name, declared_range)

        if held is not None:
            described["range"] = {"low": held[0], "high": held[1]}

        if found["histogram"] is not None:
            self._measured[key] = described

            if found.get("settled"):
                self._provisional.discard(key)
            else:
                self._provisional.add(key)

        return {**described, "name": label}
