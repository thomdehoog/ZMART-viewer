import json
import re
import threading

import numpy as np
import pytest
import zarr

from zmart_viewer import coverage, pieces
from zmart_viewer.library import Library
from zmart_viewer.published import STORE, PublishedFolders, PublishedTransfer


def write_position(folder, name, x, value, *, frames=1, channels=1, depth=1):
    store = folder / name
    group = zarr.open_group(str(store), mode="w", zarr_format=3)
    datasets = []
    for level in range(3):
        side = 128 // 2**level
        group.create_array(
            str(level),
            data=np.full((frames, channels, depth, side, side), value, dtype="uint16"),
            chunks=(1, 1, 1, 32, 32),
        )
        datasets.append(
            {
                "path": str(level),
                "coordinateTransformations": [
                    {"type": "scale", "scale": [1, 1, 1, 2**level, 2**level]},
                    {
                        "type": "translation",
                        "translation": [0, 0, 0, (2**level - 1) / 2, x + (2**level - 1) / 2],
                    },
                ],
            }
        )
    group.attrs["ome"] = {
        "version": "0.5",
        "multiscales": [
            {
                "type": "mean",
                "axes": [
                    {
                        "name": axis,
                        "type": "time" if axis == "t" else "channel" if axis == "c" else "space",
                        **(
                            {"unit": "second"}
                            if axis == "t"
                            else {}
                            if axis == "c"
                            else {"unit": "micrometer"}
                        ),
                    }
                    for axis in "tczyx"
                ],
                "datasets": datasets,
            }
        ],
    }
    return store


