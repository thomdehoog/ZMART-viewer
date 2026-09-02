"""The local web server: one address for the page, the data, and the API.

It serves the built page, image pieces under ``/data`` (guarded so a
request only reaches inside an open folder), and small JSON commands
under ``/api``. A threaded stdlib server, localhost only, and by design
no route that talks to a microscope.
"""

from __future__ import annotations

import functools
import json
import math
import os
import queue
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from zmart_viewer.record.gateway import answer_from_a_live_run

# The other way a picture can exist without being written: built when asked
# for, rather than pointed at.
from . import live, loading, pieces
from .acquisition import CAPABILITIES
from .scratch import KINDS as SCRATCH_KINDS, ScratchSession, _bytes_under
from .contrast import (
    Measurements,
    _readability_problem,
    measure_here,
)
from .library import (
    DESCRIPTION_FILES,
    Library,
    _read_attrs_at,
    axis_names,
    channel_color,
    channel_of,
    channels,
    forget,
    label_images,
    layer_names,
    normalise_units,
    written_timepoints,
    zarr_scheme,
)
from .live import SourceRegistry, live_rows

_HERE = Path(__file__).resolve().parent
_FRONTEND_DIST = (_HERE.parent / "app" / "page" / "dist").resolve()
_ANNOTATIONS_FILE = "zmart-annotations.json"
_EMPTY_ANNOTATIONS = {"version": 1, "annotations": []}


def _the_version() -> str:
    """This Viewer's version as installed, or ``unknown`` from a bare checkout."""
    try:
        from importlib.metadata import version

        return version("zmart-viewer")
    except Exception:  # noqa: BLE001 -- a checkout that was never installed
        return "unknown"
# "bytes=0-99", "bytes=500-" or "bytes=-64": a start and end, an open end, or a
# suffix. Only single ranges are honoured, which is all the engine ever asks for.
_RANGE_HEADER = re.compile(r"^bytes=(\d*)-(\d*)$")


def _validate_annotations(payload: object) -> dict:
    """Return a small, safe annotation document or raise ValueError."""
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("expected annotation document version 1")

    items = payload.get("annotations")

    if not isinstance(items, list) or len(items) > 10_000:
        raise ValueError("annotations must be a list of at most 10000 items")

    clean = []
    seen = set()

    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each annotation must be an object")

        annotation_id = item.get("id")
        kind = item.get("type")

        if not isinstance(annotation_id, str) or not annotation_id or annotation_id in seen:
            raise ValueError("annotation ids must be unique non-empty strings")

        if kind not in {"point", "axis_aligned_bounding_box"}:
            raise ValueError("unsupported annotation type")

        coordinate_keys = ("point",) if kind == "point" else ("pointA", "pointB")
        result = {"id": annotation_id, "type": kind}

        for key in coordinate_keys:
            value = item.get(key)

            if (
                not isinstance(value, list)
                or not 1 <= len(value) <= 8
                or any(
                    isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
                    for x in value
                )
            ):
                raise ValueError(f"{key} must contain finite coordinates")

            result[key] = [float(x) for x in value]

        description = item.get("description", "")

        if not isinstance(description, str) or len(description) > 1000:
            raise ValueError("description must be a string of at most 1000 characters")

        result["description"] = description
        clean.append(result)
        seen.add(annotation_id)

    return {"version": 1, "annotations": clean}


# -- what the panel calls each open acquisition ---------------------------------


def bare_group_name(label: str) -> str:
    """An acquisition's own name, with the Viewer's decorations taken off.

    The panel decorates a group label when names collide -- a session prefix
    (``session-abc · overview.zmartview.zarr``), a copy number (``… (2)``) --
    and a store suffix follows the name. None of that is what the writer
    calls the acquisition, and the writer is who says when one is finished,
    so both sides compare the bare name.
    """
    bare = str(label).rsplit(" · ", 1)[-1]
    bare = re.sub(r" \(\d+\)$", "", bare)
    for suffix in (".zmartview.zarr", ".ome.zarr", ".zarr"):
        bare = bare.removesuffix(suffix)
    return bare


def _group_number_of(rel: str) -> int | None:
    """The dataset number at the front of a ``/data/<number>/…`` address, if any."""
    first = rel.split("/", 1)[0]
    return int(first) if first.isdigit() else None


def group_labels(datasets) -> dict[int, str]:
    """What to call each open dataset in the panel."""
    shared: dict[str, int] = {}

    for dataset in datasets:
        shared[dataset.name] = shared.get(dataset.name, 0) + 1

    qualified = {
        dataset.number: (
            dataset.name if shared[dataset.name] == 1 else f"{dataset.root.name} · {dataset.name}"
        )
        for dataset in datasets
    }
    labels: dict[int, str] = {}
    worn: dict[str, int] = {}

    for dataset in datasets:
        label = qualified[dataset.number]
        worn[label] = worn.get(label, 0) + 1
        labels[dataset.number] = label if worn[label] == 1 else f"{label} ({worn[label]})"

    return labels


class _StoppedByTheOperator(Exception):
    """Raised inside a build or replay loop when the operator asked to stop."""


