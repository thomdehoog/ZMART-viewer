"""Answer for a picture that was never written down.

A linked view's full-size picture is a list of pointers into the tiles'
own files; a request for a piece is answered with the file that already
holds those exact bytes, unchanged. The map is ``zmart-links.json`` in the
view's folder, written by the view builder::

    {"version": 2, "level": "0", "separator": "/", "prefix": "",
     "tiles": [{"store": "...", "at": [z, y, x], "size": [...],
                "from": [...], "held_as": "file" | "shard"}]}
"""

from __future__ import annotations

import json
import threading
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

OURS_IN_THE_DESCRIPTION = "zmart"

# The folder a view's list lived in for a while, beside the images rather than
# inside one. Still read, so a run written that way keeps working.
LINKS_FOLDER = "zmart-links"

LINKS_FILE = "zmart-links.json"

LINKS_VERSION = 3

LINKS_VERSIONS_UNDERSTOOD = (1, 2, 3)

LINKS_ADDED_FILE = "zmart-links-added.jsonl"

LINKS_ADDED_ENDING = "-positions-arriving.jsonl"

HELD_AS_A_FILE = "file"


@dataclass(frozen=True)
class Held:
    """Where a piece of the picture really is: a file, a place in it, and a length."""

    path: str
    offset: int = 0
    length: int | None = None

    @property
    def is_the_whole_file(self) -> bool:
        """Whether these bytes are simply all of the file, which is the usual case."""
        return self.offset == 0 and self.length is None


