"""Supply a built picture's pieces while the viewer is open.

The counterpart of linking.py: where a pointed-at piece is a file that
exists, a built piece is bytes made when asked for. One composer per
picture is held open between requests — opening stores is most of the
cost of building.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from .composer import Composer, the_piece_address
from .declare import OURS
from .mosaic import Mosaic, read_the_mosaic_as_written, read_the_transfer

try:
    from zmart_live.gateway import live_run_holding

    from .governed import GovernedRun
except ImportError:  # pragma: no cover - a checkout without zmart_live
    GovernedRun = None  # type: ignore[assignment, misc]

    def live_run_holding(target):  # type: ignore[misc]
        return None


log = logging.getLogger("zmart-viewer.serving")


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
        if GovernedRun is None:
            raise RuntimeError(
                "this checkout has no zmart_live, so a governed picture "
                "cannot consult any manifest and will not be served."
            )
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


def the_bytes_behind(store: Path, inside: str) -> bytes | None:
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


def forget(store: Path) -> None:
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
    if GovernedRun is None:
        return
    with _guard:
        governed = [held for _mark, held in _composers.values() if isinstance(held, GovernedRun)]
    for held in governed:
        held.request_catch_up()
