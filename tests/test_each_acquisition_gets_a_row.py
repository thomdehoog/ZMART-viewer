"""An overview and a target scan are different things and want separate rows.

What tells them apart is what tells every acquisition apart: the voxel size
the microscope actually used, read from inside the stores. A target scan at
the overview's own voxel size would be indistinguishable by anything but its
name, and names are labels, never grouping.
"""

from __future__ import annotations

import threading

from pointed_by_hand import a_tile

from zmart_viewer.server import make_server


def test_each_acquisition_type_gets_a_row_of_its_own(browser, built_dist, tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    a_tile(run / "overview.zarr", 1200, pieces=2)
    a_tile(run / "targetscan.zarr", 3000, pieces=2, voxel=(2.0, 0.175, 0.175))

    # One store is opened; the other is discovered by the live watcher and
    # must appear as its own row, because its voxel size says it is another
    # kind of acquisition.
    server = make_server(port=0, data_dir=run, site_dir=built_dist, store="overview.zarr")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = browser.new_page(viewport={"width": 900, "height": 700})

    try:
        page.goto(f"http://127.0.0.1:{server.server_address[1]}", wait_until="domcontentloaded")
        page.wait_for_function("() => window.zmartViewer !== undefined", timeout=60_000)
        page.wait_for_function("() => window.zmartSourcesWaiting() === 0", timeout=120_000)
        page.wait_for_timeout(2_000)
        rows = page.evaluate(
            """() => window.zmartViewer.layerManager.managedLayers
                 .filter((m) => m.layer && m.layer.type === 'image')
                 .map((m) => m.name)"""
        )
        assert len(rows) == 2, (
            f"expected a row for the overview and one for the target scan, got {rows}"
        )
    finally:
        page.close()
        server.shutdown()
        thread.join(timeout=5)
