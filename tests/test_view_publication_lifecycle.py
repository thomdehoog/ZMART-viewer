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


@pytest.mark.parametrize("error", [OSError, KeyError])
def test_failing_automatic_owner_does_not_block_other_publications(
    tmp_path, monkeypatch, caplog, error
):
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
            raise error("fixture source temporarily unavailable")

        monkeypatch.setattr(failed, "publish", fail)
        write_tile(tmp_path / "b", "next.ome.zarr", np.ones((1, 1, 2, 8, 8), dtype="uint16"))
        published.refresh()
        assert healthy.revision > revision
        assert "fixture source temporarily unavailable" in caplog.text
    finally:
        published.close()


def test_source_owner_survives_registry_restart(tmp_path):
    source = tmp_path / "positions"
    source.mkdir()
    write_tile(source, "p.ome.zarr", np.ones((1, 1, 2, 8, 8), dtype="uint16"))
    options = dict(canvas=CANVAS, versions={"p.ome.zarr": 1}, composition=COMPOSITION, bake=False)
    for name in ("targets", "targts", "targets"):
        published = PublishedFolders(Library())
        try:
            request = dict(**options, views={"path": str(tmp_path / "view"), "acquisition": name})
            if name == "targts":
                with pytest.raises(ValueError, match="owner.*targets"):
                    published.open(source, **request)
            else:
                published.open(source, **request)
        finally:
            published.close()
    assert not list((tmp_path / "view").glob("targts_*.zmartview.zarr/publication.json"))


def test_split_legacy_dataset_never_inherits_another_datasets_publication(tmp_path):
    import zarr

    from zmart_viewer.published import STACK_STORE

    write_tile(tmp_path, "a.ome.zarr", np.ones((1, 1, 2, 8, 8), dtype="uint16"))
    tile = write_tile(tmp_path, "b.ome.zarr", np.full((1, 1, 2, 8, 8), 9, dtype="uint16"))
    group = zarr.open_group(str(tile.store), mode="r+")
    attrs = dict(group.attrs)
    for level in attrs["ome"]["multiscales"][0]["datasets"]:
        level["coordinateTransformations"][0]["scale"][-2:] = [
            2 * v for v in level["coordinateTransformations"][0]["scale"][-2:]
        ]
    group.attrs.update(attrs)
    library = Library()
    library.open(tmp_path, names=["a.ome.zarr"])
    published = PublishedFolders(library)
    try:
        owner = published.open(
            tmp_path,
            canvas=CANVAS,
            versions={"a.ome.zarr": 1},
            bake=False,
            composition={"regions": "complete", "order": ["a.ome.zarr"]},
        )
        library.open(tmp_path, names=["b.ome.zarr"])
        entries = published.entries(library.entries())
        other = next(n for n, _, name in entries if name == "b.ome.zarr")
        assert other != owner
        assert [(n, name) for n, _, name in entries] == [
            (owner, STACK_STORE),
            (other, "b.ome.zarr"),
        ]
        assert published.source_revision(other, "b.ome.zarr") is None
        assert published.source_depth(other, "b.ome.zarr") is None
        library.close(owner)
        assert [(n, name) for n, _, name in published.entries(library.entries())] == [
            (other, "b.ome.zarr")
        ]
    finally:
        published.close()


@pytest.mark.parametrize("problem", ["dtype", "xy", "overflow"])
def test_rejected_projection_leaves_only_previously_committed_products(tmp_path, problem):
    import zarr

    source = tmp_path / "positions"
    source.mkdir()
    write_tile(source, "p.ome.zarr", np.ones((1, 1, 2, 8, 8), dtype="uint16"))
    view = views.ViewSet(
        tmp_path / "view",
        acquisition="a",
        modes=(),
        projections=("sum" if problem == "overflow" else "max",),
        projection_folder=tmp_path / "projections",
        piece=4,
    )
    try:
        view.publish(source, {"p.ome.zarr": 1}, CANVAS, composition=COMPOSITION)
        output = next(iter(view.outputs.values()))
        before = (output._shown / "publication.json").read_bytes()
        products = set(view.projection_folder.glob("*/*.ome.zarr"))
        data = np.ones((1, 1, 2, 8, 8), dtype="uint8" if problem == "dtype" else "uint16")
        if problem == "overflow":
            data = np.full(data.shape, np.iinfo("uint32").max, dtype="uint32")
        tile = write_tile(source, "q.ome.zarr", data)
        if problem == "xy":
            group = zarr.open_group(str(tile.store), mode="r+")
            attrs = dict(group.attrs)
            for level in attrs["ome"]["multiscales"][0]["datasets"]:
                level["coordinateTransformations"][0]["scale"][-1] *= 2
            group.attrs.update(attrs)
        with pytest.raises((ValueError, OverflowError)):
            view.publish(
                source,
                {"p.ome.zarr": 1, "q.ome.zarr": 1},
                CANVAS,
                composition={"regions": "complete", "order": ["p.ome.zarr", "q.ome.zarr"]},
            )
        assert set(view.projection_folder.glob("*/*.ome.zarr")) == products
        assert not list(view.projection_folder.glob(".publishing-*"))
        assert (output._shown / "publication.json").read_bytes() == before
    finally:
        view.close()


