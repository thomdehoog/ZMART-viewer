"""Relative-Z aggregates share controls, never per-position engine sources."""

import json
import threading

import numpy as np
import pytest
import zarr
from test_published_depth import CANVAS, at_depth, composition

from zmart_viewer.published import STACK_STORE, STORE, PublishedAcquisition, PublishedTransfer


def focused(folder, name, x, height, *, reference=None, depth=3):
    path = at_depth(folder, name, x, height, depth=depth, plane_step=800)
    if reference is not None:
        group = zarr.open_group(str(path), mode="r+")
        group.attrs["zmart_microscopy"] = {
            "z_coordinate": {
                "frame": "specimen",
                "acquisition_provenance": {
                    "requested_stage_focus_z_um": reference,
                },
            }
        }
    return path


def test_interrupted_bake_can_reopen_and_recover(tmp_path, monkeypatch):
    name = "flat.ome.zarr"
    path = focused(tmp_path, name, 256.75, 70, depth=1)
    view = PublishedAcquisition(tmp_path, piece=64)
    view.publish(tmp_path, {name: 1}, CANVAS, composition=composition([name]), bake=True)
    group = zarr.open_group(str(path), mode="r+")
    for level in range(3):
        group[str(level)][:] = 2000
    replace_piece = PublishedTransfer._replace_one_piece

    def interrupted(output, *args, **kwargs):
        replace_piece(output, *args, **kwargs)
        raise OSError("interrupted baked write")

    with monkeypatch.context() as patch:
        patch.setattr(PublishedTransfer, "_replace_one_piece", interrupted)
        with pytest.raises(OSError, match="interrupted baked write"):
            view.publish(tmp_path, {name: 2}, CANVAS, composition=composition([name]), bake=True)
    view.close()
    view = PublishedAcquisition(tmp_path, piece=64)
    try:
        with pytest.raises(RuntimeError, match="needs recovery"):
            view.outputs[STORE].composer()
        view.publish(tmp_path, {name: 2}, CANVAS, composition=composition([name]), bake=True)
        made = view.outputs[STORE].composer()
        assert made.values_for(0, 0, 0, 4)[0, 2] == 2000
        assert made.values_for(2, 0, 0, 1)[0, 2] == 2000
        assert not (tmp_path / STORE / "pending.json").exists()
        assert view.outputs[STORE].revision == 2
    finally:
        view.close()


