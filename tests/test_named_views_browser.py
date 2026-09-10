import threading

import numpy as np
import pytest
import zarr
from pixels import image_middle
from test_manifest_refresh_browser import _wait_for_picture
from test_transparent_2d_browser import READ_ALPHA
from test_view_sampling import write_tile

from zmart_viewer.compose import MEAN_REDUCTION
from zmart_viewer.server import make_server


@pytest.mark.parametrize("bake", [False, True])
def test_named_view_switching_pixels_requests_and_reopen(browser, built_dist, tmp_path, bake):
    originals = tmp_path / "positions"
    originals.mkdir()
    flat = np.full((1, 1, 1, 128, 128), 100, dtype="uint16")
    stack = np.stack([np.full((128, 128), n, dtype="uint16") for n in (0, 200, 400)])[None, None]
    write_tile(originals, "flat.ome.zarr", flat, x=0)
    write_tile(originals, "stack.ome.zarr", stack, x=192)
    names = ["flat.ome.zarr", "stack.ome.zarr"]
    payload = {
        "path": str(originals),
        "bake": bake,
        "canvas": {"x_um": [0, 384], "y_um": [0, 128]},
        "source_revisions": dict.fromkeys(names, 1),
        "composition": {"regions": "complete", "order": names, "pyramid_reduction": MEAN_REDUCTION},
        "views": {
            "path": str(tmp_path / "view"),
            "acquisition": "test",
            "projections": ["min", "max", "sum"],
            "projection_path": str(tmp_path / "projections"),
        },
    }
    server = make_server(
        port=0,
        data_dir=tmp_path,
        site_dir=built_dist,
        live=True,
        allow_open=True,
        transparent_background=True,
        window=(0, 600),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = f"http://127.0.0.1:{server.server_address[1]}"
    page = browser.new_page(viewport={"width": 1200, "height": 900})
    requests, errors = [], []
    page.on("request", lambda r: requests.append(r.url))
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        response = page.request.post(address + "/api/stores/open", data=payload)
        assert response.ok, response.text()
        page.goto(address)
        _wait_for_picture(page)
        # A visible page below the canvas makes transparent gaps and acquired
        # black distinguishable in the proof photographs as well as GL pixels.
        page.evaluate("zmartViewer.display.canvas.style.background = '#ff00ff'")
        selector = page.get_by_role("combobox", name="test view")
        assert selector.input_value() == "slice"

        def depth(z):
            page.evaluate(
                """z => {
                const p = zmartViewer.navigationState.position;
                const values = Float32Array.from(p.value), names = p.coordinateSpace.value.names;
                for (const [axis, value] of Object.entries({x:192,y:64,z})) {
                    const i = names.indexOf(axis); if(i >= 0) values[i] = value;
                }
                p.value=values; zmartViewer.navigationState.zoomFactor.value=0.5;
            }""",
                z,
            )
            _wait_for_picture(page)

        depth(2)
        slice_pixels = image_middle(page)
        slice_alpha = page.evaluate(READ_ALPHA)
        page.screenshot(path=str(tmp_path / f"view-slice-{bake}.png"))
        selector.select_option("top")
        _wait_for_picture(page)
        depth(2)
        top_pixels = image_middle(page)
        top_alpha = page.evaluate(READ_ALPHA)
        page.screenshot(path=str(tmp_path / f"view-top-{bake}.png"))
        changed = int(np.count_nonzero(np.any(slice_pixels != top_pixels, axis=2)))
        assert changed > 1000
        assert top_alpha["opaque"] > slice_alpha["opaque"]
        for method in ("min", "max", "sum"):
            selector.select_option(method)
            _wait_for_picture(page)
            alpha = page.evaluate(READ_ALPHA)
            assert alpha["clear"] > 1000 and alpha["opaque"] > 1000
            if method == "min":
                assert alpha["black"] > 1000
            assert len(page.evaluate("zmartScene.filter(l => l.type === 'image')")) == 2
            page.screenshot(path=str(tmp_path / f"view-{method}-{bake}.png"))
            page.evaluate("zmartViewer.navigationState.zoomFactor.value = 4")
            _wait_for_picture(page)
            coarse = page.evaluate(READ_ALPHA)
            assert coarse["opaque"] == 2048 and coarse["partial"] == 0
            if method == "min":
                assert coarse["black"] == 1024
            page.screenshot(path=str(tmp_path / f"view-{method}-coarse-{bake}.png"))
            page.evaluate("zmartViewer.navigationState.zoomFactor.value = 0.5")
            _wait_for_picture(page)
        selector.select_option("top")
        _wait_for_picture(page)
        assert (
            page.evaluate(
                "zmartViewer.navigationState.position.value[zmartViewer.navigationState.position.coordinateSpace.value.names.indexOf('z')]"
            )
            == 2
        )
        assert page.evaluate("zmartViewer.navigationState.zoomFactor.value") == 0.5
        mark = len(requests)
        publication = {k: payload[k] for k in ("path", "source_revisions", "composition")}
        assert page.request.post(address + "/api/announce", data={"publications": [publication]}).ok
        page.wait_for_timeout(2300)
        assert not [u for u in requests[mark:] if "/data/" in u]
        assert not any("flat.ome.zarr/" in u or "stack.ome.zarr/" in u for u in requests)
        assert not errors, errors
        print(
            {
                "bake": bake,
                "slice_top_changed_pixels": changed,
                "slice_alpha": slice_alpha,
                "top_alpha": top_alpha,
                "idle_data_requests": 0,
                "position_source_requests": 0,
            }
        )
    finally:
        page.close()
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize("fail_metadata", [False, True])
@pytest.mark.parametrize("grow_frames", [False, True])
def test_geometry_refreshes_pixels_and_final_failure_retries(
    browser, built_dist, tmp_path, fail_metadata, grow_frames
):
    positions = tmp_path / "positions"
    positions.mkdir()
    tile = write_tile(
        positions, "stack.ome.zarr", np.full((2, 2, 3, 128, 128), 100, dtype="uint16"), z=100
    )
    payload = {
        "path": str(positions),
        "bake": True,
        "canvas": {"x_um": [0, 128], "y_um": [0, 128]},
        "source_revisions": {tile.name: 1},
        "composition": {
            "regions": "complete",
            "order": [tile.name],
            "z_references": {tile.name: 100},
            "pyramid_reduction": MEAN_REDUCTION,
        },
        "views": {"path": str(tmp_path / "view"), "acquisition": "shift", "modes": ["top"]},
    }
    server = make_server(
        port=0,
        data_dir=tmp_path,
        site_dir=built_dist,
        live=True,
        allow_open=True,
        transparent_background=True,
        window=(0, 800),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = f"http://127.0.0.1:{server.server_address[1]}"
    page = browser.new_page(viewport={"width": 1000, "height": 800})
    requests, errors = [], []
    page.on("request", lambda r: requests.append(r.url))
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        response = page.request.post(address + "/api/stores/open", data=payload)
        assert response.ok, response.text()
        page.goto(address)
        _wait_for_picture(page)
        page.evaluate("""() => {
          const p=zmartViewer.navigationState.position, a=Float32Array.from(p.value), n=p.coordinateSpace.value.names;
          for(const [axis,value] of Object.entries({z:2,t:1,y:64,x:64})) a[n.indexOf(axis)]=value;
          p.value=a; zmartViewer.navigationState.zoomFactor.value=0.5;
        }""")
        _wait_for_picture(page)
        before = image_middle(page)
        if grow_frames:
            write_tile(
                positions, tile.name, np.full((3, 2, 5, 128, 128), 300, dtype="uint16"), z=100
            )
        else:
            group = zarr.open_group(str(tile.store), mode="r+")
            for level in range(3):
                group[str(level)][:] = 300
        if fail_metadata:
            # One final metadata request fails; no later acquisition or rewrite
            # may be required to make the successfully published revision visible.
            failed = []

            def first_failure(route):
                if not failed:
                    failed.append(route.request.url)
                    route.fulfill(status=503, body="injected transient metadata failure")
                else:
                    route.continue_()

            page.route("**/shift_top.zmartview.zarr/zarr.json", first_failure)
        payload["source_revisions"][tile.name] = 2
        payload["composition"]["z_references"][tile.name] = 99
        publication = {k: payload[k] for k in ("path", "source_revisions", "composition")}
        mark = len(requests)
        response = page.request.post(
            address + "/api/announce", data={"publications": [publication]}
        )
        assert response.ok, response.text()
        page.wait_for_function("zmartConfig.layers.every(row => row.sourceRevisions[0] === 2)")
        page.wait_for_function("""() => zmartViewer.layerManager.managedLayers.every(m =>
          (m.layer?.dataSources||[]).every(s => !s.metadataRefresh && s.loadState && !s.loadState.error))""")
        _wait_for_picture(page)
        # Pixel equality is polled because a deliberately failed HTTP request
        # retains old pixels until its failure-only retry has completed.
        from time import monotonic

        until = monotonic() + 10
        changed = 0
        while monotonic() < until:
            changed = int(np.count_nonzero(np.any(image_middle(page) != before, axis=2)))
            if changed > 1000:
                break
            page.wait_for_timeout(100)
        assert changed > 1000
        alpha = page.evaluate(READ_ALPHA)
        assert alpha["opaque"] == 65536 and alpha["partial"] == 0
        assert page.evaluate("zmartViewer.navigationState.zoomFactor.value") == 0.5
        changed_requests = [u for u in requests[mark:] if "/data/" in u and "/c/" in u]
        assert len(changed_requests) == len(set(changed_requests)), changed_requests
        mark = len(requests)
        page.wait_for_timeout(2300)
        assert not [u for u in requests[mark:] if "/data/" in u]
        if fail_metadata:
            assert len(failed) == 1
        assert not errors, errors
        page.screenshot(path=str(tmp_path / "same-shape-rewrite.png"))
        print(
            {
                "metadata_failure": fail_metadata,
                "frame_growth": grow_frames,
                "changed_pixels": changed,
                "chunk_requests": len(changed_requests),
                "idle_requests": 0,
            }
        )
    finally:
        page.close()
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize("flat_first", [False, True])
def test_live_flat_stack_arrival_refreshes_geometry_once(browser, built_dist, tmp_path, flat_first):
    positions = tmp_path / "positions"
    positions.mkdir()
    write_tile(positions, "flat.ome.zarr", np.full((1, 2, 1, 128, 128), 100, dtype="uint16"))
    tile = write_tile(
        positions, "stack.ome.zarr", np.full((1, 2, 3, 128, 128), 300, dtype="uint16"), x=192, z=100
    )
    group = zarr.open_group(str(tile.store), mode="r+")
    metadata = dict(group.attrs)
    for level in metadata["ome"]["multiscales"][0]["datasets"]:
        level["coordinateTransformations"][0]["scale"][2] = 2
    group.attrs.update(metadata)
    first, second = (
        ("flat.ome.zarr", "stack.ome.zarr") if flat_first else ("stack.ome.zarr", "flat.ome.zarr")
    )
    payload = {
        "path": str(positions),
        "bake": True,
        "canvas": {"x_um": [0, 384], "y_um": [0, 128]},
        "source_revisions": {first: 1},
        "composition": {
            "regions": "complete",
            "order": [first],
            "pyramid_reduction": MEAN_REDUCTION,
        },
        "views": {"path": str(tmp_path / "view"), "acquisition": "live"},
    }
    server = make_server(
        port=0,
        data_dir=tmp_path,
        site_dir=built_dist,
        live=True,
        allow_open=True,
        transparent_background=True,
        window=(0, 600),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = f"http://127.0.0.1:{server.server_address[1]}"
    page = browser.new_page(viewport={"width": 1200, "height": 900})
    requests, errors = [], []
    page.on("request", lambda r: requests.append(r.url))
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        response = page.request.post(address + "/api/stores/open", data=payload)
        assert response.ok, response.text()
        page.goto(address)
        _wait_for_picture(page)
        page.get_by_role("combobox", name="live view").select_option("top")
        _wait_for_picture(page)
        payload["source_revisions"][second] = 1
        payload["composition"]["order"].append(second)
        publication = {k: payload[k] for k in ("path", "source_revisions", "composition")}
        mark = len(requests)
        response = page.request.post(
            address + "/api/announce", data={"publications": [publication]}
        )
        assert response.ok, response.text()
        page.wait_for_function("zmartConfig.layers.every(row => row.sourceRevisions[0] === 2)")
        _wait_for_picture(page)
        page.evaluate("""() => {
            const p=zmartViewer.navigationState.position, a=Float32Array.from(p.value), n=p.coordinateSpace.value.names;
            for(const [axis,value] of Object.entries({z:2,y:64,x:192})) a[n.indexOf(axis)]=value;
            p.value=a; zmartViewer.navigationState.zoomFactor.value=0.5;
        }""")
        _wait_for_picture(page)
        result = page.evaluate(READ_ALPHA)
        assert result["opaque"] == 131072
        assert result["partial"] == 0
        changed = [u for u in requests[mark:] if "/data/" in u and "/c/" in u]
        assert len(changed) == len(set(changed)), changed
        mark = len(requests)
        page.wait_for_timeout(2300)
        assert not [u for u in requests[mark:] if "/data/" in u]
        assert not errors, errors
        print(
            {
                "flat_first": flat_first,
                "alpha": result,
                "changed_chunk_requests": len(changed),
                "idle_refetches": 0,
            }
        )
    finally:
        page.close()
        server.shutdown()
        server.server_close()
        thread.join(5)
