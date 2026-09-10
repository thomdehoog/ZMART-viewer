"""Physical-coordinate and cross-origin contracts for embedded named views."""

import http.client
import json
import threading

import numpy as np
import pytest
import zarr
from test_view_sampling import write_tile

from zmart_viewer.server import make_server
from zmart_viewer.views import ViewSet


@pytest.mark.parametrize("spacing", [1, 2.5, 4])
@pytest.mark.parametrize("origin", ["corner", "center"])
@pytest.mark.parametrize("bake", [False, True])
def test_projection_and_slice_describe_the_same_physical_pixels(tmp_path, spacing, origin, bake):
    source = tmp_path / "positions"
    source.mkdir()
    data = np.full((1, 1, 3, 8, 8), 80, dtype="uint16")
    name = "p.ome.zarr"
    write_tile(source, name, data, x=8)
    group = zarr.open_group(str(source / name), mode="r+")
    ome = group.attrs["ome"]
    for level, dataset in enumerate(ome["multiscales"][0]["datasets"]):
        scale, translation = dataset["coordinateTransformations"]
        scale["scale"][-2:] = [spacing * 2**level] * 2
        offset = 0 if origin == "corner" else spacing * (2**level - 1) / 2
        translation["translation"][-2:] = [offset, 8 * spacing + offset]
    group.attrs["ome"] = ome
    originals = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
    views = ViewSet(
        tmp_path / "view",
        acquisition="a",
        projections=("max",),
        projection_folder=tmp_path / "projections",
        piece=4,
    )
    try:
        views.publish(
            source,
            {name: 1},
            {"x_um": [0, 24 * spacing], "y_um": [0, 8 * spacing]},
            composition={"regions": "complete", "order": [name], "xy_origin": origin},
            bake=bake,
        )
        transforms = []
        for output in views.outputs.values():
            composer = output.composer()
            metadata = json.loads(composer.group_json())
            multiscale = metadata["attributes"]["ome"]["multiscales"][0]
            transforms.append([d["coordinateTransformations"] for d in multiscale["datasets"]])
        for level in range(len(transforms[0])):
            for view in transforms:
                assert view[level][0]["scale"][-2:] == transforms[0][level][0]["scale"][-2:]
                assert (
                    view[level][1]["translation"][-2:]
                    == transforms[0][level][1]["translation"][-2:]
                )
            expected = spacing * (2**level - (0 if origin == "corner" else 1)) / 2
            assert transforms[0][level][1]["translation"][-2:] == [expected, expected]
        assert all(p.read_bytes() == contents for p, contents in originals.items())
    finally:
        views.close()


def test_embedding_is_a_cross_origin_module_without_operator_wrapper(tmp_path, browser):
    (tmp_path / "index.html").write_text("<!doctype html><title>Host</title>")
    servers = [make_server(port=0, site_dir=tmp_path, live=True, allow_open=True) for _ in range(2)]
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    for thread in threads:
        thread.start()
    origins = [f"http://127.0.0.1:{s.server_address[1]}" for s in servers]
    page = browser.new_page()
    try:
        connection = http.client.HTTPConnection("127.0.0.1", servers[1].server_address[1])
        connection.request("GET", "/embedding.js", headers={"Origin": origins[0]})
        response = connection.getresponse()
        assert response.headers.get_all("Cache-Control") == ["no-cache"]
        assert response.headers.get_all("Access-Control-Allow-Origin") == ["*"]
        response.read()
        connection.close()
        awaitable = "async url => (await import(url)).EMBEDDING_API_VERSION"
        page.goto(origins[0])
        assert page.evaluate(awaitable, origins[1] + "/embedding.js") == 1
    finally:
        page.close()
        for server, thread in zip(servers, threads):
            server.shutdown()
            thread.join()
            server.server_close()
