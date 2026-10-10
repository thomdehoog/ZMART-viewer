"""The HTTP contract the viewer depends on.

The viewer fetches hundreds of small chunk files from one origin, so the pieces
that matter are: a missing chunk answers 404 (zarr treats that as "background
here", and turning it into an error would break sparse volumes), a path may not
climb out of the data directory, and the JSON endpoints answer the shapes the
frontend expects.
"""

from __future__ import annotations

import http.client
import json
import os
import threading
import time

import numpy as np
import pytest
import zarr
from zmart_viewer.serving.server import make_server


def _serve_tree(tmp_path, *, live: bool = True):
    """Start a server over a small throwaway site/data tree on a free port.

    Returns the port and a function that stops it again. This exists as a plain
    function rather than only a fixture because a couple of tests need to choose
    whether the run counts as still being written — which changes what the browser
    is allowed to keep — and a fixture cannot easily be asked for twice with
    different answers.
    """
    site = tmp_path / "site"
    data = tmp_path / "data"
    (site / "assets").mkdir(parents=True)
    data.mkdir()
    (site / "index.html").write_text("<!doctype html><title>page</title>", encoding="utf-8")
    (data / "demo.zarr").mkdir()
    (data / "demo.zarr" / ".zattrs").write_text('{"multiscales": []}', encoding="utf-8")
    (data / "demo.zarr" / "chunk").write_bytes(b"\x01\x02\x03\x04")
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")

    server = make_server(port=0, data_dir=data, site_dir=site, store="demo.zarr", live=live)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop() -> None:
        server.shutdown()
        thread.join(timeout=5)

    return server.server_address[1], stop


@pytest.fixture
def serving(tmp_path):
    """A server over throwaway site/data directories, on a free port."""
    port, stop = _serve_tree(tmp_path)
    try:
        yield port
    finally:
        stop()


def request(
    port: int,
    path: str,
    method: str = "GET",
    body: bytes | None = None,
    extra: dict[str, str] | None = None,
):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        headers = (
            {"Content-Length": str(len(body)), "Content-Type": "application/json"}
            if body is not None
            else {}
        )
        headers.update(extra or {})
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def test_root_serves_the_built_page(serving):
    status, _, body = request(serving, "/")
    assert status == 200
    assert b"<title>page</title>" in body


def test_chunk_is_served_byte_exact_with_a_length(serving):
    status, headers, body = request(serving, "/data/0/demo.zarr/chunk")
    assert status == 200
    assert body == b"\x01\x02\x03\x04"
    assert headers["Content-Length"] == "4"
    assert headers["Content-Type"] == "application/octet-stream"


def test_missing_chunk_is_a_plain_404(serving):
    """Sparse volumes rely on this: absent chunk means background, not error."""
    status, _, _ = request(serving, "/data/0/demo.zarr/0/9.9.9.9")
    assert status == 404


def test_path_traversal_out_of_the_data_directory_is_refused(serving):
    status, _, _ = request(serving, "/data/0/../outside.txt")
    assert status == 403


def test_health_endpoint(serving):
    status, _, body = request(serving, "/api/health")
    assert status == 200
    assert json.loads(body) == {"ok": True}


