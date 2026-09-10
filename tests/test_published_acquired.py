"""Completed sparse snapshots use one aggregate, independently of baking."""

import json
import threading
from copy import deepcopy

import numpy as np
import pytest
import zarr
from test_acquired_composition import pixels, region, source
from test_server import request

from zmart_viewer import pieces
from zmart_viewer.published import STACK_STORE, STORE, PublishedTransfer
from zmart_viewer.server import make_server

CANVAS = {"x_um": [0, 64], "y_um": [0, 8]}


@pytest.mark.parametrize("frames,channels,depth", [(1, 1, 1), (2, 2, 2)])
@pytest.mark.parametrize("value", [0, 80])
def test_misaligned_bake_updates_ancestors_without_reopening_distant_originals(
    tmp_path, monkeypatch, frames, channels, depth, value
):
    from test_published_transfer import write_position

    from zmart_viewer.compose import MEAN_REDUCTION, Composer

    canvas = {"x_um": [0, 2049], "y_um": [0, 129]}
    for name, x, initial_value in (("a.ome.zarr", 1, 120), ("far.ome.zarr", 1537, 240)):
        write_position(tmp_path, name, x, initial_value, frames=frames, channels=channels, depth=depth)
    versions = {"a.ome.zarr": 1, "far.ome.zarr": 1}
    composition = {
        "regions": "complete",
        "order": list(versions),
        "pyramid_reduction": MEAN_REDUCTION,
    }
    view = PublishedTransfer(tmp_path / STORE, piece=64)
    view.publish(tmp_path, versions, canvas, composition=composition)
    view.close()
    # A reopened publisher cannot rely on original pixels surviving in RAM.
    view = PublishedTransfer(tmp_path / STORE, piece=64)
    write_position(
        tmp_path, "new.ome.zarr", 145, value, frames=frames, channels=channels, depth=depth
    )
    versions["new.ome.zarr"] = 1
    composition["order"].append("new.ome.zarr")
    reads = set()
    read = Composer._read_from

    def counted(self, copy, *args):
        reads.add(copy.held_in.parent.name)
        return read(self, copy, *args)

    monkeypatch.setattr(Composer, "_read_from", counted)
    try:
        view.publish(tmp_path, versions, canvas, composition=composition)
        assert reads and reads <= {"a.ome.zarr", "new.ome.zarr"}
        assert view.accounting["last_bake_pieces_rehalved"] > 0
        assert not (view._shown / "0/c").exists()
        fine = np.zeros((frames, channels, depth, 129, 2049), dtype="uint16")
        fine[..., :128, 1:129] = 120
        fine[..., :128, 145:273] = value
        fine[..., :128, 1537:1665] = 240
        described = json.loads((view._shown / "zarr.json").read_text())
        baked = described["attributes"]["zmart"]["baked"]
        for level in range(view.composer().mosaic.levels):
            if level in baked:
                actual = zarr.open_array(str(view._shown / str(level)), mode="r")[:]
                np.testing.assert_array_equal(
                    actual, fine.squeeze(axis=(0, 1)) if frames == channels == 1 else fine
                )
            h, w = fine.shape[-2:]
            fine = np.pad(fine, [(0, 0)] * 3 + [(0, h % 2), (0, w % 2)], mode="edge")
            fine = (
                fine.reshape(frames, channels, depth, (h + 1) // 2, 2, (w + 1) // 2, 2)
                .mean(axis=(-3, -1))
                .round()
                .astype("uint16")
            )
        mask = view.composer().coverage_for(0, 0, 0, 2)
        assert mask[0, 16] == 0 and mask[0, 17] == 1  # gap then acquired pixels
    finally:
        view.close()


def test_interrupted_ancestor_bake_clears_staged_pixels_when_retried_as_black(
    tmp_path, monkeypatch
):
    from test_published_transfer import write_position

    for name, value in (("a.ome.zarr", 120), ("b.ome.zarr", 0)):
        write_position(tmp_path, name, 1, value)
    versions = {"a.ome.zarr": 1, "b.ome.zarr": 1}
    composition = {"regions": "complete", "order": list(versions)}
    canvas = {"x_um": [0, 1024], "y_um": [0, 128]}
    view = PublishedTransfer(tmp_path / STORE, piece=64)
    try:
        view.publish(tmp_path, versions, canvas, composition=composition)
        real = view._rehalve_one_piece_directly

        def interrupted(*args):
            real(*args)
            raise OSError("interrupted after staging ancestor")

        with monkeypatch.context() as patch:
            patch.setattr(view, "_rehalve_one_piece_directly", interrupted)
            with pytest.raises(OSError, match="after staging ancestor"):
                view.publish(
                    tmp_path,
                    versions,
                    canvas,
                    composition={**composition, "order": list(reversed(versions))},
                )
        view.publish(tmp_path, versions, canvas, composition=composition)
        for level in range(2, view.composer().mosaic.levels):
            assert not zarr.open_array(str(view._shown / str(level)), mode="r")[:].any()
        assert view.composer().coverage_for(0, 0, 0, 0)[0, 1] == 1
        assert not (view._shown / "pending.json").exists()
    finally:
        view.close()


def snapshot():
    return {
        "regions": {
            "a.ome.zarr": [region(0, 4)],
            "b.ome.zarr": [region(0, 2), region(6, 2)],
            "far.ome.zarr": [region(0, 8)],
        },
        "order": ["a.ome.zarr", "b.ome.zarr", "far.ome.zarr"],
    }


def original_files(folder):
    return {
        str(p): p.read_bytes()
        for name in snapshot()["order"]
        for p in (folder / name).rglob("*")
        if p.is_file()
    }


def check_levels(view, fine, mask):
    made = view.composer()
    baked_levels = json.loads((view._shown / "zarr.json").read_text())["attributes"]["zmart"][
        "baked"
    ]
    assert made.mosaic.levels == 5  # Two levels beyond the originals.
    for level in range(made.mosaic.levels):
        actual, covered = pixels(made, level)
        np.testing.assert_array_equal(actual, fine)
        np.testing.assert_array_equal(covered, mask)
        if view.bake and level in baked_levels:
            baked = zarr.open_array(str(view._shown / str(level)), mode="r")[:]
            np.testing.assert_array_equal(baked[0, 0, 0], fine)
            assert not baked[1, 1, 1].any()
        if level + 1 < made.mosaic.levels:
            pad = ((0, fine.shape[0] % 2), (0, fine.shape[1] % 2))
            fine = np.pad(fine, pad, mode="edge")
            mask = np.pad(mask, pad, mode="edge")
            shape = (fine.shape[0] // 2, 2, fine.shape[1] // 2, 2)
            fine = fine.reshape(shape).mean(axis=(1, 3)).round().astype("uint16")
            mask = mask.reshape(shape).max(axis=(1, 3))


@pytest.mark.parametrize("bake", [False, True])
def test_sparse_publication_order_gap_fill_rewrite_and_idle(tmp_path, monkeypatch, bake):
    source(tmp_path, "a.ome.zarr", 120)
    source(tmp_path, "b.ome.zarr", 0, x=1)
    source(tmp_path, "far.ome.zarr", 240, x=48)
    originals = original_files(tmp_path)
    view = PublishedTransfer(tmp_path / STORE, piece=4)
    composition = snapshot()
    versions = dict.fromkeys(composition["order"], 1)
    fine = np.zeros((8, 64), dtype="uint16")
    fine[:, [0, 3]] = 120
    fine[:, 48:56] = 240
    mask = np.zeros_like(fine, dtype="uint8")
    mask[:, :4] = mask[:, 7:9] = mask[:, 48:56] = 1
    try:
        assert view.publish(tmp_path, versions, CANVAS, composition=composition, bake=bake) == 1
        check_levels(view, fine, mask)
        assert not (view._shown / "0/c").exists()
        if not bake:
            assert not list(view._shown.glob("*/c"))
        untouched = view._shown / "3/c/0/0/0/0/1"
        mark = untouched.stat().st_mtime_ns if bake else None
        # Raise A: no source revision or original data changes.
        composition["order"] = ["b.ome.zarr", "a.ome.zarr", "far.ome.zarr"]
        assert view.publish(tmp_path, versions, CANVAS, composition=composition, bake=bake) == 2
        fine[:, :4] = 120
        check_levels(view, fine, mask)
        assert original_files(tmp_path) == originals
        # A newly acquired black gap changes coverage without relying on brightness.
        composition["regions"]["b.ome.zarr"].append(region(3, 3))
        assert view.publish(tmp_path, versions, CANVAS, composition=composition, bake=bake) == 3
        mask[:, 4:7] = 1
        check_levels(view, fine, mask)
        if bake:
            assert untouched.stat().st_mtime_ns == mark
        # Warmed original blocks must not survive an actual rewrite.
        zarr.open_array(str(tmp_path / "a.ome.zarr/0"), mode="r+")[:] = 80
        versions["a.ome.zarr"] = 2
        assert view.publish(tmp_path, versions, CANVAS, composition=composition, bake=bake) == 4
        fine[:, :4] = 80
        check_levels(view, fine, mask)
        state = (view._shown / "publication.json").stat().st_mtime_ns

        def no_pixels(*args, **kwargs):
            pytest.fail("An idle publication read or built image data")

        monkeypatch.setattr(view, "_replace_one_piece", no_pixels)
        held = view.composer()
        monkeypatch.setattr(held, "_read_from", no_pixels)
        assert (
            view.publish(
                tmp_path,
                dict(reversed(list(versions.items()))),
                CANVAS,
                composition=deepcopy(composition),
                bake=bake,
            )
            == 4
        )
        assert (view._shown / "publication.json").stat().st_mtime_ns == state
        reopened = PublishedTransfer(view._shown, piece=4)
        try:
            check_levels(reopened, fine, mask)
        finally:
            reopened.close()
    finally:
        view.close()


def test_bake_switch_does_not_serve_old_baked_files(tmp_path):
    source(tmp_path, "a.ome.zarr", 120)
    composition = {"regions": {"a.ome.zarr": [region(0, 8)]}, "order": ["a.ome.zarr"]}
    view = PublishedTransfer(tmp_path / STORE, piece=4)
    try:
        view.publish(tmp_path, {"a.ome.zarr": 1}, CANVAS, composition=composition)
        assert (view._shown / "3/c/0/0/0/0/0").is_file()
        assert pieces.built_bytes_behind(view._shown, "3/c/0/0/0/0/0") is not None
        zarr.open_array(str(tmp_path / "a.ome.zarr/0"), mode="r+")[:] = 0
        view.publish(tmp_path, {"a.ome.zarr": 2}, CANVAS, composition=composition, bake=False)
        assert pieces.built_bytes_behind(view._shown, "3/c/0/0/0/0/0") is None
        view.publish(tmp_path, {"a.ome.zarr": 2}, CANVAS, composition=composition, bake=True)
        assert pieces.built_bytes_behind(view._shown, "3/c/0/0/0/0/0") is None
        assert view.composer().coverage_for(2, 0, 0, 0)[0, 0] == 1
    finally:
        pieces.forget(view._shown)
        view.close()


def test_interrupted_order_change_recovers_the_previous_order(tmp_path, monkeypatch):
    source(tmp_path, "a.ome.zarr", 120)
    source(tmp_path, "b.ome.zarr", 0)
    view = PublishedTransfer(tmp_path / STORE, piece=4)
    composition = {
        "regions": {name: [region(0, 8)] for name in ("a.ome.zarr", "b.ome.zarr")},
        "order": ["a.ome.zarr", "b.ome.zarr"],
    }
    versions = dict.fromkeys(composition["order"], 1)
    try:
        view.publish(tmp_path, versions, CANVAS, composition=composition)
        raised = deepcopy(composition)
        raised["order"].reverse()
        real = view._replace_one_piece

        def interrupted(*args, **kwargs):
            real(*args, **kwargs)
            raise OSError("interrupted order change")

        with monkeypatch.context() as patch:
            patch.setattr(view, "_replace_one_piece", interrupted)
            with pytest.raises(OSError, match="interrupted order"):
                view.publish(tmp_path, versions, CANVAS, composition=raised)
        assert zarr.open_array(str(view._shown / "2"), mode="r")[:].max() == 120
        view.publish(tmp_path, versions, CANVAS, composition=composition)
        assert not zarr.open_array(str(view._shown / "3"), mode="r")[:].any()
        assert view.revision == 2
    finally:
        view.close()


@pytest.mark.parametrize("bake", [False, True])
def test_http_sparse_contract_cannot_silently_be_dropped(tmp_path, bake):
    source(tmp_path, "a.ome.zarr", 0)
    server = make_server(port=0, data_dir=tmp_path, live=True, allow_open=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def post(route, payload):
        status, _, body = request(port, route, "POST", json.dumps(payload).encode())
        return status, json.loads(body)

    payload = {
        "path": str(tmp_path),
        "bake": bake,
        "canvas": CANVAS,
        "source_revisions": {"a.ome.zarr": 1},
        "composition": {"regions": {"a.ome.zarr": [region(0, 2)]}, "order": ["a.ome.zarr"]},
    }
    try:
        for invalid in (None, {}, {"regions": "complete"}):
            status, answer = post("/api/stores/open", {**payload, "composition": invalid})
            assert status == 400 and answer["error"]
        status, answer = post("/api/stores/open", payload)
        assert status == 200, answer
        assert {url for row in answer["layers"] for url in row["sources"]} == {
            answer["layers"][0]["sources"][0]
        }
        assert STACK_STORE in answer["layers"][0]["sources"][0]
        publication = {key: payload[key] for key in ("path", "source_revisions", "composition")}
        assert post("/api/announce", {"publications": [publication]})[0] == 200
        del publication["composition"]
        status, answer = post("/api/announce", {"publications": [publication]})
        assert status == 400 and (
            "coverage contract" in answer["error"] or "explicit acquired" in answer["error"]
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize("route,method,payload", [
    ("/api/stores/open", "open", {"composition": {"regions": "complete"}}),
    ("/api/announce", "announce", {"publications": []}),
])
@pytest.mark.parametrize("failure,expected", [(ValueError, 400), (OSError, 503)])
def test_publication_http_distinguishes_invalid_input_from_io_failure(
    tmp_path, monkeypatch, route, method, payload, failure, expected
):
    from zmart_viewer.published import PublishedFolders

    def fail(*args, **kwargs):
        raise failure("publication unavailable")

    monkeypatch.setattr(PublishedFolders, method, fail)
    server = make_server(port=0, data_dir=tmp_path, live=True, allow_open=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, body = request(server.server_address[1], route, "POST",
                                  json.dumps({"path": str(tmp_path), **payload}).encode())
        assert status == expected
        assert json.loads(body)["error"] == "publication unavailable"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


@pytest.mark.parametrize("bake", [False, True])
def test_complete_stores_append_retire_and_keep_one_aggregate_at_100(tmp_path, bake):
    from zmart_viewer.library import Library
    from zmart_viewer.published import PublishedFolders

    names = [f"p{i:03}.ome.zarr" for i in range(100)]
    for i, name in enumerate(names):
        source(tmp_path, name, 0 if i == 0 else 120, x=16 * i)
    library = Library()
    number = library.open(tmp_path)
    published = PublishedFolders(library)
    canvas = {"x_um": [0, 1600], "y_um": [0, 8]}
    try:
        published.open(
            tmp_path,
            canvas=canvas,
            versions={names[0]: 1},
            bake=bake,
            composition={"regions": "complete", "order": names[:1]},
        )
        for landed in (2, 100):
            publication = {
                "path": str(tmp_path),
                "source_revisions": dict.fromkeys(names[:landed], 1),
                "composition": {"regions": "complete", "order": names[:landed]},
            }
            published.announce([publication])
            assert published.entries(library.entries()) == [(number, tmp_path, STACK_STORE)]
        view = published.views[tmp_path.resolve(), None][0].outputs[STACK_STORE]
        made = view.composer()
        for level in range(made.mosaic.levels):
            mask = made.coverage_for(level, 1, 0, 0, moment=1, channel=1)
            assert mask[0, 0] == 1  # Acquired black, even at another C/Z/T.
        assert made.coverage_for(0, 0, 0, 0)[0, 10] == 0
        assert (
            pieces.built_bytes_behind(view._shown, f"{made.mosaic.levels - 1}/c/0/0/0/0/0")
            is not None
        )
        publication["source_revisions"].pop(names[1])
        publication["composition"]["order"].remove(names[1])
        published.announce([publication])
        assert view.composer().coverage_for(0, 0, 0, 0)[0, 16] == 0
        assert published.refresh() == ((number, 4),)
        if not bake:
            assert not list(view._shown.glob("*/c"))
        print({"bake": bake, "positions": 100, "aggregate_sources": 1})
    finally:
        pieces.forget(tmp_path / STACK_STORE)
        published.close()


def test_off_mode_retirement_is_cleared_when_baking_resumes(tmp_path):
    source(tmp_path, "a.ome.zarr", 120)
    source(tmp_path, "b.ome.zarr", 240, x=48)
    view = PublishedTransfer(tmp_path / STORE, piece=4)
    both = {"regions": "complete", "order": ["a.ome.zarr", "b.ome.zarr"]}
    just_a = {"regions": "complete", "order": ["a.ome.zarr"]}
    try:
        view.publish(tmp_path, dict.fromkeys(both["order"], 1), CANVAS, composition=both)
        view.publish(tmp_path, {"a.ome.zarr": 1}, CANVAS, composition=just_a, bake=False)
        view.publish(tmp_path, {"a.ome.zarr": 2}, CANVAS, composition=just_a, bake=False)
        view.publish(tmp_path, {"a.ome.zarr": 2}, CANVAS, composition=just_a)
        assert not (view._shown / "2/c/0/0/0/0/3").exists()
        assert zarr.open_array(str(view._shown / "4"), mode="r")[0, 0, 0, 0, 3] == 0
    finally:
        view.close()


def test_virtual_extended_levels_work_in_a_spawned_worker(tmp_path):
    from zmart_viewer.compose import Composer

    source(tmp_path, "a.ome.zarr", 120)
    view = PublishedTransfer(tmp_path / STORE, piece=4)
    view.publish(
        tmp_path,
        {"a.ome.zarr": 1},
        CANVAS,
        bake=False,
        composition={"regions": "complete", "order": ["a.ome.zarr"]},
    )
    worker = Composer(view.composer().mosaic, piece=4, workers=2)
    try:
        np.testing.assert_array_equal(
            worker.values_for(4, 0, 0, 0), view.composer().values_for(4, 0, 0, 0)
        )
    finally:
        worker.close()
        view.close()


@pytest.mark.parametrize("bake", [False, True])
@pytest.mark.parametrize("native", [False, True])
def test_browser_sparse_aggregate_refresh_pixels_and_requests(
    browser, built_dist, tmp_path, bake, native
):
    from pixels import image_middle
    from test_manifest_refresh_browser import _wait_for_picture
    from test_published_transfer import write_position
    from test_transparent_2d_browser import READ_ALPHA

    from zmart_viewer.compose import MEAN_REDUCTION

    write_position(tmp_path, "black.ome.zarr", 0, 0)
    write_position(tmp_path, "signal.ome.zarr", 512, 2400)
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
    page.on("request", lambda request: requests.append(request.url))
    page.on("pageerror", lambda error: errors.append(str(error)))
    payload = {
        "path": str(tmp_path),
        "bake": bake,
        "canvas": {"x_um": [0, 1024], "y_um": [0, 512]},
        "source_revisions": {"black.ome.zarr": 1, "signal.ome.zarr": 1},
        "composition": {
            "regions": {
                "black.ome.zarr": [region(0, 128, height=128)],
                "signal.ome.zarr": [region(0, 64, height=128)],
            },
            "order": ["black.ome.zarr", "signal.ome.zarr"],
        },
    }
    if native:
        payload["composition"]["pyramid_reduction"] = MEAN_REDUCTION
    try:
        assert page.request.post(f"{address}/api/stores/open", data=payload).ok
        page.goto(address)
        _wait_for_picture(page)
        page.evaluate("""() => {
            const p = zmartViewer.navigationState.position;
            const at = Float32Array.from(p.value), names = p.coordinateSpace.value.names;
            for (const [axis, value] of Object.entries({x:448,y:64,z:0})) at[names.indexOf(axis)]=value;
            p.value=at; zmartViewer.navigationState.zoomFactor.value=1;
            const below=document.createElement('div');
            below.style.cssText='position:absolute;inset:0;background:#ff00ff';
            document.body.prepend(below);
            document.getElementById('root').style.position='relative';
        }""")
        _wait_for_picture(page)
        assert (
            len({url for row in page.evaluate("zmartConfig.layers") for url in row["sources"]}) == 1
        )
        first = page.evaluate(READ_ALPHA)
        assert first["clear"] > 1000 and first["black"] > 1000 and first["partial"] == 0
        before = image_middle(page)
        page.screenshot(path=str(tmp_path / f"acquired-before-{bake}.png"))
        publication = {key: payload[key] for key in ("path", "source_revisions", "composition")}
        publication["composition"]["regions"]["signal.ome.zarr"].append(region(64, 64, height=128))
        mark = len(requests)
        assert page.request.post(f"{address}/api/announce", data={"publications": [publication]}).ok
        page.wait_for_function("zmartConfig.layers.some(row => row.sourceRevisions?.[0] === 2)")
        _wait_for_picture(page)
        after = image_middle(page)
        changed_pixels = int(np.count_nonzero(np.any(before != after, axis=2)))
        assert changed_pixels > 100
        assert page.evaluate(READ_ALPHA)["opaque"] > first["opaque"]
        changed_requests = [url for url in requests[mark:] if "/data/" in url]
        assert changed_requests
        assert len(changed_requests) == len(set(changed_requests))
        mark = len(requests)
        assert page.request.post(f"{address}/api/announce", data={"publications": [publication]}).ok
        page.wait_for_timeout(2200)
        assert not [url for url in requests[mark:] if "/data/" in url]
        page.screenshot(path=str(tmp_path / f"acquired-fine-{bake}.png"))
        page.evaluate("zmartViewer.navigationState.zoomFactor.value = 4")
        _wait_for_picture(page)
        coarse = page.evaluate(READ_ALPHA)
        assert coarse["clear"] > 1000 and coarse["black"] > 100 and coarse["partial"] == 0
        assert any(f"{STORE}/2/c/" in url for url in requests)
        assert not any("black.ome.zarr/" in url or "signal.ome.zarr/" in url for url in requests)
        page.screenshot(path=str(tmp_path / f"acquired-coarse-{bake}.png"))
        assert not errors, errors
        print(
            {
                "bake": bake,
                "native": native,
                "fine_alpha": first,
                "coarse_alpha": coarse,
                "change_data_requests": len(changed_requests),
                "changed_screenshot_pixels": changed_pixels,
                "idle_data_requests": 0,
                "original_source_requests": 0,
            }
        )
    finally:
        page.close()
        server.shutdown()
        server.server_close()
        thread.join(5)
