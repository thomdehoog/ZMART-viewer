"""The real viewer must preserve acquired black pixels over another surface."""

import io

import numpy as np
import pytest
import zarr
from PIL import Image
from record_fixtures import a_live_run, prepare_without_publishing, some_specimen
from test_manifest_refresh_browser import _open, _serving, _wait_for_picture, _wait_for_revision

READ_ALPHA = """() => {
  const display = window.zmartViewer.display;
  display.draw();
  const gl = display.gl, canvas = display.canvas;
  const pixels = new Uint8Array(canvas.width*canvas.height*4);
  gl.readPixels(0,0,canvas.width,canvas.height,gl.RGBA,gl.UNSIGNED_BYTE,pixels);
  const seen = {clear:0, opaque:0, partial:0, black:0};
  for(let i=0;i<pixels.length;i+=4) {
    const a = pixels[i+3];
    seen[a===0?'clear':a===255?'opaque':'partial']++;
    if(a===255 && pixels[i]===0 && pixels[i+1]===0 && pixels[i+2]===0) seen.black++;
  }
  return seen;
}"""


def test_live_mosaic_gains_opaque_black_footprints(browser, built_dist, tmp_path):
    run = a_live_run(tmp_path / "run")
    with zarr.config.set({"array.write_empty_chunks": True}):
        run.write_and_publish("posA", some_specimen(0))
    with _serving(built_dist, run, transparent_background=True) as address:
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("globalThis.zmartLiveCheckMs=300")
        try:
            _open(page, address, 1)
            _wait_for_picture(page)
            first = page.evaluate(READ_ALPHA)
            assert first["clear"] > 1000, first
            assert first["black"] > 1000, first
            assert first["partial"] == 0, first
            page.evaluate(
                """read => {
              const readAlpha = eval('(' + read + ')');
              window.alphaFrames = [];
              window.stopAlphaFrames = window.zmartViewer.display.updateFinished.add(
                () => window.alphaFrames.push(readAlpha()));
            }""",
                READ_ALPHA.replace("display.draw();", ""),
            )
            with zarr.config.set({"array.write_empty_chunks": True}):
                prepare_without_publishing(run, "posB", 0)
            page.wait_for_timeout(700)
            assert page.evaluate(READ_ALPHA) == first
            run.publish("posB")
            _wait_for_revision(page, 2)
            _wait_for_picture(page)
            second = page.evaluate(READ_ALPHA)
            frames = page.evaluate("""() => {
              window.stopAlphaFrames(); return window.alphaFrames;
            }""")
            assert frames, "no rendered update frames were inspected"
            assert min(frame["opaque"] for frame in frames) >= first["opaque"], frames
            assert all(frame["partial"] == 0 for frame in frames), frames
            assert second["black"] > first["black"] * 1.5, (first, second)
            assert second["partial"] == 0
            assert second["clear"] > 1000
            # The DOM beneath must actually show, not merely a nonopaque FBO.
            page.evaluate("""() => {
              const below=document.createElement('div');
              below.style.cssText='position:absolute;inset:0;background:#ff00ff';
              document.body.prepend(below);
              document.getElementById('root').style.position='relative';
              const above=document.createElement('div');
              above.textContent='ABOVE THE VIEWER';
              above.style.cssText='position:absolute;top:45%;left:30%;z-index:100;background:orange;color:black;padding:15px';
              document.body.append(above);
            }""")
            shot = page.screenshot(path=str(tmp_path / "live-mosaic-transparency.png"))
            rgb = np.asarray(Image.open(io.BytesIO(shot)).convert("RGB"))
            for colour in ([255, 0, 255], [255, 165, 0], [0, 0, 0]):
                assert np.all(rgb == colour, axis=-1).sum() > 1000, colour
            assert not errors, errors
        finally:
            page.close()


@pytest.mark.parametrize("transparent", [False, True])
def test_dense_czt_and_volume_keep_their_coverage(browser, built_dist, tmp_path, transparent):
    store = tmp_path / "position.ome.zarr"
    group = zarr.open_group(store, mode="w", zarr_format=2)
    group.attrs["multiscales"] = [
        {
            "version": "0.4",
            "axes": [
                {"name": "t", "type": "time", "unit": "second"},
                {"name": "c", "type": "channel"},
                *[{"name": name, "type": "space", "unit": "micrometer"} for name in "zyx"],
            ],
            "datasets": [
                {
                    "path": "0",
                    "coordinateTransformations": [{"type": "scale", "scale": [1, 1, 1, 1, 1]}],
                }
            ],
        }
    ]
    data = np.zeros((2, 2, 2, 32, 64), dtype=np.uint16)
    data[1, :, 1, :, 32:] = 4095
    group.create_array("0", data=data, chunks=(1, 1, 1, 32, 32), compressor=None)
    loads = [{"path": tmp_path, "stores": [store.name], "name": "position"}]
    with _serving(
        built_dist, loads=loads, transparent_background=transparent, live=False
    ) as address:
        page = browser.new_page(viewport={"width": 1000, "height": 800})
        try:
            page.goto(address)
            _wait_for_picture(page)
            config = page.request.get(address.rstrip("/") + "/api/config").json()
            assert not any(row.get("coverageSources") for row in config["layers"])
            if transparent:
                assert all(row["opaque"] for row in config["layers"])
            first = page.evaluate(READ_ALPHA)
            assert first["partial"] == 0
            assert (first["clear"] > 1000) == transparent
            assert first["black"] > 1000
            # Move through actual NG navigation dimensions, leaving the channel
            # pin on each of the two rows untouched.
            page.evaluate("""() => {
              const p=window.zmartViewer.navigationState.position;
              const names=p.coordinateSpace.value.names;
              const at=Float32Array.from(p.value);
              at[names.indexOf('t')]=1; at[names.indexOf('z')]=1;
              p.value=at;
            }""")
            _wait_for_picture(page)
            second = page.evaluate(READ_ALPHA)
            assert second["opaque"] == first["opaque"], (first, second)
            assert second["partial"] == 0
            assert second["black"] < first["black"]
            # Transparent wrappers must not replace the engine's theme colour.
            page.evaluate("document.documentElement.dataset.theme = 'light'")
            page.wait_for_function("""() => Array.from(
              window.zmartViewer.perspectiveViewBackgroundColor.value
            ).every(value => value > 0.5)""")
            page.get_by_title("Ray-cast volume; drag to rotate", exact=True).click()
            page.wait_for_function("() => window.zmartMode === 'volume'")
            _wait_for_picture(page)
            volume = page.evaluate(READ_ALPHA)
            assert volume["clear"] == volume["partial"] == 0, volume
        finally:
            page.close()
