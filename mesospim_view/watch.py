"""Following a folder the microscope writes into.

A mesoSPIM run is one folder holding one ``<Sample>.ome.zarr`` group per
acquisition, and inside it one ``(t, c, z, y, x)`` store per tile (see the
``MP_OME_Zarr_TCZYX_Writer``). Tiles appear as they are acquired, and a time
lapse appends time points to the tiles already there. Two small classes turn
that into calls on a :class:`Viewer`:

- :class:`Acquisitions` lists the acquisitions of a folder, newest first;
- :class:`Watcher` polls one acquisition and shows what has landed since the
  last look: a new tile becomes a new source, a grown tile is read again.

Both work on plain paths and a viewer, so they are tested without Qt; the
window in ``window.py`` only drives them from a timer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .omezarr import NotAStore, read_store
from .viewer import Viewer

STORE_SUFFIX = ".ome.zarr"


def _is_zarr_group(path: Path) -> bool:
    return path.is_dir() and ((path / "zarr.json").is_file() or (path / ".zgroup").is_file())


@dataclass(frozen=True)
class Acquisition:
    path: Path
    started: float  # when the folder appeared, as a timestamp

    @property
    def name(self) -> str:
        return (
            self.path.name[: -len(STORE_SUFFIX)]
            if self.path.name.endswith(STORE_SUFFIX)
            else self.path.name
        )


class Acquisitions:
    """The acquisitions inside a data folder, newest first."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()

    def list(self) -> list[Acquisition]:
        found = []
        try:
            entries = list(os.scandir(self.root))
        except OSError:
            return found
        for entry in entries:
            path = Path(entry.path)
            if not entry.name.endswith(STORE_SUFFIX) or not _is_zarr_group(path):
                continue
            # A tile store has multiscales itself; an acquisition holds tile stores.
            try:
                read_store(path)
            except NotAStore:
                found.append(Acquisition(path=path, started=entry.stat().st_ctime))
        return sorted(found, key=lambda a: (a.started, a.name), reverse=True)

    def newest(self) -> Acquisition | None:
        listed = self.list()
        return listed[0] if listed else None


@dataclass
class Watcher:
    """Keeps one acquisition on a viewer up to date with the disk.

    Every :meth:`poll` looks at the acquisition's tile stores. A store seen for
    the first time is added to the acquisition's layer; a store whose shape has
    changed (a time point appended) is added again, which makes the viewer read
    it afresh. Stores that cannot be read yet -- being created at that very
    moment -- are simply looked at again next time.
    """

    viewer: Viewer
    acquisition: Path
    layer: str | None = None
    shapes: dict[Path, tuple[int, ...]] = field(default_factory=dict)

    @property
    def layer_name(self) -> str:
        return self.layer or Acquisition(self.acquisition, 0).name

    def poll(self) -> list[Path]:
        """Bring the viewer up to date; return the stores added or re-read."""
        changed = []
        for path in self.stores():
            try:
                store = read_store(path)
            except NotAStore:
                continue
            if self.shapes.get(path) == store.shape:
                continue
            self.viewer.add(path, layer=self.layer_name)
            self.shapes[path] = store.shape
            changed.append(path)
        return changed

    def stores(self) -> list[Path]:
        try:
            entries = sorted(os.scandir(self.acquisition), key=lambda e: e.name)
        except OSError:
            return []
        return [Path(e.path) for e in entries if e.is_dir() and e.name.endswith(STORE_SUFFIX)]

    def forget(self) -> None:
        """Take this acquisition off the viewer."""
        self.viewer.remove(self.layer_name)
        self.shapes.clear()


class Follower:
    """What the Data viewer window does, without the window.

    Keeps a viewer on the newest acquisition of a folder as new ones appear,
    unless an older one was chosen, and shows what lands in whichever is on
    screen. The window binds a dropdown and a timer to this; everything that
    can go wrong is here and testable without Qt.
    """

    def __init__(self, viewer: Viewer, root: str | Path) -> None:
        self.viewer = viewer
        self.acquisitions = Acquisitions(root)
        self.listed: list[Acquisition] = []
        self.watcher: Watcher | None = None
        self.following = True

    @property
    def root(self) -> Path:
        return self.acquisitions.root

    @property
    def names(self) -> list[str]:
        return [a.name for a in self.listed]

    @property
    def shown(self) -> Path | None:
        return self.watcher.acquisition if self.watcher else None

    @property
    def shown_index(self) -> int:
        shown = self.shown
        return next((i for i, a in enumerate(self.listed) if a.path == shown), -1)

    def poll(self) -> bool:
        """One look at the disk; True when the list of acquisitions changed."""
        listed = self.acquisitions.list()
        relisted = [a.path for a in listed] != [a.path for a in self.listed]
        self.listed = listed
        if self.following and listed and self.shown != listed[0].path:
            self.show(listed[0])
        if self.watcher is not None:
            self.watcher.poll()
        return relisted

    def show(self, acquisition: Acquisition) -> None:
        if self.watcher is not None:
            if self.watcher.acquisition == acquisition.path:
                return
            self.watcher.forget()
        self.watcher = Watcher(self.viewer, acquisition.path)
        self.watcher.poll()
        self.viewer.fit()

    def choose(self, index: int) -> None:
        """The operator picked an entry of the list: the first one means follow again."""
        if 0 <= index < len(self.listed):
            self.following = index == 0
            self.show(self.listed[index])

    def follow_latest(self) -> None:
        self.following = True
        self.poll()
