import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from test_server import request
from test_view_sampling import write_tile

from zmart_viewer import published, readable
from zmart_viewer.building import ComposedPicture
from zmart_viewer.server import make_server


@pytest.fixture
def serving(tmp_path):
    source = tmp_path / "positions"
    source.mkdir()
    write_tile(source, "p.ome.zarr", np.full((1, 1, 3, 8, 8), 80, dtype="uint16"))
    server = make_server(port=0, data_dir=source, live=True, allow_open=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, source, tmp_path / "views"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def publish(port, source, views, revision, opening=False):
    payload = {
        "path": str(source),
        "source_revisions": {"p.ome.zarr": revision},
        "composition": {"regions": "complete", "order": ["p.ome.zarr"]},
    }
    if opening:
        payload.update(
            bake=True,
            canvas={"x_um": [0, 16], "y_um": [0, 8]},
            views={"path": str(views), "acquisition": "targets", "modes": ["top", "slice"]},
        )
    else:
        payload = {"publications": [payload]}
    return request(
        port,
        "/api/stores/open" if opening else "/api/announce",
        "POST",
        json.dumps(payload).encode(),
        {"Content-Type": "application/json"},
    )


def rows(port):
    status, _, data = request(port, "/api/config")
    assert status == 200, data
    return {row["view"]["type"]: row for row in json.loads(data)["layers"] if row.get("view")}


def get(port, row, inside):
    address = row["sources"][0].split("|", 1)[0] + inside
    status, _, data = request(port, address)
    assert status == 200, (address, status, data)
    return data


@pytest.mark.parametrize("new_depth", [3, 5])
def test_pending_update_keeps_pixels_metadata_and_coverage_readable(
    serving, monkeypatch, new_depth
):
    port, source, views = serving
    assert publish(port, source, views, 1, opening=True)[0] == 200
    before = rows(port)
    paths = ["zarr.json", "0/zarr.json", "0/c/0/0/0", "2/c/0/0/0", "__zmart_coverage__/0/c/0/0/0"]
    old = {path: get(port, before["top"], path) for path in paths}
    old_slice = get(port, before["slice"], "0/c/0/0/0")
    # In-place replacement of the originals must not alter the readable snapshot.
    write_tile(source, "p.ome.zarr", np.full((1, 1, new_depth, 8, 8), 160, dtype="uint16"))
    entered_top, entered_slice, release_top, release_slice = [threading.Event() for _ in range(4)]
    original = ComposedPicture._replace_one_piece

    def slow(self, *args, **kwargs):
        entered, release = (
            (entered_top, release_top)
            if "_top." in self._shown.name
            else (entered_slice, release_slice)
        )
        entered.set()
        assert release.wait(8)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ComposedPicture, "_replace_one_piece", slow)
    with ThreadPoolExecutor() as pool:
        work = pool.submit(publish, port, source, views, 2)
        try:
            assert entered_top.wait(5)
            during = rows(port)
            assert during["top"]["sourceRevisions"] == before["top"]["sourceRevisions"]
            for path in paths:
                assert get(port, during["top"], path) == old[path]
            release_top.set()
            assert entered_slice.wait(5)
            partial = rows(port)
            assert partial["top"]["sourceRevisions"] == [2]
            assert partial["slice"]["sourceRevisions"] == [1]
            assert get(port, partial["top"], "0/c/0/0/0") != old["0/c/0/0/0"]
            assert get(port, partial["slice"], "0/c/0/0/0") == old_slice
        finally:
            release_top.set()
            release_slice.set()
        assert work.result(timeout=10)[0] == 200
    assert rows(port)["slice"]["sourceRevisions"] == [2]


