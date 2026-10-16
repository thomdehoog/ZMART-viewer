"""The doors only the viewer's own window knocks on.

The engine serves pictures to any page; these routes serve the window's own
furniture: the folder chooser, the load window's list of folders and its
builder, and the file of marked places the selection list keeps. They are
handed to the engine through ``make_server(routes=...)`` by
:func:`zmart_viewer.gui.window_server.make_window_server`, so the engine
itself knows nothing of them and an interface that brings its own page is
not offered them.

Each door is a plain function of the request's JSON (``None`` for a ``GET``)
that answers ``(document, status)``. The engine has already checked who is
asking and that a body is JSON before any of them runs.
"""

from __future__ import annotations

import json
import math
import threading
from http import HTTPStatus
from pathlib import Path

from zmart_viewer.filesystem import (
    put_a_folder_in_place,
    put_text_in_place,
    rmtree_despite_brief_holds,
)
from zmart_viewer.opening.open_folders import DESCRIPTION_FILES, read_attrs_at

#: The file of marked places, beside the images. The software that runs an
#: experiment may act on it, so it is checked before it is written.
ANNOTATIONS_FILE = "zmart-annotations.json"
NO_ANNOTATIONS = {"version": 1, "annotations": []}

#: What can go wrong reading the file of marked places, short of it being
#: absent: a file locked by another program for a moment, or one that is not
#: (or no longer) what the viewer writes.
UNREADABLE = (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError)


def validated_annotations(payload: object) -> dict:
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


def saved_annotations(path: Path) -> dict:
    """The places saved in ``path``; an absent file is an empty list.

    Raises one of :data:`UNREADABLE` when the file is there but cannot be read.
    """
    try:
        return validated_annotations(json.loads(path.read_text("utf-8")))
    except FileNotFoundError:
        return NO_ANNOTATIONS


ONE_DIALOG_AT_A_TIME = threading.Lock()


def ask_this_machine_for_a_folder() -> str | None:
    """Open this machine's own folder chooser, for a page in a plain browser."""
    import tkinter
    from tkinter import filedialog

    with ONE_DIALOG_AT_A_TIME:
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


class StoppedByTheOperator(Exception):
    """Raised inside a build when the operator asked it to stop."""


