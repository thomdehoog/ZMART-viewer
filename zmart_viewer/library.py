"""Which folders are open, held as numbered datasets.

The numbers guard serving: a request may only reach inside an open folder,
and a number is never reused, so a stale request cannot land on whatever
was opened next. Watched folders are re-read as a run writes into them;
a different acquisition appearing there becomes its own dataset, decided
from inside the stores, never from their names.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .stores import (
    _moments_folder,
    channel_of,
    declared_channels,
    discover,
    voxel_size,
    without_format_suffix,
)

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
                        (entry.name, entry.stat().st_mtime_ns, _described_at(Path(entry.path)))
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
