"""Saved-view, recovery and frontend selection contracts for the 0.3 release."""

import gzip
import json
import subprocess
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import pytest
from test_view_sampling import write_tile

from zmart_viewer import coverage, pieces
from zmart_viewer.projections import write_projection
from zmart_viewer.published import PublishedTransfer
from zmart_viewer.server import make_server
from zmart_viewer.views import ViewSet


def test_legacy_baked_levels_beyond_original_pyramid_remain_readable(tmp_path):
    source = tmp_path / "positions"
    source.mkdir()
    write_tile(source, "p.ome.zarr", np.full((1, 1, 1, 8, 8), 7, dtype="uint16"))
    output = PublishedTransfer(tmp_path / "legacy.ome.zarr", piece=1)
    try:
        output.publish(source, {"p.ome.zarr": 1}, {"x_um": [0, 8], "y_um": [0, 8]})
        level = output.composer().mosaic.levels
        chunk = output._shown / str(level) / "c/0/0/0"
        assert chunk.is_file()
        assert pieces.built_bytes_behind(output._shown, f"{level}/c/0/0/0") == chunk.read_bytes()
    finally:
        output.close()
        pieces.forget(output._shown)


def test_all_view_subsets_and_independent_runs():
    module = Path(__file__).resolve().parents[1] / "app/page/src/named-views.js"
    run = subprocess.run(
        [
            "node",
            "--input-type=module",
            "--eval",
            f"""
      import assert from 'node:assert/strict';
      const m = await import({json.dumps(module.as_uri())});
      const row = (type, group='run A') => ({{group, view:{{acquisition:'overview', type,
        ...(type==='projection' ? {{method:'max'}} : {{}})}}}});
      for(let mask=1; mask<8; mask++) {{
        const types=['slice','top','projection'].filter((_,i) => mask & (1<<i));
        const rows=types.map(type => row(type));
        const choices=m.viewChoices(rows), selected=m.selectedViews(rows);
        assert.equal(choices.length,1);
        assert.equal(choices[0].keys.length,types.length);
        const shown=rows.filter(r => m.inSelectedView(r, selected));
        assert.equal(shown.length,1);
        assert.equal(shown[0].view.type, types[0]);
      }}
      const rows=[row('top'),row('slice','run B')];
      assert.equal(m.viewChoices(rows).length,2);
      assert.equal(rows.filter(r=>m.inSelectedView(r,m.selectedViews(rows))).length,2);
    """,
        ],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr


def test_saved_sparse_black_projection_coverage_at_every_level(tmp_path):
    tile = write_tile(tmp_path, "p.ome.zarr", np.zeros((2, 2, 3, 8, 8), dtype="uint16"))
    regions = [
        {
            "frame": 1,
            "channel": 1,
            "origin": {"z": 0, "y": 1, "x": 1},
            "shape": {"z": 3, "y": 3, "x": 3},
        }
    ]
    output = write_projection(tile.store, tmp_path / "p_min.ome.zarr", "min", regions=regions)
    assert coverage.requires_geometry(output)
    for level, count in enumerate((9, 4, 1)):
        for t in range(2):
            for c in range(2):
                raw = coverage.answer(output, f"{level}/c/{t}/{c}/0/0/0")
                mask = np.frombuffer(gzip.decompress(raw), dtype="uint8")
                assert np.count_nonzero(mask) == (count if (t, c) == (1, 1) else 0)


def _request(address, route, payload=None):
    request = Request(
        address + route,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def test_partial_publication_notifies_and_retries_without_blocking_config(tmp_path, monkeypatch):
    original = tmp_path / "positions"
    original.mkdir()
    write_tile(original, "p.ome.zarr", np.ones((1, 1, 2, 8, 8), dtype="uint16"))
    payload = {
        "path": str(original),
        "bake": True,
        "canvas": {"x_um": [0, 8], "y_um": [0, 8]},
        "source_revisions": {"p.ome.zarr": 1},
        "composition": {"regions": "complete", "order": ["p.ome.zarr"]},
        "views": {"path": str(tmp_path / "view"), "acquisition": "a"},
    }
    server = make_server(port=0, data_dir=tmp_path, live=True, allow_open=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    address = f"http://127.0.0.1:{server.server_address[1]}"
    entered, release = threading.Event(), threading.Event()
    responses = []
    operation = None
    try:
        status, body = _request(address, "/api/stores/open", payload)
        assert status == 200, body
        for options in (
            {"modes": ["slice"]},
            {"projection_path": str(tmp_path / "elsewhere")},
        ):
            bad = {**payload, "views": {**payload["views"], **options}}
            assert _request(address, "/api/stores/open", bad)[0] == 400
        announcements = server.RequestHandlerClass.keywords["announcements"]
        notices = announcements.listen()
        declare = PublishedTransfer._declare_levels

        def failing(output, *args, **kwargs):
            result = declare(output, *args, **kwargs)
            if output._shown.name.endswith("_top.zmartview.zarr"):
                entered.set()
                assert release.wait(10), "test failed to release the publisher"
                raise OSError("injected bake failure")
            return result

        monkeypatch.setattr(PublishedTransfer, "_declare_levels", failing)
        payload["source_revisions"]["p.ome.zarr"] = 2
        publication = {key: payload[key] for key in ("path", "source_revisions", "composition")}
        operation = threading.Thread(
            target=lambda: responses.append(
                _request(address, "/api/announce", {"publications": [publication]})
            )
        )
        operation.start()
        assert entered.wait(10)
        started = time.perf_counter()
        status, body = _request(address, "/api/config")
        config_ms = (time.perf_counter() - started) * 1000
        assert status == 200, body
        assert time.perf_counter() - started < 1
        for path in ("zarr.json", "0/zarr.json", "__zmart_coverage__/zarr.json"):
            assert _request(address, f"/data/0/a_top.zmartview.zarr/{path}")[0] == 503
        release.set()
        operation.join(10)
        assert responses[0][0] == 503
        assert notices.get(timeout=1) is not None
        announcements.stop_listening(notices)
        snapshots = [
            json.loads(
                (tmp_path / "view" / f"a_{mode}.zmartview.zarr" / "publication.json").read_text()
            )
            for mode in ("slice", "top")
        ]
        assert [s["revision"] for s in snapshots] == [2, 1]
        monkeypatch.setattr(PublishedTransfer, "_declare_levels", declare)
        assert _request(address, "/api/announce", {"publications": [publication]})[0] == 200
        snapshots = [
            json.loads(
                (tmp_path / "view" / f"a_{mode}.zmartview.zarr" / "publication.json").read_text()
            )
            for mode in ("slice", "top")
        ]
        assert [s["revision"] for s in snapshots] == [2, 2]
        assert not list((tmp_path / "view").rglob("pending.json"))
        print(
            {
                "config_during_bake_ms": round(config_ms),
                "partial_revisions": [2, 1],
                "retry_revisions": [2, 2],
            }
        )
    finally:
        release.set()
        if operation:
            operation.join(10)
        server.shutdown()
        server.server_close()
        worker.join(5)


def test_initial_failed_view_reopens_and_recovers(tmp_path, monkeypatch):
    source = tmp_path / "positions"
    source.mkdir()
    write_tile(source, "p.ome.zarr", np.ones((1, 1, 2, 8, 8), dtype="uint16"))
    view = ViewSet(tmp_path / "view", acquisition="a", piece=4)
    declare = PublishedTransfer._declare_levels

    def fail(output, *args, **kwargs):
        declare(output, *args, **kwargs)
        raise OSError("injected initial failure")

    monkeypatch.setattr(PublishedTransfer, "_declare_levels", fail)
    with pytest.raises(OSError):
        view.publish(
            source,
            {"p.ome.zarr": 1},
            {"x_um": [0, 8], "y_um": [0, 8]},
            composition={"regions": "complete", "order": ["p.ome.zarr"]},
        )
    view.close()
    monkeypatch.setattr(PublishedTransfer, "_declare_levels", declare)
    reopened = ViewSet(tmp_path / "view", acquisition="a")
    try:
        reopened.publish(
            source,
            {"p.ome.zarr": 1},
            {"x_um": [0, 8], "y_um": [0, 8]},
            composition={"regions": "complete", "order": ["p.ome.zarr"]},
        )
        assert reopened.revision == 2
        recovered = reopened.outputs["a_slice.zmartview.zarr"]
        assert recovered._piece == 4
        for level in range(recovered.composer().mosaic.levels):
            metadata = json.loads((recovered._shown / str(level) / "zarr.json").read_text())
            assert metadata == json.loads(recovered.composer().array_json(level))
    finally:
        reopened.close()
