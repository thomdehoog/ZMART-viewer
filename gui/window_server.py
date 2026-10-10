"""The engine, set up for the viewer's own window.

:func:`make_window_server` starts the same engine an interface starts with
:func:`zmart_viewer.make_server`, and adds what only this window uses: the
settings its page reads (where the bar of controls sits, whether the
selection list is offered, how finely a volume is sampled, whether
neuroglancer's own outline is drawn) and the window's own doors (the folder
chooser, the load window's folder list and builder, the file of marked
places; see :mod:`zmart_viewer.gui.window_routes`). Keeping them here means
the engine offers an interface only what any page needs.
"""

from __future__ import annotations

from http.server import ThreadingHTTPServer
from pathlib import Path

from zmart_viewer.gui.window_routes import WindowDoors
from zmart_viewer.serving.server import make_server


def make_window_server(
    port: int = 8848,
    *,
    data_dir: Path | None = None,
    allow_open: bool = True,
    chrome: bool = False,
    panel_side: str = "right",
    allow_selection: bool = False,
    depth_samples: int = 256,
    browse=None,
    open_from: Path | None = None,
    **engine_options,
) -> ThreadingHTTPServer:
    """Create (but do not start) the engine with the window's settings and doors.

    ``chrome`` draws neuroglancer's own outline and axis lines around the
    picture. ``panel_side`` is the edge, ``"left"`` or ``"right"``, the bar of
    controls sits on. ``allow_selection`` offers the selection list, whose
    marked places are saved beside the images. ``depth_samples`` is how finely
    a volume is sampled along each ray until the operator changes it.
    ``browse`` opens a folder chooser (see :func:`window_routes.ask_this_machine_for_a_folder`)
    and ``open_from`` is where the load window starts listing folders;
    ``data_dir`` by default.

    Everything else (``store``, ``loads``, ``live``, ``bake`` and the rest) is
    the engine's own, as :func:`zmart_viewer.make_server` takes it.
    """
    data = Path(data_dir).resolve() if data_dir is not None else Path.cwd()
    doors = WindowDoors(
        data_dir=data,
        open_from=Path(open_from).resolve() if open_from else data,
        browse=browse,
        allow_open=allow_open,
    )

    return make_server(
        port,
        data_dir=data,
        allow_open=allow_open,
        page_settings={
            "chrome": bool(chrome),
            "panelSide": "left" if str(panel_side).lower() == "left" else "right",
            "canSelect": bool(allow_selection),
            "depthSamples": depth_samples,
        },
        routes=doors.routes(),
        **engine_options,
    )
