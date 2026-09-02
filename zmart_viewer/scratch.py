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

#: How much every session folder under one root may hold together, in bytes.
#: Five gibibytes, which is the provisional operational default the design
#: settled on: large enough for a day of replays and composed scenes, small
#: enough that a forgotten Viewer cannot quietly fill a microscope PC's disk.
ROOT_LIMIT_BYTES = 5 * 1024 ** 3

#: How much a derived picture may hold, as a share of the bytes it was made
#: from. A composed scene or a baked coarse ground is a convenience, and a
#: convenience that costs more than a tenth of the acquisition it describes
#: is not one. Replays are a copy of a dataset by nature, so this share does
#: not apply to them; the root limit above does.
SHARE_OF_SOURCE = 0.10

#: The two prefixes a folder under a kind may carry. ``session-`` is a folder
#: some Viewer made for itself; ``retired-`` is one a sweep on Windows renamed
#: out of the way before removing it, and could not remove.
SESSION_PREFIX = "session-"
RETIRED_PREFIX = "retired-"


class OutOfRoom(RuntimeError):
    """Raised when writing would take the Viewer's scratch past one of its limits.

    The message is a whole sentence for the operator: which limit, what was
    asked for, and what is already held.
    """


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
    #: The two limits, as plain numbers so a test can make them tiny.
    root_limit_bytes: int = ROOT_LIMIT_BYTES
    share_of_source: float = SHARE_OF_SOURCE
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

    def _is_a_session_folder_under_root(
        self, candidate: Path, kind: str, *, prefix: str = SESSION_PREFIX
    ) -> bool:
        """Only a real directory, directly beneath this root's kind folder, not a link."""
        try:
            if candidate.is_symlink() or not candidate.is_dir():
                return False
            home = (self.root / kind).resolve()
            return candidate.resolve().parent == home and candidate.name.startswith(prefix)
        except OSError:
            return False

    def room_for(self, kind: str, *, wanted_bytes: int, source_bytes: int | None = None) -> str | None:
        """Whether ``wanted_bytes`` more may be written under ``kind``.

        Answers nothing when there is room, and otherwise one plain sentence
        saying which limit would be passed. Two limits are checked. The root
        limit is what every session folder under this root holds together,
        replays and scenes alike, plus what is asked for now. The share limit
        applies when ``source_bytes`` is given -- the size of the acquisition a
        derived picture is being made from -- and refuses a derivative larger
        than :data:`SHARE_OF_SOURCE` of it. Nothing is written by asking.
        """
        wanted = max(0, int(wanted_bytes))
        if source_bytes is not None and kind == "scenes":
            allowed = int(self.share_of_source * max(0, int(source_bytes)))
            if wanted > allowed:
                return (
                    f"the {kind} folder would hold {wanted:,} bytes, more than "
                    f"{self.share_of_source:.0%} of the {int(source_bytes):,} bytes of the "
                    "acquisition it is made from. A derived picture that costs more than "
                    "that is not worth keeping automatically; open the run without it."
                )
        held = self.managed_bytes()["total"]
        if held + wanted > self.root_limit_bytes:
            return (
                f"the viewer's own folders under {self.root} hold {held:,} bytes and "
                f"{wanted:,} more were asked for, which is past the limit of "
                f"{self.root_limit_bytes:,} bytes. Close a Viewer or remove old replays "
                "and scenes there, then try again."
            )
        return None

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
                    retired = candidate.with_name(
                        f"{RETIRED_PREFIX}{candidate.name[len(SESSION_PREFIX):]}"
                    )
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
        # And the folders an earlier sweep on Windows renamed but could not
        # remove. Nobody holds a lock on a retired folder -- the sweep that
        # renamed it had already let go -- so removal is simply tried again,
        # and a folder that still will not go is reported as stuck rather
        # than forgotten. Before this they were invisible to every sweep and
        # to the tally alike, which is exactly how scratch grows unnoticed.
        for candidate in sorted(home.iterdir()):
            if self._is_a_session_folder_under_root(candidate, kind, prefix=RETIRED_PREFIX):
                self._remove(candidate, done)
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
        told = {
            "root": str(self.root), "kinds": {}, "total": 0,
            "limits": {"root_bytes": self.root_limit_bytes, "share_of_source": self.share_of_source},
        }
        for kind in KINDS:
            home = self.root / kind
            sessions = {}
            retired = {}
            if home.is_dir():
                for candidate in sorted(home.iterdir()):
                    if self._is_a_session_folder_under_root(candidate, kind):
                        sessions[candidate.name] = _bytes_under(candidate)
                    elif self._is_a_session_folder_under_root(candidate, kind, prefix=RETIRED_PREFIX):
                        # Renamed by a sweep that could not finish removing it.
                        # Still on the disk, so still counted.
                        retired[candidate.name] = _bytes_under(candidate)
            told["kinds"][kind] = {
                "sessions": sessions,
                "retired": retired,
                "bytes": sum(sessions.values()) + sum(retired.values()),
            }
            told["total"] += told["kinds"][kind]["bytes"]
        return told