class _WhereThePiecesReallyAre:
    """One view's pointers, held as the tiles themselves rather than piece by piece."""

    def __init__(self, listed: dict) -> None:
        self.level = str(listed.get("level", "0"))
        self.pointed_levels = max(1, int(listed.get("pointed_levels", 1) or 1))
        self.separator = str(listed.get("separator") or "/")
        self.prefix = str(listed.get("prefix") or "")
        self._rows: dict[
            int,
            list[
                tuple[
                    tuple[int, int, int],
                    tuple[int, int, int],
                    tuple[int, int, int],
                    str,
                    str,
                ]
            ],
        ] = {}
        self._widest = 1
        for tile in listed.get("tiles") or []:
            store = str(tile["store"])
            at = tuple(int(n) for n in tile["at"])
            size = tuple(int(n) for n in tile["size"])
            low = tuple(int(n) for n in tile["from"])
            if len(at) != 3 or len(size) != 3 or len(low) != 3:
                raise ValueError("a tile's place in the view needs three numbers")
            if any(n < 0 for n in at + low) or any(n <= 0 for n in size):
                raise ValueError("a tile cannot begin before the view or be empty")
            held_as = str(tile.get("held_as") or HELD_AS_A_FILE)
            if held_as != HELD_AS_A_FILE:
                raise ValueError(
                    f"{store} says its pieces are held as {held_as!r}, which this "
                    "reader does not know how to find. Rather than guess at where "
                    "in a file a piece begins — and hand the viewer somebody else's "
                    "bytes to draw — the whole view is left unread."
                )
            held = (at, size, low, store, held_as)
            self._widest = max(self._widest, size[2])
            for row in range(at[1], at[1] + size[1]):
                self._rows.setdefault(row, []).append(held)
        for crossing in self._rows.values():
            crossing.sort(key=lambda tile: tile[0][2])

    def _tile_covering(
        self, at: tuple[int, int, int]
    ) -> tuple[str, tuple[int, int, int], str] | None:
        """Which tile supplies the piece at this place, and which of its pieces it is."""
        crossing = self._rows.get(at[1])
        if not crossing:
            return None
        nearest = bisect_right(crossing, at[2], key=lambda tile: tile[0][2])
        for index in range(nearest - 1, -1, -1):
            begins, size, low, store, held_as = crossing[index]
            if at[2] - begins[2] >= self._widest:
                break
            if (
                begins[2] <= at[2] < begins[2] + size[2]
                and begins[0] <= at[0] < begins[0] + size[0]
            ):
                return (
                    store,
                    (
                        low[0] + at[0] - begins[0],
                        low[1] + at[1] - begins[1],
                        low[2] + at[2] - begins[2],
                    ),
                    held_as,
                )
        return None

    def the_bytes_behind(self, inside: str) -> Held | None:
        """Where this piece of the view really is: a file, a place in it, a length."""
        named = self._numbers_in(inside)
        if named is None:
            return None
        level, frame, channel, z, y, x = named
        shrink = 2**level
        found = self._tile_covering((z, y * shrink, x * shrink))
        if found is None:
            return None
        store, (from_z, from_y, from_x), held_as = found
        piece = self._named(frame, channel, from_z, from_y // shrink, from_x // shrink)
        where = f"{store}/{level}/{piece}"
        return Held(path=where, offset=0, length=None)

    def _named(self, *numbers: int) -> str:
        """What a piece at this position is called, in the spelling this view uses."""
        parts = [str(n) for n in numbers]
        if self.prefix:
            parts.insert(0, self.prefix)
        return self.separator.join(parts)

    def _numbers_in(self, inside: str) -> tuple[int, int, int, int, int, int] | None:
        """Which copy, and the five numbers naming a piece of it, or ``None``."""
        which, _, _ = inside.partition("/")
        try:
            level = int(which)
        except ValueError:
            return None
        if not 0 <= level < self.pointed_levels:
            return None
        wanted = f"{which}/"
        if not inside.startswith(wanted):
            return None
        rest = inside[len(wanted) :]
        parts = rest.split(self.separator) if self.separator else [rest]
        if self.prefix:
            # The ``c`` version 3 puts in front is a part of the name of its own,
            # so it is taken off here rather than being read as a number.
            if not parts or parts[0] != self.prefix:
                return None
            parts = parts[1:]
        if len(parts) != 5:
            return None
        try:
            frame, channel, z, y, x = (int(part) for part in parts)
        except ValueError:
            return None
        return level, frame, channel, z, y, x


_known: dict[str, tuple[tuple[int, int], _WhereThePiecesReallyAre]] = {}
_known_lock = threading.Lock()


def where_the_list_is(store: Path) -> tuple[Path, Path]:
    """Where a view's list of pointers and its companion file are, if anywhere."""
    for description in (store / "zarr.json", store / ".zattrs"):
        if description.is_file():
            return description, store.parent / f"{store.name}{LINKS_ADDED_ENDING}"
    beside = store.parent / LINKS_FOLDER / f"{store.name}.json"
    if beside.is_file():
        return beside, beside.with_name(f"{store.name}{LINKS_ADDED_ENDING}")
    return store / LINKS_FILE, store / LINKS_ADDED_FILE


def the_map_inside(store: Path) -> dict | None:
    """The map from picture to positions, as this reader finds it."""
    listing, _ = where_the_list_is(store)
    try:
        held = json.loads(listing.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return _the_part_that_is_ours(held) if isinstance(held, dict) else None


def rewrite_the_map_inside(store: Path, ours: dict) -> None:
    """Put a different map into a view, leaving the rest of its description alone."""
    listing, _ = where_the_list_is(store)
    held = json.loads(listing.read_text(encoding="utf-8"))
    if "version" in held and "tiles" in held:
        listing.write_text(json.dumps(ours, indent=1), encoding="utf-8")
        return
    where = held.setdefault("attributes", {}) if listing.name == "zarr.json" else held
    where[OURS_IN_THE_DESCRIPTION] = ours
    listing.write_text(json.dumps(held, indent=1), encoding="utf-8")


def the_bytes_behind(store: Path, inside: str) -> Held | None:
    """Where this piece of a pointed-at picture really is, if it is one."""
    listing, added = where_the_list_is(store)
    try:
        written = listing.stat().st_mtime_ns
    except OSError:
        return None
    try:
        grown = added.stat().st_size
    except OSError:
        grown = -1
    key = str(listing)
    with _known_lock:
        remembered = _known.get(key)
    if remembered is None or remembered[0] != (written, grown):
        spread = _read(listing, added)
        if spread is None:
            return None
        with _known_lock:
            _known[key] = ((written, grown), spread)
    else:
        spread = remembered[1]
    return spread.the_bytes_behind(inside)


def _the_tiles_added_since(added: Path) -> list[dict]:
    """The tiles a run still being acquired has added, one to a line."""
    try:
        lines = added.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    tiles = []
    for at, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            tiles.append(json.loads(line))
        except json.JSONDecodeError:
            if at == len(lines) - 1:
                break
            raise
    return tiles


def _the_part_that_is_ours(held: dict) -> dict | None:
    """The list of pointers inside whatever was just read, or ``None``."""
    if "version" in held and "tiles" in held:
        return held
    inside = held.get("attributes") if isinstance(held.get("attributes"), dict) else held
    ours = inside.get(OURS_IN_THE_DESCRIPTION) if isinstance(inside, dict) else None
    return ours if isinstance(ours, dict) else None


def _read(listing: Path, added: Path) -> _WhereThePiecesReallyAre | None:
    """Read a view's list of pointers, or ``None`` if it cannot be trusted."""
    try:
        held = json.loads(listing.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(held, dict):
        return None
    held = _the_part_that_is_ours(held)
    if held is None or held.get("version") not in LINKS_VERSIONS_UNDERSTOOD:
        return None
    try:
        held = {**held, "tiles": [*(held.get("tiles") or []), *_the_tiles_added_since(added)]}
        return _WhereThePiecesReallyAre(held)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def forget(store: Path) -> None:
    """Let go of the pointers remembered for one closed view."""
    with _known_lock:
        listing, _ = where_the_list_is(store)
        _known.pop(str(listing), None)
        # Also under the older name, since a view can be closed after its list has
        # been moved and the two would then be remembered under different keys.
        _known.pop(str(store / LINKS_FILE), None)
