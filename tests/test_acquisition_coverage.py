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
    monkeypatch.setattr(
        coverage.pieces, "the_map_inside", lambda _: {"built_from": "missing"}
    )
    with pytest.raises(ValueError, match="refused"):
        coverage.answer(tmp_path, "zarr.json")


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
