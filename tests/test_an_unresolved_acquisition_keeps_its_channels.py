"""An acquisition that has not decided its window still knows its channels.

A run that has not resolved a display window writes no ``omero`` block, because
a strict reader refuses a channel entry without a complete window. That used to
cost the channel names and colours too: three named colours came back as
"channel 1", "channel 2", "channel 3" in the default turns. The names travel
under the store's ``zmart`` attributes instead, and this checks they come back
without a window being invented on the way.
"""

from __future__ import annotations

import json

import zarr

from zmart_viewer.library import channels


def _a_store_with(tmp_path, attributes: dict):
    store = tmp_path / "overview_P000000.ome.zarr"
    group = zarr.open_group(str(store), mode="w", zarr_format=3)
    group.create_array("0", shape=(1, 3, 1, 8, 8), chunks=(1, 1, 1, 8, 8), dtype="uint16")
    described = json.loads((store / "zarr.json").read_text())
    described["attributes"] = {
        "ome": {
            "version": "0.5",
            "multiscales": [{
                "axes": [{"name": n} for n in "tczyx"],
                "datasets": [{"path": "0"}],
            }],
        },
        **attributes,
    }
    (store / "zarr.json").write_text(json.dumps(described), encoding="utf-8")
    return store


THREE = [
    {"key": "405", "index": 0, "label": "DAPI", "color": "0000FF",
     "range": {"min": 0, "max": 65535}},
    {"key": "488", "index": 1, "label": "GFP", "color": "00FF00",
     "range": {"min": 0, "max": 65535}},
    {"key": "594", "index": 2, "label": "mCherry", "color": "FF0000",
     "range": {"min": 0, "max": 65535}},
]


def test_the_names_and_colours_come_from_the_zmart_block_when_there_is_no_omero(tmp_path):
    store = _a_store_with(tmp_path, {"zmart": {"channels": THREE}})

    described = channels(store)

    assert [c["name"] for c in described] == ["DAPI", "GFP", "mCherry"]
    assert [c["color"] for c in described] == [(0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)]
    # No window was decided, so none is reported -- the Viewer measures one.
    assert [c["window"] for c in described] == [None, None, None]
    assert described[0]["range"] == {"low": 0.0, "high": 65535.0}


def test_an_omero_block_still_wins_over_the_zmart_names(tmp_path):
    store = _a_store_with(tmp_path, {
        "zmart": {"channels": THREE},
        "ome": {
            "version": "0.5",
            "multiscales": [{"axes": [{"name": n} for n in "tczyx"], "datasets": [{"path": "0"}]}],
            "omero": {"channels": [
                {"label": "A", "color": "FFFFFF", "window": {"min": 0, "max": 65535, "start": 1, "end": 2}},
                {"label": "B", "color": "FFFFFF", "window": {"min": 0, "max": 65535, "start": 1, "end": 2}},
                {"label": "C", "color": "FFFFFF", "window": {"min": 0, "max": 65535, "start": 1, "end": 2}},
            ]},
        },
    })
    assert [c["name"] for c in channels(store)] == ["A", "B", "C"]


def test_a_malformed_zmart_list_falls_back_to_counting_the_pixels(tmp_path):
    store = _a_store_with(tmp_path, {"zmart": {"channels": ["not", "objects", 3]}})
    described = channels(store)
    assert [c["name"] for c in described] == ["channel 1", "channel 2", "channel 3"]
