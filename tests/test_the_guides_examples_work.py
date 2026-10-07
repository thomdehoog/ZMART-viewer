"""The requests shown in docs/README.md and the engine tutorial work as written.

Someone building their own interface will copy these examples exactly, so an
example that the engine refuses is a bug, even when the engine is right. The
guide once showed an open without ``"bake": true``, and the announcement that
followed it was refused with HTTP 400.
"""

from __future__ import annotations

import threading

from grid_scans import STEP_UM, _a_grid_scan, _post
from zmart_viewer import make_server


def test_open_then_announce_as_the_guide_shows(tmp_path):
    positions = _a_grid_scan(tmp_path / "positions", reduction="mean")
    names = sorted(p.name for p in positions.iterdir())
    canvas = {"x_um": [0, 2 * STEP_UM + 384], "y_um": [0, 2 * STEP_UM + 384]}
    server = make_server(port=0, data_dir=tmp_path, live=True, allow_open=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    address = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, answer = _post(
            address,
            "/api/stores/open",
            {
                "path": str(positions),
                "bake": True,
                "canvas": canvas,
                "source_revisions": {names[0]: 1},
            },
        )
        assert status == 200, answer
        status, answer = _post(
            address,
            "/api/announce",
            {
                "publications": [
                    {"path": str(positions), "source_revisions": {names[0]: 2, names[1]: 1}}
                ]
            },
        )
        assert status == 200, answer
    finally:
        server.shutdown()
        server.server_close()