class WindowDoors:
    """The window's routes, sharing what they need: where things are, and the one build.

    ``data_dir`` is where the file of marked places is kept; ``open_from`` is
    where the load window starts listing folders. ``browse`` opens a folder
    chooser and answers the path picked, or ``None``; without one the chooser
    door says so and the page offers a typed path instead. With
    ``allow_open`` off, the load window's folder list and builder are not
    offered at all, so a window that may not change what is open cannot be
    talked into it.
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        open_from: Path,
        browse=None,
        allow_open: bool = True,
    ):
        self.data_dir = data_dir
        self.open_from = open_from
        self.browse = browse
        self.allow_open = allow_open
        # One build at a time, shared by every request: its state, how far it
        # is, and whether the operator asked it to stop.
        self.build = {}

    def routes(self) -> dict:
        """The doors, as ``make_server(routes=...)`` takes them."""
        offered = {
            "POST /api/browse": self.choose_a_folder,
            "GET /api/annotations": self.marked_places,
            "POST /api/annotations": self.save_marked_places,
        }

        if self.allow_open:
            offered.update(
                {
                    "POST /api/stores/list": self.folders_at,
                    "POST /api/stores/construct": self.start_a_build,
                    "POST /api/stores/construct-status": self.how_the_build_is_doing,
                    "POST /api/stores/construct-cancel": self.stop_the_build,
                }
            )

        return offered

    # -- the folder chooser ---------------------------------------------------

    def choose_a_folder(self, payload):
        """Ask the operating system to show a folder chooser, and say what was picked."""
        if self.browse is None:
            return {
                "error": "no folder chooser is available here",
                "reason": "The chooser is opened by the desktop window. In a "
                "browser tab, type or paste the folder's path instead.",
            }, HTTPStatus.NOT_IMPLEMENTED

        try:
            chosen = self.browse()
        except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
            return {
                "error": f"the folder chooser could not be opened: {exc}",
                "reason": (
                    f"The window for choosing a folder could not be opened ({exc}). "
                    "Nothing has changed and whatever was already on screen is "
                    "still there. Type or paste the folder's path instead."
                ),
            }, HTTPStatus.INTERNAL_SERVER_ERROR

        if not chosen:
            return {"cancelled": True}, HTTPStatus.OK

        return {"path": chosen, "parent": str(Path(chosen).parent)}, HTTPStatus.OK

    # -- the load window's list of folders ------------------------------------

    def folders_at(self, payload):
        """List the folders at a path, for the in-page load window."""
        asked = payload.get("path") if isinstance(payload, dict) else None
        path = (
            Path(asked).expanduser()
            if isinstance(asked, str) and asked.strip()
            else self.open_from
        )

        try:
            path = path.resolve()

            if not path.is_dir():
                return {"error": f"there is no folder at {path}"}, HTTPStatus.NOT_FOUND

            folders = [
                described_folder(entry)
                for entry in sorted(path.iterdir(), key=lambda one: one.name.lower())
                if entry.is_dir() and not entry.name.startswith(".")
            ]
            here = kind_of_folder(path)
        except OSError as why:
            return {"error": str(why)}, HTTPStatus.BAD_REQUEST

        return {
            "path": str(path),
            "kind": here,
            "parent": str(path.parent) if path.parent != path else None,
            "folders": folders,
        }, HTTPStatus.OK

    # -- building a picture over raw data -------------------------------------

    def how_the_build_is_doing(self, payload):
        """The one build's state, or ``idle`` when none was started."""
        return dict(self.build) or {"state": "idle"}, HTTPStatus.OK

    def stop_the_build(self, payload):
        """Ask the running build to stop at its next step."""
        running = self.build.get("state") == "running"

        if running:
            self.build["stop"] = True

        return {"stopping": running}, HTTPStatus.OK

    def start_a_build(self, payload):
        """Construct a viewer over raw data, in the background, then open it."""
        asked = payload if isinstance(payload, dict) else {}
        data = asked.get("path")
        viewer = asked.get("viewer_folder")

        if not isinstance(data, str) or not data.strip():
            return {"error": "the folder holding the images is needed"}, HTTPStatus.BAD_REQUEST

        if not isinstance(viewer, str) or not viewer.strip():
            return {"error": "the folder for the viewer's files is needed"}, HTTPStatus.BAD_REQUEST

        if self.build.get("state") == "running":
            return {"error": "a bake is already running"}, HTTPStatus.CONFLICT

        data_path = Path(data.strip()).expanduser()

        if not data_path.is_dir():
            return {"error": f"there is no folder at {data_path}"}, HTTPStatus.NOT_FOUND

        from zmart_viewer.picture.built_picture import (
            declare_a_built_picture,
            the_scene_folder_name,
        )

        bake = bool(asked.get("bake"))
        name = asked.get("name") if isinstance(asked.get("name"), str) else None

        if name is not None and (not name.strip() or "/" in name or "\\" in name or ".." in name):
            return {
                "error": "the scene's name cannot contain path steps -- give it a plain name"
            }, HTTPStatus.BAD_REQUEST

        job = self.build
        job.clear()
        job.update({"state": "running", "fraction": 0.0, "bake": bake})
        viewer_path = Path(viewer.strip()).expanduser()
        scene = viewer_path / the_scene_folder_name(name or data_path.name)
        # Built aside and put in place only when whole, so that a build that
        # is stopped or fails takes away what it made and nothing else -- not
        # a scene that stood there before it began. The leading dot keeps the
        # half-made build out of the load window's list.
        aside = viewer_path / f".{scene.name}.building"

        def told(done, total):
            if job.get("stop"):
                raise StoppedByTheOperator()

            job["fraction"] = round(done / max(total, 1), 4)

        def work():
            """Build aside, then tidy up, and only then say how it ended.

            The load window lists the scenes folder as soon as it hears the
            build is over, so the half-made build must be gone by then.
            """
            try:
                rmtree_despite_brief_holds(aside)
                built = declare_a_built_picture(
                    aside,
                    data_path,
                    name=name or data_path.name,
                    bake=bake,
                    told=told,
                )
                put_a_folder_in_place(built, scene, spare=aside / "replaced")
                ending = {"state": "done", "fraction": 1.0, "store": str(scene)}
            except StoppedByTheOperator:
                ending = {"state": "cancelled"}
            except Exception as why:  # noqa: BLE001 -- shown to the operator whole
                ending = {"state": "error", "error": str(why)}

            try:
                rmtree_despite_brief_holds(aside)
            except OSError as why:
                ending = {
                    "state": "error",
                    "error": f"the half-made build in {aside} could not be removed: {why}",
                }

            job.update(ending)

        threading.Thread(target=work, daemon=True).start()
        return {"started": True}, HTTPStatus.OK

    # -- the file of marked places --------------------------------------------

    def marked_places(self, payload):
        """The places the operator has marked, as saved beside the images."""
        try:
            return saved_annotations(self.data_dir / ANNOTATIONS_FILE), HTTPStatus.OK
        except UNREADABLE:
            # Named plainly rather than as "the sidecar", which is our word for
            # it and means nothing to somebody reading it for the first time.
            return {
                "error": (
                    "the file of marked places beside the images "
                    f"({ANNOTATIONS_FILE}) could not be read, so none of "
                    "them are shown. It is still there and has not been "
                    "changed."
                )
            }, HTTPStatus.INTERNAL_SERVER_ERROR

    def save_marked_places(self, payload):
        """Save the places the operator has marked."""
        try:
            document = validated_annotations(payload)
        except ValueError as exc:
            return {"error": str(exc)}, HTTPStatus.BAD_REQUEST

        path = self.data_dir / ANNOTATIONS_FILE

        # Replacing a file that cannot be read would throw away whatever is in
        # it, which the page was never able to show. It is left alone instead.
        try:
            saved_annotations(path)
        except UNREADABLE:
            return {
                "error": (
                    f"the file of marked places beside the images ({ANNOTATIONS_FILE}) "
                    "could not be read, so it was not replaced: saving now would lose "
                    "what is in it. Close any program that has it open, or move it "
                    "aside, then reopen the viewer."
                )
            }, HTTPStatus.CONFLICT

        # Written beside the data and renamed into place, pushed to the disk
        # first, so a crash mid-save leaves the old file whole; a failed save
        # clears its half-written file away rather than leaving litter beside
        # the operator's data.
        try:
            put_text_in_place(path, json.dumps(document, indent=2) + "\n", pushed_to_disk=True)
        except OSError as why:
            return {
                "error": (
                    f"could not write {path} ({why.strerror or why}). The places "
                    "you have marked are still on screen and whatever was saved "
                    "before is untouched, but nothing new has reached the disk. "
                    "This is usually a folder that cannot be written to, or a "
                    "drive that has filled up or gone away."
                )
            }, HTTPStatus.INTERNAL_SERVER_ERROR

        return document, HTTPStatus.OK


def kind_of_folder(folder: Path) -> str | None:
    """What a folder holds, as the load window names it: a view, plate, image, run, or nothing."""
    described = set(DESCRIPTION_FILES)

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

        if any((folder / name / stamp).exists() for name in inside for stamp in described):
            return "run"
    except OSError:
        pass

    return None


def described_folder(folder: Path) -> dict:
    """One row of the load window's list: the folder's name, its kind, and whether it is baked."""
    kind = kind_of_folder(folder)
    told_of = {"name": folder.name, "kind": kind}

    if kind == "view":
        told_of["baked"] = bool((read_attrs_at(folder).get("zmart") or {}).get("baked"))

    return told_of
