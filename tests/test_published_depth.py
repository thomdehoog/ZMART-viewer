"""Specimen Z belongs to stacks; flat display placement never edits originals."""

import json

import numpy as np
import pytest
import zarr
from test_published_transfer import write_position

from zmart_viewer.acquired import AcquiredRegion
from zmart_viewer.published import STORE, PublishedTransfer

CANVAS = {"x_um": [0, 2048], "y_um": [0, 128]}
HEIGHTS = [62.99, 62.79, 64.26, 64.01, 61.10, 60.40, 62.20, 61.40]


def at_depth(folder, name, x, z, *, depth=1, dz=1.3):
    store = write_position(folder, name, x, 0, frames=2, channels=2, depth=depth)
    group = zarr.open_group(str(store), mode="r+")
    ome = group.attrs["ome"]
    for level, dataset in enumerate(ome["multiscales"][0]["datasets"]):
        dataset["coordinateTransformations"][0]["scale"][2] = dz
        dataset["coordinateTransformations"][1]["translation"][2] = z
        values = np.fromfunction(
            lambda t, c, p: 1000 + 400 * t + 100 * c + 10 * p, (2, 2, depth)
        ).astype("uint16")
        group[str(level)][:] = np.broadcast_to(values[..., None, None], group[str(level)].shape)
    group.attrs["ome"] = ome
    return store


def composition(names):
    return {"regions": "complete", "order": list(names)}


@pytest.mark.parametrize("bake", [False, True])
@pytest.mark.parametrize("one_at_a_time", [False, True])
def test_eight_real_height_flats_share_display_z_without_editing_originals(
    tmp_path, bake, one_at_a_time
):
    names = [f"tile{i}.ome.zarr" for i in range(8)]
    for i, (name, height) in enumerate(zip(names, HEIGHTS, strict=True)):
        at_depth(tmp_path, name, 256 * i, height, dz=1 if i % 2 else 1.3)
    original = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    view = PublishedTransfer(tmp_path / STORE, piece=64)
    try:
        for count in range(1, 9) if one_at_a_time else [8]:
            selected = names[:count]
            view.publish(
                tmp_path,
                dict.fromkeys(selected, 1),
                CANVAS,
                composition=composition(selected),
                bake=bake,
            )
            made = view.composer()
            assert made.mosaic.corner_um[0] == 0
            assert made.mosaic.shape(0)[0] == 1
            for i in range(count):
                assert made.values_for(0, 0, 0, i * 4, moment=1, channel=1)[0, 0] == 1500
                assert made.coverage_for(0, 0, 0, i * 4, moment=1, channel=1)[0, 0] == 1
        reopened = PublishedTransfer(view._shown, piece=64)
        try:
            assert reopened.composer().mosaic.corner_um[0] == 0
            assert reopened.composer().values_for(0, 0, 0, 28)[0, 0] == 1000
        finally:
            reopened.close()
        assert all(p.read_bytes() == value for p, value in original.items())
        for name, height in zip(names, HEIGHTS, strict=True):
            ome = zarr.open_group(str(tmp_path / name), mode="r").attrs["ome"]
            assert (
                ome["multiscales"][0]["datasets"][0]["coordinateTransformations"][1]["translation"][
                    2
                ]
                == height
            )
    finally:
        view.close()


@pytest.mark.parametrize("first_flat", [False, True])
def test_replacing_all_sources_cannot_change_depth_kind(tmp_path, first_flat):
    at_depth(tmp_path, "a.ome.zarr", 0, 0, depth=1 if first_flat else 3, dz=1)
    at_depth(tmp_path, "b.ome.zarr", 256, 0, depth=3 if first_flat else 1, dz=1)
    view = PublishedTransfer(tmp_path / STORE, piece=64)
    try:
        view.publish(tmp_path, {"a.ome.zarr": 1}, CANVAS, composition=composition(["a.ome.zarr"]))
        before = (view._shown / "publication.json").read_bytes()
        with pytest.raises(ValueError, match="cannot change between flat and stack"):
            view.publish(
                tmp_path, {"b.ome.zarr": 1}, CANVAS, composition=composition(["b.ome.zarr"])
            )
        assert (view._shown / "publication.json").read_bytes() == before
        assert not (view._shown / "pending.json").exists()
    finally:
        view.close()


