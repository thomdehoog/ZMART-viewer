"""One door: classify what a path is, then open it the one right way.

Every way into the viewer — the load window, the command line, a live
run binding — goes through :func:`load`. It decides what the path holds
(a plate, a run of positions, a built scene, a live run, a plain store)
and answers with what the library should open. A path that cannot be
opened raises :class:`CannotOpen` with the reason in plain words.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from zmart_viewer.live.following import LIVE_PICTURE, the_live_picture_declared
from zmart_viewer.live.record.live_serving import live_run_holding
from zmart_viewer.opening.open_folders import DESCRIPTION_FILES, is_store


class CannotOpen(Exception):
    """A refusal at the door, with the reason and any structured detail."""

    def __init__(self, reason: str, **detail):
        super().__init__(reason)
        self.detail = detail


@dataclass
class Opened:
    """What the library should open: the target, and — for a live run —
    the name of its served picture. ``composed_from`` is the folder of
    positions a scene was composed over by this door, which the scene must
    keep up with while the folder is still being written.
    """

    target: Path
    names: list[str] | None = None
    composed_from: Path | None = None


def load(path: str | Path, *, bake: bool = False, scenes: Path | None = None) -> Opened:
    """The one door. ``bake`` writes a live run's coarse pyramid as files;
    ``scenes`` is where a composed description may be written for a run of
    positions opened directly (left None, such a run is refused).
    """
    asked = Path(path).expanduser()
    plate = scene_behind_a_plate(asked, scenes)
    run = None if plate is not None else scene_behind_a_run(asked, scenes)
    target = plate or run or asked
    relink = relink_needed(target)

    if relink is not None:
        raise CannotOpen(
            f"this viewer was built from {relink['was']}, and nothing is "
            "there any more -- point it at the raw data again",
            relink=relink,
        )

    return Opened(
        target,
        names=live_run_view(target, bake=bake),
        composed_from=asked if run is not None else None,
    )


def live_run_view(target: Path, *, bake: bool = False) -> list[str] | None:
    """The one store a live run's folder is opened by: its governed picture,
    declared on first sight. None means the target is not a live run.
    """
    if live_run_holding(target) != target.resolve():
        return None

    the_live_picture_declared(target, bake=bake)
    return [LIVE_PICTURE]


def scene_behind_a_plate(target: Path, scenes: Path | None) -> Path | None:
    """The laid-out scene to open instead, when the target is an HCS plate.

    A scene the operator built and kept beside the plate is opened as it
    stands. Otherwise the scene is composed into ``scenes``, the viewer's own
    folder for this session, exactly as for a run of positions: nothing is
    written beside the plate (review S2).
    """
    if not target.is_dir() or scenes is None:
        return None

    from zmart_viewer.picture.arrangement import (
        the_description_of,  # deferred: pulls numpy and zarr
    )

    try:
        described, _ = the_description_of(target)
    except ValueError:
        return None

    if not isinstance(described.get("plate"), dict):
        return None

    from zmart_viewer.picture.built_picture import (  # deferred
        declare_a_built_picture,
        the_scene_folder_name,
    )

    name = the_scene_folder_name(target.name)
    existing = _scene_built_from(target.parent / "scenes" / name, target) or _scene_built_from(
        scenes / name, target
    )

    if existing is not None:
        return existing

    try:
        return declare_a_built_picture(scenes, target, name=target.name)
    except ValueError as why:
        raise CannotOpen(str(why)) from why


def scene_behind_a_run(target: Path, scenes: Path | None) -> Path | None:
    """The composed picture to open instead, when the target is a folder of
    position stores.
    """
    if not target.is_dir() or scenes is None:
        return None

    inside = positions_in(target)

    if inside is None:
        return None

    if len(inside) < 2:
        return None

    if all(one.endswith(".zmartview.zarr") for one in inside):
        return None  # Saved alternatives are opened, never composed or generated here.

    from zmart_viewer.picture.built_picture import (  # deferred
        declare_a_built_picture,
        the_scene_folder_name,
    )

    existing = _scene_built_from(scenes / the_scene_folder_name(target.name), target)

    if existing is not None:
        return existing

    try:
        return declare_a_built_picture(scenes, target, name=target.name)
    except ValueError:
        # The mosaic's own refusal: these stores are not one picture, so the
        # folder opens as separate positions. Any other error surfaces.
        return None


def relink_needed(store: Path) -> dict | None:
    """Whether this is a built scene whose raw data is no longer there."""
    described = store / "zarr.json"

    if not described.is_file():
        return None

    try:
        attrs = json.loads(described.read_text()).get("attributes", {})
        built_from = (attrs.get("zmart") or {}).get("built_from")
    except (OSError, ValueError):
        return None

    if not built_from:
        return None

    was = Path(built_from)
    still_a_source = was.is_dir() and (
        any((was / name).is_file() for name in DESCRIPTION_FILES)
        or any(one.is_dir() for one in was.glob("*.zarr"))
    )

    if still_a_source:
        return None

    return {
        "store": str(store),
        # Where the view stands, so the window can offer to rebuild it there;
        # worked out here because a page cutting a Windows path at "/" lost
        # its last letter (review N6).
        "parent": str(store.parent),
        "was": str(built_from),
        "name": store.name.removesuffix(".zmartview.zarr").removesuffix(".ome.zarr"),
        "baked": bool((attrs.get("zmart") or {}).get("baked")),
    }


def positions_in(folder: Path) -> tuple[str, ...] | None:
    """The names of the stores a folder holds, or None when it cannot be read now."""
    try:
        return tuple(sorted(one.name for one in folder.iterdir() if one.is_dir() and is_store(one)))
    except OSError:
        return None


class ScenesKeptUp:
    """The scenes this door composed over folders still being written, kept current.

    A folder of positions opened by its path is shown as one picture composed
    over its positions -- a description only, kilobytes, written into the
    viewer's own folder. It used to be composed once, at opening, so a
    position landing afterwards never appeared, while the guide promises it
    does within a second (review S2). Each followed scene is composed again
    whenever its folder gains or loses a position; :meth:`catch_up` does that
    and names the scenes it changed, so whoever calls it can tell the pages.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._followed: dict[Path, tuple[Path, tuple[str, ...] | None]] = {}

    def follow(self, scene: Path, data: Path) -> None:
        """Keep ``scene`` up with the positions in ``data`` from now on."""
        with self._lock:
            self._followed[scene] = (data, positions_in(data))

    def forget(self, scene: Path) -> None:
        """Stop keeping ``scene`` up, once it is no longer open."""
        with self._lock:
            self._followed.pop(scene, None)

    def catch_up(self) -> list[Path]:
        """Compose again every scene whose folder's positions changed; name those."""
        from zmart_viewer.picture.built_picture import declare_a_built_picture  # deferred

        with self._lock:
            followed = list(self._followed.items())

        changed = []

        for scene, (data, known) in followed:
            now = positions_in(data)

            if now is None or now == known:
                continue

            try:
                declare_a_built_picture(scene.parent, data, name=data.name)
                changed.append(scene)
            except (ValueError, OSError):
                # Not one picture any more, or not readable just now: what is
                # on screen stays, and the next change is tried afresh.
                logging.getLogger(__name__).warning(
                    "could not compose %s again from %s", scene, data, exc_info=True
                )

            with self._lock:
                if scene in self._followed:
                    self._followed[scene] = (data, now)

        return changed


def _scene_built_from(scene: Path, data: Path) -> Path | None:
    """The scene, when it honestly records ``data`` as what it was built from."""
    try:
        described = json.loads((scene / "zarr.json").read_text(encoding="utf-8"))
        built_from = described["attributes"]["zmart"]["built_from"]

        if Path(built_from).resolve() == data.resolve():
            return scene
    except (OSError, ValueError, KeyError, TypeError):
        pass

    return None
