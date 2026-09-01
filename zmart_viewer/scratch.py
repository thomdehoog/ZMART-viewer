"""The folders a running Viewer makes for itself, and who is allowed to delete them.

A Viewer composes scenes and replays into folders of its own under
``~/.zmart-viewer``. They are scratch: nothing in them is scientific data, and
they are meant to disappear when the Viewer that made them stops. Until this
module, they disappeared only on a *clean* stop. A Viewer that was killed — a
crash, a closed terminal, a power cut on the microscope PC — left its folders
behind for ever, in a place no cache limit ever looked at.

The fix is small and it rests on one fact: **a folder is owned while its
owner holds a lock on it, and unowned the moment it does not.** Every session
folder carries a lock file, and the Viewer that made it holds an exclusive
lock on that file for as long as it runs. The operating system releases the
lock when the process dies, however it dies. So on the next start, any session
folder whose lock can be taken has no owner and can be removed, and any whose
lock cannot be taken belongs to a Viewer that is still running and is left
alone. There is no guessing from process numbers, which are reused, and no
waiting for a folder to look old enough.

The word *lock* here means what it says: a mark the operating system keeps on
one file, that only one process can hold at a time. Taking it is instant, and
nothing is copied or written to take it.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

#: Where a Viewer keeps its own folders, unless told otherwise. Tests point it
#: somewhere throwaway through the constructor; nothing reads the environment.
DEFAULT_ROOT = Path.home() / ".zmart-viewer"

#: The two kinds of folder a running Viewer makes for itself.
KINDS = ("scenes", "replays")

LOCK_FILE = ".owner.lock"
OWNER_FILE = "owner.json"


def _take_the_lock(handle) -> bool:
    """Try to lock ``handle`` exclusively without waiting; True if it is ours now."""
    if sys.platform == "win32":
        import msvcrt

        try:
            # The same byte, from the same place, on both sides: the file is
            # never written, but saying so here keeps that from being silent.
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _let_go_of_the_lock(handle) -> None:
    if sys.platform == "win32":
        import msvcrt

        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


def _bytes_under(folder: Path) -> int:
    """Every byte of every ordinary file beneath ``folder``, symlinks not followed."""
    total = 0
    for parent, _dirs, files in os.walk(folder):
        for name in files:
            try:
                total += (Path(parent) / name).lstat().st_size
            except OSError:
                continue
    return total


@dataclass
class ScratchSession:
    """This Viewer's own folders: made on request, locked while it runs, gone after.

    ``root`` is where the folders live; every session folder sits at
    ``root / <kind> / session-XXXXXX``. One ``ScratchSession`` belongs to one
    running server and is closed when that server shuts down.
    """

    root: Path = field(default_factory=lambda: DEFAULT_ROOT)
    _held: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    def open(self, kind: str) -> Path:
        """The session folder for ``kind``, made and locked the first time it is wanted."""
        if kind not in KINDS:
            raise ValueError(f"{kind!r} is not a kind of scratch folder this viewer keeps")
        held = self._held.get(kind)
        if held is not None:
            return held[0]
        home = self.root / kind
        home.mkdir(parents=True, exist_ok=True)
        # The lock is taken before anything else is written, so a sweeper that
        # looks in between sees a locked folder, never a half-made one. There
        # is one gap the lock cannot close: a sweeper that listed this folder
        # in the instant between its creation and its lock takes the lock
        # first and removes it. The answer is not to argue with the sweeper
        # but to make another folder, which the sweeper has not seen.
        folder = handle = None
        for _ in range(3):
            folder = Path(tempfile.mkdtemp(prefix="session-", dir=home))
            try:
                handle = (folder / LOCK_FILE).open("a+")
            except OSError:
                handle = None
                continue
            if _take_the_lock(handle):
                break
            handle.close()
            handle = None
        if handle is None:
            raise RuntimeError(
                f"the viewer could not take a lock on its own scratch folder under {home}. "
                "This usually means the folder is on a share or a drive that does not "
                "support file locks; point the viewer's scratch at a local disk."
            )
        (folder / OWNER_FILE).write_text(
            json.dumps({"pid": os.getpid(), "started": time.time(), "kind": kind}, indent=1),
            encoding="utf-8",
        )
        self._held[kind] = (folder, handle)
        return folder

    def close(self) -> None:
        """Release every lock this session holds and remove its own folders."""
        for kind, (folder, handle) in list(self._held.items()):
            _let_go_of_the_lock(handle)
            handle.close()
            shutil.rmtree(folder, ignore_errors=True)
            self._held.pop(kind, None)

    def is_mine(self, folder: Path) -> bool:
        return any(Path(folder) == held[0] for held in self._held.values())

    def _is_a_session_folder_under_root(self, candidate: Path, kind: str) -> bool:
        """Only a real directory, directly beneath this root's kind folder, not a link."""
        try:
            if candidate.is_symlink() or not candidate.is_dir():
                return False
            home = (self.root / kind).resolve()
            return candidate.resolve().parent == home and candidate.name.startswith("session-")
        except OSError:
            return False

    def sweep_orphans(self, kind: str) -> dict:
        """Remove every session folder of ``kind`` that no running Viewer owns.

        Returns what was done, so a server can log it and a test can check it:
        ``reclaimed_bytes``, the ``removed`` folder names, and the ``kept``
        ones, which are the folders another process still holds the lock on.
        """
        home = self.root / kind
        done = {"reclaimed_bytes": 0, "removed": [], "kept": [], "stuck": []}
        if not home.is_dir():
            return done
        for candidate in sorted(home.iterdir()):
            if not self._is_a_session_folder_under_root(candidate, kind):
                # A symlink, a stray file, or something that resolves outside
                # this root is not ours to delete, whatever it is called.
                continue
            if self.is_mine(candidate):
                done["kept"].append(candidate.name)
                continue
            lock = candidate / LOCK_FILE
            if not lock.exists():
                # No lock file at all: a folder made before locks existed, or
                # one whose owner died before it could be locked. Nobody can
                # be holding it, so it is unowned.
                self._remove(candidate, done)
                continue
            try:
                handle = lock.open("a+")
            except OSError:
                done["kept"].append(candidate.name)
                continue
            try:
                if not _take_the_lock(handle):
                    done["kept"].append(candidate.name)
                    continue
                # The lock is held all the way through the removal. Letting go
                # first left an instant in which a Viewer starting at the same
                # moment could claim the folder and then have it deleted from
                # under it. On Windows an open file cannot be deleted, so the
                # folder is first renamed to something no sweep will ever look
                # at, and removed after the lock is closed.
                if sys.platform == "win32":
                    retired = candidate.with_name(f"retired-{candidate.name[len('session-'):]}")
                    try:
                        candidate.rename(retired)
                    except OSError:
                        done["kept"].append(candidate.name)
                        continue
                    _let_go_of_the_lock(handle)
                    handle.close()
                    handle = None
                    self._remove(retired, done, reported_as=candidate.name)
                else:
                    self._remove(candidate, done)
            finally:
                if handle is not None:
                    _let_go_of_the_lock(handle)
                    handle.close()
        return done

    @staticmethod
    def _remove(folder: Path, done: dict, *, reported_as: str | None = None) -> None:
        """Remove ``folder`` and count what actually went, not what was there."""
        before = _bytes_under(folder)
        shutil.rmtree(folder, ignore_errors=True)
        remaining = _bytes_under(folder) if folder.exists() else 0
        done["reclaimed_bytes"] += before - remaining
        name = reported_as or folder.name
        if folder.exists():
            done.setdefault("stuck", []).append(name)
        else:
            done["removed"].append(name)

    def managed_bytes(self) -> dict:
        """How much every session folder under this root holds, by kind and by folder."""
        told = {"root": str(self.root), "kinds": {}, "total": 0}
        for kind in KINDS:
            home = self.root / kind
            sessions = {}
            if home.is_dir():
                for candidate in sorted(home.iterdir()):
                    if not self._is_a_session_folder_under_root(candidate, kind):
                        continue
                    sessions[candidate.name] = _bytes_under(candidate)
            told["kinds"][kind] = {"sessions": sessions, "bytes": sum(sessions.values())}
            told["total"] += told["kinds"][kind]["bytes"]
        return told
