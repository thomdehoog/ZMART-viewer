"""Answer for a piece of a picture that is not an ordinary file.

Two answers to one question. A pointed-at piece is a byte range of a
tile's own file, named by the ``zmart-links.json`` map beside the view
(:func:`pointed_bytes_behind`). A built piece is bytes made when asked
for (:func:`built_bytes_behind`), by a composer held open per picture.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

from zmart_live.gateway import live_run_holding

from .building import OURS, GovernedRun
from .compose import (
    Composer,
    Mosaic,
    read_the_mosaic_as_written,
    read_the_transfer,
    the_piece_address,
)

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
    where[OURS] = ours
    listing.write_text(json.dumps(held, indent=1), encoding="utf-8")


def pointed_bytes_behind(store: Path, inside: str) -> Held | None:
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
    ours = inside.get(OURS) if isinstance(inside, dict) else None
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


def forget_pointers(store: Path) -> None:
    """Let go of the pointers remembered for one closed view."""
    with _known_lock:
        listing, _ = where_the_list_is(store)
        _known.pop(str(listing), None)
        # Also under the older name, since a view can be closed after its list has
        # been moved and the two would then be remembered under different keys.
        _known.pop(str(store / LINKS_FILE), None)


log = logging.getLogger("zmart-viewer.pieces")


class TemporarilyUnanswerable(Exception):
    """This piece cannot be answered right now — which is not the same as absent."""


_composers: dict[Path, tuple[tuple | None, Composer | GovernedRun | None]] = {}
_guard = threading.Lock()

_REFUSED_FOR_SECONDS = 2.0
_refused: dict[Path, float] = {}

_being_made: dict[Path, threading.Lock] = {}


def _what_it_was_built_from(store: Path) -> dict | None:
    """What a store records about being built, or ``None`` for an ordinary image."""
    described = store / "zarr.json"

    if not described.is_file():
        return None
    held = json.loads(described.read_text(encoding="utf-8"))
    ours = (held.get("attributes") or {}).get(OURS)

    if isinstance(ours, dict) and (ours.get("built_from") or ours.get("governed_from")):
        return ours
    return None


def _the_pictures_mark(store: Path) -> tuple | None:
    """The identity of the picture's own description, for cache honesty."""
    try:
        stamp = (store / "zarr.json").stat()
    except OSError:
        return None
    return (
        stamp.st_dev,
        getattr(stamp, "st_ino", 0),
        stamp.st_size,
        stamp.st_mtime_ns,
        stamp.st_ctime_ns,
    )


def _the_mosaic_behind(store: Path, ours: dict) -> Mosaic:
    """The picture's geometry, read from its own ledger when it keeps one."""
    ledger = store / "tiles.json"

    if ledger.is_file():
        return read_the_mosaic_as_written(json.loads(ledger.read_text(encoding="utf-8")))
    return read_the_transfer(Path(ours["built_from"]))


def _composer_for(store: Path) -> Composer | GovernedRun | None:
    """The composer for this picture, opened once and kept."""
    store = store.resolve()
    mark = _the_pictures_mark(store)
    stale = None

    with _guard:
        if store in _composers:
            kept_mark, kept = _composers[store]

            if kept_mark == mark:
                return kept
            del _composers[store]
            stale = kept
        stumbled = _refused.get(store)

        if stumbled is not None:
            if time.monotonic() - stumbled < _REFUSED_FOR_SECONDS:
                return None
            del _refused[store]
        making = _being_made.setdefault(store, threading.Lock())

    if stale is not None:
        try:
            stale.close()
        except Exception:
            log.exception(
                "the re-declared picture at %s would not close "
                "cleanly; serving continues from the fresh one",
                store,
            )

    # Held across the reading of the transfer, so the many requests the engine
    # makes at once produce one composer between them rather than one each.
    with making:
        with _guard:
            if store in _composers and _composers[store][0] == mark:
                return _composers[store][1]
        made = None

        try:
            ours = _what_it_was_built_from(store)
            made = _the_serving_behind(store, ours)
        except Exception:
            log.exception(
                "the picture at %s could not be opened; answering absent for the next %.0f seconds",
                store,
                _REFUSED_FOR_SECONDS,
            )

            with _guard:
                _refused[store] = time.monotonic()
            return None

        with _guard:
            _composers[store] = (mark, made)
            return made


def _the_serving_behind(store: Path, ours: dict | None) -> Composer | GovernedRun | None:
    """What answers for this store: a composer, a governed run, or nothing."""
    if ours is None:
        return None
    governs = ours.get("governed_from")

    if governs:
        return GovernedRun(Path(governs), piece=int(ours.get("piece") or 512), store=store)
    transfer = Path(ours["built_from"])
    holding = live_run_holding(transfer)

    if holding is not None:
        log.warning(
            "refusing the picture at %s: its transfer %s lies inside the "
            "governed run at %s, which would serve the run's pixels past its "
            "manifest. Declare the run itself instead.",
            store,
            transfer,
            holding,
        )
        return None
    workers = int(os.environ.get("ZMART_BUILD_WORKERS") or 1)
    made = Composer(
        _the_mosaic_behind(store, ours), piece=int(ours.get("piece") or 512), workers=workers
    )

    if not ours.get("baked"):
        made.keep_the_coarse_levels_warm()
    return made


def a_manifest_governs(store: Path) -> bool:
    """Whether this picture's pieces may only be answered through its run."""
    where = Path(store).resolve()
    held = _composer_for(where)

    if GovernedRun is not None and isinstance(held, GovernedRun):
        return True

    if held is None:
        with _guard:
            return where in _refused
    return False


