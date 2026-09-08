"""Corner-coordinate pyramids, including qualification with the real producer."""

import json
import os
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import zarr
from test_acquired_composition import region, source

from zmart_viewer.compose import (
    MEAN_CROP_REDUCTION,
    Composer,
    Mosaic,
    _read_one_tile,
    halve_xy,
    read_the_mosaic_as_written,
    the_mosaic_written_down,
)
from zmart_viewer.published import STORE, PublishedTransfer


@pytest.mark.parametrize(
    "dtype,side,eligible",
    [
        ("uint8", 8, True),
        ("uint16", 8, True),
        ("int16", 8, True),
        ("uint32", 8, False),
        ("float32", 8, False),
        ("uint16", 7, False),
    ],
)
def test_crop_reducer_qualification_and_corner_metadata(tmp_path, dtype, side, eligible):
    tile = source(tmp_path, "a.ome.zarr", 120)
    tile.copies = [
        replace(
            copy, dtype=dtype, corner_um=(0, 0, 0), shape=(2, side // 2**level, side // 2**level)
        )
        for level, copy in enumerate(tile.copies)
    ]
    base = Mosaic([tile], 3, ("z", "y", "x"), dtype, averaged=True)
    mosaic = base.with_acquired_regions(
        {tile.name: [region(0, side, height=side)]},
        order=[tile.name],
        pyramid_reduction=MEAN_CROP_REDUCTION,
        xy_origin="corner",
    )
    restored = read_the_mosaic_as_written(the_mosaic_written_down(mosaic))
    composer = Composer(restored, piece=4)
    try:
        assert composer._can_read_native(2, 0, 0) is eligible
        assert restored.xy_origin == "corner"
        levels = json.loads(composer.group_json())["attributes"]["ome"]["multiscales"][0][
            "datasets"
        ]
        for level, description in enumerate(levels):
            scale, translation = description["coordinateTransformations"]
            assert translation["translation"][-3] == 0  # Z remains a plane coordinate.
            for axis in (-2, -1):
                assert translation["translation"][axis] - scale["scale"][axis] / 2 == 0
    finally:
        composer.close()


@pytest.fixture
def operator_writer(monkeypatch):
    root = os.environ.get("ZMART_OPERATOR_SOURCE")
    if not root:
        pytest.skip("Set ZMART_OPERATOR_SOURCE to qualify the actual acquisition writer")
    monkeypatch.syspath_prepend(str(Path(root).resolve()))
    from application.parts.storage.zarr_positions import position_store_from_record

    return position_store_from_record


def write_capture(writer, folder, name, x, value=None, dtype="uint16"):
    import tifffile

    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(18)
    values = (
        rng.integers(0, 4096, (256, 256), dtype=np.uint16)
        if value is None
        else np.full((256, 256), value, dtype=np.uint16)
    ).astype(dtype)
    path = folder.parent / f"{name}.ome.tif"
    tifffile.imwrite(
        path,
        values,
        metadata={
            "axes": "YX",
            "PhysicalSizeX": 1.0,
            "PhysicalSizeY": 1.0,
            "PhysicalSizeXUnit": "um",
            "PhysicalSizeYUnit": "um",
        },
    )
    record = {
        "acquisition_type": "overview",
        "position_label": name,
        "planes": [
            {"t": 0, "c": 0, "z": 0, "path": str(path), "x_um": x + 128, "y_um": 128, "z_um": 0},
        ],
    }
    return writer(record, folder)


@pytest.mark.parametrize("bake", [False, True])
def test_actual_writer_native_pixels_sparse_coverage_and_unchanged_originals(
    operator_writer, tmp_path, monkeypatch, bake
):
    folder = tmp_path / "positions"
    a = write_capture(operator_writer, folder, "signal", 0)
    b = write_capture(operator_writer, folder, "black", 512, value=0)
    originals = {p: p.read_bytes() for store in (a, b) for p in store.rglob("*") if p.is_file()}
    composition = {
        "regions": "complete",
        "order": [a.name, b.name],
        "pyramid_reduction": MEAN_CROP_REDUCTION,
        "xy_origin": "corner",
    }
    view = PublishedTransfer(folder / STORE)
    try:
        view.publish(
            folder,
            {a.name: 1, b.name: 1},
            {"x_um": [0, 1024], "y_um": [0, 512]},
            composition=composition,
            bake=bake,
        )
        made = view.composer()
        levels = []
        read = made._read_from
        monkeypatch.setattr(
            made,
            "_read_from",
            lambda copy, *args: (levels.append(copy.held_in.name), read(copy, *args))[1],
        )
        expected = np.zeros((512, 1024), dtype=np.uint16)
        expected[:256, :256] = zarr.open_array(str(a / "0"), mode="r")[0, 0, 0]
        # Virtual mode must read the producer's L1, not reconstruct it from L0.
        actual = made.values_for(1, 0, 0, 0)
        np.testing.assert_array_equal(actual[:256], halve_xy(expected))
        if not bake:
            assert levels and set(levels) == {"1"}
        mask = made.coverage_for(1, 0, 0, 0)
        assert mask[0, 0] == mask[0, 256] == 1 and mask[0, 128] == 0
        assert actual[0, 256] == 0
        assert all(p.read_bytes() == data for p, data in originals.items())
        with pytest.raises(ValueError, match="coordinate convention"):
            view.publish(
                folder,
                {a.name: 1, b.name: 1},
                {"x_um": [0, 1024], "y_um": [0, 512]},
                composition={**composition, "xy_origin": "center"},
                bake=bake,
            )
    finally:
        view.close()


@pytest.mark.parametrize("bake", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_actual_writer_stack_grid_survives_retirement_reopen_and_append(
    operator_writer, tmp_path, bake, reverse
):
    import tifffile

    folder = tmp_path / "positions"

    def write_stack(name, x, heights, values):
        planes = []
        for z, (height, value) in enumerate(zip(heights, values, strict=True)):
            path = tmp_path / f"{name}-{z}.ome.tif"
            tifffile.imwrite(
                path,
                np.full((256, 256), value, dtype="uint16"),
                metadata={
                    "axes": "YX",
                    "PhysicalSizeX": 1.0,
                    "PhysicalSizeY": 1.0,
                    "PhysicalSizeXUnit": "um",
                    "PhysicalSizeYUnit": "um",
                },
            )
            planes.append(
                {
                    "t": 0,
                    "c": 0,
                    "z": z,
                    "path": str(path),
                    "x_um": x + 128,
                    "y_um": 128,
                    "z_um": height,
                }
            )
        return operator_writer(
            {"acquisition_type": "focus", "position_label": name, "planes": planes}, folder
        )

    a = write_stack("a", 0, [60.0, 61.3], [500, 1500])
    b = write_stack("b", 512, [61.3, 62.6], [2500, 3500])
    assert _read_one_tile(a).copies[0].voxel_um[0] != _read_one_tile(b).copies[0].voxel_um[0]
    originals = {p: p.read_bytes() for store in (a, b) for p in store.rglob("*") if p.is_file()}
    names = [a.name, b.name]
    if reverse:
        names.reverse()
    canvas = {"x_um": [0, 1024], "y_um": [0, 512]}

    def snapshot(order):
        return {
            "regions": "complete",
            "order": order,
            "pyramid_reduction": MEAN_CROP_REDUCTION,
            "xy_origin": "corner",
        }

    view = PublishedTransfer(folder / STORE, piece=64)
    try:
        view.publish(
            folder, dict.fromkeys(names, 1), canvas, composition=snapshot(names), bake=bake
        )
        made = view.composer()
        spacing = made.mosaic.voxel_um(0)[0]
        assert made.mosaic.corner_um[0] == 60 and made.mosaic.shape(0)[0] == 3
        for level in range(made.mosaic.levels):
            for plane in range(3):
                for x, offset, values in ((0, 0, [500, 1500, 0]), (512, 1, [0, 2500, 3500])):
                    col, pixel = divmod(x // 2**level, 64)
                    data = made.values_for(level, plane, 0, col)
                    assert (data[0, pixel] if data is not None else 0) == values[plane]
                    assert made.coverage_for(level, plane, 0, col)[0, pixel] == (
                        offset <= plane < offset + 2
                    )
        # Remove whichever original selected the grid, then reload its persisted replacement.
        survivor = names[1]
        view.publish(folder, {survivor: 1}, canvas, composition=snapshot([survivor]), bake=bake)
        assert view.composer().mosaic.voxel_um(0)[0] == spacing
        view.close()
        view = PublishedTransfer(folder / STORE, piece=64)
        assert view.composer().mosaic.voxel_um(0)[0] == spacing
        c = write_stack("c", 256, [61.3, 62.6], [1200, 2200])
        originals.update({p: p.read_bytes() for p in c.rglob("*") if p.is_file()})
        order = [c.name, survivor]
        view.publish(
            folder, dict.fromkeys(order, 1), canvas, composition=snapshot(order), bake=bake
        )
        made = view.composer()
        assert made.mosaic.voxel_um(0)[0] == spacing
        assert made.mosaic.corner_um[0] == 60 and made.mosaic.shape(0)[0] == 3
        assert made.values_for(0, 1, 0, 4)[0, 0] == 1200
        assert all(p.read_bytes() == data for p, data in originals.items())
    finally:
        view.close()


@pytest.mark.parametrize("bake", [False, True])
def test_actual_writer_browser_placement_and_idle(
    operator_writer, browser, built_dist, tmp_path, bake
):
    import threading

    from test_manifest_refresh_browser import _wait_for_picture
    from test_transparent_2d_browser import READ_ALPHA

    from zmart_viewer.server import make_server

    folder = tmp_path / "positions"
    a = write_capture(operator_writer, folder, "signal", 0, value=2400)
    b = write_capture(operator_writer, folder, "black", 512, value=0)
    server = make_server(
        port=0,
        data_dir=folder,
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
    composition = {
        "regions": "complete",
        "order": [a.name, b.name],
        "pyramid_reduction": MEAN_CROP_REDUCTION,
        "xy_origin": "corner",
    }
    payload = {
        "path": str(folder),
        "canvas": {"x_um": [0, 1024], "y_um": [0, 512]},
        "bake": bake,
        "source_revisions": {a.name: 1, b.name: 1},
        "composition": composition,
    }
    bounds_js = """() => {
        const dc=zmartViewer.display, gl=dc.gl; dc.draw();
        const w=gl.drawingBufferWidth,h=gl.drawingBufferHeight, data=new Uint8Array(w*h*4);
        gl.readPixels(0,0,w,h,gl.RGBA,gl.UNSIGNED_BYTE,data);
        let x0=w,x1=-1,y0=h,y1=-1;
        for(let y=0;y<h;y++) for(let x=0;x<w;x++) if(data[4*(y*w+x)+3]===255) {
            x0=Math.min(x0,x);x1=Math.max(x1,x);y0=Math.min(y0,y);y1=Math.max(y1,y);
        }
        return [x0,x1,y0,y1];
    }"""
    try:
        assert page.request.post(f"{address}/api/stores/open", data=payload).ok
        page.goto(address)
        _wait_for_picture(page)
        page.evaluate("""() => {
            const p=zmartViewer.navigationState.position, at=Float32Array.from(p.value), names=p.coordinateSpace.value.names;
            for(const [axis,v] of Object.entries({x:384,y:128,z:0})) at[names.indexOf(axis)]=v;
            p.value=at;zmartViewer.navigationState.zoomFactor.value=1;
            document.body.style.background='#ff00ff';
        }""")
        _wait_for_picture(page)
        fine = page.evaluate(bounds_js)
        assert page.evaluate(READ_ALPHA)["black"] > 1000
        page.screenshot(path=str(tmp_path / f"writer-fine-{bake}.png"))
        page.evaluate("zmartViewer.navigationState.zoomFactor.value=2")
        _wait_for_picture(page)
        coarse = page.evaluate(bounds_js)
        # Both levels cover the same specimen rectangle, at exactly half the size.
        assert fine[1] - fine[0] + 1 == 2 * (coarse[1] - coarse[0] + 1)
        assert fine[3] - fine[2] + 1 == 2 * (coarse[3] - coarse[2] + 1)
        assert abs(fine[0] + fine[1] - coarse[0] - coarse[1]) <= 2
        assert abs(fine[2] + fine[3] - coarse[2] - coarse[3]) <= 2
        alpha = page.evaluate(READ_ALPHA)
        assert alpha["clear"] > 1000 and alpha["black"] > 1000 and alpha["partial"] == 0
        assert any(f"{STORE}/1/c/" in url for url in requests)
        assert not any(f"/{name}/" in url for name in (a.name, b.name) for url in requests)
        mark = len(requests)
        publication = {k: payload[k] for k in ("path", "source_revisions", "composition")}
        assert page.request.post(f"{address}/api/announce", data={"publications": [publication]}).ok
        page.wait_for_timeout(2200)
        assert not [r for r in requests[mark:] if "/data/" in r]
        page.screenshot(path=str(tmp_path / f"writer-coarse-{bake}.png"))
        assert not errors, errors
        print(
            {
                "bake": bake,
                "fine_bounds": fine,
                "coarse_bounds": coarse,
                "alpha": alpha,
                "idle_refetches": 0,
            }
        )
    finally:
        page.close()
        server.shutdown()
        server.server_close()
        thread.join(5)
