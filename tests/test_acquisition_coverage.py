"""Coverage follows geometry and publication, including exact-zero images."""

import gzip
import json

import numpy as np
import pytest
import zarr
from record_fixtures import a_live_run, prepare_without_publishing, some_specimen

from zmart_viewer import coverage
from zmart_viewer.building import GovernedRun
from zmart_viewer.compose import Composer, Copy, Mosaic, Tile
from zmart_viewer.server import make_server


@pytest.mark.parametrize("level", [0, 1])
def test_gaps_inside_one_chunk_and_czt_availability(tmp_path, level):
    tiles = []
    for number, x in enumerate([0, 48]):
        copies = [
            Copy(
                tmp_path / f"{number}/{k}",
                (2, 32 // (2**k), 32 // (2**k)),
                (1, 16, 16),
                "uint16",
                (1, 2**k, 2**k),
                (0, 0, x),
                (2, 2),
            )
            for k in range(2)
        ]
        tiles.append(
            Tile(
                str(number),
                tmp_path / str(number),
                copies,
                moments=frozenset([0]) if number == 0 else frozenset([1]),
            )
        )
    composer = Composer(Mosaic(tiles, 2, ("z", "y", "x"), "uint16"), piece=128)
    try:
        scale = 2**level
        first = composer.coverage_for(level, 0, 0, 0, moment=0, channel=1)
        assert first[4, 4] == 1
        assert first[4, 40 // scale] == 0  # unacquired gap within the same chunk
        assert first[4, 52 // scale] == 0  # B has no published T=0
        second = composer.coverage_for(level, 1, 0, 0, moment=1, channel=1)
        assert second[4, 4] == 0
        assert second[4, 52 // scale] == 1
        assert not composer.coverage_for(level, 2, 0, 0).any()
        assert not composer.coverage_for(level, 0, 0, 0, channel=2).any()
        assert composer.tile_reads == 0
    finally:
        composer.close()


def test_governed_coverage_only_changes_at_publication(tmp_path):
    run = a_live_run(tmp_path)
    with zarr.config.set({"array.write_empty_chunks": True}):
        run.write_and_publish("posA", some_specimen(0))
        prepare_without_publishing(run, "posB", 0)
    governed = GovernedRun(run.folder, piece=256)
    try:
        before = governed.composer()
        _, width = run._mosaic_extent()
        column = (width - 1) // 256
        assert before.coverage_for(0, 0, 0, 0).any()  # acquired black
        assert not before.coverage_for(0, 0, 0, column).any()
        run.publish("posB")
        assert governed.composer().coverage_for(0, 0, 0, column).any()
    finally:
        governed.close()


def test_dense_yx_black_extent_and_compressed_padding(tmp_path):
    group = zarr.open_group(tmp_path, mode="w", zarr_format=2)
    group.attrs["multiscales"] = [{"axes": ["y", "x"], "datasets": [{"path": "0"}]}]
    group.create_array("0", shape=(19, 31), chunks=(19, 31), dtype="uint8")
    # No data chunks were written: an all-zero acquired position is still opaque.
    description = json.loads(coverage.answer(tmp_path, "0/zarr.json"))
    assert description["shape"] == [19, 31]
    chunk = coverage.answer(tmp_path, "0/c/0/0")
    mask = np.frombuffer(gzip.decompress(chunk), np.uint8).reshape(256, 256)
    assert mask.sum() == 19 * 31
    assert len(chunk) < 1000
    assert coverage.answer(tmp_path, "0/c/1/0") is None
    assert coverage.answer(tmp_path, "0/c/-1/0") is None


def test_refused_composer_never_becomes_dense(monkeypatch, tmp_path):
    monkeypatch.setattr(coverage.pieces, "_composer_for", lambda _: None)
    monkeypatch.setattr(coverage.pieces, "the_map_inside", lambda _: {"built_from": "missing"})
    with pytest.raises(ValueError, match="refused"):
        coverage.answer(tmp_path, "zarr.json")
    assert coverage.requires_geometry(tmp_path)


def test_baked_overview_serves_coverage_at_extended_levels(monkeypatch, tmp_path):
    group = zarr.open_group(tmp_path, mode="w", zarr_format=2)
    group.attrs["multiscales"] = [
        {
            "axes": ["z", "y", "x"],
            "datasets": [{"path": "0"}, {"path": "1"}],
        }
    ]
    group.create_array("0", shape=(1, 32, 32), chunks=(1, 32, 32), dtype="uint16")
    group.create_array("1", shape=(1, 16, 16), chunks=(1, 16, 16), dtype="uint16")
    copy = Copy(tmp_path / "0", (1, 32, 32), (1, 32, 32), "uint16", (1, 1, 1), (0, 0, 0))
    composer = Composer(
        Mosaic([Tile("a", tmp_path, [copy])], 1, ("z", "y", "x"), "uint16"), piece=128
    )
    monkeypatch.setattr(coverage.pieces, "_composer_for", lambda _: composer)
    try:
        root = json.loads(coverage.answer(tmp_path, "zarr.json"))
        assert root["attributes"]["ome"]["multiscales"][0]["datasets"] == [{"path": "0"}, {"path": "1"}]
        assert json.loads(coverage.answer(tmp_path, "1/zarr.json"))["shape"] == [1, 16, 16]
        coarse = gzip.decompress(coverage.answer(tmp_path, "1/c/0/0/0"))
        assert np.frombuffer(coarse, np.uint8).sum() == 16 * 16
        chunk = gzip.decompress(coverage.answer(tmp_path, "0/c/0/0/0"))
        assert np.frombuffer(chunk, np.uint8).sum() == 32 * 32
        assert composer.tile_reads == 0
    finally:
        composer.close()


def test_dataset_cannot_escape_image_store(tmp_path):
    group = zarr.open_group(tmp_path, mode="w", zarr_format=2)
    group.attrs["multiscales"] = [{"axes": ["y", "x"], "datasets": [{"path": "../outside"}]}]
    with pytest.raises(ValueError, match="escapes"):
        coverage.answer(tmp_path, "../outside/zarr.json")


def test_direct_position_cannot_bypass_live_publication(monkeypatch, tmp_path):
    monkeypatch.setattr(coverage.pieces, "_composer_for", lambda _: None)
    monkeypatch.setattr(coverage, "live_run_holding", lambda _: tmp_path)
    with pytest.raises(ValueError, match="governed composed view"):
        coverage.answer(tmp_path / "unpublished.ome.zarr", "zarr.json")


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("positions", [1, 2])
def test_only_fixed_single_source_rows_omit_underpainting(tmp_path, live, positions):
    names = []
    for position in range(positions):
        name = f"position{position}_Ch488.ome.zarr"
        names.append(name)
        group = zarr.open_group(tmp_path / name, mode="w", zarr_format=2)
        group.attrs["multiscales"] = [
            {
                "axes": ["z", "y", "x"],
                "datasets": [{"path": "0"}],
            }
        ]
        group.create_array("0", shape=(1, 16, 16), chunks=(1, 16, 16), dtype="uint16")
    server = make_server(
        port=0,
        data_dir=tmp_path,
        site_dir=tmp_path,
        loads=[{"path": tmp_path, "stores": names, "name": "test"}],
        live=live,
        transparent_background=True,
    )
    try:
        config = server.RequestHandlerClass.keywords["config"]()
        assert len(config["layers"]) == 1
        assert len(config["layers"][0]["sources"]) == positions
        for row in config["layers"]:
            opaque = not live and len(row["sources"]) == 1
            assert row["opaque"] == opaque
            assert bool(row.get("coverageSources")) != opaque
    finally:
        server.server_close()