def config_from(**kwargs) -> dict:
    """The /api/config a server built with ``kwargs`` answers."""
    server = make_server(port=0, **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        _, _, body = request(server.server_address[1], "/api/config")
    finally:
        server.shutdown()
        thread.join(timeout=5)
    return json.loads(body)


def test_config_tells_the_page_what_to_open(serving):
    """The page fetches this instead of hardcoding a store, so --data works."""
    status, _, body = request(serving, "/api/config")
    assert status == 200
    config = json.loads(body)
    layers = config["layers"]
    assert len(layers) == 1
    assert layers[0]["sources"] == ["/data/0/demo.zarr/|zarr2:"]
    assert layers[0]["window"]["high"] > layers[0]["window"]["low"]
    # Both windows travel up front so the 2-D/3-D toggle needs no round trip.
    assert layers[0]["volumeWindow"]["high"] > layers[0]["volumeWindow"]["low"]
    # This fixture has metadata but no readable array, so the server is honest:
    # display can fall back, while a histogram is not invented.
    assert layers[0]["histogram"] is None
    assert config["chrome"] is False, "the engine's own furniture stays hidden"


def test_config_reports_the_store_it_was_given(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("x", encoding="utf-8")
    config = config_from(
        data_dir=tmp_path, site_dir=site, store="acquisition.zarr", window=(5.0, 50.0)
    )
    assert config["layers"][0]["sources"] == ["/data/0/acquisition.zarr/|zarr2:"]
    assert config["layers"][0]["window"] == {"low": 5.0, "high": 50.0}


def test_config_includes_a_histogram_for_a_readable_store(tmp_path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("x", encoding="utf-8")
    data = np.arange(4 * 8 * 8, dtype=np.uint16).reshape(4, 8, 8)
    store = tmp_path / "sample.zarr"
    group = zarr.open_group(str(store), mode="w", zarr_format=2)
    group.create_array("0", data=data, chunks=data.shape)
    (store / ".zattrs").write_text(
        json.dumps({"multiscales": [{"datasets": [{"path": "0"}]}]}),
        encoding="utf-8",
    )
    config = config_from(data_dir=tmp_path, site_dir=site, store="sample.zarr")
    histogram = config["layers"][0]["histogram"]
    assert len(histogram["counts"]) == 64
    # One pixel short of the image on purpose: declared-but-unwritten ground
    # reads back as the store's fill value (zero here), so the measurement
    # drops that value to keep a half-written run from washing the window
    # out. The single genuine zero in this ramp goes uncounted with it —
    # the documented trade in contrast._samples.
    assert sum(histogram["counts"]) == data.size - 1


def test_tiles_of_one_channel_merge_into_a_single_layer(tmp_path):
    """A tiled acquisition is many stores, but not many layers.

    Several tiles of the same channel are one picture of one specimen taken in
    pieces, so they become one row that reads from all of them — the engine places
    each piece using the stage position recorded inside it. Asking the engine for
    one layer with many sources is also far less work than many layers, each
    needing its own setup and shader.
    """
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("x", encoding="utf-8")
    names = [
        "Mag5_Tile0_Ch488_FltEmpty_Sh1_Rot0.ome.zarr",
        "Mag5_Tile0_Ch647_FltEmpty_Sh1_Rot0.ome.zarr",
        "Mag5_Tile1_Ch488_FltEmpty_Sh1_Rot0.ome.zarr",
    ]
    config = config_from(data_dir=tmp_path, site_dir=site, store=names, window=(0.0, 100.0))
    layers = config["layers"]
    # Two channels across three stores, so two rows.
    assert [layer["name"] for layer in layers] == ["Ch488", "Ch647"]
    # The 488 row reads from both of its tiles; the 647 row from its one.
    by_name = {layer["name"]: layer for layer in layers}
    assert len(by_name["Ch488"]["sources"]) == 2
    assert len(by_name["Ch647"]["sources"]) == 1
    assert all(source.startswith("/data/0/") for source in by_name["Ch488"]["sources"])


def test_channels_are_coloured_only_when_overlaid(tmp_path):
    """One channel alone stays greyscale; 488 and 647 together go green/magenta."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("x", encoding="utf-8")
    alone = config_from(
        data_dir=tmp_path, site_dir=site, store="Tile0_Ch488.ome.zarr", window=(0.0, 1.0)
    )
    assert alone["layers"][0]["color"] is None

    together = config_from(
        data_dir=tmp_path,
        site_dir=site,
        store=["Tile0_Ch488.ome.zarr", "Tile0_Ch647.ome.zarr"],
        window=(0.0, 1.0),
    )
    green, magenta = (layer["color"] for layer in together["layers"])
    assert green == [0.0, 1.0, 0.4]
    assert magenta == [1.0, 0.2, 1.0]


def test_an_unreadable_target_file_is_never_overwritten(tmp_path):
    """The server says the file "has not been changed"; a save must keep that true.

    A file that cannot be read for a moment (locked by a virus scanner, say)
    used to be shown as an empty list, which the page then saved back over it,
    losing every target.
    """
    port, stop = _serve_tree(tmp_path)
    sidecar = tmp_path / "data" / "zmart-annotations.json"
    sidecar.write_text('{"version": 1, "annotations": [{"id": "p1", "type": "po', encoding="utf-8")
    before = sidecar.read_bytes()
    try:
        status, _, body = request(port, "/api/annotations")
        assert status == 500 and b"has not been changed" in body
        empty = json.dumps({"version": 1, "annotations": []}).encode()
        status, _, body = request(port, "/api/annotations", method="POST", body=empty)
        assert status == 409, body
        assert b"could not be read" in body
    finally:
        stop()
    assert sidecar.read_bytes() == before


def test_annotations_start_empty_and_round_trip_as_a_sidecar(serving):
    status, _, body = request(serving, "/api/annotations")
    assert status == 200
    assert json.loads(body) == {"version": 1, "annotations": []}
    document = {
        "version": 1,
        "annotations": [
            {"id": "p1", "type": "point", "point": [1, 2, 3], "description": "cell"},
            {
                "id": "b1",
                "type": "axis_aligned_bounding_box",
                "pointA": [1, 2, 3],
                "pointB": [4, 5, 6],
                "description": "",
            },
        ],
    }
    status, _, body = request(
        serving, "/api/annotations", method="POST", body=json.dumps(document).encode()
    )
    assert status == 200
    assert json.loads(body) == {
        **document,
        "annotations": [
            {**document["annotations"][0], "point": [1.0, 2.0, 3.0]},
            {
                **document["annotations"][1],
                "pointA": [1.0, 2.0, 3.0],
                "pointB": [4.0, 5.0, 6.0],
            },
        ],
    }
    assert json.loads(request(serving, "/api/annotations")[2]) == json.loads(body)


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"version": 1, "annotations": "not a list"},
        {"version": 1, "annotations": [{"id": "x", "type": "line"}]},
        {
            "version": 1,
            "annotations": [{"id": "x", "type": "point", "point": [float("inf")]}],
        },
    ],
)
def test_invalid_annotation_documents_are_rejected(serving, document):
    status, _, _ = request(
        serving,
        "/api/annotations",
        method="POST",
        body=json.dumps(document).encode(),
    )
    assert status == 400


def test_unknown_api_routes_are_404(serving):
    assert request(serving, "/api/nope")[0] == 404
    assert request(serving, "/api/nope", method="POST", body=b"{}")[0] == 404


def test_post_outside_the_api_is_404(serving):
    assert request(serving, "/index.html", method="POST", body=b"{}")[0] == 404


def test_serves_data_from_an_unresolved_directory(tmp_path):
    """The guard must compare like with like, or real stores 403.

    ``make_server`` is handed whatever path the caller has: a mapped network
    drive (``Z:\\...`` resolving to a UNC path), a symlink, or simply a path
    with a ``..`` in it. The traversal check resolves the *request* target, so
    unless the configured directory is resolved too, nothing under it is ever
    served — which is how a real acquisition folder fails while the demo works.
    """
    data = tmp_path / "data"
    data.mkdir()
    (data / "chunk").write_bytes(b"\xaa\xbb")
    unresolved = tmp_path / "data" / ".." / "data"

    server = make_server(port=0, data_dir=unresolved, site_dir=tmp_path, store="chunk")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, body = request(server.server_address[1], "/data/0/chunk")
    finally:
        server.shutdown()
        thread.join(timeout=5)
    assert status == 200
    assert body == b"\xaa\xbb"


def test_server_binds_localhost_only(tmp_path):
    """The command endpoint must not be reachable from the network."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("x", encoding="utf-8")
    server = make_server(port=0, data_dir=tmp_path, site_dir=site)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_config_is_worked_out_fresh_on_every_request(tmp_path):
    """A store written after the viewer opened must still be able to appear.

    During a smart-microscopy run the folder is still being written to. An answer
    prepared once when the server started could never mention an acquisition that
    arrived later, so the cheap part — looking to see what is there — has to happen
    per request. This checks the answer is rebuilt rather than handed back frozen.
    """
    site = tmp_path / "site"
    data = tmp_path / "data"
    site.mkdir()
    data.mkdir()
    (site / "index.html").write_text("x", encoding="utf-8")

    server = make_server(port=0, data_dir=data, site_dir=site, store="one.zarr")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        first = json.loads(request(port, "/api/config")[2])
        second = json.loads(request(port, "/api/config")[2])
        # Same content both times...
        assert first == second
        # ...but genuinely rebuilt, not the identical object served twice: the
        # handler calls a function rather than holding a fixed dict.
        assert callable(server.RequestHandlerClass.keywords["config"])
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_image_is_revalidated_by_the_browser_during_a_run(serving, tmp_path):
    """While the instrument is still writing, every shown piece was just checked.

    This is the half that matters for a smart-microscopy experiment. Nothing on
    disk is settled while a run is in progress, so a copy kept in the browser
    could quietly go on showing an old version of a region — and there would be
    nothing on screen to say it was old. The original answer was to forbid
    keeping anything (``no-store``), which bought the safety by re-sending every
    byte: zooming across a large live survey re-fetched and re-decoded the whole
    picture while the server was also busy patching commits, and the operator
    felt it as lag. The safety actually needed is not "hold nothing" but "never
    USE a held copy without asking" — which is precisely ``no-cache`` with a
    validator. An unchanged piece now costs a bodiless round trip instead of its
    bytes, and a stale picture remains impossible: every piece on screen was
    revalidated against the file's identity on its way there.
    """
    chunk = tmp_path / "data" / "demo.zarr" / "chunk"
    _a_settled_file(chunk)
    status, headers, body = request(serving, "/data/0/demo.zarr/chunk")
    assert status == 200 and body == b"\x01\x02\x03\x04"
    assert headers.get("Cache-Control") == "no-cache"
    validator = headers.get("ETag")
    assert validator, "a live piece must carry a validator to revalidate against"

    status, headers, body = request(
        serving,
        "/data/0/demo.zarr/chunk",
        extra={"If-None-Match": validator},
    )
    assert status == 304, "an unchanged piece should be answered without a body"
    assert body == b""
    assert headers.get("ETag") == validator


def test_a_piece_the_clock_cannot_vouch_for_is_not_given_a_validator(serving, tmp_path):
    """A just-written piece is served the old careful way: sent whole, kept by nobody.

    File identity leans on the modification stamp, and filesystems stamp files
    from a clock that ticks more coarsely than a writer writes — the same
    still-moving rule the table caches follow (see ``STAMPS_STILL_MOVING_NS``
    in ``zmart_viewer.live.record/shardlink.py``). A piece patched twice in one tick at the
    same size would carry the same identity, and a 304 against it would hand
    the browser exactly the stale picture all of this exists to prevent. So a
    piece still within the clock's reach of "now" gets no validator at all and
    ``no-store``, and only a settled one earns the cheap answer.
    """
    chunk = tmp_path / "data" / "demo.zarr" / "chunk"
    chunk.write_bytes(b"\x05\x06\x07\x08")
    status, headers, _ = request(serving, "/data/0/demo.zarr/chunk")
    assert status == 200
    assert headers.get("Cache-Control") == "no-store"
    assert "ETag" not in headers


def test_a_patched_piece_fails_revalidation_and_arrives_fresh(serving, tmp_path):
    """The other half of the bargain: a changed piece must never be 304'd.

    The validator is the file's own identity — stamp and size — so a patch that
    changes either retires every held copy at the next ask. The browser asks
    with the identity it holds, the answer is the new bytes, and the picture it
    paints is the picture on disk.
    """
    chunk = tmp_path / "data" / "demo.zarr" / "chunk"
    _a_settled_file(chunk)
    _, headers, _ = request(serving, "/data/0/demo.zarr/chunk")
    held = headers.get("ETag")
    assert held

    chunk.write_bytes(b"\x09\x0a\x0b\x0c")
    _a_settled_file(chunk, later=True)
    status, headers, body = request(
        serving,
        "/data/0/demo.zarr/chunk",
        extra={"If-None-Match": held},
    )
    assert status == 200, "a patched piece answered 304 would freeze a stale picture"
    assert body == b"\x09\x0a\x0b\x0c"
    assert headers.get("ETag") and headers.get("ETag") != held


def _a_settled_file(target, *, later: bool = False) -> None:
    """Age ``target`` past the clock's reach, so its identity can be trusted.

    ``later`` keeps the stamp distinct from the previous settling of the same
    file — two settlings a moment apart must not produce one identity.
    """
    stamp = time.time_ns() - (1_000_000_000 if later else 2_000_000_000)
    os.utime(target, ns=(stamp, stamp))


def test_image_may_be_kept_by_the_browser_once_the_data_is_finished(tmp_path):
    """Moving back over somewhere already seen in an old run must cost nothing.

    Nothing is writing to finished data, so nothing can change under us, and there
    is no reason to fetch a region twice. ``immutable`` tells the browser not even
    to check — no request at all — which is what makes an old acquisition feel
    light to move around in.
    """
    port, stop = _serve_tree(tmp_path, live=False)
    try:
        _, headers, _ = request(port, "/data/0/demo.zarr/chunk")
    finally:
        stop()
    cache = headers.get("Cache-Control", "")
    assert "immutable" in cache, cache
    # A year, not an hour. An operator may look through a finished run all day,
    # and anything shorter would start re-fetching partway through for no reason.
    assert int(cache.split("max-age=")[1].split(",")[0]) > 86_400, cache


@pytest.mark.parametrize("live", [True, False])
def test_a_store_description_is_never_kept_by_the_browser(tmp_path, live):
    """The files describing a store must be re-asked for every time, in both modes.

    A timelapse growing a frame rewrites its shape, and that shape is what tells the
    engine how far the data goes. A stale copy — even a few seconds old — would leave
    the engine believing the old length, so a frame sitting on disk would simply not
    appear, with nothing on screen to explain why.

    This holds even for finished data, where the pieces of image themselves may be
    kept for a year: the cost is one small round trip answered from memory, and it
    removes a whole class of confusing behaviour for very little.
    """
    port, stop = _serve_tree(tmp_path, live=live)
    try:
        _, describing, _ = request(port, "/data/0/demo.zarr/.zattrs")
    finally:
        stop()
    assert describing.get("Cache-Control") == "no-cache"


class TestClosingGivesTheMemoryBack:
    """Closing an acquisition should let go of what was remembered about it.

    Remembering is what keeps the viewer quick — a store's description is read once
    and thereafter only glanced at — but nothing is ever forgotten on its own. A
    session in which somebody opens a large folder, looks at it, closes it and opens
    the next one would otherwise hold on to every folder they had visited for as
    long as the viewer ran. "Close what you are not using" has to be advice the
    viewer actually honours, so these check that it does.
    """

    def _two_acquisitions(self, tmp_path):
        """A folder holding two acquisition types, so one can be closed."""
        site, data = tmp_path / "site", tmp_path / "data"
        site.mkdir()
        data.mkdir()
        (site / "index.html").write_text("<!doctype html><title>page</title>", encoding="utf-8")
        for name in ("overview_pos001.ome.zarr", "targetscan_cell001.ome.zarr"):
            store = data / name
            store.mkdir()
            group = zarr.open_group(str(store), mode="w", zarr_format=2)
            pixels = np.full((1, 2, 16, 16), 700, dtype=np.uint16)
            group.create_array("0", shape=pixels.shape, chunks=(1, 1, 16, 16), dtype="uint16")[
                :
            ] = pixels
            (store / ".zattrs").write_text(
                json.dumps(
                    {
                        "multiscales": [
                            {
                                "version": "0.4",
                                "axes": [
                                    {"name": "c", "type": "channel"},
                                    {"name": "z", "type": "space", "unit": "micrometer"},
                                    {"name": "y", "type": "space", "unit": "micrometer"},
                                    {"name": "x", "type": "space", "unit": "micrometer"},
                                ],
                                "datasets": [
                                    {
                                        "path": "0",
                                        "coordinateTransformations": [
                                            {"type": "scale", "scale": [1.0, 1.0, 0.5, 0.5]}
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
        return site, data

    @pytest.fixture
    def two_open(self, tmp_path):
        """A server with two acquisition types open, ready for one to be closed."""
        site, data = self._two_acquisitions(tmp_path)
        # Two acquisitions means two loads: one load is one dataset, so opening
        # both together would make them a single thing with nothing to close.
        server = make_server(
            port=0,
            data_dir=data,
            site_dir=site,
            loads=[
                {"stores": ["overview_pos001.ome.zarr"], "name": "overview"},
                {"stores": ["targetscan_cell001.ome.zarr"], "name": "targetscan"},
            ],
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            # Asking once is what makes the server read and remember these stores.
            # Without it there would be nothing to forget and the tests would pass
            # while proving nothing.
            status, _, body = request(port, "/api/config")
            assert status == 200
            assert "targetscan" in json.loads(body)["groups"]
            yield port, data
        finally:
            server.shutdown()
            thread.join(timeout=5)

    def _close(self, port, group):
        status, _, body = request(
            port,
            "/api/stores/close",
            method="POST",
            body=json.dumps({"group": group}).encode("utf-8"),
        )
        assert status == 200
        return json.loads(body)

    def test_the_description_is_forgotten(self, two_open):
        """What a store contains, remembered while reading it, is dropped on close."""
        from zmart_viewer.opening import open_folders as stores

        port, data = two_open
        closed = str(data / "targetscan_cell001.ome.zarr")
        assert any(key.startswith(closed) for key in stores._attrs_cache), (
            "the store should have been read and remembered before it was closed"
        )
        self._close(port, "targetscan")
        assert not any(key.startswith(closed) for key in stores._attrs_cache)

    def test_what_stays_open_is_still_remembered(self, two_open):
        """Forgetting must be confined to what was closed.

        Dropping too much would be quietly expensive rather than wrong: the
        acquisition still on screen would be read from disk all over again.
        """
        from zmart_viewer.opening import open_folders as stores

        port, data = two_open
        kept = str(data / "overview_pos001.ome.zarr")
        self._close(port, "targetscan")
        assert any(key.startswith(kept) for key in stores._attrs_cache)

    def test_the_files_served_to_the_browser_are_forgotten(self, two_open):
        """The small files handed to the page are held in memory too."""
        from zmart_viewer.serving.server import _Handler

        port, data = two_open
        closed = str(data / "targetscan_cell001.ome.zarr")
        # Dataset 1: the second load above, which is the targetscan.
        request(port, "/data/1/targetscan_cell001.ome.zarr/.zattrs")
        assert any(key.startswith(closed) for key in _Handler._described)
        self._close(port, "targetscan")
        assert not any(key.startswith(closed) for key in _Handler._described)

    def test_closing_says_which_images_went(self, tmp_path):
        """The server can only forget what the library tells it was closed."""
        from zmart_viewer.opening.open_folders import Library

        _, data = self._two_acquisitions(tmp_path)
        library = Library()
        number = library.open(data, names=["targetscan_cell001.ome.zarr"], name="targetscan")
        closed = library.close_group("targetscan", folder=number)
        assert [name for _, _, name in closed] == ["targetscan_cell001.ome.zarr"]
        assert [root for _, root, _ in closed] == [data.resolve()]


def test_the_page_itself_is_never_taken_from_the_cache(tmp_path):
    """A reload must fetch today's viewer, not the one the browser kept.

    The page names its own code by content -- ``index-5OLplHed.js`` -- so the
    code files can be cached forever and a rebuild simply produces new names.
    That only works if the page naming them is not cached: hold on to
    yesterday's ``index.html`` and the browser asks for a bundle that no
    longer exists, which is a 404 and a viewer that either does not start or,
    worse, keeps running yesterday's build with today's data.

    That is not hypothetical. An operator reported settings being lost on the
    2-D/3-D switch, and their screenshot carried a button removed from the
    source the day before: the tab was running a bundle the folder no longer
    held (2026-08-21).
    """
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text(
        "<!doctype html><script src=/assets/index-abc.js></script>", encoding="utf-8"
    )
    assets = site / "assets"
    assets.mkdir()
    (assets / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
    data = tmp_path / "data"
    (data / "demo.zarr").mkdir(parents=True)
    (data / "demo.zarr" / ".zattrs").write_text('{"multiscales": []}', encoding="utf-8")

    server = make_server(port=0, data_dir=data, site_dir=site, store="demo.zarr")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def headers_for(path: str) -> str:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1])
        try:
            connection.request("GET", path)
            answer = connection.getresponse()
            answer.read()
            return answer.getheader("Cache-Control") or ""
        finally:
            connection.close()

    try:
        page = headers_for("/")
        code = headers_for("/assets/index-abc.js")
    finally:
        server.shutdown()
        thread.join(timeout=5)

    assert "no-store" in page.lower(), (
        f"the page came back with Cache-Control {page!r}, so a browser may "
        "keep it and go on asking for a bundle that has been rebuilt away"
    )
    assert "no-store" not in code.lower(), (
        f"the code came back with Cache-Control {code!r}: it is named by its "
        "own content, so refusing to cache it makes every reload pay for the "
        "whole viewer again"
    )


def test_a_browser_that_hung_up_is_noticed_before_its_piece_is_built():
    """A piece is only worth building while somebody is still waiting for it.

    A burst of zooming leaves thousands of requests whose page has moved on; each
    used to build its piece anyway, and the queue behind them stalled the server.
    """
    import socket

    from zmart_viewer.serving.server import _peer_has_gone

    ours, theirs = socket.socketpair()
    try:
        ours.settimeout(7.0)
        assert _peer_has_gone(ours) is False
        theirs.close()
        assert _peer_has_gone(ours) is True
        assert ours.gettimeout() == 7.0
    finally:
        ours.close()


def test_a_request_still_unread_is_not_mistaken_for_a_hang_up():
    """Bytes the browser sent but nobody has read yet mean it is still there."""
    import socket

    from zmart_viewer.serving.server import _peer_has_gone

    ours, theirs = socket.socketpair()
    try:
        theirs.sendall(b"GET / HTTP/1.1\r\n")
        assert _peer_has_gone(ours) is False
    finally:
        ours.close()
        theirs.close()


# --- names that have to be spelled for the address bar (review M4) ------------


@pytest.mark.parametrize("name", ["my image.zarr", "Überblick.zarr", "a#b.zarr", "50%.zarr"])
def test_an_image_whose_name_needs_spelling_out_is_drawn(tmp_path, name):
    """A space, an umlaut, a ``#`` or a ``%`` in a name must not cost the picture.

    The address the page is handed spells such a name out (``my%20image``), the
    browser asks for exactly that, and the server reads it back as the name.
    Before, the addresses were handed out raw -- a browser cut ``a#b`` at the
    ``#`` -- and the server never read a spelled-out name back, so each of
    these answered 404.
    """
    from test_open_and_close import _store

    data = tmp_path / "data"
    _store(data / name, channels=1)
    server = make_server(port=0, data_dir=data, store=name, live=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        config = json.loads(request(port, "/api/config")[2])
        address = config["layers"][0]["sources"][0].split("|", 1)[0]
        assert "#" not in address and " " not in address
        status, _, body = request(port, f"{address}.zattrs")
        assert status == 200
        assert json.loads(body)["multiscales"][0]["datasets"][0]["path"] == "0"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_a_spelled_out_way_out_of_the_folder_is_still_refused(serving):
    """Reading a spelled-out name back must not reopen the way out of the folder."""
    for sneaky in ("/data/0/%2e%2e/outside.txt", "/data/0/demo.zarr/%2e%2e%5c..%5coutside.txt"):
        status, _, body = request(serving, sneaky)
        assert status in (403, 404), sneaky
        assert body != b"secret"


# --- what the browser may keep (review S6) -------------------------------------


@pytest.fixture
def a_built_site(tmp_path):
    """A site with a hashed bundle, a worker with a fixed name, and the page."""
    site = tmp_path / "site"
    (site / "assets").mkdir(parents=True)
    (site / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (site / "assets" / "index-abc.js").write_text("1", encoding="utf-8")
    (site / "async_computation.bundle.js").write_text("2", encoding="utf-8")
    (site / "build-manifest.json").write_text("{}", encoding="utf-8")
    server = make_server(port=0, site_dir=site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    "path, kept",
    [
        ("/assets/index-abc.js", "public, max-age=31536000, immutable"),
        ("/async_computation.bundle.js", "no-cache"),
        ("/build-manifest.json", "no-cache"),
        ("/no-such-file.js", "no-cache"),
        ("/assets/no-such-file.js", "no-cache"),
        ("/", "no-store"),
        ("/?v=2", "no-store"),
        ("/index.html?v=2", "no-store"),
    ],
)
def test_only_files_named_by_their_content_are_kept_for_good(a_built_site, path, kept):
    """A file kept "for good" must change its name when it changes.

    Vite names everything under ``assets/`` by its content, so those may be
    kept for a year. A worker with a fixed name, the build manifest, or a
    404 kept that long outlives an upgrade: the browser goes on running the
    old worker beside the new page.
    """
    assert request(a_built_site, path)[1]["Cache-Control"] == kept


# --- requests that are not what they say (review N1) ---------------------------


def raw(port: int, text: bytes, *, wait: float = 5.0) -> bytes:
    """Send bytes exactly as given and return whatever comes back within ``wait`` seconds."""
    import socket

    with socket.create_connection(("127.0.0.1", port), timeout=wait) as connection:
        connection.sendall(text)
        received = b""
        try:
            while chunk := connection.recv(65536):
                received += chunk
                head, gap, rest = received.partition(b"\r\n\r\n")
                if gap:
                    lengths = [
                        int(line.split(b":", 1)[1])
                        for line in head.split(b"\r\n")
                        if line.lower().startswith(b"content-length:")
                    ]
                    if lengths and len(rest) >= lengths[0]:
                        break
        except TimeoutError:
            pass
        return received


@pytest.mark.parametrize("length", [b"abc", b"-1", b"1e9"])
def test_a_length_that_is_not_a_length_is_answered(serving, length):
    """A broken ``Content-Length`` gets a plain refusal, not a dropped or hung connection."""
    answer = raw(
        serving,
        b"POST /api/annotations HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\nContent-Length: " + length + b"\r\n\r\n{}",
    )
    assert answer.startswith(b"HTTP/1.1 400"), answer[:80]


def test_a_body_that_is_not_text_is_answered(serving):
    body = b'{"version": 1, "annotations": "\xff\xfe"}'
    status, _, answer = request(serving, "/api/annotations", method="POST", body=body)
    assert status == 400
    assert "JSON" in json.loads(answer)["error"]


def test_head_asks_for_the_embedding_module_too(serving):
    status, headers, body = request(serving, "/embedding.js", method="HEAD")
    assert status == 200
    assert int(headers["Content-Length"]) > 0
    assert body == b""


def test_head_on_the_change_stream_does_not_open_it(serving):
    """A HEAD is answered with headers and ended; it never becomes an endless stream."""
    import time

    started = time.perf_counter()
    got = raw(serving, b"HEAD /api/events HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n", wait=5)
    head, _, rest = got.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200")
    assert b"text/event-stream" in head
    assert rest == b""
    assert time.perf_counter() - started < 3, "the stream was opened and held"


def test_head_on_a_built_answer_sends_no_body(tmp_path):
    """Pieces built on request (here: coverage) answer HEAD with headers alone."""
    from test_open_and_close import _store

    data = tmp_path / "data"
    _store(data / "one.ome.zarr", channels=1)
    server = make_server(
        port=0, data_dir=data, store="one.ome.zarr", transparent_background=True, live=True
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        config = json.loads(request(port, "/api/config")[2])
        coverage = config["layers"][0]["coverageSources"][0].split("|", 1)[0] + "zarr.json"
        got = raw(port, f"HEAD {coverage} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode(), wait=2)
        head, _, rest = got.partition(b"\r\n\r\n")
        assert head.startswith(b"HTTP/1.1 200"), head
        assert rest == b"", "a HEAD answer carried a body, which the next answer would be read as"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_a_publication_that_cannot_be_made_now_is_answered(tmp_path, monkeypatch):
    """A publisher that has been closed says so rather than dropping the connection."""
    from zmart_viewer.views.publishing import PublishedFolders

    def closed(self, publications):
        raise RuntimeError("the publisher has been closed")

    monkeypatch.setattr(PublishedFolders, "announce", closed)
    server = make_server(port=0, data_dir=tmp_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, body = request(
            server.server_address[1],
            "/api/announce",
            method="POST",
            body=json.dumps({"publications": []}).encode(),
        )
        assert status == 503
        assert "closed" in json.loads(body)["error"]
    finally:
        server.shutdown()
        thread.join(timeout=5)


# --- the coverage the page is told about is served (review S5) -----------------


def test_every_coverage_address_handed_out_is_served(tmp_path):
    """A published acquisition without a named view, on an opaque background.

    ``/api/config`` hands out coverage for it, so the server must serve it;
    before, it answered 403 unless the background was transparent.
    """
    from test_published_depth import CANVAS, composition
    from test_published_transfer import write_position

    names = ["flat.ome.zarr", "stack.ome.zarr"]
    for name, depth in zip(names, (1, 3)):
        write_position(tmp_path, name, 0 if depth == 1 else 256, 1000, depth=depth, frames=1)
    server = make_server(port=0, data_dir=tmp_path, live=True, transparent_background=False)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        payload = {
            "path": str(tmp_path),
            "source_revisions": dict.fromkeys(names, 1),
            "composition": composition(names),
            "canvas": CANVAS,
        }
        status, _, body = request(port, "/api/stores/open", "POST", json.dumps(payload).encode())
        assert status == 200, body
        config = json.loads(request(port, "/api/config")[2])
        handed_out = [
            source.split("|", 1)[0] + "zarr.json"
            for row in config["layers"]
            for source in row.get("coverageSources", [])
        ]
        assert handed_out, "this setup is meant to hand out coverage"
        assert [request(port, address)[0] for address in handed_out] == [200] * len(handed_out)
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_coverage_nobody_was_told_about_is_not_served(serving):
    """The other half: a plain store on an opaque background has no coverage to serve."""
    assert request(serving, "/data/0/demo.zarr/__zmart_coverage__/zarr.json")[0] == 403


# --- the viewer's own folders in the home folder (review N3) -------------------


def test_folders_left_by_a_viewer_that_was_killed_are_cleared_at_start(tmp_path, monkeypatch):
    """A viewer that was killed never ran its shutdown, so its folder stayed for good."""
    from pathlib import Path

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    left = tmp_path / ".zmart-viewer" / "scenes" / "session-killed"
    (left / "a.zmartview.zarr").mkdir(parents=True)
    server = make_server(port=0, data_dir=tmp_path)
    server.server_close()
    assert not left.exists()


def test_the_folder_of_a_viewer_still_running_is_left_alone(tmp_path, monkeypatch):
    """Two viewers may run at once; the second must not clear the first one's scenes."""
    from pathlib import Path

    from test_open_and_close import _store

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    data = tmp_path / "run"
    for name in ("pos1.ome.zarr", "pos2.ome.zarr"):
        _store(data / name, channels=1)
    first = make_server(port=0, data_dir=tmp_path)
    thread = threading.Thread(target=first.serve_forever, daemon=True)
    thread.start()
    try:
        port = first.server_address[1]
        opening = json.dumps({"path": str(data)}).encode()
        status, _, body = request(port, "/api/stores/open", "POST", opening)
        assert status == 200, body
        sessions = list((tmp_path / ".zmart-viewer" / "scenes").glob("session-*"))
        assert len(sessions) == 1
        second = make_server(port=0, data_dir=tmp_path)
        second.server_close()
        assert sessions[0].is_dir(), "a running viewer's scenes were cleared by another"
        assert any(sessions[0].iterdir())
    finally:
        first.shutdown()
        thread.join(timeout=5)
    assert not sessions[0].exists(), "a viewer that stops tidies its own folder away"
