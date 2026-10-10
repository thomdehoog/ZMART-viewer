"""The local web server: one address for the page, the data, and the API.

It serves the built page, image pieces under ``/data`` (guarded so a
request only reaches inside an open folder), and small JSON commands
under ``/api``. A threaded stdlib server, localhost only, and by design
no route that talks to a microscope.
"""

from __future__ import annotations

import functools
import json
import logging
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
import urllib.parse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from zmart_viewer.filesystem import put_text_in_place
from zmart_viewer.live import following as live
from zmart_viewer.live.following import SourceRegistry, live_rows
from zmart_viewer.live.record.live_serving import answer_from_a_live_run, live_run_holding
from zmart_viewer.opening import open_a_path as loading
from zmart_viewer.opening.contrast import (
    Measurements,
    measure_here,
)
from zmart_viewer.opening.open_folders import (
    DESCRIPTION_FILES,
    Library,
    axis_names,
    channel_color,
    channel_of,
    channels,
    described_channels,
    forget,
    label_images,
    layer_names,
    normalise_units,
    read_array_description,
    read_attrs_at,
    the_address_of,
    written_timepoints,
)

# The other way a picture can exist without being written: built when asked
# for, rather than pointed at.
from zmart_viewer.serving import coverage
from zmart_viewer.serving import picture_pieces as pieces

_HERE = Path(__file__).resolve().parent
_ENGINE = _HERE.parent
_DRAWING = _ENGINE / "drawing"
_FRONTEND_DIST = _ENGINE / "_frontend"
if not _FRONTEND_DIST.is_dir():
    _FRONTEND_DIST = (_ENGINE.parent / "gui" / "build").resolve()
_ANNOTATIONS_FILE = "zmart-annotations.json"
_EMPTY_ANNOTATIONS = {"version": 1, "annotations": []}

# How many pieces are built at the same time. Building one reads the source
# images and encodes the result; with no limit, a burst of zooming started a
# thread per request, thousands of them, all queued behind the same readers,
# and the server stopped answering until they had all finished.
_BUILDS_AT_ONCE = max(4, os.cpu_count() or 4)
_BUILDING = threading.BoundedSemaphore(_BUILDS_AT_ONCE)


class _NobodyIsWaiting(Exception):
    """The browser that asked for a piece has hung up, so it is not built."""


def _peer_has_gone(connection: socket.socket) -> bool:
    """Whether the other end of ``connection`` has closed it.

    A peek reads nothing away: an orderly close shows as an empty read, a reset
    as an error, and a browser still connected as nothing to read yet (or the
    next request it sent). The socket's timeout is put back as it was.
    """
    before = connection.gettimeout()
    try:
        connection.setblocking(False)
        return connection.recv(1, socket.MSG_PEEK) == b""
    except (BlockingIOError, InterruptedError):
        return False
    except OSError:
        return True
    finally:
        try:
            connection.settimeout(before)
        except OSError:
            pass


# What can go wrong reading the targets file, short of it being absent: a file
# locked by another program for a moment, or one that is not (or no longer)
# what the viewer writes.
_UNREADABLE = (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError)


def _saved_annotations(path: Path) -> dict:
    """The targets saved in ``path``; an absent file is an empty list.

    Raises one of :data:`_UNREADABLE` when the file is there but cannot be read.
    """
    try:
        return _validate_annotations(json.loads(path.read_text("utf-8")))
    except FileNotFoundError:
        return _EMPTY_ANNOTATIONS
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


# -- who may ask ------------------------------------------------------------------

#: The names this computer answers to. The server listens on 127.0.0.1 only.
THIS_COMPUTER = frozenset({"127.0.0.1", "localhost", "::1"})


def on_this_computer(address: str) -> bool:
    """Whether ``address`` (``host:port``, or a page's origin) names this computer."""
    if "//" not in address:
        address = f"//{address}"

    try:
        return (urllib.parse.urlsplit(address).hostname or "") in THIS_COMPUTER
    except ValueError:
        return False


def the_origin_of(address: str) -> str:
    """A page's origin written one way only: ``scheme://host[:port]``, in lower case."""
    return address.strip().rstrip("/").lower()