@pytest.mark.parametrize("bake", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_stacks_keep_relative_z_and_different_depths_independent_of_snapshot_order(
    tmp_path, bake, reverse
):
    at_depth(tmp_path, "a.ome.zarr", 0, 60, depth=3)
    at_depth(tmp_path, "b.ome.zarr", 256, 61.3, depth=4)
    names = ["a.ome.zarr", "b.ome.zarr"]
    if reverse:
        names.reverse()
    view = PublishedTransfer(tmp_path / STORE, piece=64)
    try:
        view.publish(
            tmp_path, dict.fromkeys(names, 1), CANVAS, composition=composition(names), bake=bake
        )
        made = view.composer()
        assert made.mosaic.corner_um[0] == 60
        assert made.mosaic.shape(0)[0] == 5
        for level in range(made.mosaic.levels):
            for plane in range(5):
                for column, offset, depth in ((0, 0, 3), (256, 1, 4)):
                    col, pixel = divmod(column // 2**level, 64)
                    mask = made.coverage_for(level, plane, 0, col, moment=1, channel=1)
                    acquired = offset <= plane < offset + depth
                    assert mask[0, pixel] == acquired
                    values = made.values_for(level, plane, 0, col, moment=1, channel=1)
                    actual = values[0, pixel] if values is not None else 0
                    assert actual == (1500 + 10 * (plane - offset) if acquired else 0)
        # Retiring the lowest stack must not shift the remaining stack's grid.
        view.publish(
            tmp_path, {"b.ome.zarr": 1}, CANVAS, composition=composition(["b.ome.zarr"]), bake=bake
        )
        assert view.composer().mosaic.corner_um[0] == 60
        assert view.composer().mosaic.shape(0)[0] == 5
        assert not view.composer().coverage_for(0, 0, 0, 4).any()
    finally:
        view.close()


@pytest.mark.parametrize("bake", [False, True])
def test_sparse_stack_planes_and_later_gap_fill(tmp_path, bake):
    a = at_depth(tmp_path, "a.ome.zarr", 0, 60, depth=3)
    at_depth(tmp_path, "b.ome.zarr", 256, 61.3, depth=4)
    group = zarr.open_group(str(a), mode="r+")
    for level in range(3):
        group[str(level)][:, :, 2] = 0

    def planes(indices):
        return [
            AcquiredRegion(t, c, (z, 0, 0), (1, 128, 128)).as_written()
            for t in range(2)
            for c in range(2)
            for z in indices
        ]

    names = ["a.ome.zarr", "b.ome.zarr"]
    acquired = {
        "order": names,
        "regions": {names[0]: planes([0, 2]), names[1]: planes([3])},
    }
    view = PublishedTransfer(tmp_path / STORE, piece=64)
    try:
        view.publish(tmp_path, dict.fromkeys(names, 1), CANVAS, composition=acquired, bake=bake)
        for level in range(view.composer().mosaic.levels):
            made = view.composer()
            for plane in range(5):
                assert made.coverage_for(level, plane, 0, 0, moment=1, channel=1)[0, 0] == (
                    plane in (0, 2)
                )
            black = made.values_for(level, 2, 0, 0, moment=1, channel=1)
            assert black is None or not black.any()
        for level in range(3):
            group[str(level)][:, :, 1] = 3333
        acquired["regions"][names[0]] = planes([0, 1, 2])
        view.publish(tmp_path, {names[0]: 2, names[1]: 1}, CANVAS, composition=acquired, bake=bake)
        made = view.composer()
        for level in range(made.mosaic.levels):
            assert made.coverage_for(level, 1, 0, 0, moment=1, channel=1)[0, 0] == 1
            assert made.values_for(level, 1, 0, 0, moment=1, channel=1)[0, 0] == 3333
            assert made.coverage_for(level, 2, 0, 0, moment=1, channel=1)[0, 0] == 1
            black = made.values_for(level, 2, 0, 0, moment=1, channel=1)
            assert black is None or not black.any()
    finally:
        view.close()


@pytest.mark.parametrize("spacing,depth", [(0.1, 3), (0.3, 7), (1.3, 13)])
def test_physical_extent_round_trip_does_not_add_a_plane(tmp_path, spacing, depth):
    at_depth(tmp_path, "a.ome.zarr", 0, 60, depth=depth, dz=spacing)
    view = PublishedTransfer(tmp_path / STORE, piece=64)
    try:
        view.publish(
            tmp_path,
            {"a.ome.zarr": 1},
            CANVAS,
            composition=composition(["a.ome.zarr"]),
            bake=False,
        )
        made = view.composer()
        assert all(made.mosaic.shape(level)[0] == depth for level in range(made.mosaic.levels))
    finally:
        view.close()


@pytest.mark.parametrize("bake", [False, True])
def test_stack_append_within_initial_domain_does_not_move_old_planes(tmp_path, bake):
    at_depth(tmp_path, "a.ome.zarr", 0, 60, depth=6)
    view = PublishedTransfer(tmp_path / STORE, piece=64)
    try:
        view.publish(
            tmp_path,
            {"a.ome.zarr": 1},
            CANVAS,
            composition=composition(["a.ome.zarr"]),
            bake=bake,
        )
        before = view.composer().values_for(0, 1, 0, 0, moment=1, channel=1).copy()
        at_depth(tmp_path, "b.ome.zarr", 256, 61.3, depth=3)
        names = ["a.ome.zarr", "b.ome.zarr"]
        view.publish(
            tmp_path, dict.fromkeys(names, 1), CANVAS, composition=composition(names), bake=bake
        )
        made = view.composer()
        assert made.mosaic.corner_um[0] == 60 and made.mosaic.shape(0)[0] == 6
        np.testing.assert_array_equal(made.values_for(0, 1, 0, 0, moment=1, channel=1), before)
        assert made.values_for(0, 1, 0, 4, moment=1, channel=1)[0, 0] == 1500
        assert made.coverage_for(0, 1, 0, 4, moment=1, channel=1)[0, 0] == 1
        assert not made.coverage_for(0, 0, 0, 4, moment=1, channel=1).any()
    finally:
        view.close()


@pytest.mark.parametrize("problem", ["mixed", "spacing", "fractional", "growth"])
def test_unsupported_z_is_refused_before_publication_changes(tmp_path, problem):
    at_depth(tmp_path, "a.ome.zarr", 0, 60, depth=3)
    view = PublishedTransfer(tmp_path / STORE, piece=64)
    try:
        view.publish(tmp_path, {"a.ome.zarr": 1}, CANVAS, composition=composition(["a.ome.zarr"]))
        before = (view._shown / "publication.json").read_bytes()
        at_depth(
            tmp_path,
            "b.ome.zarr",
            256,
            61 if problem == "fractional" else 61.3,
            depth=1 if problem == "mixed" else 3,
            dz=1 if problem == "spacing" else 1.3,
        )
        expected = {
            "mixed": "flat and stack",
            "spacing": "Z spacing",
            "fractional": "aligned",
            "growth": "Z domain",
        }[problem]
        names = ["a.ome.zarr", "b.ome.zarr"]
        with pytest.raises(ValueError, match=expected):
            view.publish(tmp_path, dict.fromkeys(names, 1), CANVAS, composition=composition(names))
        assert (view._shown / "publication.json").read_bytes() == before
        assert not (view._shown / "pending.json").exists()
        assert json.loads(before)["revision"] == 1
    finally:
        view.close()


@pytest.mark.parametrize("bake", [False, True])
@pytest.mark.parametrize("kind", ["flat", "stack"])
def test_browser_aggregate_depth_pixels_and_zoom(browser, built_dist, tmp_path, bake, kind):
    import threading

    from test_manifest_refresh_browser import _wait_for_picture
    from test_transparent_2d_browser import READ_ALPHA

    from zmart_viewer.server import make_server

    if kind == "flat":
        names = [f"tile{i}.ome.zarr" for i in range(8)]
        for i, (name, height) in enumerate(zip(names, HEIGHTS, strict=True)):
            store = at_depth(tmp_path, name, 256 * i, height)
            if i == 0:
                group = zarr.open_group(str(store), mode="r+")
                for level in range(3):
                    group[str(level)][:] = 0
        positions, zoom, center = [0], 4, 960
    else:
        names = ["a.ome.zarr", "b.ome.zarr"]
        at_depth(tmp_path, names[0], 0, 60, depth=3)
        at_depth(tmp_path, names[1], 256, 61.3, depth=4)
        positions, zoom, center = [60, 61.3, 65.2], 1, 192

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
    payload = {
        "path": str(tmp_path),
        "bake": bake,
        "canvas": CANVAS,
        "source_revisions": dict.fromkeys(names, 1),
        "composition": composition(names),
    }

    def look(z, magnification):
        page.evaluate(
            """([x,z,zoom]) => {
                const p=zmartViewer.navigationState.position, space=p.coordinateSpace.value;
                const at=Float32Array.from(p.value);
                for (const [axis,um] of Object.entries({x,y:64,z})) {
                    const i=space.names.indexOf(axis);
                    at[i]=um/(space.scales[i]*1e6);
                }
                at[space.names.indexOf('t')]=1;
                p.value=at; zmartViewer.navigationState.zoomFactor.value=zoom;
            }""",
            [center, z, magnification],
        )
        _wait_for_picture(page)
        return page.evaluate(READ_ALPHA)

    try:
        response = page.request.post(f"{address}/api/stores/open", data=payload)
        assert response.ok, response.text()
        page.goto(address)
        _wait_for_picture(page)
        page.evaluate("document.body.style.background='#ff00ff'")
        counts = []
        for index, z in enumerate(positions):
            alpha = look(z, zoom)
            tiles = 8 if kind == "flat" else (2 if index == 1 else 1)
            assert alpha["opaque"] == pytest.approx(tiles * (128 / zoom) ** 2, rel=0.02), alpha
            assert alpha["clear"] > 1000 and alpha["partial"] == 0
            if kind == "flat":
                assert alpha["black"] >= (128 / zoom) ** 2
            counts.append(alpha["opaque"])
            page.screenshot(path=str(tmp_path / f"{kind}-{bake}-z{z}.png"))
        coarse = look(positions[-1], zoom * 2)
        assert coarse["opaque"] == pytest.approx(counts[-1] / 4, rel=0.02)
        page.screenshot(path=str(tmp_path / f"{kind}-{bake}-coarse.png"))
        assert (
            len({url for row in page.evaluate("zmartConfig.layers") for url in row["sources"]}) == 1
        )
        assert not any(f"/{name}/" in url for name in names for url in requests)
        mark = len(requests)
        publication = {key: payload[key] for key in ("path", "source_revisions", "composition")}
        assert page.request.post(f"{address}/api/announce", data={"publications": [publication]}).ok
        page.wait_for_timeout(2200)
        assert not [url for url in requests[mark:] if "/data/" in url]
        assert not errors, errors
        print(
            {
                "kind": kind,
                "bake": bake,
                "specimen_z": positions,
                "opaque_pixels": counts,
                "coarse_opaque": coarse["opaque"],
                "aggregate_sources": 1,
                "idle_refetches": 0,
            }
        )
    finally:
        page.close()
        server.shutdown()
        server.server_close()
        thread.join(5)
