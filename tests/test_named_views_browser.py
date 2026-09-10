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
from zmart_viewer.views import ViewSet


@pytest.mark.parametrize("bake", [False, True])
def test_same_folder_acquisitions_cover_instead_of_add(browser, built_dist, tmp_path, bake):
    opened = []
    for name, value in (("a", 400), ("b", 0)):
        positions = tmp_path / name
        positions.mkdir()
        write_tile(positions, "p.ome.zarr", np.full((1, 1, 2, 64, 64), value, dtype="uint16"))
        regions = (
            "complete"
            if name == "a"
            else {
                "p.ome.zarr": [
                    {
                        "frame": 0,
                        "channel": 0,
                        "origin": {"z": 0, "y": 0, "x": 32},
                        "shape": {"z": 2, "y": 64, "x": 32},
                    }
                ]
            }
        )
        view = ViewSet(
            tmp_path / "view",
            acquisition=name,
            projections=("max",),
            projection_folder=positions / "projections",
        )
        opened.append(view)
        view.publish(
            positions,
            {"p.ome.zarr": 1},
            {"x_um": [0, 64], "y_um": [0, 64]},
            composition={"regions": regions, "order": ["p.ome.zarr"]},
            bake=bake,
        )
    server = make_server(
        port=0,
        data_dir=tmp_path,
        site_dir=built_dist,
        live=False,
        transparent_background=True,
        window=(0, 600),
        loads=[{"path": str(tmp_path / "view")}],
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = browser.new_page()
    try:
        page.goto(f"http://127.0.0.1:{server.server_address[1]}")
        _wait_for_picture(page)
        page.evaluate("zmartViewer.display.canvas.style.background = '#ff00ff'")
        for mode in ("slice", "top", "max"):
            for name in ("a", "b"):
                page.get_by_role("combobox", name=f"{name} view").select_option(mode)
            _wait_for_picture(page)
            page.evaluate("""() => {
              const p=zmartViewer.navigationState.position, v=Float32Array.from(p.value), n=p.coordinateSpace.value.names;
              for(const [axis,value] of Object.entries({z:0,x:32,y:32})) if(n.includes(axis)) v[n.indexOf(axis)]=value;
              p.value=v; zmartViewer.navigationState.zoomFactor.value=0.5;
            }""")
            _wait_for_picture(page)
            alpha = page.evaluate(READ_ALPHA)
            assert alpha["opaque"] == 16384 and alpha["black"] == 8192, alpha
            assert alpha["partial"] == 0
            page.screenshot(path=str(tmp_path / f"overlap-{mode}-{bake}.png"))
    finally:
        page.close()
        server.shutdown()
        server.server_close()
        thread.join(5)
        for view in opened:
            view.close()


def test_legacy_3d_and_named_2d_can_share_the_page(browser, built_dist, tmp_path):
    originals = tmp_path / "positions"
    originals.mkdir()
    write_tile(originals, "p.ome.zarr", np.full((1, 1, 3, 64, 64), 200, dtype="uint16"))
    views = ViewSet(tmp_path / "view", acquisition="a", modes=("top",))
    views.publish(
        originals,
        {"p.ome.zarr": 1},
        {"x_um": [0, 64], "y_um": [0, 64]},
        composition={"regions": "complete", "order": ["p.ome.zarr"]},
    )
    server = make_server(
        port=0,
        data_dir=tmp_path,
        site_dir=built_dist,
        live=False,
        loads=[{"path": str(originals)}, {"path": str(tmp_path / "view")}],
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.goto(f"http://127.0.0.1:{server.server_address[1]}")
        _wait_for_picture(page)
        assert page.get_by_role("combobox", name="a view").is_enabled()
        page.get_by_role("button", name="3D", exact=True).click()
        page.wait_for_function(
            "zmartScene.some(l => l.volumeRendering) && !zmartScene.some(l => l.boundaryHeld)"
        )
        _wait_for_picture(page)
        assert page.get_by_role("combobox", name="a view").is_disabled()
        page.get_by_role("button", name="2D", exact=True).click()
        page.wait_for_function(
            "zmartScene.some(l => l.boundaryHeld) && !zmartScene.some(l => l.volumeRendering)"
        )
        _wait_for_picture(page)
        assert page.get_by_role("combobox", name="a view").is_enabled()
        assert not errors, errors
    finally:
        page.close()
        server.shutdown()
        server.server_close()
        thread.join(5)
        views.close()


@pytest.mark.parametrize("bake", [False, True])
def test_top_holds_each_acquisition_with_shared_folder_and_slider(
    browser, built_dist, tmp_path, bake
):
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
        for acquisition, z, x, values in (
            ("short", 0, 0, (200, 0)),
            ("long", -2, 192, (50, 100, 150, 200, 250, 300, 350)),
        ):
            source = tmp_path / acquisition
            source.mkdir()
            data = np.stack([np.full((128, 128), v, dtype="uint16") for v in values])[None, None]
            write_tile(source, "p.ome.zarr", data, x=x, z=z)
            payload = {
                "path": str(source),
                "bake": bake,
                "canvas": {"x_um": [0, 384], "y_um": [0, 128]},
                "source_revisions": {"p.ome.zarr": 1},
                "composition": {
                    "regions": "complete",
                    "order": ["p.ome.zarr"],
                    "z_references": {"p.ome.zarr": 0},
                },
                "views": {"path": str(tmp_path / "view"), "acquisition": acquisition},
            }
            response = page.request.post(address + "/api/stores/open", data=payload)
            assert response.ok, response.text()
        page.goto(address)
        _wait_for_picture(page)
        for acquisition in ("short", "long"):
            page.get_by_role("combobox", name=f"{acquisition} view").select_option("top")
        _wait_for_picture(page)
        page.evaluate("zmartViewer.display.canvas.style.background = '#ff00ff'")
        for zoom in (0.5, 4):
            counts = []
            for z in (-2, 0, 1, 4):
                page.evaluate(
                    """({z,zoom}) => {
                  const p=zmartViewer.navigationState.position, v=Float32Array.from(p.value), n=p.coordinateSpace.value.names;
                  for(const [axis,value] of Object.entries({z,x:192,y:64})) v[n.indexOf(axis)]=value;
                  p.value=v; zmartViewer.navigationState.zoomFactor.value=zoom;
                }""",
                    {"z": z, "zoom": zoom},
                )
                _wait_for_picture(page)
                counts.append(page.evaluate(READ_ALPHA))
            assert len({c["opaque"] for c in counts}) == 1, counts
            assert counts[-1]["black"] > 100 and counts[-1]["clear"] > 1000
            assert counts[-1]["black"] > counts[0]["black"]
            print({"bake": bake, "zoom": zoom, "z": [-2, 0, 1, 4], "alpha": counts})
            page.screenshot(path=str(tmp_path / f"two-top-{bake}-{zoom}.png"))
        mark = len(requests)
        page.wait_for_timeout(2300)
        assert not [url for url in requests[mark:] if "/data/" in url]
        assert not errors, errors
        assert len(page.evaluate("zmartScene.filter(l => l.type === 'image')")) == 4
    finally:
        page.close()
        server.shutdown()
        server.server_close()
        thread.join(5)


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