def refusal_for(headers, allowed_origins: frozenset[str] = frozenset()) -> str | None:
    """Why a request must not be answered, or None when a page may ask it.

    The server listens on 127.0.0.1, so other computers cannot reach it. A web
    page open in a browser on this computer can, though, and a browser sends
    such a page's request without asking first when it is dressed as plain
    text. Three things the browser always says tell such a request apart: the
    name it was sent to (``Host``, which a page that renamed itself to reach
    127.0.0.1 cannot hide), the page it came from (``Origin``), and, for a
    plain read such as a picture, whether it crossed from another site
    (``Sec-Fetch-Site``). The viewer's own page, and an interface's page on
    another port of this computer, are on this computer by all three; a
    script run here sends no ``Origin`` and is let through. ``Origin: null``,
    which a sandboxed frame or a local file sends, names no page at all.
    """
    host = headers.get("Host") or ""

    if not on_this_computer(host):
        return f"the viewer only answers requests addressed to this computer, not to {host!r}"

    origin = headers.get("Origin")

    if origin is None:
        if headers.get("Sec-Fetch-Site") == "cross-site":
            return "the viewer only answers pages served on this computer, not from another site"
        return None

    if origin != "null" and (on_this_computer(origin) or the_origin_of(origin) in allowed_origins):
        return None

    return f"the viewer only answers pages served on this computer, not one from {origin!r}"


