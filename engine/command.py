"""The command that opens the viewer: ``zmart-viewer``, with a folder or without.

This is the front door for anyone who wants to look at their own images.
After installing the package, type ``zmart-viewer`` to open an empty window
with a way to choose a folder, or ``zmart-viewer /path/to/data`` to open
that folder straight away. The folder may be one OME-Zarr image or a folder
holding many of them; the viewer works out which.

The window is a native desktop window (on Windows it uses the WebView2 engine
that ships with Edge). When no such window can be shown, the viewer prints an
address instead, and you open that address in an ordinary browser. Either way
the same page appears and works the same.

Smart-microscopy integrations do not go through here. They start the engine
directly with :func:`zmart_viewer.serving.server.make_server` and build their own
window around it; see ``docs/inside-your-own-interface.md``.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from zmart_viewer.serving.server import _FRONTEND_DIST, make_server


def _webview2_present() -> bool:
    """On Windows, check that the WebView2 runtime the window needs is installed."""
    if not sys.platform.startswith("win"):
        return True  # not Windows: not our concern here

    try:
        import winreg

        for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for key in (
                r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
                r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
            ):
                try:
                    with winreg.OpenKey(root, key) as k:
                        version, _ = winreg.QueryValueEx(k, "pv")
                        # An uninstalled runtime can leave the key behind with a
                        # zero version; treat that as "not present".
                        if version and version != "0.0.0.0":
                            return True
                except OSError:
                    continue
    except Exception:
        return False

    return False


def open_window(
    port: int = 8848,
    *,
    width: int = 1500,
    height: int = 950,
    data_dir: Path | None = None,
    store: str | list[str] | None = None,
    window: tuple[float, float] | None = None,
    depth_samples: int = 256,
    chrome: bool = False,
    live: bool = True,
    allow_open: bool = True,
    allow_selection: bool = False,
    panel_side: str = "right",
    transparent_background: bool = False,
    open_from: Path | None = None,
    native: bool = True,
) -> None:
    """Start the engine and open the viewer in a native window.

    With ``native=False`` no window is opened: the address is printed and the
    engine keeps serving until you press Ctrl+C. That is the way to use the
    viewer over a remote desktop or from a machine without a window library.
    """
    chooser: dict = {}

    def browse():
        show = chooser.get("show")

        if show:
            return show()

        from zmart_viewer.serving.server import ask_this_machine_for_a_folder

        return ask_this_machine_for_a_folder()

    kwargs = {
        "store": store,
        "window": window,
        "depth_samples": depth_samples,
        "chrome": chrome,
        "browse": browse,
        "live": live,
        "allow_open": allow_open,
        "allow_selection": allow_selection,
        "panel_side": panel_side,
        "transparent_background": transparent_background,
        # Where the load window starts browsing, when that is somewhere other
        # than the data folder itself.
        "open_from": open_from,
    }

    if data_dir is not None:
        kwargs["data_dir"] = data_dir

    server = make_server(port, **kwargs)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    if not native:
        _serve_until_interrupt(server, url)
        return

    if not _webview2_present():
        print(
            "The WebView2 runtime does not appear to be installed, so a native "
            "window cannot be shown.\n"
            "Install it (a small free download from Microsoft, search "
            '"WebView2 Evergreen Runtime"), or just open this address in Edge '
            f"or Chrome:\n    {url}"
        )
        _serve_until_interrupt(server, url)
        return

    try:
        import webview  # pywebview
    except ImportError:
        print(
            "The native-window library (pywebview) is not installed, so the "
            f"viewer will not pop up on its own.\nOpen this address in a "
            f"browser instead:\n    {url}\n"
            "(To get the pop-up window, install it with: pip install pywebview)"
        )
        _serve_until_interrupt(server, url)
        return

    native_window = webview.create_window("ZMART Viewer", url, width=width, height=height)

    def show_folder_dialog():
        """Show the operating system's own folder chooser and return what was picked."""
        chosen = native_window.create_file_dialog(webview.FOLDER_DIALOG)

        if not chosen:
            return None

        return chosen[0] if isinstance(chosen, (list, tuple)) else str(chosen)

    chooser["show"] = show_folder_dialog
    webview.start()
    server.shutdown()


