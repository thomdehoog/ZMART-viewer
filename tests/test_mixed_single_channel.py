"""A singleton channel keeps one identity across flat and stack aggregates."""

import json
import threading

import numpy as np
import pytest
import zarr
from test_published_depth import CANVAS, composition
from test_published_transfer import write_position
from test_server import request

from zmart_viewer.library import axis_names
from zmart_viewer.published import STACK_STORE, STORE, PublishedAcquisition
from zmart_viewer.server import make_server


@pytest.mark.parametrize("bake", [False, True])
@pytest.mark.parametrize("flat_first", [False, True])
@pytest.mark.parametrize("label", [None, "DNA"])
@pytest.mark.parametrize("stack_frames", [1, 2])
def test_single_channel_keeps_shared_controls(tmp_path, bake, flat_first, label, stack_frames):
    names = ["flat.ome.zarr", "stack.ome.zarr"]
    for name, depth in zip(names, (1, 3), strict=True):
        store = write_position(
            tmp_path,
            name,
            0 if depth == 1 else 256,
            1000,
            depth=depth,
            frames=1 if depth == 1 else stack_frames,
        )
        if label:
            group = zarr.open_group(str(store), mode="r+")
            ome = group.attrs["ome"]
            ome["omero"] = {"channels": [{"label": label, "color": "00FF00"}]}
            group.attrs["ome"] = ome
    server = make_server(port=0, data_dir=tmp_path, live=True, allow_open=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def publish(selected, opening=False):
        payload = {
            "path": str(tmp_path),
            "source_revisions": dict.fromkeys(selected, 1),
            "composition": composition(selected),
        }
        if opening:
            payload.update(canvas=CANVAS, bake=bake)
        status, _, body = request(
            port,
            "/api/stores/open" if opening else "/api/announce",
            "POST",
            json.dumps(payload if opening else {"publications": [payload]}).encode(),
        )
        assert status == 200, body
        status, _, body = request(port, "/api/config")
        assert status == 200, body
        return json.loads(body)

    try:
        initial = publish([names[0 if flat_first else 1]], opening=True)
        combined = publish(names)
        assert len(initial["layers"]) == len(combined["layers"]) == 1
        before, after = initial["layers"][0], combined["layers"][0]
        assert before["name"] == after["name"] == (label or "channel 1")
        assert after["sourceDepths"] == ["flat", "stack"]
        assert len(after["sources"]) == len(after["coverageSources"]) == 2
        assert after.get("channelIndex") is None
        assert after.get("localPosition") is None
        if label:
            assert after["color"] == [0, 1, 0]
        for output in (STORE, STACK_STORE):
            grown = output == STACK_STORE and stack_frames > 1
            assert axis_names(tmp_path / output) == list("tczyx" if grown else "zyx")
            assert zarr.open_array(str(tmp_path / output / "0"), mode="r").ndim == (
                5 if grown else 3
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize("flat_first", [False, True])
def test_mixed_channel_counts_are_refused_before_publication(tmp_path, flat_first):
    names = ["flat.ome.zarr", "stack.ome.zarr"]
    write_position(tmp_path, names[0], 0, 1000, depth=1, channels=1)
    write_position(tmp_path, names[1], 256, 2000, depth=3, channels=2)
    first = [names[0 if flat_first else 1]]
    view = PublishedAcquisition(tmp_path, piece=64)
    try:
        view.publish(tmp_path, dict.fromkeys(first, 1), CANVAS, composition=composition(first))
        before = {
            name: (tmp_path / name / "publication.json").read_bytes() for name in view.sources
        }
        with pytest.raises(ValueError, match="share a channel count"):
            view.publish(tmp_path, dict.fromkeys(names, 1), CANVAS, composition=composition(names))
        assert view.sources == list(before)
        assert all(
            (tmp_path / name / "publication.json").read_bytes() == data
            for name, data in before.items()
        )
    finally:
        view.close()


@pytest.mark.parametrize("bake", [False, True])
@pytest.mark.parametrize("flat_first", [False, True])
def test_browser_single_channel_mixed_time(browser, built_dist, tmp_path, bake, flat_first):
    from test_manifest_refresh_browser import _wait_for_picture

    names = ["flat.ome.zarr", "stack.ome.zarr"]
    write_position(tmp_path, names[0], 0, 1000, depth=1)
    stack = write_position(tmp_path, names[1], 256, 1000, frames=2, depth=3)
    group = zarr.open_group(str(stack), mode="r+")
    for level in range(3):
        values = np.fromfunction(lambda t, z: 1000 + 400 * t + 800 * z, (2, 3)).astype("uint16")
        group[str(level)][:] = np.broadcast_to(
            values[:, None, :, None, None], group[str(level)].shape
        )
    server = make_server(
        port=0,
        data_dir=tmp_path,
        site_dir=built_dist,
        transparent_background=True,
        window=(0, 4095),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = f"http://127.0.0.1:{server.server_address[1]}"
    page = browser.new_page(viewport={"width": 1200, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def publish(selected, opening=False):
        payload = {
            "path": str(tmp_path),
            "source_revisions": dict.fromkeys(selected, 1),
            "composition": composition(selected),
        }
        if opening:
            payload.update(canvas=CANVAS, bake=bake)
        answer = page.request.post(
            address + ("/api/stores/open" if opening else "/api/announce"),
            data=payload if opening else {"publications": [payload]},
        )
        assert answer.ok, answer.text()

    def pixels(z, t, zoom=1):
        page.evaluate(
            """([z,t,zoom]) => {
          const p=zmartViewer.navigationState.position, s=p.coordinateSpace.value;
          const at=Float32Array.from(p.value);
          for(const [axis,um] of Object.entries({x:192,y:64,z})) {
            const i=s.names.indexOf(axis); if(i>=0) at[i]=um/(s.scales[i]*1e6);
          }
          at[s.names.indexOf('t')]=t;
          p.value=at; zmartViewer.navigationState.zoomFactor.value=zoom;
        }""",
            [z, t, zoom],
        )
        _wait_for_picture(page)
        return page.evaluate(
            """zoom => {
          const dc=zmartViewer.display; dc.draw(); const gl=dc.gl;
          return [64,192,320].map(x=>{
            const rgba=new Uint8Array(4);
            gl.readPixels(Math.round(gl.drawingBufferWidth/2+(x-192)/zoom),
              Math.floor(gl.drawingBufferHeight/2),1,1,gl.RGBA,gl.UNSIGNED_BYTE,rgba);
            return Array.from(rgba);
          });
        }""",
            zoom,
        )

    try:
        publish([names[0 if flat_first else 1]], opening=True)
        page.goto(address)
        _wait_for_picture(page)
        publish(names)
        page.wait_for_function(
            "zmartConfig.layers.length===1 && zmartConfig.layers[0].sources.length===2"
        )
        _wait_for_picture(page)
        page.wait_for_function("""() => zmartViewer.navigationState.position.coordinateSpace.value.names.includes('t')
          && zmartViewer.layerManager.managedLayers.filter(m=>m.layer.type==='image').length === 4
          && zmartViewer.layerManager.managedLayers.every(m=>m.layer.dataSources.every(s=>s.loadState))""")
        measured = [pixels(z, t) for z, t in ((0, 0), (1, 0), (1, 1), (2, 1), (10, 1))]
        assert all(frame[0][3] == 255 and max(frame[0][:3]) > 50 for frame in measured)
        np.testing.assert_array_equal(np.array(measured)[:, 0], [measured[0][0]] * 5)
        assert all(frame[1][3] == 0 for frame in measured)
        assert [frame[2][3] for frame in measured] == [255, 255, 255, 255, 0]
        assert np.all(np.diff([max(frame[2][:3]) for frame in measured[:4]]) > 15)
        np.testing.assert_allclose(pixels(1, 1, 4), measured[2], atol=1, rtol=0)
        page.screenshot(path=str(tmp_path / "single-channel-mixed-time.png"))
        assert not errors, errors
        (tmp_path / "single-channel-pixels.json").write_text(json.dumps(measured))
    finally:
        page.close()
        server.shutdown()
        server.server_close()
        thread.join(5)