@pytest.mark.parametrize("shared_output_folder", [False, True])
def test_sum_normalizes_original_dtype_and_z_before_validation(tmp_path, shared_output_folder):
    import zarr

    source = tmp_path / "positions"
    source.mkdir()
    write_tile(source, "p.ome.zarr", np.ones((1, 1, 2, 8, 8), dtype="uint8"))
    tile = write_tile(source, "q.ome.zarr", np.ones((1, 1, 3, 8, 8), dtype="uint16"), x=8)
    group = zarr.open_group(str(tile.store), mode="r+")
    attrs = dict(group.attrs)
    for level in attrs["ome"]["multiscales"][0]["datasets"]:
        level["coordinateTransformations"][0]["scale"][2] = 2
    group.attrs.update(attrs)
    view = views.ViewSet(
        tmp_path / "view",
        acquisition="a",
        modes=(),
        projections=("sum",),
        projection_folder=tmp_path / ("view" if shared_output_folder else "projections"),
        piece=4,
    )
    try:
        view.publish(
            source,
            {"p.ome.zarr": 1, "q.ome.zarr": 1},
            {**CANVAS, "x_um": [0, 16]},
            composition={"regions": "complete", "order": ["p.ome.zarr", "q.ome.zarr"]},
        )
        output = next(iter(view.outputs.values()))
        composer = output.composer()
        assert composer.values_for(0, 0, 0, 0)[0, 0] == 2
        assert composer.values_for(0, 0, 0, 2)[0, 0] == 3
        assert ".publishing-" not in (output._shown / "publication.json").read_text()
    finally:
        view.close()


def test_later_projection_failure_never_promotes_earlier_methods(tmp_path, monkeypatch):
    source = tmp_path / "positions"
    source.mkdir()
    write_tile(source, "p.ome.zarr", np.ones((1, 1, 2, 8, 8), dtype="uint16"))
    view = views.ViewSet(
        tmp_path / "view",
        acquisition="a",
        modes=(),
        projections=("min", "sum"),
        projection_folder=tmp_path / "projections",
        piece=4,
    )
    try:
        view.publish(source, {"p.ome.zarr": 1}, CANVAS, composition=COMPOSITION)
        previous = {
            name: (out._shown / "publication.json").read_bytes()
            for name, out in view.outputs.items()
        }
        products = set(view.projection_folder.glob("*/*.ome.zarr"))
        write_tile(
            source, "p.ome.zarr", np.full((1, 1, 2, 8, 8), np.iinfo("uint32").max, dtype="uint32")
        )
        with pytest.raises(OverflowError):
            view.publish(source, {"p.ome.zarr": 2}, CANVAS, composition=COMPOSITION)
        assert set(view.projection_folder.glob("*/*.ome.zarr")) == products
        assert not list(view.projection_folder.glob(".publishing-*"))
        assert {
            name: (out._shown / "publication.json").read_bytes()
            for name, out in view.outputs.items()
        } == previous
        # Re-announcing the durable snapshot does not read source pixels, replace
        # products/metadata, or write chunks. This oracle does not depend on mtime.
        from zmart_viewer import projections

        def unexpected(*args, **kwargs):
            pytest.fail("Identical announcement performed image work")

        monkeypatch.setattr(projections, "_source_window", unexpected)
        monkeypatch.setattr(views.os, "replace", unexpected)
        monkeypatch.setattr(Path, "write_bytes", unexpected)
        view.publish(source, {"p.ome.zarr": 1}, CANVAS, composition=COMPOSITION)
    finally:
        view.close()


def test_independent_views_can_concurrently_share_projection_products(tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    source = tmp_path / "positions"
    source.mkdir()
    write_tile(source, "p.ome.zarr", np.ones((1, 1, 2, 8, 8), dtype="uint16"))
    handles = [
        views.ViewSet(
            tmp_path / name,
            acquisition="a",
            modes=(),
            projections=("sum",),
            projection_folder=tmp_path / "projections",
            piece=4,
        )
        for name in ("first", "second")
    ]
    ready = threading.Barrier(2)
    write = views.write_projection

    def both_staged(*args, **kwargs):
        result = write(*args, **kwargs)
        ready.wait(timeout=10)
        return result

    monkeypatch.setattr(views, "write_projection", both_staged)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [
                workers.submit(
                    handle.publish, source, {"p.ome.zarr": 1}, CANVAS, composition=COMPOSITION
                )
                for handle in handles
            ]
            assert [future.result(timeout=15) for future in futures] == [1, 1]
        assert len(list((tmp_path / "projections").glob("sum/*.ome.zarr"))) == 1
        for handle in handles:
            assert next(iter(handle.outputs.values())).composer().values_for(0, 0, 0, 0)[0, 0] == 2
    finally:
        for handle in handles:
            handle.close()


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
