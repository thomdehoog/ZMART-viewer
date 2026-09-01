"""Choosing the intensity window a store is first displayed with.

The case that matters is the real one: 16-bit data whose signal occupies a few
hundred counts near the bottom of the range. Stretching the type's full range
there renders black, so these assert the window actually tracks the data.
"""

from __future__ import annotations

import json

import numpy as np
from pathlib import Path
import zarr
from demo_data import write_demo_zarr

from zmart_viewer.contrast import (
    HISTOGRAM_BINS,
    Measurements,
    display_window,
    intensity_histogram,
    measure,
)


def write_store(path, data: np.ndarray, omero: dict | None = None) -> str:
    """A minimal single-level OME-Zarr, enough for the window logic."""
    group = zarr.open_group(str(path), mode="w", zarr_format=2)
    array = group.create_array("0", shape=data.shape, chunks=data.shape, dtype=data.dtype)
    array[:] = data
    attrs = {
        "multiscales": [
            {
                "version": "0.4",
                "axes": [{"name": n, "type": "space"} for n in "zyx"],
                "datasets": [{"path": "0", "coordinateTransformations": []}],
            }
        ]
    }
    if omero is not None:
        attrs["omero"] = omero
    (path / ".zattrs").write_text(json.dumps(attrs), encoding="utf-8")
    return str(path)


def test_window_tracks_a_narrow_band_of_the_sixteen_bit_range(tmp_path):
    """Real mesoSPIM-like data: ~198 background, signal a few counts above."""
    rng = np.random.default_rng(0)
    data = rng.integers(198, 210, size=(8, 64, 64)).astype(np.uint16)
    window = display_window(write_store(tmp_path / "a.zarr", data))
    assert 195 <= window[0] <= 200
    assert 205 <= window[1] <= 212
    assert window[1] < 300, "a full-range window is what renders real data black"


def test_a_single_hot_pixel_does_not_stretch_the_window(tmp_path):
    """Min/max would blow the ramp out to 60000 and darken everything else."""
    data = np.full((8, 64, 64), 200, dtype=np.uint16)
    data[0, 0, 0] = 60000
    window = display_window(write_store(tmp_path / "b.zarr", data))
    assert window[1] < 1000


def test_a_declared_omero_window_is_honoured_over_measurement(tmp_path):
    data = np.full((4, 16, 16), 500, dtype=np.uint16)
    omero = {"channels": [{"window": {"start": 100.0, "end": 4000.0}}]}
    assert display_window(write_store(tmp_path / "c.zarr", data, omero)) == (100.0, 4000.0)


def test_the_demo_volume_uses_its_own_declared_window(tmp_path):
    # The declared start sits 20 counts below the 800 background on purpose:
    # values at or below the black point are drawn transparent by the
    # coverage rule, and a black point sitting ON the background bit a
    # corner out of the demo volume (the operator moved it, 2026-08-23).
    store = write_demo_zarr(tmp_path / "demo.zarr")
    assert display_window(store) == (780.0, 20800.0)


def test_uniform_data_still_yields_a_usable_window(tmp_path):
    data = np.full((4, 16, 16), 42, dtype=np.uint16)
    low, high = display_window(write_store(tmp_path / "d.zarr", data))
    assert high > low


def test_an_unreadable_store_has_no_invented_window(tmp_path):
    assert display_window(tmp_path / "missing.zarr") is None


def test_histogram_covers_every_sample_in_compact_bins(tmp_path):
    # Starting at 1, not 0: a voxel equal to the store's fill value is
    # excluded from the measurement by design (declared-but-unwritten ground
    # reads back as fill — see the generously-declared-room gate in
    # test_brightness_is_measured_honestly), and this test's own claim is
    # about bins covering the sample, not about that rule.
    data = np.arange(1, 8 * 16 * 16 + 1, dtype=np.uint16).reshape(8, 16, 16)
    histogram = intensity_histogram(write_store(tmp_path / "hist.zarr", data))
    assert histogram is not None
    assert len(histogram["counts"]) == HISTOGRAM_BINS
    assert sum(histogram["counts"]) == data.size
    assert histogram["high"] > histogram["low"]
    assert histogram["autoWindow"]["high"] > histogram["autoWindow"]["low"]


def test_histogram_range_resists_a_single_hot_pixel(tmp_path):
    data = np.full((8, 32, 32), 200, dtype=np.uint16)
    data[0, 0, 0] = 60000
    histogram = intensity_histogram(write_store(tmp_path / "hot.zarr", data))
    assert histogram is not None
    assert histogram["high"] < 1000
    assert sum(histogram["counts"]) == data.size