def built_bytes_behind(store: Path, inside: str) -> bytes | None:
    """The piece of a built picture the browser asked for, made now."""
    where = Path(store)
    held = _composer_for(where)

    if held is None:
        with _guard:
            if where.resolve() in _refused:
                raise TemporarilyUnanswerable(
                    f"the picture at {where} could not be opened just now"
                )
        return None
    address = the_piece_address(inside)

    if address is None:
        return None
    level, moment, channel, plane, row, column = address
    composer = None

    if GovernedRun is not None and isinstance(held, GovernedRun):
        try:
            composer = held.composer()
        except Exception as problem:
            log.exception(
                "the governed run behind %s could not derive; answering 'try again shortly'", store
            )
            raise TemporarilyUnanswerable(
                f"the governed run behind {store} could not derive"
            ) from problem
    baked = Path(store).joinpath(*inside.strip("/").split("/"))

    if baked.is_file():
        return baked.read_bytes()

    try:
        if composer is None:
            composer = held

        if not 0 <= level < composer.mosaic.levels:
            return None
        deep, down, across = composer.grid(level)
        moments, channels = composer.mosaic.frame_room

        if not (
            0 <= plane < deep
            and 0 <= row < down
            and 0 <= column < across
            and 0 <= moment < moments
            and 0 <= channel < channels
        ):
            return None
        return composer.bytes_for(level, plane, row, column, moment, channel)
    except Exception as problem:
        log.exception(
            "the piece %s of %s could not be served; answering 'try again shortly'", inside, store
        )
        raise TemporarilyUnanswerable(
            f"the piece {inside} of {store} could not be served just now"
        ) from problem


def a_sample_behind(store: Path, channel: int = 0):
    """A built picture's pixels for measuring: the composer's own coarsest ground."""
    held = _composer_for(Path(store))

    if held is None:
        return None

    try:
        composer = (
            held.composer() if GovernedRun is not None and isinstance(held, GovernedRun) else held
        )
        level = composer.mosaic.levels - 1
        deep, down, across = composer.grid(level)
        moments, channels = composer.mosaic.frame_room

        if not 0 <= channel < channels:
            return None
        middle = (down // 2, across // 2)
        nearby = sorted(
            ((row, column) for row in range(down) for column in range(across)),
            key=lambda at: abs(at[0] - middle[0]) + abs(at[1] - middle[1]),
        )

        for row, column in nearby[:9]:
            piece = composer.values_for(level, deep // 2, row, column, moment=0, channel=channel)

            if piece is not None:
                return piece
    except Exception:
        log.exception(
            "the picture behind %s could not be sampled for measuring; it will open unmeasured",
            store,
        )
    return None


def the_values_inside(store: Path, level: int, box, *, channel: int = 0, pieces: int = 4):
    """A built picture's pixels inside a share of itself, for measuring."""
    import numpy as np

    held = _composer_for(Path(store))

    if held is None:
        return None

    try:
        composer = (
            held.composer() if GovernedRun is not None and isinstance(held, GovernedRun) else held
        )
        depth, height, width = composer.mosaic.shape(level)
        moments, channels = composer.mosaic.frame_room

        if not 0 <= channel < channels or not height or not width:
            return None
        (top, left), (bottom, right) = box
        first_row = max(0, min(height - 1, int(top * height)))
        last_row = max(first_row, min(height - 1, int(bottom * height) - 1))
        first_column = max(0, min(width - 1, int(left * width)))
        last_column = max(first_column, min(width - 1, int(right * width) - 1))
        wanted = [
            (row, column)
            for row in range(first_row // composer.piece, last_row // composer.piece + 1)
            for column in range(first_column // composer.piece, last_column // composer.piece + 1)
        ]
        middle = (
            (first_row + last_row) / 2 / composer.piece,
            (first_column + last_column) / 2 / composer.piece,
        )
        wanted.sort(key=lambda at: abs(at[0] - middle[0]) + abs(at[1] - middle[1]))

        taken = []

        for row, column in wanted[: max(1, pieces)]:
            piece = composer.values_for(level, depth // 2, row, column, moment=0, channel=channel)

            if piece is None:
                continue
            block = np.asarray(piece)
            rows = slice(
                max(0, first_row - row * composer.piece),
                max(1, last_row + 1 - row * composer.piece),
            )
            columns = slice(
                max(0, first_column - column * composer.piece),
                max(1, last_column + 1 - column * composer.piece),
            )
            taken.append(block[..., rows, columns].ravel())

        if not taken:
            return None
        return np.concatenate(taken)
    except Exception:
        log.exception(
            "the picture behind %s could not be measured where it is being looked at", store
        )
        return None


def forget_composer(store: Path) -> None:
    """Let go of a built picture, closing the tiles it was holding open."""
    with _guard:
        where = Path(store).resolve()
        remembered = _composers.pop(where, None)
        _being_made.pop(where, None)
        _refused.pop(where, None)
    held = remembered[1] if remembered is not None else None

    if held is not None:
        held.close()


def catch_up_governed_runs() -> None:
    """Nudge every opened governed picture after an acquisition announcement."""
    with _guard:
        governed = [held for _mark, held in _composers.values() if isinstance(held, GovernedRun)]

    for held in governed:
        held.request_catch_up()


def forget(store: Path) -> None:
    """Drop everything cached for a store, pointed and built alike."""
    forget_pointers(store)
    forget_composer(store)