def _serve_until_interrupt(server, url: str) -> None:
    import time

    print(f"Serving at {url} — press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        server.shutdown()


def main(argv: list[str] | None = None) -> int:
    """Run the ``zmart-viewer`` command. Returns the exit code for the shell."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="zmart-viewer",
        description=(
            "Open the ZMART Viewer. Give it a folder of OME-Zarr images to open "
            "that folder; give it nothing to open an empty window and choose a "
            "folder from there."
        ),
    )
    parser.add_argument(
        "folder",
        nargs="?",
        type=Path,
        help="an OME-Zarr image, or a folder holding many of them (optional)",
    )
    parser.add_argument(
        "--range",
        help="display window as LOW,HIGH; by default it is read from the "
        "image's own description, or measured from the smallest copy of it",
    )
    parser.add_argument(
        "--tiles",
        help="which tiles to open, e.g. 0,1 — default is every tile found",
    )
    parser.add_argument(
        "--filter",
        dest="filter_name",
        help="when a tile and channel were acquired through several filters, "
        "keep the one whose name contains this, e.g. Empty",
    )
    parser.add_argument(
        "--depth-samples",
        type=int,
        default=256,
        help="samples along each viewing ray in 3-D mode (default 256). "
        "Higher is sharper and slower.",
    )
    parser.add_argument(
        "--chrome",
        action="store_true",
        help="show the engine's own bounding box and axis lines (off by default)",
    )
    parser.add_argument(
        "--static",
        action="store_true",
        help="the data is finished, not still being written. The viewer then stops "
        "looking for new images and new frames, which is the largest thing it "
        "does on a big folder. Leave it off while an experiment is producing data.",
    )
    parser.add_argument(
        "--select",
        action="store_true",
        help="show the selection list, where places you mark on the image are "
        "gathered. Off by default.",
    )
    parser.add_argument(
        "--panel-side",
        choices=("right", "left"),
        default="right",
        help="which edge the bar of controls sits on, and which way it folds away",
    )
    parser.add_argument(
        "--no-open-button",
        action="store_true",
        help="leave out the way of choosing folders by hand, so only what was "
        "given on the command line can be shown",
    )
    parser.add_argument(
        "--no-window",
        action="store_true",
        help="do not open a window; print the address and serve until Ctrl+C. "
        "Use this over a remote desktop, or to open the viewer in a browser.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8848,
        help="which port on this machine the viewer answers on (default 8848). "
        "Change it when something else is already using that number, or give 0 "
        "to let the machine pick a free one.",
    )
    args = parser.parse_args(argv)

    if not (_FRONTEND_DIST / "index.html").exists():
        print(
            "The viewer page is not built, so there is nothing to show.\n"
            "An installed wheel already contains the page. From a source checkout, "
            "build it once with:\n"
            "    npm --prefix app/page install\n"
            "    npm --prefix app/page run build\n"
            "then run this again."
        )
        return 1

    window = None
    if args.range:
        low, _, high = args.range.partition(",")
        window = (float(low), float(high))

    common = {
        "port": args.port,
        "window": window,
        "depth_samples": args.depth_samples,
        "chrome": args.chrome,
        "allow_selection": args.select,
        "panel_side": args.panel_side,
        "allow_open": not args.no_open_button,
        "native": not args.no_window,
    }

    if args.folder is None:
        # Nothing to open yet: an empty viewer with the load window, where a
        # folder can be chosen by hand.
        print("Opening the ZMART Viewer. Use the load button to choose a folder.")
        open_window(live=not args.static, **common)
        return 0

    from zmart_viewer.opening.library import discover, prefer_filter, select_tiles

    try:
        parent, names = discover(args.folder)
    except OSError as unreadable:
        print(f"That folder could not be read: {unreadable}")
        return 1
    if args.tiles:
        names = select_tiles(names, [int(t) for t in args.tiles.split(",")])
    names = prefer_filter(names, args.filter_name)
    if not names:
        print(
            f"No OME-Zarr image was found at {args.folder}.\n"
            "The viewer opens OME-Zarr folders (usually ending in .ome.zarr or "
            ".zarr), or a folder that holds several of them. Try the folder above "
            "or below this one."
        )
        return 1
    what = names[0] if len(names) == 1 else f"{len(names)} images from {parent.name}"
    print(f"Opening {what}...")
    # A narrowed selection must stay narrowed: were the folder still being
    # watched, the tiles and filters deliberately left out would reappear.
    narrowed = bool(args.tiles or args.filter_name)
    try:
        open_window(
            data_dir=parent,
            store=names,
            live=not narrowed and not args.static,
            **common,
        )
    except ValueError as refused:
        # The viewer declines to open a folder holding more than one acquisition
        # (an overview and a target scan are two pictures, not one) and names
        # the images in each. The message was written for the person at the
        # microscope, so it is printed as it stands rather than as a traceback.
        print(f"\n{refused}")
        print(
            "\nPoint the command at one of the images listed above to open that "
            "acquisition on its own, for example:\n"
            f'    zmart-viewer "{parent / names[0]}"\n'
            "While the folder is being watched, the rest of that acquisition "
            "joins it as it is written, and a different acquisition appearing "
            "in the folder is given its own heading."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
