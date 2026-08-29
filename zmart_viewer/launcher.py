"""Open the studio in a native desktop window, or print the address.

The window uses the OS web engine (WebView2 on Windows); without one the
viewer runs the same in an ordinary browser.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from .server import make_server


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
    store: str | list[str] = "demo.zarr",
    window: tuple[float, float] | None = None,
    depth_samples: int = 256,
    chrome: bool = False,
    live: bool = True,
    allow_open: bool = True,
    allow_selection: bool = False,
    panel_side: str = "right",
    open_from: Path | None = None,
) -> None:
    """Start the server and open the studio in a native window."""
    chooser: dict = {}

    def browse():
        show = chooser.get("show")
        if show:
            return show()
        from .server import ask_this_machine_for_a_folder

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
        # Where the load window starts browsing, when that is somewhere other
        # than the data folder itself.
        "open_from": open_from,
    }
    if data_dir is not None:
        kwargs["data_dir"] = data_dir
    server = make_server(port, **kwargs)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

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
            "(To get the pop-up window, install pywebview: it is in the conda "
            "environment file.)"
        )
        _serve_until_interrupt(server, url)
        return

    native = webview.create_window("ZMART Viewer", url, width=width, height=height)

    def show_folder_dialog():
        """Show the operating system's own folder chooser and return what was picked."""
        chosen = native.create_file_dialog(webview.FOLDER_DIALOG)
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


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Open the visualization studio window.")
    parser.add_argument("--port", type=int, default=8848)
    args = parser.parse_args()
    open_window(args.port)