def test_first_committed_view_is_exposed_before_the_remaining_views(serving, monkeypatch):
    port, source, views = serving
    entered, release = threading.Event(), threading.Event()
    original = ComposedPicture._replace_one_piece

    def slow(self, *args, **kwargs):
        if "_slice." in self._shown.name:
            entered.set()
            assert release.wait(8)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ComposedPicture, "_replace_one_piece", slow)
    with ThreadPoolExecutor() as pool:
        work = pool.submit(publish, port, source, views, 1, True)
        try:
            assert entered.wait(5)
            current = rows(port)
            assert set(current) == {"top"}
            assert get(port, current["top"], "0/c/0/0/0")
        finally:
            release.set()
        assert work.result(timeout=10)[0] == 200


def test_snapshot_lives_until_its_last_http_reader_finishes(serving):
    port, source, views = serving
    assert publish(port, source, views, 1, True)[0] == 200
    store = views / "targets_top.zmartview.zarr"
    with readable.reading(store) as old:
        old_source = Path(
            json.loads((old / "publication.json").read_text())["mosaic"]["tiles"][0]["store"]
        )
        write_tile(source, "p.ome.zarr", np.full((1, 1, 3, 8, 8), 160, dtype="uint16"))
        assert publish(port, source, views, 2)[0] == 200
        assert old.is_dir()
        assert old_source.is_dir()
        assert readable.current(store) != old
    readable._cleanup()
    deadline = time.monotonic() + 2
    while (old.exists() or old_source.exists()) and time.monotonic() < deadline:
        threading.Event().wait(0.01)
    assert not old.exists()
    assert not old_source.exists()


def test_views_share_frozen_sources_but_never_link_mutable_originals(serving):
    port, source, views = serving
    assert publish(port, source, views, 1, True)[0] == 200
    copies = []
    for mode in ("top", "slice"):
        snapshot = readable.current(views / f"targets_{mode}.zmartview.zarr")
        state = json.loads((snapshot / "publication.json").read_text())
        copies.append(Path(state["mosaic"]["tiles"][0]["store"]))
    assert copies[0] == copies[1], (
        "Unchanged source folders are reused without relinking their files"
    )
    chunks = [
        path for path in (copies[0] / "0").rglob("*") if path.is_file() and path.name != "zarr.json"
    ]
    assert chunks
    for chunk in chunks:
        relative = chunk.relative_to(copies[0])
        assert os.path.samefile(chunk, copies[1] / relative)
        assert not os.path.samefile(chunk, source / "p.ome.zarr" / relative)


@pytest.mark.parametrize("failure", ["bake", "snapshot", "ledger"])
def test_failed_publication_retains_the_last_readable_generation(serving, monkeypatch, failure):
    port, source, views = serving
    assert publish(port, source, views, 1, True)[0] == 200
    before = rows(port)["top"]
    body = get(port, before, "0/c/0/0/0")
    write_tile(source, "p.ome.zarr", np.full((1, 1, 3, 8, 8), 160, dtype="uint16"))
    original = ComposedPicture._replace_one_piece

    def fail(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise OSError("injected publication failure")

    with monkeypatch.context() as patch:
        if failure == "bake":
            patch.setattr(ComposedPicture, "_replace_one_piece", fail)
        elif failure == "snapshot":

            def fail_copy(*args, **kwargs):
                raise OSError("injected snapshot copy failure")

            patch.setattr(readable, "_tree", fail_copy)
        else:
            atomic_json = published._atomic_json

            def fail_ledger(path, data):
                if path.name == "publication.json":
                    raise OSError("injected ledger write failure")
                return atomic_json(path, data)

            patch.setattr(published, "_atomic_json", fail_ledger)
        assert publish(port, source, views, 2)[0] != 200
        assert (views / "targets_top.zmartview.zarr" / "pending.json").exists()
        assert rows(port)["top"]["sourceRevisions"] == [1]
        assert get(port, before, "0/c/0/0/0") == body
    assert publish(port, source, views, 2)[0] == 200
    assert rows(port)["top"]["sourceRevisions"] == [2]
    assert get(port, rows(port)["top"], "0/c/0/0/0") != body
