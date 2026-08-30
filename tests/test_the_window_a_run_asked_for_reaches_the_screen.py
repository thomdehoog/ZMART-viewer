"""A run's own brightness window has to reach the screen.

A microscopist choosing a brightness range at the instrument expects to see
their acquisition looking that way when they open it. OME-Zarr carries the
choice in the ``omero`` block, as ``start`` and ``end`` — beside them sit
``min`` and ``max``, the *camera's whole range*, and reading those instead
opens the picture very nearly black.

**For a pointed-at picture there is no way to recover from getting this
wrong.** Where a run says nothing, the viewer reads pixels and works a
window out — but a pointed view holds no pixels of its own, so that road
finds only emptiness and the window comes out covering the whole data type.
Measured before the fix: the specimen on screen at fourteen out of two
hundred and fifty-five, over a perfectly healthy frame rate.
"""

from __future__ import annotations

import numpy as np
from pixels import fraction_lit
from pointed_by_hand import TILE, a_pointed_view, a_tile

from zmart_viewer.library import channels

# The band the specimen really occupies, and what the run asks to be shown.
# Well inside the camera's range on purpose: that gap is the whole subject.
ASKED_FOR = (400, 3800)
CAMERA_RANGE = (0, 65535)


def _a_pointed_picture(folder):
    """Four banded tiles and the pixel-less view that points at them."""
    folder.mkdir(parents=True)
    rng = np.random.default_rng(7)

    for index in range(4):
        body = (2500 + rng.normal(0, 300, (1, 1, 1, TILE, TILE))).clip(0, 4000)
        a_tile(folder / f"pos_{index}.zarr", 0, body=body.astype("uint16"))
    return a_pointed_view(
        folder / "picture.zarr",
        [(f"pos_{index}.zarr", (index // 2, index % 2)) for index in range(4)],
        canvas_chunks=(2, 2),
        omero={
            "channels": [
                {
                    "label": "488",
                    "color": "00FF66",
                    "active": True,
                    "window": {
                        "start": ASKED_FOR[0],
                        "end": ASKED_FOR[1],
                        "min": CAMERA_RANGE[0],
                        "max": CAMERA_RANGE[1],
                    },
                }
            ]
        },
    )


def test_the_store_reports_the_window_the_run_asked_for(tmp_path):
    """Read from the description, and from the right two of the four numbers."""
    picture = _a_pointed_picture(tmp_path / "experiment")
    described = channels(picture)

    assert len(described) == 1
    window = described[0].get("window")
    assert window is not None, (
        "the store says which brightness it should be shown between and the viewer "
        "did not read it, so the picture will open on the camera's whole range"
    )
    assert (window["low"], window["high"]) == ASKED_FOR, (
        f"the window came back as {window}, which is not what the run asked for. "
        "If it is the camera's whole range, min and max are being read where start "
        "and end were meant"
    )
    assert (window["low"], window["high"]) != CAMERA_RANGE


def test_a_picture_that_holds_no_pixels_still_opens_bright(browser, built_dist, tmp_path):
    """The end of it: open the picture and look at the screen.

    Every request answered and a healthy frame rate prove nothing here — the
    fault this holds out was a black screen behind both.
    """
    import threading

    from zmart_viewer.server import make_server

    folder = tmp_path / "experiment"
    picture = _a_pointed_picture(folder)

    server = make_server(
        port=0, data_dir=folder, site_dir=built_dist, store=picture.name, live=False
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = browser.new_page(viewport={"width": 900, "height": 700})

    try:
        page.goto(f"http://127.0.0.1:{server.server_address[1]}", wait_until="domcontentloaded")
        page.wait_for_function("() => window.zmartViewer !== undefined", timeout=60_000)
        page.wait_for_function("() => window.zmartSourcesWaiting() === 0", timeout=120_000)
        page.wait_for_timeout(4000)

        lit = fraction_lit(page)
        assert lit > 0.3, (
            f"only {lit:.3f} of the picture reached the screen. The specimen is "
            "there and every piece is served — a dim screen here means the window "
            "came from the camera's range or from reading a pixel-less picture"
        )
    finally:
        page.close()
        server.shutdown()
        thread.join(timeout=5)
