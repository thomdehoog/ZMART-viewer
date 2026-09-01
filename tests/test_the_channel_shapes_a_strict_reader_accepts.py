"""Which channel descriptions a strict reader opens, checked against the reader itself.

The whole "omit the block when unresolved" decision rests on one fact: that
a strict OME-Zarr reader refuses a channel block whose window has ``min`` and
``max`` but no ``start`` and ``end``, while a store with no block at all opens
perfectly well. That fact lived in a code comment dated 2026-09-02. A comment
is not re-checked the day the reader is upgraded, so here it is as a test,
against ngio itself, over the four shapes the writers can produce. Skipped
where ngio is not installed, never faked.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import zarr

ngio = pytest.importorskip("ngio")


def _a_store(folder: Path, name: str, omero: dict | None) -> Path:
    store = folder / f"{name}.ome.zarr"
    group = zarr.open_group(str(store), mode="w", zarr_format=3)
    array = group.create_array("0", shape=(1, 1, 8, 8), chunks=(1, 1, 8, 8), dtype="uint16")
    array[:] = np.arange(64, dtype="uint16").reshape(1, 1, 8, 8)
    ome = {
        "version": "0.5",
        "multiscales": [{
            "axes": [
                {"name": "c", "type": "channel"},
                {"name": "z", "type": "space", "unit": "micrometer"},
                {"name": "y", "type": "space", "unit": "micrometer"},
                {"name": "x", "type": "space", "unit": "micrometer"},
            ],
            "datasets": [{"path": "0", "coordinateTransformations": [
                {"type": "scale", "scale": [1.0, 1.0, 1.0, 1.0]},
            ]}],
        }],
    }
    if omero is not None:
        ome["omero"] = omero
    described = json.loads((store / "zarr.json").read_text(encoding="utf-8"))
    described.setdefault("attributes", {})["ome"] = ome
    (store / "zarr.json").write_text(json.dumps(described), encoding="utf-8")
    return store


def _opens(store: Path) -> bool:
    try:
        ngio.open_ome_zarr_container(str(store))
    except Exception:  # noqa: BLE001 -- refused, whatever the reader calls it
        return False
    return True


COMPLETE = {"channels": [{"label": "GFP", "color": "00FF00",
                          "window": {"min": 0, "max": 65535, "start": 300, "end": 4200}}]}
LABEL_ONLY = {"channels": [{"label": "GFP", "color": "00FF00"}]}
RANGE_ONLY = {"channels": [{"label": "GFP", "color": "00FF00",
                            "window": {"min": 0, "max": 65535}}]}


def test_no_block_and_a_complete_window_open_while_incomplete_blocks_are_refused(tmp_path):
    assert _opens(_a_store(tmp_path, "none", None)), "no channel block must open"
    assert _opens(_a_store(tmp_path, "complete", COMPLETE)), "a complete window must open"
    assert not _opens(_a_store(tmp_path, "label-only", LABEL_ONLY)), (
        "if a label-only channel opens, the writers may keep names for unresolved "
        "channels again; revisit acquisition.source_metadata and the writers"
    )
    assert not _opens(_a_store(tmp_path, "range-only", RANGE_ONLY)), (
        "if a min/max-only window opens, an unresolved channel may carry its range "
        "again; revisit the decision recorded in acquisition.source_metadata"
    )
