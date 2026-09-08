"""Interactively add and rewrite six positions over a transparent background.

From the repository root: python demos/show_source_refresh.py
Build app/page first. Generated runs are retained under testdata/source_refresh_demo.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import threading
from pathlib import Path

import numpy as np
import zarr

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from zmart_viewer.record.coordinator import LivePublisher  # noqa: E402
from zmart_viewer.record.model import GridCell  # noqa: E402
from zmart_viewer.record.profiles import plan_the_writing  # noqa: E402
from zmart_viewer.server import make_server  # noqa: E402

FRAME = 1152
POSITIONS = 6


def picture(number: int, revision: int) -> np.ndarray:
    y, x = np.indices((FRAME, FRAME))
    radius = np.hypot(x - FRAME / 2, y - FRAME / 2)
    bright = ((radius // 90 + revision) % 2 == 0) & (radius < FRAME * 0.44)
    return np.where(bright, 1700 + number * 300, 0).astype("uint16")[None]


class Demo:
    def __init__(self, folder: Path):
        profile, _ = plan_the_writing("overview", frame=FRAME, z_planes=1)
        self._run = LivePublisher(
            folder,
            profile,
            run_id="source-refresh-demo",
            cells={
                GridCell(row, col): f"pos{row * 3 + col}" for row in range(2) for col in range(3)
            },
        )
        self._lock = threading.Lock()
        self.count = 1
        self.revision = 1
        # Stored zero-valued chunks prove acquisition, unlike absent chunks.
        with zarr.config.set({"array.write_empty_chunks": True}):
            self._run.write_and_publish("pos0", picture(0, 1))

    def publish(self, rewrite: bool = False) -> str:
        with self._lock, zarr.config.set({"array.write_empty_chunks": True}):
            if not rewrite and self.count == POSITIONS:
                return "All 6 positions acquired. Try Rewrite last."
            number = self.count - 1 if rewrite else self.count
            revision = self.revision + 1
            if rewrite:
                self._run.replace_a_position(f"pos{number}", picture(number, revision))
            else:
                self._run.write_and_publish(f"pos{number}", picture(number, revision))
                self.count += 1
            self.revision = revision
            return f"{self.count}/6 positions — revision {revision}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bake", action="store_true", help="bake the combined coarse overview (off by default)")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "testdata/source_refresh_demo",
        help="parent directory for a fresh run and its window profile",
    )
    args = parser.parse_args(argv)
    site = ROOT / "app/page/dist"
    if not (site / "index.html").is_file():
        parser.error("Build the page first: npm --prefix app/page run build")

    import webview

    args.output.mkdir(parents=True, exist_ok=True)
    session = Path(tempfile.mkdtemp(prefix="run-", dir=args.output))
    demo = Demo(session / "data")
    server = make_server(
        port=0,
        data_dir=session / "data",
        site_dir=site,
        store="views/live/live.ome.zarr",
        window=(0, 4095),
        live=True,
        allow_open=False,
        allow_selection=True,
        transparent_background=True,
        bake=args.bake,
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    address = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Viewer: {address}\nDemo files: {session}", flush=True)
    try:
        window = webview.create_window(
            "ZMART viewer — whole-source refresh test (not the operator)",
            address,
            width=1500,
            height=950,
            js_api=demo,
        )
        controls = Path(__file__).with_suffix(".js").read_text(encoding="utf-8")
        window.events.loaded += lambda: window.evaluate_js(controls)
        webview.start(
            gui="edgechromium" if sys.platform == "win32" else None,
            storage_path=str(session / "webview-profile"),
        )
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
