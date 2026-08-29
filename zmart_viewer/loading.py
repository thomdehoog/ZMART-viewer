"""One door: classify what a path is, then open it the one right way.

Every way into the viewer — the load window, the CLI, the replay, a live
run binding — goes through :func:`load`. It decides what the path holds
(a plate, a run of positions, a built scene, a live run, a plain store)
and answers with what the library should open. A path that cannot be
opened raises :class:`CannotOpen` with the reason in plain words.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from zmart_live.gateway import live_run_holding

from .live_config import LIVE_PICTURE, the_live_picture_declared
from .stores import DESCRIPTION_FILES, is_store


class CannotOpen(Exception):
    """A refusal at the door, with the reason and any structured detail."""

    def __init__(self, reason: str, **detail):
        super().__init__(reason)
        self.detail = detail


@dataclass
class Opened:
    """What the library should open: the target, and — for a live run —
    the name of its served picture.
    """

    target: Path
    names: list[str] | None = None


def load(path: str | Path, *, bake: bool = False, scenes: Path | None = None) -> Opened:
    """The one door. ``bake`` writes a live run's coarse pyramid as files;
    ``scenes`` is where a composed description may be written for a run of
    positions opened directly (left None, such a run is refused).
    """
    asked = Path(path).expanduser()
    target = scene_behind_a_plate(asked) or scene_behind_a_run(asked, scenes) or asked
    relink = relink_needed(target)
    if relink is not None:
        raise CannotOpen(
            f"this viewer was built from {relink['was']}, and nothing is "
            "there any more -- point it at the raw data again",
            relink=relink,
        )
    return Opened(target, names=live_run_view(target, bake=bake))


def live_run_view(target: Path, *, bake: bool = False) -> list[str] | None:
    """The one store a live run's folder is opened by: its governed picture,
    declared on first sight. None means the target is not a live run.
    """
    if live_run_holding(target) != target.resolve():
        return None
    the_live_picture_declared(target, bake=bake)
    return [LIVE_PICTURE]


def scene_behind_a_plate(target: Path) -> Path | None:
    """The laid-out scene to open instead, when the target is an HCS plate."""
    if not target.is_dir():
        return None
    from .mosaic import _the_description_of  # deferred: pulls numpy and zarr

    try:
        described, _ = _the_description_of(target)
    except ValueError:
        return None
    if not isinstance(described.get("plate"), dict):
        return None
    from .declare import declare_a_built_picture, the_scene_folder_name  # deferred

    scenes = target.parent / "scenes"
    existing = _scene_built_from(scenes / the_scene_folder_name(target.name), target)
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
    try:
        inside = [one for one in sorted(target.iterdir()) if one.is_dir() and is_store(one)]
    except OSError:
        return None
    if len(inside) < 2:
        return None
    from .declare import declare_a_built_picture, the_scene_folder_name  # deferred

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
        "was": str(built_from),
        "name": store.name.removesuffix(".zmartview.zarr").removesuffix(".ome.zarr"),
        "baked": bool((attrs.get("zmart") or {}).get("baked")),
    }


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
