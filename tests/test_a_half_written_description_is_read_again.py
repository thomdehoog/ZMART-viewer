"""A description that could not be read is read again, not remembered as empty.

The viewer remembers each image's description, keyed on the file's
timestamp, so that the one-second watcher does not re-read hundreds of
files. A ``zarr.json`` caught half-written was remembered as "no image", and
when the writer finished it within the same timestamp it stayed "no image"
for good (finding S4 of the review of 10 October 2026). File systems with
coarse timestamps make that realistic: FAT32 and exFAT drives round to two
seconds, and network shares vary.

The memory is now keyed on timestamp *and* size, and a failed read is never
remembered.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from zmart_viewer.opening import open_folders
from zmart_viewer.opening.open_folders import read_array_description, read_attrs_at

IMAGE = {
    "zarr_format": 3,
    "node_type": "group",
    "attributes": {"ome": {"version": "0.5", "multiscales": [{"name": "picture"}]}},
}
ARRAY = {
    "zarr_format": 3,
    "node_type": "array",
    "shape": [1, 64, 64],
    "data_type": "uint16",
    "chunk_grid": {"name": "regular", "configuration": {"chunk_shape": [1, 32, 32]}},
}


def completed_within_the_same_timestamp(path: Path, text: str) -> None:
    """Finish the file and give it the timestamp the half-written one had."""
    stamp = path.stat().st_mtime_ns
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(stamp, stamp))


def test_a_group_finished_within_one_timestamp_is_read_again(tmp_path):
    store = tmp_path / "picture.ome.zarr"
    store.mkdir()
    whole = json.dumps(IMAGE)
    (store / "zarr.json").write_text(whole[: len(whole) // 3], encoding="utf-8")
    assert read_attrs_at(store) == {}

    completed_within_the_same_timestamp(store / "zarr.json", whole)

    assert read_attrs_at(store)["multiscales"] == [{"name": "picture"}]


def test_an_array_finished_within_one_timestamp_is_read_again(tmp_path):
    level = tmp_path / "0"
    level.mkdir()
    whole = json.dumps(ARRAY)
    (level / "zarr.json").write_text(whole[: len(whole) // 3], encoding="utf-8")
    assert read_array_description(level) == {}

    completed_within_the_same_timestamp(level / "zarr.json", whole)

    assert read_array_description(level)["shape"] == [1, 64, 64]


def test_a_read_that_failed_for_a_moment_is_not_remembered(tmp_path, monkeypatch):
    store = tmp_path / "picture.ome.zarr"
    store.mkdir()
    (store / "zarr.json").write_text(json.dumps(IMAGE), encoding="utf-8")
    real = Path.read_text
    failures = []

    def held_once(path, *arguments, **named):
        if path.name == "zarr.json" and not failures:
            failures.append(path)
            raise PermissionError(13, "held for a moment", str(path))
        return real(path, *arguments, **named)

    monkeypatch.setattr(Path, "read_text", held_once)
    open_folders._attrs_cache.pop(str(store), None)

    assert read_attrs_at(store) == {}
    assert read_attrs_at(store)["multiscales"] == [{"name": "picture"}]