class _Handler(SimpleHTTPRequestHandler):
    """Serve the built page, the image data, and the small JSON endpoints."""

    # Keep connections alive between requests. The viewer fetches hundreds of
    # small chunks; without this each one would open a fresh connection.
    protocol_version = "HTTP/1.1"

    wbufsize = 64 * 1024
    disable_nagle_algorithm = True

    def __init__(
        self,
        *args,
        data_dir: Path,
        site_dir: Path,
        config: dict,
        library=None,
        browse=None,
        bake_job=None,
        replay_job=None,
        scratch=None,
        allow_open: bool = True,
        live: bool = True,
        announcements=None,
        live_state=None,
        forget_measurements=None,
        open_from=None,
        liveness=None,
        **kwargs,
    ):
        self._data_dir = data_dir  # where drawn targets are saved
        self._open_from = open_from or data_dir
        self._library = library  # which folders may be read from, and what is in them
        self._browse = browse  # opens a native folder chooser, when one is available
        self._allow_open = allow_open  # may the operator change what is open?
        # One prebake at a time, shared by every request this server answers.
        # See _serve_bake for the shape of what it holds.
        self._bake_job = bake_job if bake_job is not None else {}
        # One replay at a time, for the same reason: "how far along?" must
        # mean one thing. See _serve_replay.
        self._replay_job = replay_job if replay_job is not None else {}
        # Where this viewer puts the pictures it composes for itself, shared
        # by every request it answers. See loading.scene_behind_a_run.
        self._scratch = scratch if scratch is not None else {}
        self._site_dir = site_dir  # the built page, served as the base directory
        self._live = live  # is the data still being written? decides what may be kept
        # How open pages are told that something has changed. See announcements.py.
        self._announcements = announcements or live.Announcements()
        # A cheap authoritative answer used for conditional catch-up after a
        # missed SSE hint.  It returns ``(document, etag)`` and touches no image.
        self._live_state = live_state
        # Asked afresh on each /api/config request rather than held as a fixed
        # answer, so a store written after the viewer opened can still appear.
        self._config = config
        self._forget_measurements = forget_measurements or (lambda closed: None)
        # Which acquisitions the writer has said are finished, by the bare
        # name of their group. Shared by every request, because the writer
        # says it once and every measurement afterwards has to know.
        self._liveness = liveness if liveness is not None else {"finished": set()}
        super().__init__(*args, directory=str(site_dir), **kwargs)

    def handle_one_request(self) -> None:
        """Serve one request, ignoring the client hanging up early."""
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def finish(self) -> None:
        """The request's last flush, with the same hang-up tolerance as above."""
        try:
            super().finish()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def send_response(self, code, message=None):
        """Every reply, with what the browser may keep of it."""
        super().send_response(code, message)

        if not self.path.startswith(("/data/", "/api/")):
            page = self.path in ("/", "/index.html") or self.path.endswith("/")
            self.send_header(
                "Cache-Control",
                "no-store" if page else "public, max-age=31536000, immutable",
            )

    # -- routing ---------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (name fixed by base class)

        if self.path.startswith("/data/"):
            self._serve_from_data()
            return

        if self.path.startswith("/api/"):
            self._serve_api_get()
            return

        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802

        if self.path.startswith("/api/"):
            self._serve_api_post()
            return

        self._send_empty(HTTPStatus.NOT_FOUND)

    def do_HEAD(self) -> None:  # noqa: N802
        """Answer "does this exist, and how big is it?" — headers only, no body."""
        if self.path.startswith("/data/") or self.path.startswith("/api/"):
            self.do_GET()
            return

        super().do_HEAD()

    def _wanted_range(self, total: int) -> tuple[int, int] | None:
        """The byte range asked for as ``(start, length)``, or ``None`` for all of it."""
        asked = self.headers.get("Range")

        if not asked:
            return None

        found = _RANGE_HEADER.match(asked.strip())

        if not found:
            # A range we do not understand is not an error: answering with the
            # whole file is always a correct response to a Range request.
            return None

        first, last = found.group(1), found.group(2)

        if not first:
            # "the last N bytes" -- how the index at the end of a shard is read.
            length = int(last or 0)

            if length == 0:
                raise ValueError("an empty suffix range cannot be satisfied")

            start = max(0, total - length)
            return start, total - start

        start = int(first)

        if start >= total:
            raise ValueError(f"range starts at {start}, past the end at {total}")

        end = int(last) if last else total - 1
        end = min(end, total - 1)
        return start, end - start + 1

    # -- image data ------------------------------------------------------

    def _serve_from_data(self) -> None:
        """Serve one file from an open OME-Zarr store under ``/data``."""
        rel = self.path[len("/data/") :].split("?", 1)[0].split("#", 1)[0]
        target = self._library.resolve(rel)

        if target is None:
            self._send_empty(HTTPStatus.FORBIDDEN)
            return

        live = answer_from_a_live_run(target)

        if live is not None:
            if not live.allowed:
                self._send_empty(HTTPStatus.NOT_FOUND)
                return

            if live.serving is not None:
                number = rel.partition("/")[0]
                root = self._library.resolve(f"{number}/.")
                source = live.serving.path.resolve()

                if root is None or (source != root and root not in source.parents):
                    self._send_empty(HTTPStatus.FORBIDDEN)
                    return

                if not source.is_file():
                    self._send_empty(HTTPStatus.NOT_FOUND)
                    return

                self._send_file(
                    source,
                    begins_at=live.serving.offset,
                    how_many=live.serving.length,
                )
                return

        if not target.is_file():
            if live is not None:
                self._send_empty(HTTPStatus.NOT_FOUND)
                return

            elsewhere = self._pointed_at(rel)

            if elsewhere is not None:
                target, begins_at, how_many = elsewhere
                self._send_file(target, begins_at=begins_at, how_many=how_many)
                return

            try:
                made = self._built(rel)
            except pieces.TemporarilyUnanswerable:
                self._send_empty(HTTPStatus.SERVICE_UNAVAILABLE)
                return

            if made is not None:
                self._send_bytes(made)
                return

            self._send_empty(HTTPStatus.NOT_FOUND)
            return

        governed = self._a_governed_piece_behind(target)

        if governed is not None:
            try:
                made = pieces.built_bytes_behind(*governed)
            except pieces.TemporarilyUnanswerable:
                self._send_empty(HTTPStatus.SERVICE_UNAVAILABLE)
                return

            if made is not None:
                self._send_bytes(made)
            else:
                self._send_empty(HTTPStatus.NOT_FOUND)

            return

        self._send_file(target)

    def _a_governed_piece_behind(self, target: Path) -> tuple[Path, str] | None:
        """The (store, piece address) when this FILE is a governed chunk."""
        parents = target.parents

        if len(parents) < 5:
            return None

        store = parents[4]
        parts = target.relative_to(store).parts

        if len(parts) != 5 or parts[1] != "c":
            return None

        if not all(one.isdecimal() for one in (parts[0], *parts[2:])):
            return None

        if not (store / "zarr.json").is_file():
            return None

        if not pieces.a_manifest_governs(store):
            return None

        return store, "/".join(parts)

    def _pointed_at(self, rel: str) -> tuple[Path, int, int | None] | None:
        """The file that really holds this piece, when the picture was never written."""
        number, _, rest = rel.partition("/")
        image, _, inside = rest.partition("/")

        if not inside:
            return None

        store = self._library.resolve(f"{number}/{image}")

        if store is None:
            return None

        found = pieces.pointed_bytes_behind(store, inside)

        if found is None:
            return None

        where = self._library.resolve(f"{number}/{found.path}")

        if where is None:
            return None

        return where, found.offset, found.length

    def _built(self, rel: str) -> bytes | None:
        """This piece, built now, when the picture it belongs to holds no pixels."""
        parts = rel.split("/")

        for arity in (7, 5):
            if len(parts) < arity + 2:
                continue

            suffix = "/".join(parts[-arity:])

            if pieces.the_piece_address(suffix) is None:
                continue

            store = self._library.resolve("/".join(parts[:-arity]))

            if store is None:
                return None

            return pieces.built_bytes_behind(store, suffix)

        return None

    def _send_bytes(self, body: bytes) -> None:
        """Answer with bytes that are not a file and never were."""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))

        if self._live:
            self.send_header("Cache-Control", "no-store")

        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, status: HTTPStatus) -> None:
        """Answer with a bare status, keeping the connection open for the next ask."""
        self.send_response(status)
        self.send_header("Content-Length", "0")

        if self._live:
            self.send_header("Cache-Control", "no-store")

        self.end_headers()

    _DESCRIBING_FILES = DESCRIPTION_FILES
    _described: dict[str, tuple[int, bytes]] = {}
    _described_lock = threading.Lock()

    @classmethod
    def forget_described(cls, store: Path) -> None:
        """Let go of the description files remembered for one closed store."""
        inside = str(store) + os.sep

        with cls._described_lock:
            for key in [key for key in cls._described if key.startswith(inside)]:
                del cls._described[key]

    def _send_file(self, target: Path, *, begins_at: int = 0, how_many: int | None = None) -> None:
        """Answer with a file, or with one stretch of bytes out of the middle of it."""
        describing = target.name in self._DESCRIBING_FILES
        data = self._read(target) if describing else None
        about = None if data is not None else target.stat()
        on_disk = len(data) if data is not None else about.st_size
        validator = self._a_live_pieces_identity(about)

        if validator and self.headers.get("If-None-Match") == validator:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("ETag", validator)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        total = (
            max(0, on_disk - begins_at)
            if how_many is None
            else max(0, min(how_many, on_disk - begins_at))
        )

        try:
            wanted = self._wanted_range(total)
        except ValueError:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{total}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if wanted is None:
            start, length = 0, total
        else:
            start, length = wanted

        if data is None:
            with target.open("rb") as handle:
                handle.seek(begins_at + start)
                body = handle.read(length)
        else:
            body = data[begins_at + start : begins_at + start + length]

        self.send_response(HTTPStatus.PARTIAL_CONTENT if wanted else HTTPStatus.OK)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        # Says a range may be asked for at all. Without it a well-behaved client
        # will not try, and a sharded store would be fetched a whole shard at a time.
        self.send_header("Accept-Ranges", "bytes")

        if wanted:
            self.send_header("Content-Range", f"bytes {start}-{start + len(body) - 1}/{total}")

        if validator:
            self.send_header("ETag", validator)
            self.send_header("Cache-Control", "no-cache")
        else:
            self.send_header("Cache-Control", self._how_long_to_keep(describing))

        self.end_headers()

        if self.command != "HEAD":
            self.wfile.write(body)

    _STAMP_STILL_MOVING_NS = 100_000_000

    def _a_live_pieces_identity(self, about: os.stat_result | None) -> str | None:
        """The validator a live piece of image may be revalidated against."""
        if not self._live or about is None:
            return None

        if time.time_ns() - about.st_mtime_ns < self._STAMP_STILL_MOVING_NS:
            return None

        return f'"{about.st_mtime_ns:x}-{about.st_size:x}"'

    def _how_long_to_keep(self, describing: bool) -> str:
        """How long the browser may keep a copy of what we are about to send."""
        if describing or self._live:
            return "no-cache" if describing else "no-store"

        return "max-age=31536000, immutable"

    def _read(self, target: Path) -> bytes:
        """The file's contents, remembering the small ones that describe a store."""
        if target.name not in self._DESCRIBING_FILES:
            return target.read_bytes()

        key = str(target)
        written = target.stat().st_mtime_ns

        with self._described_lock:
            remembered = self._described.get(key)

        if remembered is not None and remembered[0] == written:
            return remembered[1]

        data = normalise_units(target.read_bytes())

        with self._described_lock:
            self._described[key] = (written, data)

        return data

    # -- JSON endpoints --------------------------------------------------

    def _serve_api_get(self) -> None:
        if self.path.rstrip("/") == "/api/health":
            # More than a heartbeat: the writer beside this server asks here
            # what the Viewer can promise before it stops stamping windows of
            # its own. See ``acquisition.CAPABILITIES``.
            self._send_json(
                {"ok": True, "version": _the_version(), "capabilities": list(CAPABILITIES)}
            )
            return

        if self.path.rstrip("/") == "/api/events":
            self._serve_events()
            return

        if self.path.rstrip("/") == "/api/config":
            self._serve_config()
            return

        if self.path.rstrip("/") == "/api/scratch":
            # What this Viewer holds under its own root, by folder, in bytes,
            # and what the last start-up reclaimed. The point is that scratch
            # is counted somewhere: a folder nothing ever measures is the one
            # that quietly fills a disk.
            sessions = self._scratch.get("sessions")
            told = sessions.managed_bytes() if sessions is not None else {
                "root": str(ScratchSession().root), "kinds": {}, "total": 0,
            }
            told["swept_at_start"] = self._scratch.get("swept", {})
            told["refused_bakes"] = dict(live.REFUSED_BAKES)
            self._send_json(told)
            return

        if self.path.rstrip("/") == "/api/live-state":
            self._serve_live_state()
            return

        if self.path.rstrip("/") == "/api/annotations":
            path = self._data_dir / _ANNOTATIONS_FILE

            try:
                payload = _validate_annotations(json.loads(path.read_text("utf-8")))
            except FileNotFoundError:
                payload = _EMPTY_ANNOTATIONS
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                # Named plainly rather than as "the sidecar", which is our word for
                # it and means nothing to somebody reading it for the first time.
                self._send_json(
                    {
                        "error": (
                            "the file of marked places beside the images "
                            f"({_ANNOTATIONS_FILE}) could not be read, so none of "
                            "them are shown. It is still there and has not been "
                            "changed."
                        )
                    },
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return

            self._send_json(payload)
            return

        self._send_empty(HTTPStatus.NOT_FOUND)

    def _serve_events(self) -> None:
        """Hold a connection open and write a line whenever something changes."""
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        # This reply is terminated by closing the connection, so it cannot be one
        # of several on a kept-alive connection.
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()

        waiting = self._announcements.listen()

        try:
            self.wfile.write(b": listening\n\n")
            self.wfile.flush()

            while True:
                try:
                    message = waiting.get(timeout=live.QUIET_HEARTBEAT_S)
                except queue.Empty:
                    message = live.HEARTBEAT

                if message is None:
                    return  # the server is shutting down

                self.wfile.write(message)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            # The page was closed or navigated away. Ordinary, not an error.
            pass
        finally:
            self._announcements.stop_listening(waiting)

    def _serve_measurement(self, payload: object) -> None:
        """Measure the brightness of the part of a picture on screen."""
        asked = payload if isinstance(payload, dict) else {}
        source = asked.get("source")

        if not isinstance(source, str) or not source.strip():
            self._send_json({"error": "which picture to measure is needed"}, HTTPStatus.BAD_REQUEST)
            return

        rel = source.split("/data/", 1)[-1].split("|", 1)[0].strip("/")
        store = self._library.resolve(rel)

        if store is None or not store.is_dir():
            self._send_json({"error": "that picture is not open here"}, HTTPStatus.NOT_FOUND)
            return

        box = asked.get("box")

        try:
            (top, left), (bottom, right) = box
            corners = ((float(top), float(left)), (float(bottom), float(right)))
        except (TypeError, ValueError):
            self._send_json(
                {
                    "error": "the part of the picture in view is needed, as "
                    "fractions: [[top, left], [bottom, right]]"
                },
                HTTPStatus.BAD_REQUEST,
            )
            return

        channel = asked.get("channel")
        channel = channel if isinstance(channel, int) else None
        found = measure_here(store, channel=channel, box=corners)

        if found is None:
            # Nothing measurable in the part on screen. Say why, because an
            # empty answer for a store that cannot be read at all would leave
            # the panel waiting for pixels that are never coming.
            problem = _readability_problem(store)
            self._send_json(
                {
                    "empty": True,
                    "measurementState": "unreadable" if problem is not None else "waiting",
                    "measurementError": problem,
                }
            )
            return

        low, high = found["window"]
        self._send_json(
            {
                "window": {"low": low, "high": high},
                "histogram": found["histogram"],
                "measurementState": self._how_final_a_measurement_of(rel),
            }
        )

    def _how_final_a_measurement_of(self, rel: str) -> str:
        """``settled`` once the acquisition can no longer change, ``provisional`` until then.

        This used to be decided by whether the store's smallest, whole-field
        copy had been written, and that answered the wrong question. A single
        position is written whole in one go, so its first field looked
        "settled" while the scan was still landing fields around it; and a
        picture composed over positions holds no copy of its own, so it looked
        "provisional" for ever, long after the scan had ended. What the word
        is meant to tell an operator is whether the picture may still grow --
        which is a fact about the acquisition, not about this storage form.
        So it is taken from the acquisition: a server over finished data says
        settled for everything, and a server over a live run says settled only
        for the acquisitions the writer has told it are finished.
        """
        return "settled" if self._is_finished(_group_number_of(rel)) else "provisional"

    def _is_finished(self, root_number: int | None) -> bool:
        if not self._live:
            return True
        if root_number is None or self._library is None:
            return False
        named = group_labels(self._library.datasets()).get(root_number)
        return named is not None and bare_group_name(named) in self._liveness.get("finished", ())

    def _serve_announcement(self, payload: object) -> None:
        """Accept the legacy optional hint used by generic live folders."""
        in_place = bool(isinstance(payload, dict) and payload.get("wrote_image_in_place"))
        # The writer may also say that an acquisition is over: nothing more
        # will be written under that name. From then on a measurement of it is
        # the last word rather than a reading of what has landed so far.
        finished = payload.get("finished") if isinstance(payload, dict) else None
        finished = [finished] if isinstance(finished, str) else finished
        if isinstance(finished, list):
            for name in finished:
                if isinstance(name, str) and name:
                    self._liveness.setdefault("finished", set()).add(bare_group_name(name))
        covering = None

        try:
            if self._library is not None:
                covering = self._library.revision()
        except Exception:
            covering = None

        self._send_json(
            {
                "told": self._announcements.say_something_changed(
                    image_written_in_place=in_place,
                    covering=covering,
                )
            }
        )

    def _serve_config(self) -> None:
        """Tell the page which stores to open and how to display them."""
        self._send_json(self._config())

    def _serve_live_state(self) -> None:
        """Return bounded committed revisions, conditionally when unchanged."""
        if self._live_state is None:
            self._send_empty(HTTPStatus.NOT_FOUND)
            return

        document, etag = self._live_state()

        if self.headers.get("If-None-Match") == etag:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self._send_json(
            document,
            headers={"ETag": etag, "Cache-Control": "no-store"},
        )

    def _serve_api_post(self) -> None:
        """Handle the things the viewer asks Python to do."""
        route = self.path.rstrip("/")

        if route not in {
            "/api/browse",
            "/api/stores/open",
            "/api/stores/close",
            "/api/stores/list",
            "/api/stores/construct",
            "/api/stores/construct-status",
            "/api/stores/construct-cancel",
            "/api/stores/replay",
            "/api/stores/replay-status",
            "/api/stores/replay-cancel",
            "/api/measure",
            "/api/annotations",
            "/api/announce",
        }:
            self._send_empty(HTTPStatus.NOT_FOUND)
            return

        if (
            route
            in (
                "/api/stores/list",
                "/api/stores/construct",
                "/api/stores/construct-status",
                "/api/stores/construct-cancel",
                "/api/stores/replay",
                "/api/stores/replay-status",
                "/api/stores/replay-cancel",
            )
            and not self._allow_open
        ):
            self._send_json({"error": "opening by hand is switched off here"}, HTTPStatus.NOT_FOUND)
            return

        length = int(self.headers.get("Content-Length", 0))

        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json({"error": "that was not readable JSON"}, HTTPStatus.BAD_REQUEST)
            return

        if route == "/api/browse":
            self._serve_browse()
        elif route == "/api/stores/open":
            self._serve_open(payload)
        elif route == "/api/stores/close":
            self._serve_close(payload)
        elif route == "/api/stores/list":
            self._serve_list_folders(payload)
        elif route == "/api/stores/construct":
            self._serve_construct(payload)
        elif route == "/api/stores/construct-status":
            self._send_json(dict(self._bake_job) or {"state": "idle"})
        elif route == "/api/stores/construct-cancel":
            self._serve_cancel(self._bake_job)
        elif route == "/api/stores/replay":
            self._serve_replay(payload)
        elif route == "/api/stores/replay-status":
            self._send_json(dict(self._replay_job) or {"state": "idle"})
        elif route == "/api/stores/replay-cancel":
            self._serve_cancel(self._replay_job)
        elif route == "/api/measure":
            self._serve_measurement(payload)
        elif route == "/api/announce":
            self._serve_announcement(payload)
        else:
            self._save_annotations(payload)

    def _serve_browse(self) -> None:
        """Ask the operating system to show a folder chooser, and say what was picked."""
        if self._browse is None:
            self._send_json(
                {
                    "error": "no folder chooser is available here",
                    "reason": "The chooser is opened by the desktop window. In a "
                    "browser tab, type or paste the folder's path instead.",
                },
                HTTPStatus.NOT_IMPLEMENTED,
            )
            return

        try:
            chosen = self._browse()
        except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
            self._send_json(
                {
                    "error": f"the folder chooser could not be opened: {exc}",
                    "reason": (
                        f"The window for choosing a folder could not be opened ({exc}). "
                        "Nothing has changed and whatever was already on screen is "
                        "still there. Type or paste the folder's path instead."
                    ),
                },
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return

        self._send_json(
            {"path": chosen, "parent": str(Path(chosen).parent)} if chosen else {"cancelled": True}
        )

    def _serve_list_folders(self, payload: object) -> None:
        """List the folders at a path, for the in-page load window."""
        asked = payload.get("path") if isinstance(payload, dict) else None
        path = (
            Path(asked).expanduser()
            if isinstance(asked, str) and asked.strip()
            else self._open_from
        )

        try:
            path = path.resolve()

            if not path.is_dir():
                self._send_json({"error": f"there is no folder at {path}"}, HTTPStatus.NOT_FOUND)
                return

            described = set(DESCRIPTION_FILES)

            def kind_of(folder: Path) -> str | None:
                try:
                    inside = [child.name for child in folder.iterdir()]
                    told = _read_attrs_at(folder)

                    if any(name in described for name in inside):
                        if folder.name.endswith(".zmartview.zarr"):
                            return "view"

                        if told.get("plate"):
                            return "plate"

                        if told.get("multiscales"):
                            return "image"

                    if any(
                        (folder / name / stamp).exists() for name in inside for stamp in described
                    ):
                        return "run"
                except OSError:
                    pass

                return None

            def describe(folder: Path) -> dict:
                kind = kind_of(folder)
                told_of = {"name": folder.name, "kind": kind}

                if kind == "view":
                    told_of["baked"] = bool(
                        (_read_attrs_at(folder).get("zmart") or {}).get("baked")
                    )

                return told_of

            folders = [
                describe(entry)
                for entry in sorted(path.iterdir(), key=lambda one: one.name.lower())
                if entry.is_dir() and not entry.name.startswith(".")
            ]
            here = kind_of(path)
        except OSError as why:
            self._send_json({"error": str(why)}, HTTPStatus.BAD_REQUEST)
            return

        self._send_json(
            {
                "path": str(path),
                "kind": here,
                "parent": str(path.parent) if path.parent != path else None,
                "folders": folders,
            }
        )

    def _serve_cancel(self, job: dict) -> None:
        """Ask the running build or replay to stop at its next step."""
        running = job.get("state") == "running"

        if running:
            job["stop"] = True

        self._send_json({"stopping": running})

    def _serve_construct(self, payload: object) -> None:
        """Construct a viewer over raw data, in the background, then open it."""
        asked = payload if isinstance(payload, dict) else {}
        data = asked.get("path")
        viewer = asked.get("viewer_folder")

        if not isinstance(data, str) or not data.strip():
            self._send_json(
                {"error": "the folder holding the images is needed"},
                HTTPStatus.BAD_REQUEST,
            )
            return

        if not isinstance(viewer, str) or not viewer.strip():
            self._send_json(
                {"error": "the folder for the viewer's files is needed"},
                HTTPStatus.BAD_REQUEST,
            )
            return

        if self._bake_job.get("state") == "running":
            self._send_json({"error": "a bake is already running"}, HTTPStatus.CONFLICT)
            return

        data_path = Path(data.strip()).expanduser()

        if not data_path.is_dir():
            self._send_json({"error": f"there is no folder at {data_path}"}, HTTPStatus.NOT_FOUND)
            return

        from .building import declare_a_built_picture, the_scene_folder_name

        bake = bool(asked.get("bake"))
        name = asked.get("name") if isinstance(asked.get("name"), str) else None

        if name is not None and (not name.strip() or "/" in name or "\\" in name or ".." in name):
            self._send_json(
                {"error": "the scene's name cannot contain path steps -- give it a plain name"},
                HTTPStatus.BAD_REQUEST,
            )
            return

        job = self._bake_job
        job.clear()
        job.update({"state": "running", "fraction": 0.0, "bake": bake})
        viewer_path = Path(viewer.strip()).expanduser()

        def told(done, total):
            if job.get("stop"):
                raise _StoppedByTheOperator()

            job["fraction"] = round(done / max(total, 1), 4)

        def work():
            try:
                store = declare_a_built_picture(
                    viewer_path,
                    data_path,
                    name=name or data_path.name,
                    bake=bake,
                    told=told,
                )
                job.update({"state": "done", "fraction": 1.0, "store": str(store)})
            except _StoppedByTheOperator:
                shutil.rmtree(
                    viewer_path / the_scene_folder_name(name or data_path.name),
                    ignore_errors=True,
                )
                job.update({"state": "cancelled"})
            except Exception as why:  # noqa: BLE001 -- shown to the operator whole
                job.update({"state": "error", "error": str(why)})

        threading.Thread(target=work, daemon=True).start()
        self._send_json({"started": True})

    def _serve_replay(self, payload: object) -> None:
        """Relive a dataset as a live run, one position at a time."""
        asked = payload if isinstance(payload, dict) else {}
        path = asked.get("path")

        if not isinstance(path, str) or not path.strip():
            self._send_json(
                {"error": "the folder holding the dataset is needed"},
                HTTPStatus.BAD_REQUEST,
            )
            return

        if self._replay_job.get("state") == "running":
            self._send_json({"error": "a replay is already running"}, HTTPStatus.CONFLICT)
            return

        data_path = Path(path.strip()).expanduser()

        if not data_path.is_dir():
            self._send_json({"error": f"there is no folder at {data_path}"}, HTTPStatus.NOT_FOUND)
            return

        from .rehearsal import replay_the_dataset

        every = asked.get("every")
        every_s = float(every) if isinstance(every, (int, float)) else 0.7
        every_s = max(0.0, every_s)
        # A replay writes a copy of the whole dataset into the viewer's own
        # folders, so before a byte of it is written the copy is checked
        # against what those folders may hold. Refused whole rather than
        # stopped part-way: a half-replayed run is nothing anybody wants.
        sessions = self._scratch.get("sessions")
        no_room = (
            sessions.room_for("replays", wanted_bytes=_bytes_under(data_path))
            if sessions is not None else None
        )
        if no_room is not None:
            self._send_json(
                {"error": f"the replay was refused: {no_room}"},
                HTTPStatus.INSUFFICIENT_STORAGE,
            )
            return
        replays = self._a_session_folder("replays")
        number = 1

        while (replays / f"replay-{number}").exists():
            number += 1

        run_folder = replays / f"replay-{number}"
        job = self._replay_job
        job.clear()
        job.update({"state": "running", "done": 0, "total": None})
        ready = threading.Event()
        announcements = self._announcements

        def told(done, total):
            job["done"], job["total"] = done, total
            ready.set()

            if job.get("stop"):
                raise _StoppedByTheOperator()

        def work():
            try:
                view = replay_the_dataset(
                    data_path,
                    run_folder,
                    every_s=every_s,
                    told=told,
                    announce=lambda: announcements.say_something_changed(
                        image_written_in_place=True
                    ),
                )
                job.update({"state": "done", "view": str(view)})
            except _StoppedByTheOperator:
                job.update({"state": "cancelled"})
            except Exception as why:  # noqa: BLE001 -- shown to the operator whole
                job.update({"state": "error", "error": str(why)})
            finally:
                ready.set()

        threading.Thread(target=work, daemon=True).start()
        ready.wait(timeout=120)

        if job.get("state") == "error":
            self._send_json({"error": job["error"]}, HTTPStatus.BAD_REQUEST)
            return

        self._library.close_group(f"{data_path.name} replay")

        try:
            self._library.open(
                str(run_folder),
                names=loading.live_run_view(
                    run_folder, bake=self._asked_for_the_live_bake(run_folder, asked)
                ),
                name=f"{data_path.name} replay",
            )
        except (FileNotFoundError, ValueError, OSError) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        self._send_json(self._config())

    def _scenes_of_this_session(self) -> Path:
        """The folder this viewer composes into, made the first time it is wanted."""
        return self._a_session_folder("scenes")

    def _a_session_folder(self, kind: str) -> Path:
        """A folder of the viewer's own for this session, made and locked when wanted.

        Locked, so that a Viewer started after this one died can tell this
        folder was abandoned and reclaim it — see ``scratch.py``.
        """
        sessions = self._scratch.get("sessions")

        if sessions is None:
            sessions = self._scratch["sessions"] = ScratchSession()

        return sessions.open(kind)

    def _serve_open(self, payload: object) -> None:
        """Open a folder of images and answer with the viewer's new contents."""
        path = payload.get("path") if isinstance(payload, dict) else None

        if not isinstance(path, str) or not path.strip():
            self._send_json({"error": "a folder path is needed"}, HTTPStatus.BAD_REQUEST)
            return

        target = Path(path.strip()).expanduser()

        try:
            opened = loading.load(
                target,
                bake=self._asked_for_the_live_bake(target, payload),
                scenes=self._scenes_of_this_session(),
            )
        except loading.CannotOpen as why:
            refused = {"error": str(why), **why.detail}
            status = HTTPStatus.CONFLICT if "relink" in why.detail else HTTPStatus.BAD_REQUEST
            self._send_json(refused, status)
            return

        try:
            self._library.open(str(opened.target), names=opened.names)
        except FileNotFoundError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            return
        except (ValueError, OSError) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        self._send_json(self._config())

    def _asked_for_the_live_bake(self, run_folder: Path, payload: object) -> bool:
        """Whether this run was opened with a bake asked for, and remember it."""
        asked = payload if isinstance(payload, dict) else {}
        baking = self._scratch.setdefault("bake_live", set())

        if bool(asked.get("bake")):
            baking.add(Path(run_folder).resolve())

        return Path(run_folder).resolve() in baking

    def _serve_close(self, payload: object) -> None:
        """Close an acquisition type, and answer with what is left."""
        group = payload.get("group") if isinstance(payload, dict) else None

        if not isinstance(group, str) or not group:
            self._send_json(
                {"error": "which acquisition to close is needed"},
                HTTPStatus.BAD_REQUEST,
            )
            return

        datasets = self._library.datasets()
        named = group_labels(datasets)
        by_number = {dataset.number: dataset for dataset in datasets}
        chosen = [number for number, label in named.items() if label == group]
        closed = []

        if chosen:
            for number in chosen:
                closed += self._library.close_group(by_number[number].name, folder=number)
        else:
            closed += self._library.close_group(group)

        for _, root, name in closed:
            forget(root / name)
            pieces.forget(root / name)
            self.forget_described(root / name)

        self._forget_measurements(closed)
        self._send_json(self._config())

    def _save_annotations(self, payload: object) -> None:
        """Save the targets the operator has drawn."""
        try:
            document = _validate_annotations(payload)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        path = self._data_dir / _ANNOTATIONS_FILE
        temporary = None

        try:
            fd, temporary = tempfile.mkstemp(
                prefix=f".{_ANNOTATIONS_FILE}.", suffix=".tmp", dir=self._data_dir
            )

            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(document, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())

            os.replace(temporary, path)
        except OSError as why:
            # The half-written file is cleared away so a failed save does not
            # leave litter beside the operator's data.
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

            self._send_json(
                {
                    "error": (
                        f"could not write {path} ({why.strerror or why}). The places "
                        "you have marked are still on screen and whatever was saved "
                        "before is untouched, but nothing new has reached the disk. "
                        "This is usually a folder that cannot be written to, or a "
                        "drive that has filled up or gone away."
                    )
                },
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return

        self._send_json(document)

    def _send_json(
        self,
        obj: dict,
        status: HTTPStatus = HTTPStatus.OK,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))

        for name, value in (headers or {}).items():
            self.send_header(name, value)

        self.end_headers()

        if self.command != "HEAD":
            self.wfile.write(body)

    # Quieten the default per-request logging so the console stays readable.
    def log_message(self, *args) -> None:  # noqa: D401
        pass


_ONE_DIALOG_AT_A_TIME = threading.Lock()


def ask_this_machine_for_a_folder() -> str | None:
    """Open this machine's own folder chooser, for a page in a plain browser."""
    import tkinter
    from tkinter import filedialog

    with _ONE_DIALOG_AT_A_TIME:
        root = tkinter.Tk()
        root.withdraw()
        # In front of the browser, or the operator sees nothing happen.
        root.attributes("-topmost", True)

        try:
            chosen = filedialog.askdirectory(
                parent=root, title="Choose the folder holding the images"
            )
        finally:
            root.destroy()

    return chosen or None


def make_server(
    port: int = 8848,
    *,
    data_dir: Path | None = None,
    site_dir: Path = _FRONTEND_DIST,
    store: str | list[str] | None = None,
    loads: list[dict] | None = None,
    window: tuple[float, float] | None = None,
    depth_samples: int = 256,
    chrome: bool = False,
    browse=None,
    live: bool = True,
    allow_open: bool = True,
    scratch_root: Path | None = None,
    allow_selection: bool = False,
    panel_side: str = "right",
    open_from: Path | None = None,
) -> ThreadingHTTPServer:
    """Create (but do not start) the viewer's web server.

    Given nothing to open, it serves an empty studio: no store, no demo,
    just the open door. ``data_dir`` then only says where the open dialog
    starts and where drawn targets are saved.
    """
    data_dir = Path(data_dir).resolve() if data_dir is not None else Path.cwd()
    names = [store] if isinstance(store, str) else list(store or [])
    library = Library()
    wanted = loads if loads is not None else ([{"stores": names}] if names else [])

    for spec in wanted:
        library.open(
            Path(spec.get("path", data_dir)),
            names=spec.get("stores"),
            watch=live and (len(wanted) == 1 or spec.get("stores") is None),
            name=spec.get("name"),
        )

    scratch: dict = {"sessions": ScratchSession(scratch_root) if scratch_root else ScratchSession()}
    registry = SourceRegistry(
        library,
        watching=live,
        wants_the_bake=lambda run_root: Path(run_root).resolve() in scratch.get("bake_live", ()),
    )

    measurements = Measurements(fixed_window=window)
    # Which acquisitions the writer has said are finished. See
    # ``_how_final_a_measurement_of``: the word an operator reads beside a
    # measured window comes from here, not from the shape of the store.
    liveness: dict = {"finished": set()}

    last_built: dict = {"revision": None, "config": None}
    building_config = threading.Lock()

    def config_now() -> dict:
        (
            live_bindings,
            live_numbers,
            live_document,
            live_snapshots,
            live_etag,
        ) = registry.state()
        revision = (
            library.revision(excluding=live_numbers),
            live_etag,
            tuple(sorted(liveness["finished"])),
        )

        if last_built["revision"] == revision:
            return last_built["config"]

        with building_config:
            # Asked again with the lock held: while waiting, another thread may have
            # built exactly what this one was about to build.
            if last_built["revision"] == revision:
                return last_built["config"]

            built = build_config(
                live_document,
                live_bindings,
                live_snapshots,
                live_numbers,
            )
            last_built["revision"] = revision
            last_built["config"] = built
            return built

    def build_config(
        live_document: dict,
        live_bindings,
        live_snapshots,
        live_numbers: frozenset[int],
    ) -> dict:
        """Describe every row the layer panel should show, and its group."""
        entries = library.entries()
        present = [name for _, _, name in entries]
        labels = layer_names(present)
        groups_named = group_labels(library.datasets())
        merged: dict[tuple, dict] = {}

        for (root_number, root, name), label in zip(entries, labels, strict=True):
            if root_number in live_numbers:
                continue

            group = groups_named[root_number]
            store_path = root / name
            address = f"/data/{root_number}/{name}/|{zarr_scheme(store_path)}:"

            if "c" in axis_names(store_path):
                found = [
                    (
                        index,
                        channel["name"],
                        channel["color"],
                        channel.get("range"),
                        channel.get("active", True),
                    )
                    for index, channel in enumerate(channels(store_path))
                ]
            else:
                wavelength = channel_of(name)
                colour = channel_color(name) if len(present) > 1 else None
                found = [
                    (
                        None,
                        f"Ch{wavelength}" if wavelength else label,
                        colour,
                        None,
                        True,
                    )
                ]

            frames = written_timepoints(store_path)

            for index, channel_name, color, declared_range, active in found:
                key = (root_number, index, channel_name)
                row = merged.get(key)

                if row is None:
                    base = measurements.describe(
                        root_number,
                        root,
                        name,
                        label,
                        coloured=len(present) > 1,
                        channel=index,
                        declared_range=declared_range,
                    )
                    if base.get("measurementState") in ("provisional", "settled"):
                        # The same rule the measure route uses: a reading is
                        # final when the acquisition is, not when this store
                        # happens to hold a whole-field copy.
                        base["measurementState"] = (
                            "settled"
                            if not live or bare_group_name(group) in liveness["finished"]
                            else "provisional"
                        )
                    merged[key] = {
                        **base,
                        "sources": [address],
                        "name": channel_name,
                        "group": group,
                        "channelIndex": index,
                        # How many frames exist so far, so the time slider stops
                        # there rather than running out over frames not yet imaged.
                        "frames": frames,
                        "frameCounts": [frames],
                        "color": list(color) if color else None,
                        **({} if active else {"active": False}),
                    }
                else:
                    row["sources"].append(address)
                    row["frameCounts"].append(frames)

                    if frames and (row.get("frames") or 0) < frames:
                        row["frames"] = frames

            for mask in label_images(store_path):
                key = (root_number, "mask", mask)
                row = merged.get(key)
                source = f"/data/{root_number}/{name}/labels/{mask}/|{zarr_scheme(store_path / 'labels' / mask)}:"

                if row is None:
                    merged[key] = {
                        "name": mask,
                        "group": group,
                        "kind": "segmentation",
                        "channelIndex": None,
                        "color": None,
                        "window": None,
                        "volumeWindow": None,
                        "histogram": None,
                        "sources": [source],
                        "frames": frames,
                        "frameCounts": [frames],
                    }
                else:
                    row["sources"].append(source)
                    row["frameCounts"].append(frames)

        rows = [{"kind": "image", **row} for row in merged.values()]

        for binding in live_bindings:
            rows.extend(
                live_rows(
                    binding,
                    chosen_window=window,
                    group=groups_named[binding.dataset_number],
                    snapshot=live_snapshots[binding.dataset_number],
                )
            )
        # Group order follows first appearance, which follows the sorted store
        # names, so the panel does not reshuffle itself between runs.
        groups = list(dict.fromkeys(row["group"] for row in rows))
        return {
            "layers": rows,
            "groups": groups,
            "depthSamples": depth_samples,
            "chrome": chrome,
            "canOpen": allow_open,
            # Whether the selection list is offered. See ``allow_selection``.
            "canSelect": allow_selection,
            # Which edge the bar of controls sits on. See ``panel_side``.
            "panelSide": "left" if str(panel_side).lower() == "left" else "right",
            # Whether the page should keep asking if anything has changed. See
            # ``live`` above: on finished data there is nothing to notice.
            "live": live,
            **({"liveState": live_document} if live_bindings else {}),
        }

    class _Server(ThreadingHTTPServer):
        request_queue_size = 128
        daemon_threads = True

        allow_reuse_address = sys.platform != "win32"

        def server_bind(self):
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)

            super().server_bind()

        def serve_forever(self, *args, **kwargs):
            # Before anything else: reclaim the scratch folders of Viewers that
            # died without cleaning up. Only folders nobody holds a lock on go;
            # a Viewer still running beside this one keeps its own.
            swept = {}
            for kind in SCRATCH_KINDS:
                try:
                    swept[kind] = scratch["sessions"].sweep_orphans(kind)
                except Exception as why:  # noqa: BLE001 -- a sweep must never stop a server
                    swept[kind] = {"error": str(why)}
            scratch["swept"] = swept
            # The disk is watched only while the server is actually running.
            registry.start()
            super().serve_forever(*args, **kwargs)

        def shutdown(self):
            registry.stop()
            # Stop serving first, then let go of the scratch: a request that
            # arrived in between would otherwise make a fresh locked folder
            # that nothing closes until the next start sweeps it.
            super().shutdown()
            scratch["sessions"].close()

    handler = functools.partial(
        _Handler,
        scratch=scratch,
        data_dir=data_dir,
        site_dir=Path(site_dir).resolve(),
        config=config_now,
        library=library,
        browse=browse,
        bake_job={},
        replay_job={},
        open_from=Path(open_from).resolve() if open_from else None,
        allow_open=allow_open,
        live=live,
        announcements=registry.announcements,
        live_state=registry.state_document,
        forget_measurements=measurements.forget,
        liveness=liveness,
    )

    try:
        return _Server(("127.0.0.1", port), handler)
    except OSError as why:
        registry.stop()
        raise OSError(
            f"the viewer could not start on port {port}: {why}\n\n"
            "That usually means something else on this machine is already using "
            f"it — most often another copy of this viewer. Either close that one, "
            "or start this one on a different port:\n\n"
            "    python run_demo.py --port 8849\n\n"
            "Any number between 1024 and 65535 that nothing else is using will do, "
            "and --port 0 lets the machine choose a free one for you and prints "
            "which it picked."
        ) from why


# -- running it -----------------------------------------------------------------


def serve(port: int = 8848) -> None:
    """Run the server until interrupted. The viewer page will be at ``/``."""
    server = make_server(port)
    print(f"ZMART Viz Studio serving on http://127.0.0.1:{server.server_address[1]}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Serve the visualization studio.")
    parser.add_argument("--port", type=int, default=8848)
    args = parser.parse_args()
    serve(args.port)
