"""Writing and removing files on a machine where something else glances at them.

Two small chores come up all over the engine, and this module is the one place
they are done.

The first is **patience with a brief hold**. A microscope PC runs software that
opens files the moment they change: a virus scanner reading what was just
written, a search indexer cataloguing it, and the viewer itself, which reads
the picture while the acquisition writes it. On Windows a file that somebody
else has open cannot be renamed over or deleted; the operating system answers
``Access is denied`` or ``The process cannot access the file`` on ground that
is free again a few milliseconds later. On Linux and macOS none of this
happens, so the helpers here do nothing special there. Measured on a lab PC, a
ten-thousand-position run watched live died about twenty tiles in before this
patience was added.

The second is **putting a file in place in one step**. Writing straight over a
file leaves a moment in which a reader sees half of the old contents and half
of the new. Instead the new contents are written beside it under a temporary
name and then renamed over the top. Renaming is the one filesystem operation
that is genuinely all-or-nothing, on Windows as well as elsewhere, so a reader
looking at the same moment sees either the whole old file or the whole new
one, never a torn mixture. That matters here because many of these files are
what tells zarr that a folder holds a picture at all: half of one is not a
slightly wrong image, it is no image.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path

#: The Windows error numbers that mean "something briefly has this file open":
#: ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION and
#: ERROR_DIR_NOT_EMPTY. The last one appears when a folder is being removed
#: and one of its files is held: the delete of that file pends, so the parent
#: is still "not empty" for a moment.
_BRIEF_HOLDS = (5, 32, 33, 145)

#: The same refusal as Python's own error numbers see it: EIO and EACCES. A
#: ``PermissionError`` raised by Python itself, rather than by Windows, carries
#: one of these and no Windows number, and still means a reader's brief hold.
_BRIEF_HOLDS_AS_ERRNO = (5, 13)

#: How long a hold is waited out before it is treated as a real refusal.
_PATIENCE_S = 10.0

#: The first pause between two attempts, and the longest one. The pause doubles
#: each time, so a hold of a few milliseconds costs a few milliseconds, and a
#: hold of seconds is not polled thousands of times.
_FIRST_PAUSE_S = 0.002
_LONGEST_PAUSE_S = 1.0


def done_despite_brief_holds(operation, *arguments):
    """Perform one file operation, waiting out a brief hold on Windows.

    ``operation`` is called with ``arguments``, and whatever it returns is
    returned. If it fails because another program briefly has the file open,
    which only happens on Windows, it is tried again after a short pause, for
    up to ten seconds. Any other failure, and a hold that is still there after
    ten seconds, is raised as it always would have been. On other operating
    systems the operation is simply called once.
    """
    deadline = time.monotonic() + _PATIENCE_S
    pause = _FIRST_PAUSE_S

    while True:
        try:
            return operation(*arguments)
        except OSError as problem:
            held = os.name == "nt" and (
                getattr(problem, "winerror", None) in _BRIEF_HOLDS
                or (
                    isinstance(problem, PermissionError)
                    and problem.errno in _BRIEF_HOLDS_AS_ERRNO
                )
            )

            if not held or time.monotonic() >= deadline:
                raise

            time.sleep(pause)
            pause = min(pause * 2, _LONGEST_PAUSE_S)


def written_despite_brief_holds(write) -> None:
    """Make one write, waiting out a brief hold on Windows.

    ``write`` is called with no arguments. It is the same patience as
    :func:`done_despite_brief_holds`, kept under the name the record code has
    always used for it.
    """
    done_despite_brief_holds(write)


def rmtree_despite_brief_holds(tree: str | Path) -> None:
    """Remove a folder tree, waiting out a brief hold on Windows.

    A tree that is already gone counts as removed. Everything else is
    :func:`shutil.rmtree` with the patience of :func:`done_despite_brief_holds`:
    on Windows a file being deleted only truly goes when the last program
    holding it lets go, so a tree being removed under a scanner's glance
    refuses to come down for a moment, and is simply tried again.
    """
    try:
        done_despite_brief_holds(shutil.rmtree, tree)
    except FileNotFoundError:
        return


def push_directory_to_disk(folder: Path) -> None:
    """Make a renamed directory entry durable where the platform permits it.

    Flushing a file protects its contents. On Linux and macOS the folder that
    holds it must be flushed as well, or a power cut can forget the rename even
    though the file itself was safe. Windows does not offer Python an
    equivalent operation on a folder; ``os.replace`` is still all-or-nothing
    there, but durability through a power cut remains a property to measure on
    the target filesystem rather than a promise made by this helper.
    """
    if os.name == "nt":  # pragma: no cover - exercised on the Windows target
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(folder, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def put_text_in_place(destination: Path, text: str, *, pushed_to_disk: bool = False) -> None:
    """Put new text contents into a file in one indivisible step.

    The text is written to a temporary file beside ``destination`` and then
    renamed over it, so a reader never sees a half-written file. The rename is
    made with :func:`done_despite_brief_holds`, because these files are
    rewritten on every commit while a live viewer reads them over and over.
    If anything goes wrong, the temporary file is removed again, so a failed
    write leaves no litter beside the data.

    With ``pushed_to_disk=True`` the contents are pushed all the way to the
    disk before the rename, and the folder entry after it. That costs a little
    time and matters for the records a run cannot afford to lose: without it
    the operating system may still hold the contents in memory while the
    rename has already happened, so a power cut could leave a record that
    points confidently at data which never reached the platter. Files that are
    rebuilt from the data anyway can leave it off.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        dir=destination.parent,
        prefix=destination.name + ".",
        suffix=".tmp",
        delete=False,
        encoding="utf-8",
    )
    try:
        with handle as writing:
            writing.write(text)
            if pushed_to_disk:
                writing.flush()
                os.fsync(writing.fileno())
        done_despite_brief_holds(os.replace, handle.name, destination)
        if pushed_to_disk:
            push_directory_to_disk(destination.parent)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def put_json_in_place(
    destination: Path, value, *, pushed_to_disk: bool = False, indent: int | None = None
) -> None:
    """Write ``value`` as JSON into a file in one indivisible step.

    This is :func:`put_text_in_place` for the many small JSON descriptions the
    engine keeps beside the pixels. ``indent`` is passed to :func:`json.dumps`,
    so ``None`` writes the compact form and ``2`` the readable one.
    """
    put_text_in_place(destination, json.dumps(value, indent=indent), pushed_to_disk=pushed_to_disk)
