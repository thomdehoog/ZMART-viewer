"""Tell open pages that something changed, the moment it happens.

One kind of message — *something changed* — pushed over a connection each
page holds; the page then re-reads state in the ordinary way, so the disk
stays the one description that has to be right. A ZMART run's atomic
publication marker is watched directly; ordinary folders get the generic
folder watcher.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

log = logging.getLogger("zmart-viewer.announcements")

QUIET_HEARTBEAT_S = 15.0

HEARTBEAT = b": still here\n\n"

SOMETHING_CHANGED = b"event: changed\ndata: {}\n\n"

IMAGE_WRITTEN_IN_PLACE = b'event: changed\ndata: {"imageWrittenInPlace": true}\n\n'


class Announcements:
    """Everyone currently listening, and the way to tell them something changed."""

    def __init__(self, when_changed: Callable[[], None] | None = None) -> None:
        self._listeners: set[queue.SimpleQueue] = set()
        self._lock = threading.Lock()
        self._closed = False
        # Server-owned work that must happen even when no page is listening.
        # It is a nudge, not the work itself: the callback must return quickly.
        self._when_changed = when_changed
        self._already_told: object | None = None

    def listen(self) -> queue.SimpleQueue:
        """Start listening. Returns the queue to read messages from."""
        waiting: queue.SimpleQueue = queue.SimpleQueue()
        with self._lock:
            if self._closed:
                # The server is shutting down, so hand back something that ends
                # immediately rather than a listener that would wait for ever.
                waiting.put(None)
                return waiting
            self._listeners.add(waiting)
        return waiting

    def stop_listening(self, waiting: queue.SimpleQueue) -> None:
        with self._lock:
            self._listeners.discard(waiting)

    def say_something_changed(
        self,
        *,
        image_written_in_place: bool = False,
        covering: object | None = None,
    ) -> int:
        """Tell every open page to ask again. Returns how many were told."""
        message = IMAGE_WRITTEN_IN_PLACE if image_written_in_place else SOMETHING_CHANGED
        with self._lock:
            if covering is not None:
                self._already_told = covering
            listeners = list(self._listeners)
        if self._when_changed is not None:
            try:
                self._when_changed()
            except Exception:
                log.exception("server-side announcement work failed")
        for waiting in listeners:
            waiting.put(message)
        return len(listeners)

    def already_told_about(self) -> object | None:
        """What the disk looked like when the microscope last announced something."""
        with self._lock:
            return self._already_told

    def close(self) -> None:
        """Let every listener go, so their threads can finish."""
        with self._lock:
            self._closed = True
            listeners = list(self._listeners)
            self._listeners.clear()
        for waiting in listeners:
            waiting.put(None)

    @property
    def listening(self) -> int:
        """How many pages are currently connected. Useful in tests and diagnosis."""
        with self._lock:
            return len(self._listeners)


class FolderWatcher:
    """Watches the open folders and announces when something on disk changes."""

    def __init__(
        self,
        library,
        announcements: Announcements,
        *,
        every: float = 1.0,
        excluding=frozenset(),
    ) -> None:
        self._library = library
        self._announcements = announcements
        self._every = every
        self._excluding = excluding
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._watch, daemon=True, name="zmart-folder-watch")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _watch(self) -> None:
        last = None
        while not self._stop.is_set():
            try:
                now = (
                    self._library.revision(excluding=self._excluding)
                    if callable(self._excluding) or self._excluding
                    else self._library.revision()
                )
            except Exception:
                # A folder that cannot be read this moment -- a share hiccuping --
                # is not a reason to stop watching for the rest of the session.
                now = last
            if last is not None and now != last:
                if now != self._announcements.already_told_about():
                    self._announcements.say_something_changed()
            last = now
            self._stop.wait(self._every)


class ManifestWatcher:
    """Announce only higher, strictly validated manifest revisions."""

    def __init__(
        self,
        trackers,
        announcements: Announcements,
        *,
        every: float = 1.0,
    ) -> None:
        self._trackers = trackers if callable(trackers) else lambda: tuple(trackers)
        self._announcements = announcements
        self._every = every
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        initial = self._trackers()
        self._announced = {tracker: tracker.revision for tracker in initial}

    def check_once(self) -> int:
        """Observe every marker once and return effective announcements made."""
        announced = 0
        trackers = self._trackers()
        alive = set(trackers)
        self._announced = {
            tracker: revision for tracker, revision in self._announced.items() if tracker in alive
        }
        for tracker in trackers:
            tracker.observe()
            if tracker not in self._announced:
                self._announced[tracker] = tracker.revision
                self._announcements.say_something_changed()
                announced += 1
                continue
            previous = self._announced[tracker]
            if tracker.error is not None or tracker.revision <= previous:
                continue
            self._announced[tracker] = tracker.revision
            self._announcements.say_something_changed()
            announced += 1
        return announced

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._watch,
            daemon=True,
            name="zmart-manifest-watch",
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _watch(self) -> None:
        while not self._stop.is_set():
            self.check_once()
            self._stop.wait(self._every)