def test_uniform_histogram_still_has_a_usable_auto_window(tmp_path):
    data = np.full((4, 8, 8), 42, dtype=np.uint16)
    histogram = intensity_histogram(write_store(tmp_path / "uniform.zarr", data))
    assert histogram is not None
    assert histogram["high"] > histogram["low"]
    assert histogram["autoWindow"] == {"low": 42.0, "high": 43.0}


def test_unreadable_store_has_no_invented_histogram(tmp_path):
    assert intensity_histogram(tmp_path / "missing.zarr") is None


def test_volume_window_starts_far_above_the_background(tmp_path):
    """Sparse bright structure in a sea of background — the real 3-D case."""
    rng = np.random.default_rng(1)
    data = rng.integers(198, 205, size=(16, 64, 64)).astype(np.uint16)
    data[data.shape[0] // 2, ::16, ::16] = 5000  # a little real signal

    flat = display_window(write_store(tmp_path / "a.zarr", data))
    volume = display_window(write_store(tmp_path / "b.zarr", data), volumetric=True)
    assert volume[0] > flat[0], "a volume window must clear the background"
    assert volume[0] >= 204


def test_volume_window_ignores_a_declared_omero_window(tmp_path):
    """omero describes how to show a slice; obeying it in 3-D gives fog."""
    data = np.full((8, 32, 32), 300, dtype=np.uint16)
    omero = {"channels": [{"window": {"start": 0.0, "end": 65535.0}}]}
    store = write_store(tmp_path / "c.zarr", data, omero)
    assert display_window(store) == (0.0, 65535.0)
    assert display_window(store, volumetric=True) != (0.0, 65535.0)


def test_one_measurement_answers_all_three_questions(tmp_path):
    """The server asks once and gets a plane window, a volume window and a histogram.

    Reading pixels is the most expensive thing the viewer does when an
    acquisition is first opened, so the three answers come from a single look at
    the data rather than from three separate ones. This checks that the shared
    path agrees with asking each question on its own.
    """
    rng = np.random.default_rng(4)
    data = (500 + rng.integers(0, 4000, size=(8, 64, 64))).astype(np.uint16)
    store = write_store(tmp_path / "measured.zarr", data)

    together = measure(store)

    assert together["window"] == display_window(store)
    assert together["volumeWindow"] == display_window(store, volumetric=True)
    assert together["histogram"] == intensity_histogram(store)


def test_measuring_an_unreadable_store_reports_that_no_window_exists_yet(tmp_path):
    """A broken store is kept distinct from an ordinary pixel-empty one."""
    broken = tmp_path / "not-really.zarr"
    broken.mkdir()
    (broken / ".zattrs").write_text("{ this is not json", encoding="utf-8")

    together = measure(broken)

    assert together["window"] is None
    assert together["volumeWindow"] is None
    assert together["histogram"] is None
    assert together["settled"] is False
    # And the sentence names the fact that would help: the file is not JSON,
    # not "a description with no levels".
    assert together["measurementState"] == "unreadable"
    assert ".zattrs cannot be read" in together["measurementError"]
    assert together["measurementState"] == "unreadable"
    assert together["measurementError"]


def test_a_window_the_store_declares_is_reported_as_declared_not_measured(tmp_path):
    """The panel says whether a run chose its window or the Viewer measured one.

    A store that declares ``start``/``end`` has made a decision, and the
    configuration row must say ``declared`` so the page does not label it
    "measured from pixels acquired so far" and invite an operator to overrule
    the run's own choice.
    """
    data = (np.arange(64 * 64, dtype=np.uint16).reshape(1, 64, 64) % 3000) + 100
    store = write_store(
        tmp_path / "declared.zarr", data,
        omero={"channels": [{"label": "GFP", "color": "00FF00",
                             "window": {"min": 0, "max": 65535, "start": 300, "end": 4200}}]},
    )

    found = measure(store, channel=0)
    assert found["window"] == (300.0, 4200.0)
    assert found["measurementState"] == "declared"

    row = Measurements().describe(0, tmp_path, Path(store).name, "GFP", True, channel=0)
    assert row["window"] == {"low": 300.0, "high": 4200.0}
    assert row["measurementState"] == "declared"


def test_an_empty_config_row_carries_null_windows_instead_of_camera_range(tmp_path):
    store = tmp_path / "arriving.zarr"
    store.mkdir()
    (store / ".zattrs").write_text("{ this is not json", encoding="utf-8")

    described = Measurements().describe(0, tmp_path, store.name, "arriving", False)

    assert described["window"] is None
    assert described["volumeWindow"] is None
    assert described["histogram"] is None
    assert described["settled"] is False
    assert described["measurementState"] == "unreadable"


def test_an_empty_live_source_says_it_is_waiting_instead_of_showing_camera_range(
    browser, built_dist, tmp_path
):
    import threading

    from zmart_viewer.server import make_server

    store = tmp_path / "arriving.zarr"
    group = zarr.open_group(str(store), mode="w", zarr_format=2)
    group.create_array("0", shape=(1, 32, 32), chunks=(1, 32, 32), dtype="uint16")
    (store / ".zattrs").write_text(
        json.dumps(
            {
                "multiscales": [
                    {
                        "axes": [{"name": name} for name in "zyx"],
                        "datasets": [{"path": "0"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    server = make_server(
        port=0, data_dir=tmp_path, site_dir=built_dist, store=store.name, live=True
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = browser.new_page(viewport={"width": 900, "height": 700})
    try:
        page.goto(f"http://127.0.0.1:{server.server_address[1]}", wait_until="domcontentloaded")
        waiting = page.get_by_role("status")
        waiting.wait_for(state="visible", timeout=60_000)
        assert waiting.inner_text() == "waiting for measurable pixels"
        assert page.get_by_label("min arriving").count() == 0
        assert page.get_by_label("max arriving").count() == 0
    finally:
        page.close()
        server.shutdown()
        thread.join(timeout=5)


def test_an_unreadable_live_source_reports_a_fault_instead_of_waiting(
    browser, built_dist, tmp_path
):
    import threading

    from zmart_viewer.server import make_server

    store = tmp_path / "broken.zarr"
    store.mkdir()
    (store / ".zattrs").write_text("{ this is not json", encoding="utf-8")
    server = make_server(
        port=0, data_dir=tmp_path, site_dir=built_dist, store=store.name, live=True
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = browser.new_page(viewport={"width": 900, "height": 700})
    try:
        page.goto(f"http://127.0.0.1:{server.server_address[1]}", wait_until="domcontentloaded")
        fault = page.get_by_role("alert")
        fault.wait_for(state="visible", timeout=60_000)
        assert fault.inner_text() == "this acquisition cannot be read"
        assert page.get_by_text("waiting for measurable pixels").count() == 0
    finally:
        page.close()
        server.shutdown()
        thread.join(timeout=5)


# -- measuring a linked live picture ------------------------------------------
#
# A live run's picture holds no voxels of its own: every piece of it is
# answered by handing over a position's file, so its level folders hold only
# descriptions. Measuring it directly finds nothing, and for as long as that
# was the whole story a live run opened with no histogram -- the panel had
# nothing to draw and the Auto button nothing to work from. The contract
# layout says exactly where the real pixels are (the members of the
# acquisition's data collection), so the measurement follows them -- and it
# reaches them the same way from either store in the view folder, because
# what it walks up to is the run, not the view.


def a_linked_run(folder, *, channels=("channel 0",), value=1200):
    from zmart_viewer.record.coordinator import LivePublisher
    from zmart_viewer.record.model import GridCell
    from zmart_viewer.record.profiles import plan_the_writing

    frame = 384
    profile, _ = plan_the_writing("overview", frame=frame, z_planes=1, channels=channels)
    run = LivePublisher(folder, profile, run_id="measured", cells={GridCell(0, 0): "p00"})
    if len(channels) == 1:
        pixels = np.full((1, frame, frame), value, "uint16")
    else:
        pixels = np.empty((len(channels), 1, frame, frame), "uint16")
        for index in range(len(channels)):
            pixels[index] = value * (index + 1)
    run.write_and_publish("p00", pixels)
    # The picture the viewer is actually served, which the live registry has
    # named as the live source since 2026-08-12. It used to be the linked
    # view beside it; that one is written when the run FINISHES, so a
    # measurement aimed at it had nothing to read while the microscope was
    # still going -- Auto went dead on exactly the runs it is for
    # (2026-08-26). Both sit in the same view folder and both measure
    # through the same members, so what this gate asks has not changed.
    #
    # Declared here because that is what opening the run does, and a
    # measurement only ever happens on a run somebody has opened. Before the
    # linked view moved to end-of-run this fixture got away with naming a
    # store nothing had declared, because the writer left one behind on every
    # publish.
    from zmart_viewer.live import LIVE_PICTURE, the_live_picture_declared

    the_live_picture_declared(folder)
    return folder / LIVE_PICTURE


def test_a_linked_live_picture_measures_through_its_members(tmp_path):
    live = a_linked_run(tmp_path, value=1200)
    found = intensity_histogram(live)
    assert found is not None, (
        "a live picture holds no voxels of its own, but the members of its "
        "data collection do -- the measurement must follow the link"
    )
    assert found["low"] <= 1200 <= found["high"]


def test_each_channel_of_a_linked_picture_measures_its_own_brightness(tmp_path):
    live = a_linked_run(tmp_path, channels=("green", "red"), value=900)
    first = intensity_histogram(live, channel=0)
    second = intensity_histogram(live, channel=1)
    assert first is not None and second is not None
    assert first["high"] < second["low"], (
        "the two channels hold different brightness on purpose; a shared "
        "histogram would set both channels' windows from the mixture"
    )
