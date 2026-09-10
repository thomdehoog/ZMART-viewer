"""Durable ownership and rejected updates, independent of an open handle's cache."""

import json
from pathlib import Path

import numpy as np
import pytest
from test_view_sampling import write_tile

from zmart_viewer import views
from zmart_viewer.library import Library
from zmart_viewer.published import PublishedFolders

CANVAS = {"x_um": [0, 8], "y_um": [0, 8]}
COMPOSITION = {"regions": "complete", "order": ["p.ome.zarr"]}


def test_named_identity_never_adopts_unknown_legacy_geometry():
    from zmart_viewer.library import _same_acquisition

    assert not _same_acquisition(None, "a")
    assert not _same_acquisition("a", None)
    assert _same_acquisition(None, ((), None))
    assert _same_acquisition("a", "a")


@pytest.mark.parametrize("interrupted", [False, True])
def test_committed_revision_wins_over_stale_handle_and_late_failure(
    tmp_path, monkeypatch, interrupted
):
    positions = tmp_path / "positions"
    positions.mkdir()
    options = dict(
        acquisition="a",
        modes=(),
        projections=("sum",),
        projection_folder=tmp_path / "projections",
        piece=4,
    )
    current = views.ViewSet(tmp_path / "view", **options)
    older = None
    try:
        write_tile(positions, "p.ome.zarr", np.full((1, 1, 2, 8, 8), 3, dtype="uint16"))
        current.publish(positions, {"p.ome.zarr": 1}, CANVAS, composition=COMPOSITION)
        older = views.ViewSet(tmp_path / "view", **options)
        write_tile(positions, "p.ome.zarr", np.full((1, 1, 2, 8, 8), 7, dtype="uint16"))
        if interrupted:
            unlink = Path.unlink

            def fail_pending(path, *args, **kwargs):
                if path.name == "pending.json":
                    raise PermissionError("injected after durable commit")
                return unlink(path, *args, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(Path, "unlink", fail_pending)
                with pytest.raises(PermissionError, match="durable commit"):
                    current.publish(positions, {"p.ome.zarr": 2}, CANVAS, composition=COMPOSITION)
        else:
            current.publish(positions, {"p.ome.zarr": 2}, CANVAS, composition=COMPOSITION)
        output = next(iter(current.outputs.values()))
        state = json.loads((output._shown / "publication.json").read_text())
        assert state["composition"]["original_revisions"] == {"p.ome.zarr": 2}
        for handle in (older, current):
            with monkeypatch.context() as patch:
                patch.setattr(
                    views,
                    "write_projection",
                    lambda *a, **k: pytest.fail("Rejected snapshot wrote pixels"),
                )
                with pytest.raises(ValueError, match="regress"):
                    handle.publish(positions, {"p.ome.zarr": 1}, CANVAS, composition=COMPOSITION)
        # Equal-revision recovery must preserve the committed result, not roll it back.
        older.publish(positions, {"p.ome.zarr": 2}, CANVAS, composition=COMPOSITION)
        assert next(iter(older.outputs.values())).composer().values_for(0, 0, 0, 0)[0, 0] == 14
    finally:
        current.close()
        if older:
            older.close()


@pytest.mark.parametrize("problem", ["preview", "changed_canvas", "invalid_canvas"])
def test_rejected_contract_does_not_create_projection_products(tmp_path, monkeypatch, problem):
    positions = tmp_path / "positions"
    positions.mkdir()
    write_tile(positions, "p.ome.zarr", np.ones((1, 1, 2, 8, 8), dtype="uint16"))
    options = dict(
        acquisition="a",
        modes=(),
        projections=("sum",),
        projection_folder=tmp_path / "projections",
        piece=4,
    )
    view = views.ViewSet(tmp_path / "view", **options)
    view.publish(positions, {"p.ome.zarr": 1}, CANVAS, composition=COMPOSITION)
    canvas = CANVAS
    if problem == "preview":
        view.close()
        snapshot = tmp_path / "view/a_sum.zmartview.zarr/publication.json"
        state = json.loads(snapshot.read_text())
        del state["composition"]["original_revisions"]
        snapshot.write_text(json.dumps(state))
        # Saved pixels remain readable; only further live publication is refused.
        library = Library()
        library.open(tmp_path / "view")
        assert library.entries()
        view = views.ViewSet(tmp_path / "view", **options)
    else:
        canvas = {**CANVAS, "x_um": [0, 16] if problem == "changed_canvas" else [0, float("nan")]}
    monkeypatch.setattr(
        views, "write_projection", lambda *a, **k: pytest.fail("Rejected contract wrote pixels")
    )
    try:
        with pytest.raises(ValueError, match="a_sum.zmartview.zarr|canvas"):
            view.publish(positions, {"p.ome.zarr": 2}, canvas, composition=COMPOSITION)
    finally:
        view.close()


def test_one_original_folder_has_one_owner_per_destination(tmp_path, monkeypatch):
    positions = tmp_path / "positions"
    positions.mkdir()
    write_tile(positions, "p.ome.zarr", np.ones((1, 1, 2, 8, 8), dtype="uint16"))
    published = PublishedFolders(Library())
    options = dict(canvas=CANVAS, versions={"p.ome.zarr": 1}, composition=COMPOSITION, bake=False)
    try:
        published.open(
            positions, **options, views={"path": str(tmp_path / "view"), "acquisition": "targets"}
        )
        with monkeypatch.context() as patch:
            patch.setattr(
                views.ViewSet, "publish", lambda *a, **k: pytest.fail("Alias caused a publication")
            )
            with pytest.raises(ValueError, match="owner"):
                published.open(
                    positions,
                    **options,
                    views={"path": str(tmp_path / "view"), "acquisition": "targts"},
                )
        # Independent output folders are legitimate, not a global ownership collision.
        published.open(
            positions,
            **options,
            views={"path": str(tmp_path / "another"), "acquisition": "targets"},
        )
        assert len(published.views) == 2
    finally:
        published.close()


def test_config_is_observational_while_automatic_publication_waits(tmp_path, monkeypatch):
    import threading
    from urllib.request import urlopen

    from zmart_viewer.server import make_server

    positions = tmp_path / "positions"
    positions.mkdir()
    write_tile(positions, "p.ome.zarr", np.ones((1, 1, 2, 8, 8), dtype="uint16"))
    server = make_server(
        port=0,
        data_dir=positions,
        live=True,
        bake=True,
        canvas=CANVAS,
        loads=[{"path": str(positions)}],
    )
    publisher = server.RequestHandlerClass.keywords["scratch"]["published"]
    output = next(iter(publisher.views.values()))[0]
    initial = output.revision
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    publish = output.publish
    callers = []

    def slow(*args, **kwargs):
        callers.append(threading.current_thread().name)
        entered.set()
        assert release.wait(10), "test did not release publication"
        result = publish(*args, **kwargs)
        finished.set()
        return result

    monkeypatch.setattr(output, "publish", slow)
    write_tile(positions, "next.ome.zarr", np.ones((1, 1, 2, 8, 8), dtype="uint16"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert entered.wait(5)
        for _ in range(3):
            with urlopen(
                f"http://127.0.0.1:{server.server_address[1]}/api/config", timeout=3
            ) as response:
                assert json.load(response)["layers"]
        assert output.revision == initial
        assert callers == ["zmart-folder-watch"]
        release.set()
        assert finished.wait(5)
        assert output.revision > initial
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(5)


def test_failing_automatic_owner_does_not_block_other_publications(tmp_path, monkeypatch, caplog):
    published = PublishedFolders(Library())
    try:
        for name in ("a", "b"):
            source = tmp_path / name
            source.mkdir()
            write_tile(source, "p.ome.zarr", np.ones((1, 1, 2, 8, 8), dtype="uint16"))
            published.open(source, canvas=CANVAS)
        failed, healthy = [item[0] for item in published.views.values()]
        revision = healthy.revision

        def fail(*args, **kwargs):
            raise OSError("fixture source temporarily unavailable")

        monkeypatch.setattr(failed, "publish", fail)
        write_tile(tmp_path / "b", "next.ome.zarr", np.ones((1, 1, 2, 8, 8), dtype="uint16"))
        published.refresh()
        assert healthy.revision > revision
        assert "fixture source temporarily unavailable" in caplog.text
    finally:
        published.close()


def test_legacy_publisher_does_not_substitute_named_entries_in_same_root(tmp_path):
    library = Library()
    published = PublishedFolders(library)
    write_tile(tmp_path, "p.ome.zarr", np.ones((1, 1, 2, 8, 8), dtype="uint16"))
    try:
        legacy = published.open(tmp_path, canvas=CANVAS, versions={"p.ome.zarr": 1})
        named = published.open(
            tmp_path,
            canvas=CANVAS,
            versions={"p.ome.zarr": 1},
            composition=COMPOSITION,
            bake=False,
            views={"path": str(tmp_path), "acquisition": "a"},
        )
        entries = published.entries(library.entries())
        assert [(n, name) for n, _, name in entries if n == named] == [
            (named, "a_slice.zmartview.zarr"),
            (named, "a_top.zmartview.zarr"),
        ]
        assert len([n for n, _, _ in entries if n == legacy]) == 1
    finally:
        published.close()