@pytest.mark.parametrize("depth", [1, 3])
@pytest.mark.parametrize("reopen", [False, True])
def test_interrupted_fractional_bake_can_retire(tmp_path, monkeypatch, depth, reopen):
    name = "position.ome.zarr"
    focused(tmp_path, name, 256.75, 60, reference=61.3, depth=depth)
    view = PublishedAcquisition(tmp_path, piece=64)
    view.publish(tmp_path, {name: 1}, CANVAS, composition=composition([name]), bake=True)
    output_name = STORE if depth == 1 else STACK_STORE
    corner = view.outputs[output_name].composer().mosaic.tiles[0].copies[0].corner_um
    replace_piece = PublishedTransfer._replace_one_piece

    def interrupted(output, *args, **kwargs):
        replace_piece(output, *args, **kwargs)
        raise OSError("interrupted baked write")

    with monkeypatch.context() as patch:
        patch.setattr(PublishedTransfer, "_replace_one_piece", interrupted)
        with pytest.raises(OSError, match="interrupted baked write"):
            view.publish(tmp_path, {name: 2}, CANVAS, composition=composition([name]), bake=True)
    if reopen:
        view.close()
        view = PublishedAcquisition(tmp_path, piece=64)
    try:
        view.publish(tmp_path, {}, CANVAS, composition=composition([]), bake=True)
        made = view.outputs[output_name].composer()
        assert made.mosaic.tiles[0].copies[0].corner_um == corner
        for level in range(made.mosaic.levels):
            col = (257 // 2**level) // 64
            for plane in range(depth):
                assert not made.coverage_for(level, plane, 0, col).any()
                assert made.values_for(level, plane, 0, col) is None
        assert not (tmp_path / output_name / "pending.json").exists()
    finally:
        view.close()


@pytest.mark.parametrize("bake", [False, True])
def test_relative_stack_offsets_survive_public_acquisition_composition(tmp_path, bake):
    names = ["a.ome.zarr", "b.ome.zarr"]
    focused(tmp_path, names[0], 0, 60, reference=61.3)
    focused(tmp_path, names[1], 256, 104.5, reference=104.5)
    view = PublishedAcquisition(tmp_path, piece=64)
    try:
        view.publish(
            tmp_path, dict.fromkeys(names, 1), CANVAS, composition=composition(names), bake=bake
        )
        made = view.outputs[STACK_STORE].composer()
        assert made.mosaic.corner_um[0] == pytest.approx(-1.3)
        assert made.mosaic.shape(0)[0] == 4
        for level in range(made.mosaic.levels):
            col, x = divmod(256 // 2**level, 64)
            # At shared relative Z=0, A is on its second plane and B on its first.
            assert made.values_for(level, 1, 0, 0)[0, 0] == 1800
            assert made.values_for(level, 1, 0, col)[0, x] == 1000
            assert not made.coverage_for(level, 0, 0, col)[0, x]
    finally:
        view.close()


@pytest.mark.parametrize("bake", [False, True])
def test_mixed_publication_reopens_and_validates_both_outputs_before_commit(tmp_path, bake):
    names = ["flat.ome.zarr", "stack.ome.zarr"]
    focused(tmp_path, names[0], 0, 70, depth=1)
    focused(tmp_path, names[1], 256, 60, reference=61.3)
    view = PublishedAcquisition(tmp_path, piece=64)
    view.publish(
        tmp_path, dict.fromkeys(names, 1), CANVAS, composition=composition(names), bake=bake
    )
    before = {name: (tmp_path / name / "publication.json").read_bytes() for name in view.sources}
    view.close()
    view = PublishedAcquisition(tmp_path, piece=64)
    try:
        view.publish(
            tmp_path, dict.fromkeys(names, 1), CANVAS, composition=composition(names), bake=bake
        )
        assert view.outputs[STACK_STORE].composer().mosaic.corner_um[0] == pytest.approx(-1.3)
        invalid = {**composition(names), "z_references": {names[1]: float("nan")}}
        with pytest.raises(ValueError, match="finite specimen heights"):
            view.publish(
                tmp_path, {names[0]: 2, names[1]: 1}, CANVAS, composition=invalid, bake=bake
            )
        assert all(
            (tmp_path / name / "publication.json").read_bytes() == data
            for name, data in before.items()
        )
    finally:
        view.close()


@pytest.mark.parametrize("bake", [False, True])
def test_single_level_fractional_positions_and_complete_retirement(tmp_path, bake):
    name = "small-target.ome.zarr"
    path = focused(tmp_path, name, 256.75, 50, depth=1)
    group = zarr.open_group(str(path), mode="r+")
    ome = group.attrs["ome"]
    ome["multiscales"][0]["datasets"] = ome["multiscales"][0]["datasets"][:1]
    group.attrs["ome"] = ome
    original = {p: p.read_bytes() for p in path.rglob("*") if p.is_file()}
    view = PublishedAcquisition(tmp_path, piece=64)
    try:
        view.publish(tmp_path, {name: 1}, CANVAS, composition=composition([name]), bake=bake)
        made = view.outputs[STORE].composer()
        assert made.mosaic.tiles[0].copies[0].corner_um[2] == 257
        fine = np.zeros((128, 2048), dtype="uint16")
        fine[:, 257:385] = 1000
        for level in range(made.mosaic.levels):
            col, pixel = divmod(257 // 2**level, 64)
            block = made.values_for(level, 0, 0, col)
            assert block[0, pixel] == fine[0, 257 // 2**level]
            assert made.coverage_for(level, 0, 0, col)[0, pixel] == 1
            pad = ((0, fine.shape[0] % 2), (0, fine.shape[1] % 2))
            fine = np.pad(fine, pad, mode="edge")
            fine = (
                fine.reshape(fine.shape[0] // 2, 2, fine.shape[1] // 2, 2)
                .mean(axis=(1, 3))
                .round()
                .astype("uint16")
            )
        assert not (tmp_path / STORE / "0/c").exists()
        if not bake:
            assert not list((tmp_path / STORE).glob("*/c"))
        view.publish(tmp_path, {}, CANVAS, composition=composition([]), bake=bake)
        assert not view.outputs[STORE].composer().coverage_for(0, 0, 0, 4).any()
        assert all(p.read_bytes() == value for p, value in original.items())
    finally:
        view.close()


@pytest.mark.parametrize("bake", [False, True])
@pytest.mark.parametrize("flat_first", [False, True])
def test_mixed_relative_publication(tmp_path, monkeypatch, bake, flat_first):
    focused(tmp_path, "flat.ome.zarr", 0, 76, depth=1)
    focused(tmp_path, "a.ome.zarr", 256, 60, reference=61.3)
    focused(tmp_path, "b.ome.zarr", 512, 104.5, reference=105.8)
    original = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    view = PublishedAcquisition(tmp_path, piece=64)
    first = ["flat.ome.zarr"] if flat_first else ["a.ome.zarr", "b.ome.zarr"]
    names = ["flat.ome.zarr", "a.ome.zarr", "b.ome.zarr"]
    try:
        view.publish(
            tmp_path, dict.fromkeys(first, 1), CANVAS, composition=composition(first), bake=bake
        )
        held = {n: o.revision for n, o in view.outputs.items()}
        view.publish(
            tmp_path, dict.fromkeys(names, 1), CANVAS, composition=composition(names), bake=bake
        )
        assert view.sources == [STORE, STACK_STORE]
        assert all(view.outputs[n].revision == rev for n, rev in held.items())
        made = view.outputs[STACK_STORE].composer()
        assert made.mosaic.corner_um[0] == pytest.approx(-1.3)
        assert made.mosaic.shape(0)[0] == 3
        for level in range(made.mosaic.levels):
            for plane in range(3):
                for x in (256, 512):
                    col, pixel = divmod(x // 2**level, 64)
                    assert (
                        made.values_for(level, plane, 0, col, moment=1, channel=1)[0, pixel]
                        == 1500 + 800 * plane
                    )
                    assert (
                        made.coverage_for(level, plane, 0, col, moment=1, channel=1)[0, pixel] == 1
                    )
        assert all(p.read_bytes() == value for p, value in original.items())
        monkeypatch.setattr(
            "zmart_viewer.published._read_one_tile", lambda *_: pytest.fail("idle metadata read")
        )
        before = view.revision
        view.publish(
            tmp_path, dict.fromkeys(names, 1), CANVAS, composition=composition(names), bake=bake
        )
        assert view.revision == before
        # Entire depth kinds can retire without losing their address or showing stale pixels.
        view.publish(
            tmp_path, {"flat.ome.zarr": 1}, CANVAS, composition=composition([names[0]]), bake=bake
        )
        assert not view.outputs[STACK_STORE].composer().coverage_for(0, 1, 0, 4).any()
        assert view.sources == [STORE, STACK_STORE]
    finally:
        view.close()


@pytest.mark.parametrize("bake", [False, True])
@pytest.mark.parametrize("flat_first", [False, True])
def test_browser_mixed_relative_depth(browser, built_dist, tmp_path, bake, flat_first):
    from test_manifest_refresh_browser import _wait_for_picture

    from zmart_viewer.server import make_server

    focused(tmp_path, "flat.ome.zarr", 0, 76, depth=1)
    focused(tmp_path, "focus.ome.zarr", 256, 60, reference=61.3)
    # A black stack overlapping the flat must cover it; an uncovered neighbour must not.
    black = focused(tmp_path, "black.ome.zarr", 0, 201, reference=202.3)
    group = zarr.open_group(str(black), mode="r+")
    for level in range(3):
        group[str(level)][:] = 0
    server = make_server(
        port=0,
        data_dir=tmp_path,
        site_dir=built_dist,
        live=True,
        allow_open=True,
        transparent_background=True,
        window=(0, 4095),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = f"http://127.0.0.1:{server.server_address[1]}"
    page = browser.new_page(viewport={"width": 1200, "height": 900})
    requests, errors = [], []
    page.on("request", lambda r: requests.append(r.url))
    page.on("pageerror", lambda e: errors.append(str(e)))
    names = ["flat.ome.zarr", "focus.ome.zarr"]

    def publish(selected, *, opening=False, acquired=None, versions=None):
        payload = {
            "path": str(tmp_path),
            "source_revisions": versions or dict.fromkeys(selected, 1),
            "composition": acquired or composition(selected),
        }
        if opening:
            payload.update(bake=bake, canvas=CANVAS)
        response = page.request.post(
            address + ("/api/stores/open" if opening else "/api/announce"),
            data=payload if opening else {"publications": [payload]},
        )
        assert response.ok, response.text()

    def pixels(z, zoom=1, moment=1):
        page.evaluate(
            """([z,zoom,moment]) => {
          const p=zmartViewer.navigationState.position, s=p.coordinateSpace.value;
          const at=Float32Array.from(p.value);
          for(const [axis,um] of Object.entries({x:192,y:64,z})) {
            const i=s.names.indexOf(axis); if(i>=0) at[i]=um/(s.scales[i]*1e6);
          }
          if(s.names.includes('t')) at[s.names.indexOf('t')]=moment;
          p.value=at; zmartViewer.navigationState.zoomFactor.value=zoom;
        }""",
            [z, zoom, moment],
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
        page.evaluate("window.firstViewer=zmartViewer; document.body.style.background='#ff00ff'")
        publish(names)
        page.wait_for_function("zmartConfig.layers.every(r=>r.sources.length===2)")
        _wait_for_picture(page)
        page.wait_for_function("""() => zmartViewer.navigationState.position.coordinateSpace.value.names.includes('z')
          && zmartViewer.layerManager.managedLayers.filter(m=>m.layer.type==='image').length === zmartConfig.layers.length * 4
          && zmartViewer.layerManager.managedLayers.every(m => m.layer.dataSources.every(s => s.loadState))""")
        _wait_for_picture(page)
        measured = [pixels(z) for z in (-1.3, 0, 1.3, 10)]
        print({"bake": bake, "flat_first": flat_first, "pixels": measured})
        for frame in measured:
            assert frame[0][3] == 255 and max(frame[0][:3]) > 50, (frame, errors)
            assert frame[1][3] == 0
        np.testing.assert_array_equal(np.array(measured)[:, 0], [measured[0][0]] * 4)
        assert [frame[2][3] for frame in measured] == [255, 255, 255, 0]
        assert np.all(np.diff([max(frame[2][:3]) for frame in measured[:3]]) > 25)
        np.testing.assert_allclose(pixels(0, 4), measured[1], atol=1, rtol=0)
        page.screenshot(path=str(tmp_path / "mixed-coarse.png"))
        # Black acquired coverage in part of the overlapping stack, gaps elsewhere.
        acquired = composition([*names, "black.ome.zarr"])
        acquired["regions"] = {
            name: [
                {
                    "frame": t,
                    "channel": c,
                    "origin": {"z": 0, "y": 0, "x": 0},
                    "shape": {
                        "z": 1 if name == names[0] else 3,
                        "y": 128,
                        "x": 32 if name == "black.ome.zarr" else 128,
                    },
                }
                for t in range(2)
                for c in range(2)
            ]
            for name in acquired["order"]
        }
        publish(acquired["order"], acquired=acquired)
        page.wait_for_timeout(1800)
        np.testing.assert_allclose(pixels(0)[0], measured[1][0], atol=1)  # x64 still uncovered.
        acquired["regions"]["black.ome.zarr"] = [
            {**region, "shape": {**region["shape"], "x": 128}}
            for region in acquired["regions"]["black.ome.zarr"]
        ]
        publish(acquired["order"], acquired=acquired)
        page.wait_for_timeout(1800)
        assert pixels(0)[0] == [0, 0, 0, 255]
        assert pixels(0, 4)[0] == [0, 0, 0, 255]
        assert pixels(0, 4)[1][3] == 0
        # Stack-over-flat is fixed; source order applies within each depth kind.
        acquired["order"] = [names[1], "black.ome.zarr", names[0]]
        publish(acquired["order"], acquired=acquired)
        assert pixels(0)[0] == [0, 0, 0, 255]
        page.screenshot(path=str(tmp_path / "mixed-black-and-gap.png"))
        assert pixels(10)[0] == measured[0][0]
        # Acquired coverage is specific to channel and time, not their union.
        acquired["regions"]["black.ome.zarr"] = [
            r
            for r in acquired["regions"]["black.ome.zarr"]
            if r["frame"] == 1 and r["channel"] == 0
        ]
        publish(acquired["order"], acquired=acquired)
        page.wait_for_timeout(1800)
        flat_t0 = pixels(10, moment=0)[0]
        assert pixels(0, moment=0)[0] == flat_t0
        assert pixels(0, moment=1)[0] == [0, 0, 0, 255]
        page.evaluate("""() => {
          const spec=zmartConfig.layers[0], prefix=`${spec.group} · ${spec.name}`;
          for(const m of zmartViewer.layerManager.managedLayers)
            if([`${prefix}__flat`,`${prefix}__stack`,
                `__coverage__${prefix}__flat`,`__coverage__${prefix}__stack`].includes(m.name))
              m.setVisible(false);
        }""")
        flat_c1 = pixels(10)[0]
        assert max(flat_c1[:3]) > 0
        assert pixels(0)[0] == flat_c1
        mark = len(requests)
        publish(acquired["order"], acquired=acquired)
        page.wait_for_timeout(2200)
        assert not [url for url in requests[mark:] if "/data/" in url]
        assert not any(f"/{name}/" in url for name in acquired["order"] for url in requests)
        assert page.evaluate("firstViewer===zmartViewer")
        assert not errors, errors
        (tmp_path / "evidence.json").write_text(
            json.dumps(
                {
                    "bake": bake,
                    "flat_first": flat_first,
                    "pixels": measured,
                    "data_requests": len([u for u in requests if "/data/" in u]),
                    "idle_refetches": 0,
                }
            )
        )
    finally:
        page.close()
        server.shutdown()
        server.server_close()
        thread.join(5)