# -- what the panel calls each open acquisition ---------------------------------


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
    """Raised inside a build loop when the operator asked to stop."""


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
        scratch=None,
        allow_open: bool = True,
        transparent_background: bool = False,
        live: bool = True,
        announcements=None,
        live_state=None,
        forget_measurements=None,
        open_from=None,
        allowed_origins: frozenset[str] = frozenset(),
        coverage_handed_out=None,
        **kwargs,
    ):
        self._data_dir = data_dir  # where drawn targets are saved
        self._open_from = open_from or data_dir
        self._library = library  # which folders may be read from, and what is in them
        self._browse = browse  # opens a native folder chooser, when one is available
        self._allow_open = allow_open  # may the operator change what is open?
        self._transparent_background = transparent_background
        # One prebake at a time, shared by every request this server answers.
        # See _serve_bake for the shape of what it holds.
        self._bake_job = bake_job if bake_job is not None else {}
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
        # Pages away from this computer that may ask all the same. See refusal_for.
        self._allowed_origins = allowed_origins
        # Whether /api/config told the page about a store's coverage. Only
        # what it was told about is served. See _LayerPanelConfig.hands_out_coverage.
        self._coverage_handed_out = coverage_handed_out or (lambda store: False)
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
        """Every reply, with what the browser may keep of it.

        Only what the build names by its content -- everything under
        ``assets/`` -- may be kept for good, because it changes its name
        when it changes. The page itself is never kept, so a reload always
        names today's bundle. Anything else (a worker with a fixed name, the
        build manifest, a 404) is checked again each time; kept for good, it
        would outlive an upgrade beside the new page.
        """
        super().send_response(code, message)
        path = self.path.split("?", 1)[0].split("#", 1)[0]

        if path != "/embedding.js" and not path.startswith(("/data/", "/api/")):
            page = path in ("/", "/index.html") or path.endswith("/")
            named_by_content = code in (HTTPStatus.OK, HTTPStatus.NOT_MODIFIED) and path.startswith(
                "/assets/"
            )
            self.send_header(
                "Cache-Control",
                "no-store"
                if page
                else "public, max-age=31536000, immutable"
                if named_by_content
                else "no-cache",
            )

    def end_headers(self) -> None:
        """Every reply, readable by the page that asked when that page may ask at all."""
        origin = self.headers.get("Origin")

        if origin is not None and self.path != "/embedding.js":
            # Named, never everyone (``*``): a page that may not ask was
            # refused before anything was answered.
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

        super().end_headers()

    # -- routing ---------------------------------------------------------

    def refused(self) -> bool:
        """Answer 403 to a request no page on this computer sent, and say why."""
        why = refusal_for(self.headers, self._allowed_origins)

        if why is None:
            return False

        # A body left unread on a connection closed on Windows resets it, and
        # the refusal never arrives; and the next request on a kept-alive
        # connection would start inside this one's body. So it is read away.
        length = self.the_body_length()

        if length:
            self.rfile.read(length)
        elif length is None:
            self.close_connection = True

        body = json.dumps({"error": why}).encode("utf-8")
        self.send_response(HTTPStatus.FORBIDDEN)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # Straight to the base class: the page that may not ask is not named
        # in the answer, so it cannot read it.
        super().end_headers()

        if self.command != "HEAD":
            self.wfile.write(body)

        return True

    def the_body_length(self) -> int | None:
        """How many bytes of body the request says follow, or None when it says nonsense."""
        declared = (self.headers.get("Content-Length") or "0").strip()
        return int(declared) if declared.isdigit() else None

    def do_OPTIONS(self) -> None:  # noqa: N802
        """Tell a page on this computer that it may send JSON here.

        A browser asks this first before a page on another port sends a POST
        with a JSON body, which is exactly what an interface's page does.
        """
        if self.refused():
            return

        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 (name fixed by base class)
        if self.refused():
            return

        if self.path == "/embedding.js":
            self._serve_embedding()
            return

        if self.path.startswith("/data/"):
            self._serve_from_data()
            return

        if self.path.startswith("/api/"):
            self._serve_api_get()
            return

        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        if self.refused():
            return

        if self.path.startswith("/api/"):
            self._serve_api_post()
            return

        self._send_empty(HTTPStatus.NOT_FOUND)

    def do_HEAD(self) -> None:  # noqa: N802
        """Answer "does this exist, and how big is it?" — headers only, no body."""
        if self.refused():
            return

        if self.path == "/embedding.js":
            self._serve_embedding()
            return

        if self.path.startswith("/data/"):
            self._serve_from_data()
            return

        if self.path.startswith("/api/"):
            self._serve_api_get()
            return

        super().do_HEAD()

    def _serve_embedding(self) -> None:
        """The module an interface imports to drive the viewer's drawing, from any page."""
        data = (_DRAWING / "embedding.js").read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        if self.command != "HEAD":
            self.wfile.write(data)

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
        # The address spells names out (see the_address_of); read it back to
        # the names themselves before the library decides what it may reach.
        rel = urllib.parse.unquote(self.path[len("/data/") :].split("?", 1)[0].split("#", 1)[0])
        number, _, rest = rel.partition("/")
        marker = f"/{coverage.MARKER}/"
        if marker in rel:
            store_rel, inside = rel.split(marker, 1)
            store = self._library.resolve(store_rel)
            # The page asks for coverage exactly where /api/config handed it
            # out, so that is the one condition: a second, separate rule here
            # drifted from it once and refused coverage the page was told of.
            if store is None or not self._coverage_handed_out(store_rel):
                self._send_empty(HTTPStatus.FORBIDDEN)
                return
            try:
                body = self._build(coverage.answer, store, inside)
            except _NobodyIsWaiting:
                return
            except pieces.TemporarilyUnanswerable:
                # A piece a publication is rewriting just now: try again shortly.
                self._send_empty(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            except Exception:
                logging.getLogger(__name__).exception("coverage unavailable for %s", store)
                self._send_empty(HTTPStatus.SERVICE_UNAVAILABLE)
                return
            if body is None:
                self._send_empty(HTTPStatus.NOT_FOUND)
            else:
                self._send_bytes(body)
            return
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
                root = self._library.root_of(int(number))
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
                made = self._build(self._built, rel)
            except _NobodyIsWaiting:
                return
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
                made = self._build(pieces.built_bytes_behind, *governed)
            except _NobodyIsWaiting:
                return
            except pieces.TemporarilyUnanswerable:
                self._send_empty(HTTPStatus.SERVICE_UNAVAILABLE)
                return

            if made is not None:
                self._send_bytes(made)
            else:
                self._send_empty(HTTPStatus.NOT_FOUND)

            return

        self._send_file(target)

    def _build(self, make, *args):
        """Build a piece in one of the few building slots, for a browser still waiting.

        Raises :class:`_NobodyIsWaiting`, and the connection is closed without an
        answer, when the browser hangs up before the piece is started.
        """
        while not _BUILDING.acquire(timeout=0.25):
            if _peer_has_gone(self.connection):
                self.close_connection = True
                raise _NobodyIsWaiting
        try:
            if _peer_has_gone(self.connection):
                self.close_connection = True
                raise _NobodyIsWaiting
            return make(*args)
        finally:
            _BUILDING.release()

    def _a_governed_piece_behind(self, target: Path) -> tuple[Path, str] | None:
        """The (store, piece address) when this FILE is a governed chunk."""
        for arity in (7, 5):
            if len(target.parents) < arity:
                continue
            store = target.parents[arity - 1]
            inside = target.relative_to(store).as_posix()
            if pieces.the_piece_address(inside) is None or not (store / "zarr.json").is_file():
                continue
            if pieces.a_manifest_governs(store):
                return store, inside
        return None

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

        if self.command != "HEAD":
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
            self._send_json({"ok": True})
            return

        if self.path.rstrip("/") == "/api/events":
            self._serve_events()
            return

        if self.path.rstrip("/") == "/api/config":
            self._serve_config()
            return

        if self.path.rstrip("/") == "/api/live-state":
            self._serve_live_state()
            return

        if self.path.rstrip("/") == "/api/annotations":
            path = self._data_dir / _ANNOTATIONS_FILE

            try:
                payload = _saved_annotations(path)
            except _UNREADABLE:
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

        if self.command == "HEAD":
            # Headers only: a HEAD that became a stream would never end.
            return

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
        sources = asked.get("sources", [asked.get("source")])

        if (
            not isinstance(sources, list)
            or not sources
            or any(not isinstance(source, str) or not source.strip() for source in sources)
        ):
            self._send_json({"error": "which picture to measure is needed"}, HTTPStatus.BAD_REQUEST)
            return

        stores = []
        for source in dict.fromkeys(sources):
            rel = urllib.parse.unquote(source.split("/data/", 1)[-1].split("|", 1)[0].strip("/"))
            store = self._library.resolve(rel)
            if store is None or not store.is_dir():
                self._send_json({"error": "that picture is not open here"}, HTTPStatus.NOT_FOUND)
                return
            stores.append(store)

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
        found = measure_here(stores, channel=channel, box=corners)

        if found is None:
            self._send_json({"empty": True})
            return

        low, high = found["window"]
        self._send_json(
            {
                "window": {"low": low, "high": high},
                "histogram": found["histogram"],
            }
        )

    def _serve_announcement(self, payload: object) -> None:
        """Publish completed folder snapshots, or accept a legacy change hint."""
        in_place = bool(isinstance(payload, dict) and payload.get("wrote_image_in_place"))
        if isinstance(payload, dict) and "publications" in payload:
            publisher = self._scratch["published"]
            before = publisher.revisions()
            try:
                publisher.announce(payload["publications"])
            except (ValueError, KeyError, TypeError, OverflowError, OSError, RuntimeError) as why:
                # Each named view commits atomically. A later view can fail after
                # an earlier one succeeded; those committed pixels still need notice.
                if publisher.revisions() != before:
                    self._announcements.say_something_changed()
                # A disk that held a file for a moment, or a publisher that
                # was closed under it, may answer next time; a bad request never.
                status = (
                    HTTPStatus.SERVICE_UNAVAILABLE
                    if isinstance(why, (OSError, RuntimeError))
                    else HTTPStatus.BAD_REQUEST
                )
                self._send_json({"error": str(why)}, status)
                return
        else:
            # A hint names no snapshot, so the folders this server publishes on
            # its own are brought up to date before anyone is told. Telling first
            # sent the page for a configuration still holding the old revision,
            # and the watcher, which commits the new one a moment later, stays
            # quiet about a change it counts as already announced.
            self._scratch["published"].refresh()
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

        length = self.the_body_length()

        if length is None:
            self.close_connection = True
            self._send_json(
                {"error": "the request's Content-Length is not a number of bytes"},
                HTTPStatus.BAD_REQUEST,
            )
            return

        raw = self.rfile.read(length) if length else b""

        if route not in {
            "/api/browse",
            "/api/stores/open",
            "/api/stores/close",
            "/api/stores/list",
            "/api/stores/construct",
            "/api/stores/construct-status",
            "/api/stores/construct-cancel",
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
            )
            and not self._allow_open
        ):
            self._send_json({"error": "opening by hand is switched off here"}, HTTPStatus.NOT_FOUND)
            return

        declared = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()

        if raw and declared != "application/json":
            # A page from elsewhere dresses its body as plain text so that the
            # browser sends it without asking first (review M3); a body sent
            # here says what it is.
            self._send_json(
                {"error": "send the body as JSON, with Content-Type: application/json"},
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            )
            return

        try:
            payload = json.loads(raw or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
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
                    told = read_attrs_at(folder)

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
                        (read_attrs_at(folder).get("zmart") or {}).get("baked")
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
        """Ask the running build to stop at its next step."""
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

        from zmart_viewer.picture.built_picture import (
            declare_a_built_picture,
            the_scene_folder_name,
        )

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

    def _scenes_of_this_session(self) -> Path:
        """The folder this viewer composes into, made the first time it is wanted."""
        return self._a_session_folder("scenes")

    def _a_session_folder(self, kind: str) -> Path:
        """A folder of the viewer's own for this session, made when wanted."""
        with self._scratch["making"]:
            folder = self._scratch.get(kind)

            if folder is None:
                folder, held = a_held_session_folder(the_viewers_home() / kind)
                self._scratch[kind] = folder
                self._scratch[f"{kind}-held"] = held

        return folder

    def _serve_open(self, payload: object) -> None:
        """Open a folder of images and answer with the viewer's new contents."""
        path = payload.get("path") if isinstance(payload, dict) else None

        if not isinstance(path, str) or not path.strip():
            self._send_json({"error": "a folder path is needed"}, HTTPStatus.BAD_REQUEST)
            return

        target = Path(path.strip()).expanduser()

        published = self._scratch["published"]
        asked_bake = bool(payload.get("bake", published.bake))
        canvas = payload.get("canvas", published.canvas)
        if (asked_bake or "composition" in payload or "views" in payload) and live_run_holding(
            target
        ) is None:
            try:
                if "composition" in payload and not isinstance(payload["composition"], dict):
                    raise ValueError(
                        "composition must explicitly describe acquired coverage and order"
                    )
                published.open(
                    target,
                    canvas=canvas,
                    versions=payload.get("source_revisions"),
                    composition=payload.get("composition"),
                    bake=asked_bake,
                    views=payload.get("views"),
                )
            except (ValueError, KeyError, TypeError, OverflowError) as why:
                self._send_json({"error": str(why)}, HTTPStatus.BAD_REQUEST)
                return
            except OSError as why:
                self._send_json({"error": str(why)}, HTTPStatus.SERVICE_UNAVAILABLE)
                return
            self._send_json(self._config())
            return

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
            # A folder is watched for new images only while the data is still
            # being written. A viewer started for finished data (--static)
            # watches nothing, whichever way the folder was opened.
            self._library.open(
                str(opened.target),
                names=opened.names,
                watch=self._live and opened.names is None,
            )
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

        # Replacing a file that cannot be read would throw away whatever is in
        # it, which the page was never able to show. It is left alone instead.
        try:
            _saved_annotations(path)
        except _UNREADABLE:
            self._send_json(
                {
                    "error": (
                        f"the file of marked places beside the images ({_ANNOTATIONS_FILE}) "
                        "could not be read, so it was not replaced: saving now would lose "
                        "what is in it. Close any program that has it open, or move it "
                        "aside, then reopen the viewer."
                    )
                },
                HTTPStatus.CONFLICT,
            )
            return

        # Written beside the data and renamed into place, pushed to the disk
        # first, so a crash mid-save leaves the old file whole; a failed save
        # clears its half-written file away rather than leaving litter beside
        # the operator's data.
        try:
            put_text_in_place(path, json.dumps(document, indent=2) + "\n", pushed_to_disk=True)
        except OSError as why:
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


# -- the viewer's own folders in the home folder ---------------------------------


def the_viewers_home() -> Path:
    """Where the viewer keeps what it makes for itself: ``~/.zmart-viewer``."""
    return Path.home() / ".zmart-viewer"


#: The file inside a session folder its viewer holds locked while it runs.
HELD = "held-by-a-running-viewer"

#: A session folder without that file is cleared only once it is this old, so a
#: viewer that has just made its folder, and not yet locked it, keeps it.
UNHELD_GRACE_S = 60


def a_held_session_folder(home: Path):
    """A new ``session-*`` folder in ``home``, and the open file that holds it.

    The file stays open and locked for as long as this viewer runs, which is
    how another viewer starting later tells a folder in use from one left
    behind by a viewer that was killed and never tidied up.
    """
    home.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="session-", dir=home))
    held = open(folder / HELD, "a+b")  # noqa: SIM115 -- held for the session's life
    held.write(b"x")
    held.flush()
    lock_one_byte(held)
    return folder, held


