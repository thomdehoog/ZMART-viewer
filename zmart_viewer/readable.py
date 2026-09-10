"""Immutable HTTP generations while the working aggregate is being rebuilt.

Aggregate chunks are atomically replaced by the baker, so hard links preserve
their committed bytes. Original position files may be edited in place by an
external producer: copy new source revisions, and link only unchanged snapshots.
"""

import json
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
from pathlib import Path
from uuid import uuid4

_guard = threading.RLock()
_readers = {}
_retired = set()
_removing = set()
_references = {}
_scope = ContextVar("readable_scope", default=None)


def with_readers(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with ExitStack() as stack:
            token = _scope.set(stack)
            try:
                return function(*args, **kwargs)
            finally:
                _scope.reset(token)

    return wrapped


def pinned(store):
    stack = _scope.get()
    if stack is None:
        raise RuntimeError("Snapshot metadata needs a reader scope")
    return stack.enter_context(reading(store))


def _tree(source, target, *, link):
    def files(source, target):
        target.mkdir(parents=True, exist_ok=True)
        for entry in source.iterdir():
            if entry.name in {".readable", "readable.json", "pending.json", ".bake.lock"}:
                continue
            if entry.name.startswith(".patching-") or entry.name.endswith(
                (".baking", ".publishing")
            ):
                continue
            destination = target / entry.name
            if entry.is_dir():
                yield from files(entry, destination)
            else:
                yield entry, destination

    def copy(pair):
        entry, destination = pair
        if link:
            try:
                os.link(entry, destination)
                return
            except OSError:
                pass
        shutil.copyfile(entry, destination)

    work = list(files(source, target))
    if len(work) < 64:
        for pair in work:
            copy(pair)
    else:
        # Bound filesystem concurrency; small files otherwise serialize Windows
        # file-open latency for every source chunk before a view can advance.
        with ThreadPoolExecutor(max_workers=8) as workers:
            for _ in workers.map(copy, work):
                pass


@contextmanager
def _source_copies(store):
    """Share immutable source revisions across Top and Slice without recopying pixels."""
    with ExitStack() as readers:
        available = {}
        for peer in store.parent.glob("*.zmartview.zarr"):
            snapshot = readers.enter_context(reading(peer))
            if snapshot is None:
                continue
            state = json.loads((snapshot / "publication.json").read_text(encoding="utf-8"))
            originals = state.get("source_stores", {})
            for tile in state["mosaic"]["tiles"]:
                name = tile["name"]
                if name in originals:
                    available[(originals[name], state["versions"].get(name))] = Path(tile["store"])
        yield available


def _remember(path, state=None):
    if path not in _references:
        if state is None:
            state = json.loads((path / "publication.json").read_text(encoding="utf-8"))
        _references[path] = {Path(tile["store"]) for tile in state["mosaic"]["tiles"]}


def current(store):
    store = Path(store).resolve()
    try:
        name = json.loads((store / "readable.json").read_text(encoding="utf-8"))["generation"]
    except FileNotFoundError:
        return None
    target = (store / ".readable" / name).resolve()
    if target.parent != store / ".readable" or not target.name.startswith("r-"):
        raise ValueError("Readable generation escapes its aggregate")
    return target


def _remove(path):
    # Only private generation directories are eligible for recursive deletion.
    path = path.resolve()
    if path.parent.name != ".readable" or not path.name.startswith("r-"):
        raise ValueError("Not a private readable generation")
    from . import pieces
    from .server import _Handler

    with _guard:
        if path not in _references and (path / "publication.json").exists():
            _remember(path)
    pieces.forget(path)
    _Handler.forget_described(path)
    shutil.rmtree(path)
    with _guard:
        candidates = _references.pop(path, set())
        used = set().union(*_references.values())
    for source in candidates - used:
        source = source.resolve()
        if (
            source.parent.name != "sources"
            or source.parent.parent.name != ".readable"
            or not source.name.startswith("s-")
            or source.parents[3] != path.parents[2]
        ):
            continue
        if source.exists():
            shutil.rmtree(source)


def _cleanup():
    with _guard:
        ready = _retired - _removing - _readers.keys()
        _removing.update(ready)
    for path in ready:
        try:
            _remove(path)
        except OSError:
            # A Windows reader outside this server may still hold a file.
            # Leave this generation eligible for cleanup after the next commit.
            continue
        finally:
            with _guard:
                _removing.discard(path)
                if not path.exists():
                    _retired.discard(path)


def _cleanup_later():
    threading.Thread(target=_cleanup, daemon=True).start()


@contextmanager
def reading(store):
    with _guard:
        path = current(store)
        if path is not None:
            _remember(path)
            _readers[path] = _readers.get(path, 0) + 1
    try:
        yield path
    finally:
        cleanup = False
        with _guard:
            if path is not None:
                _readers[path] -= 1
                if not _readers[path]:
                    del _readers[path]
                    if path in _retired:
                        cleanup = True
        if cleanup:
            _cleanup_later()


def publish(store, state, *, commit_ledger):
    """Atomically switch HTTP readers only after every snapshot file is ready."""
    store = Path(store).resolve()
    made = store / ".readable" / ("r-" + uuid4().hex)
    made.mkdir(parents=True)
    with _guard:
        _references[made] = set()
    try:
        # Clone only metadata and baked levels, never the private snapshot tree.
        _tree(store, made, link=True)
        frozen = deepcopy(state)
        frozen["source_stores"] = {
            tile["name"]: tile["store"] for tile in frozen["mosaic"]["tiles"]
        }
        with _source_copies(store) as available:
            for tile in frozen["mosaic"]["tiles"]:
                original = Path(tile["store"])
                prior = available.get((tile["store"], state["versions"].get(tile["name"])))
                target = prior or (store / ".readable" / "sources" / ("s-" + uuid4().hex))
                with _guard:
                    _references[made].add(target)
                if prior is None:
                    _tree(original, target, link=False)
                tile["store"] = target.as_posix()
                for copy in tile["copies"]:
                    copy["held_in"] = (
                        target / Path(copy["held_in"]).relative_to(original)
                    ).as_posix()
        # Replace, rather than overwrite, the hard-linked old publication ledger.
        ledger = made / "publication.json"
        ledger.unlink(missing_ok=True)
        ledger.write_text(json.dumps(frozen), encoding="utf-8")
        # Persist the producer ledger before exposing the completed generation.
        # A failed ledger write must leave the previous HTTP image selected.
        commit_ledger()
        with _guard:
            pointer = store / "readable.json.publishing"
            pointer.write_text(json.dumps({"generation": made.name}), encoding="utf-8")
            os.replace(pointer, store / "readable.json")
            for path in made.parent.iterdir():
                if path != made and path.is_dir() and path.name.startswith("r-"):
                    _retired.add(path)
        _cleanup_later()
        return made
    except Exception:
        if current(store) != made and made.exists():
            _remove(made)
        raise
