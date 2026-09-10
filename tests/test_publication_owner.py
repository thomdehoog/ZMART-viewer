"""Reopening a published folder updates its owner, not the number of owners."""

import json
import threading

import pytest
from test_acquired_composition import source
from test_published_acquired import CANVAS
from test_server import request

from zmart_viewer.published import STACK_STORE, PublishedTransfer
from zmart_viewer.server import make_server


@pytest.mark.parametrize("preloaded", [False, True])
def test_concurrent_folder_and_store_opens_share_owner(tmp_path, preloaded):
    from concurrent.futures import ThreadPoolExecutor

    from zmart_viewer.library import Library
    from zmart_viewer.published import PublishedFolders

    source(tmp_path, "a.ome.zarr", 120)
    library = Library()
    if preloaded:
        library.open(tmp_path)
    published = PublishedFolders(library)

    def open_path(path):
        return published.open(
            path,
            canvas=CANVAS,
            versions={"a.ome.zarr": 1},
            bake=False,
            composition={"regions": "complete", "order": ["a.ome.zarr"]},
        )

    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            numbers = list(workers.map(open_path, [tmp_path, tmp_path / "a.ome.zarr"]))
        assert numbers == [0, 0]
        assert len(library.datasets()) == len(published.views) == 1
        assert published.refresh() == ((0, 1),)
        assert published.entries(library.entries()) == [(0, tmp_path, STACK_STORE)]
    finally:
        published.close()


@pytest.mark.parametrize("first_bake", [False, True])
def test_http_reopen_retains_identity_and_idle_revision(tmp_path, monkeypatch, first_bake):
    source(tmp_path, "a.ome.zarr", 120)
    server = make_server(port=0, data_dir=tmp_path, live=True, allow_open=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    payload = {
        "path": str(tmp_path),
        "canvas": CANVAS,
        "bake": first_bake,
        "source_revisions": {"a.ome.zarr": 1},
        "composition": {"regions": "complete", "order": ["a.ome.zarr"]},
    }

    def post(route, value):
        status, _, body = request(port, route, "POST", json.dumps(value).encode())
        return status, json.loads(body)

    def sources(config):
        return {url for row in config["layers"] for url in row["sources"]}

    try:
        status, original = post("/api/stores/open", payload)
        assert status == 200, original
        assert len(sources(original)) == 1
        payload["bake"] = not first_bake
        status, reopened = post("/api/stores/open", payload)
        assert status == 200, reopened
        assert sources(reopened) == sources(original)
        assert len(reopened["layers"]) == len(original["layers"])
        state = tmp_path / STACK_STORE / "publication.json"
        assert json.loads(state.read_text())["revision"] == 2
        mark = state.stat().st_mtime_ns

        def no_bake(*args, **kwargs):
            pytest.fail("An unchanged re-announcement baked pixels")

        monkeypatch.setattr(PublishedTransfer, "_replace_one_piece", no_bake)
        publication = {key: payload[key] for key in ("path", "source_revisions", "composition")}
        for _ in range(3):
            assert post("/api/announce", {"publications": [publication]})[0] == 200
            assert state.stat().st_mtime_ns == mark
            assert json.loads(state.read_text())["revision"] == 2
        # A rejected reopen must not close or replace the working dataset.
        bad = {**payload, "composition": {"regions": "complete", "order": []}}
        assert post("/api/stores/open", bad)[0] == 400
        status, _, body = request(port, "/api/config")
        assert status == 200
        assert sources(json.loads(body)) == sources(original)
        assert post("/api/announce", {"publications": [publication]})[0] == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize("first_bake", [False, True])
def test_browser_reopen_keeps_one_source_and_idle_cache(browser, built_dist, tmp_path, first_bake):
    import numpy as np
    from pixels import image_middle
    from test_manifest_refresh_browser import _wait_for_picture
    from test_published_transfer import write_position

    write_position(tmp_path, "a.ome.zarr", 0, 2400)
    server = make_server(
        port=0,
        data_dir=tmp_path,
        site_dir=built_dist,
        live=True,
        allow_open=True,
        window=(0, 4095),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = f"http://127.0.0.1:{server.server_address[1]}"
    page = browser.new_page(viewport={"width": 1200, "height": 900})
    requests, errors = [], []
    page.on("request", lambda request: requests.append(request.url))
    page.on("pageerror", lambda error: errors.append(str(error)))
    payload = {
        "path": str(tmp_path),
        "bake": first_bake,
        "canvas": {"x_um": [0, 1024], "y_um": [0, 512]},
        "source_revisions": {"a.ome.zarr": 1},
        "composition": {"regions": "complete", "order": ["a.ome.zarr"]},
    }
    try:
        assert page.request.post(f"{address}/api/stores/open", data=payload).ok
        page.goto(address)
        _wait_for_picture(page)
        page.evaluate("""() => {
            const p = zmartViewer.navigationState.position;
            const at = Float32Array.from(p.value), names = p.coordinateSpace.value.names;
            for (const [axis, value] of Object.entries({x:64,y:64,z:0})) at[names.indexOf(axis)]=value;
            p.value=at; zmartViewer.navigationState.zoomFactor.value=1;
        }""")
        _wait_for_picture(page)
        before = image_middle(page)
        assert np.count_nonzero(before) > 1000
        original = page.evaluate("zmartConfig.layers")
        assert len({url for row in original for url in row["sources"]}) == 1
        payload["bake"] = not first_bake
        assert page.request.post(f"{address}/api/stores/open", data=payload).ok
        publication = {key: payload[key] for key in ("path", "source_revisions", "composition")}
        # An external opener announces the change to already-open browser sessions.
        assert page.request.post(f"{address}/api/announce", data={"publications": [publication]}).ok
        page.wait_for_function("zmartConfig.layers.some(row => row.sourceRevisions?.[0] === 2)")
        _wait_for_picture(page)
        reopened = page.evaluate("zmartConfig.layers")
        assert [row["sources"] for row in reopened] == [row["sources"] for row in original]
        np.testing.assert_array_equal(image_middle(page), before)
        mark = len(requests)
        for _ in range(3):
            assert page.request.post(
                f"{address}/api/announce", data={"publications": [publication]}
            ).ok
            page.wait_for_timeout(1700)
        assert not [url for url in requests[mark:] if "/data/" in url]
        assert page.evaluate("zmartConfig.layers[0].sourceRevisions[0]") == 2
        assert not errors, errors
        page.screenshot(path=str(tmp_path / f"reopened-bake-{not first_bake}.png"))
        print(
            {
                "first_bake": first_bake,
                "aggregate_sources": 1,
                "changed_pixels_on_mode_switch": 0,
                "idle_data_requests": 0,
            }
        )
    finally:
        page.close()
        server.shutdown()
        server.server_close()
        thread.join(5)
