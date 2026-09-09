import json

import numpy as np
import zarr

from zmart_viewer.building import declare_a_built_picture
from zmart_viewer.compose import Composer, read_the_transfer


def positions(root):
    root.mkdir()
    group = zarr.open_group(str(root / "p.ome.zarr"), mode="w", zarr_format=3)
    for level, side in enumerate((8, 4, 2)):
        group.create_array(str(level), data=np.ones((2, 2, 1, side, side), dtype="uint16"),
                           chunks=(1, 1, 1, 2, 2))
    group.attrs["ome"] = {"version": "0.5", "multiscales": [{"type": "mean",
        "axes": [{"name": a, "type": "space" if a in "zyx" else "time" if a == "t" else "channel"} for a in "tczyx"],
        "datasets": [{"path": str(level), "coordinateTransformations": [
            {"type": "scale", "scale": [1, 1, 1, 2**level, 2**level]},
        ]} for level in range(3)]}]}


def test_baked_warm_prefills_blocks_at_each_time_and_channel(tmp_path, monkeypatch):
    positions(tmp_path / "positions")
    composer = Composer(read_the_transfer(tmp_path / "positions"), piece=4)
    calls = []
    actual = composer._a_block_of
    def read(copy, at, outer):
        calls.append(outer)
        return actual(copy, at, outer)
    monkeypatch.setattr(composer, "_a_block_of", read)
    composer.warm_from_the_baked(tmp_path / "not-baked", frozenset())
    try:
        composer.warm_the_coarse_levels()
        assert {(0, 0), (0, 1), (1, 0), (1, 1)} <= set(calls)
        assert composer._blocks_prefilled
    finally:
        composer.close()


def test_redeclare_clears_previous_bake_completion_stamp(tmp_path):
    positions(tmp_path / "positions")
    output = declare_a_built_picture(tmp_path / "view", tmp_path / "positions", piece=4)
    stamp = output / "baked.json"
    stamp.write_text(json.dumps({"events": 12, "tail": 12}))
    declare_a_built_picture(tmp_path / "view", tmp_path / "positions", piece=4, bake=False)
    assert not stamp.exists()