def test_published_append_rewrite_idle_and_extended_bake(tmp_path):
    folder = tmp_path / "positions"
    write_position(folder, "a.ome.zarr", 0, 1200)
    view = PublishedTransfer(folder / STORE, piece=64)
    canvas = {"x_um": [0, 1024], "y_um": [0, 512]}
    try:
        assert view.publish(folder, {"a.ome.zarr": 1}, canvas) == 1
        assert not (folder / STORE / "0/c").exists()
        before = {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in (folder / STORE / "2/c").rglob("*")
            if path.is_file()
        }
        write_position(folder, "b.ome.zarr", 512, 2800)
        versions = {"a.ome.zarr": 1, "b.ome.zarr": 1}
        assert view.publish(folder, versions, canvas) == 2
        assert all(
            (path.read_bytes(), path.stat().st_mtime_ns) == held for path, held in before.items()
        )
        described = json.loads((folder / STORE / "zarr.json").read_text())
        assert described["attributes"]["zmart"]["baked"] == [2, 3, 4]
        for level in (2, 3, 4):
            actual = zarr.open_array(str(folder / STORE / str(level)), mode="r")[:]
            assert actual.max() == 2800
            assert actual[0, 0, 0] == 1200
            assert actual[0, 0, 512 // 2**level] == 2800
        stamp = (folder / STORE / "publication.json").stat().st_mtime_ns
        assert view.publish(folder, versions, canvas) == 2
        assert (folder / STORE / "publication.json").stat().st_mtime_ns == stamp
        write_position(folder, "b.ome.zarr", 512, 0)
        assert view.publish(folder, {**versions, "b.ome.zarr": 2}, canvas) == 3
        assert zarr.open_array(str(folder / STORE / "4"), mode="r")[0, 0, 32] == 0
        mask = view.composer().coverage_for(4, 0, 0, 0)
        assert mask[0, 32] == 1 and mask[0, 16] == 0
        assert pieces.built_bytes_behind(folder / STORE, "0/c/0/0/0") is not None
        assert coverage.answer(folder / STORE, "4/zarr.json") is not None
    finally:
        pieces.forget(folder / STORE)
        view.close()


def test_published_bake_preserves_czt(tmp_path):
    store = write_position(tmp_path, "a.ome.zarr", 0, 0, frames=2, channels=2, depth=2)
    expected = np.fromfunction(lambda t, c, z: 1000 + 500 * t + 100 * c + 10 * z, (2, 2, 2)).astype(
        "uint16"
    )
    for level in range(3):
        zarr.open_array(str(store / str(level)), mode="r+")[:] = np.broadcast_to(
            expected[..., None, None], (2, 2, 2, 128 // 2**level, 128 // 2**level)
        )
    view = PublishedTransfer(tmp_path / STORE, piece=64)
    try:
        view.publish(tmp_path, {"a.ome.zarr": 1}, {"x_um": [0, 1024], "y_um": [0, 512]})
        array = zarr.open_array(str(tmp_path / STORE / "4"), mode="r")
        assert array.shape == (2, 2, 2, 32, 64)
        np.testing.assert_array_equal(array[:, :, :, 0, 0], expected)
        assert view.composer().values_for(0, 1, 0, 0, moment=1, channel=1).max() == 1610
    finally:
        view.close()


@pytest.mark.parametrize("bake", [False, True])
def test_optional_bake_is_one_source_and_updates_in_the_browser(
    browser, built_dist, tmp_path, bake
):
    from pixels import image_middle
    from test_manifest_refresh_browser import _serving, _wait_for_picture
    from test_transparent_2d_browser import READ_ALPHA

    write_position(tmp_path, "a.ome.zarr", 0, 1200)
    write_position(tmp_path, "b.ome.zarr", 512, 2200)
    loads = [{"path": tmp_path, "name": "positions"}]
    canvas = {"x_um": [0, 1024], "y_um": [0, 512]}
    with _serving(
        built_dist, loads=loads, bake=bake, canvas=canvas, transparent_background=True
    ) as address:
        page = browser.new_page(viewport={"width": 1200, "height": 900})
        requested = []
        page.on("request", lambda request: requested.append(request.url))
        try:
            page.goto(address)
            _wait_for_picture(page)
            page.evaluate("""() => {
                const position = zmartViewer.navigationState.position;
                const at = Float32Array.from(position.value);
                const names = position.coordinateSpace.value.names;
                for (const [axis, value] of Object.entries({x: 320, y: 64, z: 0})) {
                    at[names.indexOf(axis)] = value;
                }
                position.value = at;
                zmartViewer.navigationState.zoomFactor.value = 1;
            }""")
            _wait_for_picture(page)
            rows = page.evaluate("zmartConfig.layers")
            sources = {url for row in rows for url in row["sources"]}
            assert len(sources) == (1 if bake else 2)
            if bake:
                assert all(STORE in url for url in sources)
                assert any(f"{STORE}/" in url and "/c/" in url for url in requested)
                assert not any("a.ome.zarr/" in url or "b.ome.zarr/" in url for url in requested)
                initial_zoom = page.evaluate("zmartViewer.navigationState.zoomFactor.value")
                mark = len(requested)
                page.evaluate("zmartViewer.navigationState.zoomFactor.value *= 16")
                _wait_for_picture(page)
                coarse = [
                    url for url in requested if re.search(r"overview\.ome\.zarr/[2-9]/c/", url)
                ]
                assert coarse, requested
                assert page.evaluate(READ_ALPHA)["opaque"] > 0
                page.screenshot(path=str(tmp_path / "bake-coarse.png"))
                page.evaluate(
                    "zoom => zmartViewer.navigationState.zoomFactor.value = zoom", initial_zoom
                )
                _wait_for_picture(page)
                assert any(f"{STORE}/0/c/" in url for url in requested)
                print(
                    {
                        "combined_coarse_chunk_requests": len(coarse),
                        "zoom_out_additional_requests": len(requested) - mark,
                        "original_position_requests": 0,
                    }
                )
            before = image_middle(page)
            assert np.count_nonzero(before.max(axis=2) > 40) > 100
            write_position(tmp_path, "b.ome.zarr", 512, 0)
            response = page.request.post(
                f"{address}/api/announce", data={"wrote_image_in_place": True}
            )
            assert response.ok
            if bake:
                page.wait_for_function(
                    "zmartConfig.layers.some(row => row.sourceRevisions?.[0] === 2)"
                )
            _wait_for_picture(page)
            after = image_middle(page)
            assert np.count_nonzero(np.any(before != after, axis=2)) > 100
            alpha = page.evaluate(READ_ALPHA)
            assert alpha["clear"] > 1000 and alpha["black"] > 1000
            assert alpha["partial"] == 0
            print({"bake": bake, "alpha_after_black_rewrite": alpha})
            if bake:
                mark = len(requested)
                page.request.post(f"{address}/api/announce", data={"wrote_image_in_place": True})
                page.wait_for_timeout(1800)
                assert not any("/data/" in url for url in requested[mark:])
            page.screenshot(path=str(tmp_path / f"bake-{bake}.png"))
        finally:
            page.close()


def test_interrupted_publication_is_refused_until_retry_recovers(tmp_path, monkeypatch):
    write_position(tmp_path, "a.ome.zarr", 0, 1200)
    view = PublishedTransfer(tmp_path / STORE, piece=64)
    reader = PublishedTransfer(tmp_path / STORE, piece=64)
    canvas = {"x_um": [0, 1024], "y_um": [0, 512]}
    try:
        view.publish(tmp_path, {"a.ome.zarr": 1}, canvas)
        assert reader.composer().values_for(0, 0, 0, 0).max() == 1200
        write_position(tmp_path, "a.ome.zarr", 0, 2700)
        patch_piece = view._replace_one_piece

        def interrupted(*args, **kwargs):
            patch_piece(*args, **kwargs)
            raise OSError("simulated interrupted bake")

        monkeypatch.setattr(view, "_replace_one_piece", interrupted)
        with pytest.raises(OSError, match="interrupted bake"):
            view.publish(tmp_path, {"a.ome.zarr": 2}, canvas)
        assert view.revision == 1
        with pytest.raises(RuntimeError, match="needs recovery"):
            reader.composer()
        monkeypatch.setattr(view, "_replace_one_piece", patch_piece)
        assert view.publish(tmp_path, {"a.ome.zarr": 2}, canvas) == 2
        assert reader.composer().values_for(0, 0, 0, 0).max() == 2700
        assert zarr.open_array(str(tmp_path / STORE / "4"), mode="r")[0, 0, 0] == 2700
    finally:
        reader.close()
        view.close()


def test_explicit_completed_revisions_are_the_only_publication_trigger(tmp_path):
    write_position(tmp_path, "a.ome.zarr", 0, 1200)
    library = Library()
    number = library.open(tmp_path)
    published = PublishedFolders(library)
    try:
        published.open(
            tmp_path, canvas={"x_um": [0, 1024], "y_um": [0, 512]}, versions={"a.ome.zarr": 1}
        )
        write_position(tmp_path, "a.ome.zarr", 0, 2400)
        assert published.refresh() == ((number, 1),)
        published.announce([{"path": str(tmp_path), "source_revisions": {"a.ome.zarr": 2}}])
        assert published.refresh() == ((number, 2),)
        manifest = tmp_path / STORE / "publication.json"
        stamp = manifest.stat().st_mtime_ns
        published.announce([{"path": str(tmp_path), "source_revisions": {"a.ome.zarr": 2}}])
        assert published.refresh() == ((number, 2),)
        assert manifest.stat().st_mtime_ns == stamp
        assert published.entries(library.entries()) == [(number, tmp_path, STORE)]
    finally:
        published.close()


def test_recovery_clears_a_partly_baked_position_withdrawn_before_retry(tmp_path, monkeypatch):
    write_position(tmp_path, "a.ome.zarr", 0, 1200)
    write_position(tmp_path, "b.ome.zarr", 512, 2700)
    view = PublishedTransfer(tmp_path / STORE, piece=64)
    canvas = {"x_um": [0, 1024], "y_um": [0, 512]}
    try:
        view.publish(tmp_path, {"a.ome.zarr": 1}, canvas)
        real_patch = view._replace_one_piece

        def interrupted(*args, **kwargs):
            real_patch(*args, **kwargs)
            raise OSError("interrupted append")

        monkeypatch.setattr(view, "_replace_one_piece", interrupted)
        with pytest.raises(OSError, match="interrupted append"):
            view.publish(tmp_path, {"a.ome.zarr": 1, "b.ome.zarr": 1}, canvas)
        assert zarr.open_array(str(tmp_path / STORE / "2"), mode="r")[0, 0, 128] == 2700
        monkeypatch.setattr(view, "_replace_one_piece", real_patch)
        view.publish(tmp_path, {"a.ome.zarr": 1}, canvas)
        for level in (2, 3, 4):
            assert (
                zarr.open_array(str(tmp_path / STORE / str(level)), mode="r")[0, 0, 512 // 2**level]
                == 0
            )
            assert (
                view.composer().coverage_for(level, 0, 0, 512 // (2**level * 64))[
                    0, (512 // 2**level) % 64
                ]
                == 0
            )
    finally:
        view.close()


def test_coarse_pixels_and_coverage_match_detail_on_an_offset_canvas(tmp_path):
    write_position(tmp_path, "black.ome.zarr", 0, 0)
    write_position(tmp_path, "signal.ome.zarr", 512, 2400)
    view = PublishedTransfer(tmp_path / STORE, piece=64)
    try:
        view.publish(
            tmp_path,
            {"black.ome.zarr": 1, "signal.ome.zarr": 1},
            {"x_um": [-128, 896], "y_um": [-128, 384]},
        )
        detail = np.zeros((1, 512, 1024), dtype="uint16")
        detail[:, 128:256, 640:768] = 2400
        footprint = np.zeros((512, 1024), dtype="uint8")
        footprint[128:256, 128:256] = 1
        footprint[128:256, 640:768] = 1
        metadata = json.loads((tmp_path / STORE / "zarr.json").read_text())
        for level, dataset in enumerate(
            metadata["attributes"]["ome"]["multiscales"][0]["datasets"]
        ):
            factor = 2**level
            transform = dataset["coordinateTransformations"]
            translation = next(
                one["translation"] for one in transform if one["type"] == "translation"
            )
            assert translation == [0, -128 + (factor - 1) / 2, -128 + (factor - 1) / 2]
            mask = footprint.reshape(512 // factor, factor, 1024 // factor, factor).max(axis=(1, 3))
            expected = detail.reshape(1, 512 // factor, factor, 1024 // factor, factor).mean(
                axis=(2, 4)
            )
            actual = np.zeros_like(expected, dtype="uint16")
            actual_mask = np.zeros_like(mask)
            for row in range((mask.shape[0] + 63) // 64):
                for col in range((mask.shape[1] + 63) // 64):
                    height, width = (
                        min(64, mask.shape[0] - row * 64),
                        min(64, mask.shape[1] - col * 64),
                    )
                    at = (slice(row * 64, row * 64 + height), slice(col * 64, col * 64 + width))
                    actual_mask[at] = view.composer().coverage_for(level, 0, row, col)[
                        :height, :width
                    ]
                    if level < 2:
                        block = view.composer().values_for(level, 0, row, col)
                        if block is not None:
                            actual[(0, *at)] = block[:height, :width]
            if level >= 2:
                actual = zarr.open_array(str(tmp_path / STORE / str(level)), mode="r")[:]
            np.testing.assert_array_equal(actual, expected)
            np.testing.assert_array_equal(actual_mask, mask)
    finally:
        view.close()


def test_non_mean_pyramids_are_refused_before_baking(tmp_path):
    store = write_position(tmp_path, "a.ome.zarr", 0, 1200)
    group = zarr.open_group(str(store), mode="r+")
    ome = group.attrs["ome"]
    ome["multiscales"][0]["type"] = "nearest"
    group.attrs["ome"] = ome
    view = PublishedTransfer(tmp_path / STORE)
    try:
        with pytest.raises(ValueError, match="mean-reduced"):
            view.publish(tmp_path, {store.name: 1}, {"x_um": [0, 1024], "y_um": [0, 512]})
        assert not (tmp_path / STORE / "pending.json").exists()
    finally:
        view.close()


def test_http_publication_reports_refusals_and_advances_only_changed_revisions(
    tmp_path, monkeypatch
):
    from test_server import request

    from zmart_viewer.server import make_server

    write_position(tmp_path, "a.ome.zarr", 0, 1200)
    server = make_server(port=0, data_dir=tmp_path, live=True, allow_open=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def post(route, payload):
        status, _, body = request(port, route, "POST", json.dumps(payload).encode())
        return status, json.loads(body)

    try:
        opening = {"path": str(tmp_path), "bake": True, "source_revisions": {"a.ome.zarr": 1}}
        status, answer = post("/api/stores/open", opening)
        assert status == 400 and "canvas" in answer["error"]
        opening["canvas"] = {"x_um": [0, 1024], "y_um": [0, 512]}
        status, answer = post("/api/stores/open", opening)
        assert status == 200, answer
        changed = {"publications": [{"path": str(tmp_path), "source_revisions": {"a.ome.zarr": 1}}]}
        manifest = tmp_path / STORE / "publication.json"
        mark = manifest.stat().st_mtime_ns
        assert post("/api/announce", changed)[0] == 200
        assert manifest.stat().st_mtime_ns == mark
        for invalid in (None, {}, [{"path": str(tmp_path / "unopened"), "source_revisions": {}}]):
            status, answer = post("/api/announce", {"publications": invalid})
            assert status == 400 and answer["error"]
        write_position(tmp_path, "a.ome.zarr", 0, 2400)
        changed["publications"][0]["source_revisions"]["a.ome.zarr"] = 2
        assert post("/api/announce", changed)[0] == 200
        assert json.loads(manifest.read_text())["revision"] == 2
        with monkeypatch.context() as patch:

            def disk_failure(*args, **kwargs):
                raise OSError("simulated disk failure")

            patch.setattr(PublishedTransfer, "publish", disk_failure)
            status, answer = post("/api/announce", changed)
            assert status == 503 and "disk failure" in answer["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_publication_keeps_its_own_revision_snapshot(tmp_path):
    write_position(tmp_path, "a.ome.zarr", 0, 1200)
    view = PublishedTransfer(tmp_path / STORE, piece=64)
    versions = {"a.ome.zarr": 1}
    canvas = {"x_um": [0, 1024], "y_um": [0, 512]}
    try:
        view.publish(tmp_path, versions, canvas)
        write_position(tmp_path, "a.ome.zarr", 0, 2400)
        versions["a.ome.zarr"] = 2
        assert view.publish(tmp_path, versions, canvas) == 2
        assert zarr.open_array(str(tmp_path / STORE / "4"), mode="r")[0, 0, 0] == 2400
        canvas["x_um"][1] = 2048
        with pytest.raises(ValueError, match="canvas cannot change"):
            view.publish(tmp_path, versions, canvas)
    finally:
        view.close()


def test_initial_external_folder_bake_requires_canvas(tmp_path):
    from zmart_viewer.server import make_server

    write_position(tmp_path, "a.ome.zarr", 0, 1200)
    with pytest.raises(ValueError, match="canvas bounds"):
        make_server(port=0, data_dir=tmp_path, loads=[{"path": tmp_path}], bake=True)