def lock_one_byte(handle) -> None:
    """Lock the first byte of an open file, or raise OSError when another holds it."""
    handle.seek(0)

    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def held_by_a_running_viewer(folder: Path) -> bool:
    """Whether a viewer that is still running holds this session folder."""
    try:
        with open(folder / HELD, "a+b") as handle:
            lock_one_byte(handle)
    except FileNotFoundError:
        try:
            return time.time() - folder.stat().st_mtime < UNHELD_GRACE_S
        except OSError:
            return False
    except OSError:
        return True

    # Locked and let go again by closing: nobody held it.
    return False


def clear_what_stopped_viewers_left(home: Path) -> None:
    """Remove the session folders in ``home`` that no running viewer holds.

    A viewer removes its own folder when it stops; one that was killed never
    gets to, and its folder stayed in the home folder for good (review N3).
    """
    try:
        found = [one for one in home.glob("session-*") if one.is_dir()]
    except OSError:
        return

    for folder in found:
        if not held_by_a_running_viewer(folder):
            shutil.rmtree(folder, ignore_errors=True)


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


def the_coverage_handed_out(config: dict) -> frozenset[str]:
    """The stores an answer to /api/config hands out coverage for, as ``<number>/<store>``."""
    return frozenset(
        urllib.parse.unquote(source.split("|", 1)[0].rstrip("/").removeprefix("/data/"))
        .rsplit(f"/{coverage.MARKER}", 1)[0]
        for row in config.get("layers", ())
        for source in row.get("coverageSources", ())
    )


class _LayerPanelConfig:
    """The answer to ``/api/config``: every row the layer panel shows, and its group.

    The page asks for this on every refresh, and building it means reading the
    description of every open store, so the answer is kept and handed back
    unchanged until something it depends on moves: the library of open
    folders, a live run's state, or a publication's revision. :meth:`now`
    makes that decision; :meth:`build` does the work when it has to.
    """

    def __init__(
        self,
        *,
        library,
        published,
        registry,
        measurements,
        window,
        depth_samples: int,
        chrome: bool,
        transparent_background: bool,
        allow_open: bool,
        allow_selection: bool,
        panel_side: str,
        live: bool,
    ):
        self._library = library
        self._published = published
        self._registry = registry
        self._measurements = measurements
        self._window = window
        self._depth_samples = depth_samples
        self._chrome = chrome
        self._transparent_background = transparent_background
        self._allow_open = allow_open
        self._allow_selection = allow_selection
        self._panel_side = panel_side
        self._live = live
        # What was last built, as one value: (revision, answer, stores whose
        # coverage the answer hands out). Replaced whole, never field by
        # field, so a request reading it without the lock can never see a new
        # revision beside an old answer (review S10).
        self._last_built: tuple = (None, None, frozenset())
        self._building = threading.Lock()

    def now(self) -> dict:
        """The current answer, built afresh only when something has changed."""
        published_revision = self._published.revisions()
        (
            live_bindings,
            live_numbers,
            live_document,
            live_snapshots,
            live_etag,
        ) = self._registry.state()
        revision = (
            self._library.revision(excluding=live_numbers),
            live_etag,
            published_revision,
        )

        built_for, built, _ = self._last_built

        if built_for == revision:
            return built

        with self._building:
            # Asked again with the lock held: while waiting, another thread may have
            # built exactly what this one was about to build.
            built_for, built, _ = self._last_built

            if built_for == revision:
                return built

            built = self.build(
                live_document,
                live_bindings,
                live_snapshots,
                live_numbers,
            )
            self._last_built = (revision, built, the_coverage_handed_out(built))
            return built

    def hands_out_coverage(self, store: str) -> bool:
        """Whether the last answer told the page about this store's coverage.

        ``store`` is the store's place as a request names it, read back to
        plain names: ``<number>/<store>``.
        """
        return store in self._last_built[2]

    def build(
        self,
        live_document: dict,
        live_bindings,
        live_snapshots,
        live_numbers: frozenset[int],
    ) -> dict:
        """Describe every row the layer panel should show, and its group."""
        entries = self._published.entries(self._library.entries())
        present = [name for _, _, name in entries]
        labels = layer_names(present)
        groups_named = group_labels(self._library.datasets())
        merged: dict[tuple, dict] = {}
        store_paths: dict[str, Path] = {}

        for (root_number, root, name), label in zip(entries, labels, strict=True):
            if root_number in live_numbers:
                continue
            self._describe_one_store(
                root_number,
                root,
                name,
                label,
                present=present,
                group=groups_named[root_number],
                merged=merged,
                store_paths=store_paths,
            )

        rows = [{"kind": "image", **row} for row in merged.values()]

        for binding in live_bindings:
            rows.extend(
                live_rows(
                    binding,
                    chosen_window=self._window,
                    group=groups_named[binding.dataset_number],
                    snapshot=live_snapshots[binding.dataset_number],
                )
            )
        # Group order follows first appearance, which follows the sorted store
        # names, so the panel does not reshuffle itself between runs.
        groups = list(dict.fromkeys(row["group"] for row in rows))
        self._mark_coverage(rows, store_paths)
        return {
            "layers": rows,
            "groups": groups,
            "depthSamples": self._depth_samples,
            "chrome": self._chrome,
            "transparentBackground": self._transparent_background,
            "canOpen": self._allow_open,
            # Whether the selection list is offered. See ``allow_selection``.
            "canSelect": self._allow_selection,
            # Which edge the bar of controls sits on. See ``panel_side``.
            "panelSide": "left" if str(self._panel_side).lower() == "left" else "right",
            # Whether the page should keep asking if anything has changed. See
            # ``live`` above: on finished data there is nothing to notice.
            "live": self._live,
            **({"liveState": live_document} if live_bindings else {}),
        }

    def _describe_one_store(
        self, root_number, root, name, label, *, present, group, merged, store_paths
    ) -> None:
        """Add one store's channels and label images to the rows being merged.

        A channel that another store of the same acquisition already
        contributed to is merged into that row as a further source, so one row
        can span many positions; a new channel starts a row of its own.
        """
        store_path = root / name
        address = the_address_of(root_number, name, store_path)
        store_paths[address] = store_path
        source_attrs = read_attrs_at(store_path)
        named_view = (source_attrs.get("zmart") or {}).get("view")
        found = self._channels_of(store_path, name, label, root_number, present, named_view)
        frames, revision, depth, geometry_revision = self._frames_and_revisions(
            store_path, name, root_number, source_attrs, named_view
        )

        for index, channel_name, color, declared_range, active in found:
            logical_channel = 0 if depth is not None and index is None else index
            key = (root_number, logical_channel, channel_name, name if named_view else None)
            row = merged.get(key)

            if row is None:
                base = self._measurements.describe(
                    root_number,
                    root,
                    name,
                    label,
                    coloured=len(present) > 1,
                    channel=index,
                    declared_range=declared_range,
                )
                merged[key] = {
                    **base,
                    **({"view": named_view} if named_view else {}),
                    **({"acquiredCoverage": True} if "zmart_projection" in source_attrs else {}),
                    **(
                        {"sourceGeometryRevisions": [geometry_revision]}
                        if geometry_revision is not None
                        else {}
                    ),
                    **({"sourceRevisions": [revision]} if revision is not None else {}),
                    **({"sourceDepths": [depth]} if depth is not None else {}),
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
                if revision is not None:
                    row["sourceRevisions"].append(revision)
                if depth is not None:
                    row["sourceDepths"].append(depth)

                if frames and (row.get("frames") or 0) < frames:
                    row["frames"] = frames

        for mask in label_images(store_path):
            key = (root_number, "mask", mask)
            row = merged.get(key)
            source = the_address_of(
                root_number, f"{name}/labels/{mask}", store_path / "labels" / mask
            )

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

    def _channels_of(self, store_path, name, label, root_number, present, named_view):
        """The channels one store holds, each as (index, name, colour, range, active).

        A store with a channel axis names its channels inside itself. One
        without is a single channel: named after the wavelength in its folder
        name when there is one, otherwise after its label, and coloured only
        when it sits beside other stores. A published or named view without a
        channel axis still carries one described channel, which is used.
        """
        if "c" in axis_names(store_path):
            return [
                (
                    index,
                    channel["name"],
                    channel["color"],
                    channel.get("range"),
                    channel.get("active", True),
                )
                for index, channel in enumerate(channels(store_path))
            ]
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
        if self._published.source_depth(root_number, name) is not None or named_view:
            declared = read_attrs_at(store_path).get("omero", {}).get("channels", [])
            channel = described_channels(declared if isinstance(declared, list) else [], 1)[0]
            found = [
                (
                    None,
                    channel["name"],
                    channel["color"],
                    channel.get("range"),
                    channel.get("active", True),
                )
            ]
        return found

    def _frames_and_revisions(self, store_path, name, root_number, source_attrs, named_view):
        """How many frames a store has, and the revisions a reader should hold.

        Returns ``(frames, revision, depth, geometry_revision)``. A view or a
        projection counts its frames from its own array rather than from the
        written timepoints, and a named view takes its revision and frame
        count from its committed snapshot, so a reader never has to take them
        from arrays that change before the snapshot does.
        """
        frames = written_timepoints(store_path)
        revision = self._published.source_revision(root_number, name)
        depth = self._published.source_depth(root_number, name)
        if named_view or "zmart_projection" in source_attrs:
            multiscale = source_attrs["multiscales"][0]
            axes = [axis["name"] for axis in multiscale["axes"]]
            array = read_array_description(store_path / multiscale["datasets"][0]["path"])
            frames = array["shape"][axes.index("t")] if "t" in axes else 1
        geometry_revision = None
        if named_view:
            snapshot_path = store_path / "publication.json"
            if snapshot_path.exists():
                snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
                revision = snapshot["revision"]
                geometry_revision = snapshot.get("geometry_revision", 0)
                # The length as committed with those revisions. A publication
                # that changes the view's geometry declares its new arrays
                # first and commits publication.json last, so the arrays read
                # in between already count frames the revisions do not yet
                # promise, and the page re-read the view for that torn answer
                # and again for the commit. A snapshot written before the
                # length was recorded is read the old way.
                frames = snapshot.get("frames", frames)
        return frames, revision, depth, geometry_revision

    def _mark_coverage(self, rows, store_paths) -> None:
        """Say for each image row whether it is opaque or needs coverage sources.

        With a transparent background, or whenever a row shows relative depth,
        a named view or acquired coverage, the page has to know where each row
        has pixels. Only a fixed, single-source, dense row can be declared
        opaque and skip that; live rows must keep the same strategy as sources
        arrive, and covering multi-source rows must not erase lower-channel
        colour.
        """
        if not (
            self._transparent_background
            or any(
                row.get("sourceDepths") or row.get("view") or row.get("acquiredCoverage")
                for row in rows
            )
        ):
            return
        for row in rows:
            if row.get("kind", "image") != "image":
                continue
            if (
                not self._transparent_background
                and not row.get("sourceDepths")
                and not row.get("view")
                and not row.get("acquiredCoverage")
            ):
                continue
            store = store_paths.get(row["sources"][0]) if len(row["sources"]) == 1 else None
            row["opaque"] = bool(not self._live and store and not coverage.requires_geometry(store))
            if not row["opaque"]:
                row["coverageSources"] = [coverage.source_url(url) for url in row["sources"]]


class _Server(ThreadingHTTPServer):
    """The viewer's HTTP server: many requests at once, and a tidy stop.

    Starting it starts watching the disk for a live run; stopping it stops the
    watch, closes the published views, and removes the scenes this viewer
    composed for itself in its scratch folder.
    """

    request_queue_size = 128
    daemon_threads = True

    allow_reuse_address = sys.platform != "win32"

    def __init__(self, address, handler, *, registry, published, scratch):
        self._registry = registry
        self._published = published
        self._scratch = scratch
        super().__init__(address, handler)

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)

        super().server_bind()

    def serve_forever(self, *args, **kwargs):
        # The disk is watched only while the server is actually running.
        self._registry.start()
        super().serve_forever(*args, **kwargs)

    def shutdown(self):
        self._registry.stop()
        self._published.close()

        for own in ("scenes",):
            made = self._scratch.pop(own, None)
            held = self._scratch.pop(f"{own}-held", None)

            if held is not None:
                held.close()

            if made is not None:
                shutil.rmtree(made, ignore_errors=True)

        super().shutdown()


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
    allow_selection: bool = False,
    panel_side: str = "right",
    transparent_background: bool = False,
    open_from: Path | None = None,
    bake: bool = False,
    canvas: dict | None = None,
    allowed_origins: list[str] | tuple[str, ...] = (),
) -> ThreadingHTTPServer:
    """Create (but do not start) the viewer's web server.

    Given nothing to open, it serves an empty studio: no store, no demo,
    just the open door. ``data_dir`` then only says where the open dialog
    starts and where drawn targets are saved.

    Only pages served on this computer may ask the server anything, on any
    port: the viewer's own page, and an interface's page served beside it.
    ``allowed_origins`` names further pages that may, each as the origin a
    browser reports (``"http://lab-pc:8000"``). See :func:`refusal_for`.
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

    from zmart_viewer.views.publishing import PublishedFolders

    published = PublishedFolders(library, bake=bake, canvas=canvas)
    clear_what_stopped_viewers_left(the_viewers_home() / "scenes")
    scratch: dict = {"published": published, "making": threading.Lock()}
    if bake:
        for dataset in library.datasets():
            if live_run_holding(dataset.root) is None:
                published.open(dataset.root)
    registry = SourceRegistry(
        library,
        watching=live,
        refresh_publications=published.refresh,
        wants_the_bake=lambda run_root: (
            bake or Path(run_root).resolve() in scratch.get("bake_live", ())
        ),
    )

    measurements = Measurements(fixed_window=window)
    config = _LayerPanelConfig(
        library=library,
        published=published,
        registry=registry,
        measurements=measurements,
        window=window,
        depth_samples=depth_samples,
        chrome=chrome,
        transparent_background=transparent_background,
        allow_open=allow_open,
        allow_selection=allow_selection,
        panel_side=panel_side,
        live=live,
    )

    handler = functools.partial(
        _Handler,
        scratch=scratch,
        data_dir=data_dir,
        site_dir=Path(site_dir).resolve(),
        config=config.now,
        coverage_handed_out=config.hands_out_coverage,
        library=library,
        browse=browse,
        bake_job={},
        open_from=Path(open_from).resolve() if open_from else None,
        allow_open=allow_open,
        transparent_background=transparent_background,
        live=live,
        announcements=registry.announcements,
        live_state=registry.state_document,
        forget_measurements=measurements.forget,
        allowed_origins=frozenset(the_origin_of(origin) for origin in allowed_origins),
    )

    try:
        return _Server(
            ("127.0.0.1", port), handler, registry=registry, published=published, scratch=scratch
        )
    except OSError as why:
        registry.stop()
        raise OSError(
            f"the viewer could not start on port {port}: {why}\n\n"
            "That usually means something else on this machine is already using "
            f"it — most often another copy of this viewer. Either close that one, "
            "or start this one on a different port:\n\n"
            "    zmart-viewer --port 8849\n\n"
            "Any number between 1024 and 65535 that nothing else is using will do, "
            "and --port 0 lets the machine choose a free one for you and prints "
            "which it picked."
        ) from why


# -- running it -----------------------------------------------------------------


def serve(port: int = 8848) -> None:
    """Run the server until interrupted. The viewer page will be at ``/``."""
    server = make_server(port)
    print(f"ZMART Viewer serving on http://127.0.0.1:{server.server_address[1]}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Serve the ZMART Viewer engine without opening a window.")
    parser.add_argument("--port", type=int, default=8848)
    args = parser.parse_args()
    serve(args.port)
