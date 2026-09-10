"""Small measured cost gates; no long acquisition replay or scaling above 100."""

import json
import time

import numpy as np
import pytest
from test_view_sampling import write_tile

from zmart_viewer import pieces
from zmart_viewer.compose import MEAN_REDUCTION, Composer
from zmart_viewer.views import ViewSet


def _chunks(folder):
    return {
        p: (p.stat().st_mtime_ns, p.stat().st_size)
        for p in folder.rglob("*")
        if p.is_file() and p.name.isdecimal()
    }


@pytest.mark.parametrize("staggered", [False, True])
def test_top_sampling_runs_cost_and_rewrite(tmp_path, monkeypatch, staggered):
    source = tmp_path / "positions"
    source.mkdir()
    layout = (
        [(3, z, z * 2) for z in range(38)]
        if staggered
        else [(1, 0, 80), (5, 10, 0), (12, 14, 4), (2, 38, 64)]
    )
    names, arrays = [], []
    for i, (depth, start, x) in enumerate(layout):
        name = f"p{i}.ome.zarr"
        data = np.broadcast_to(
            np.arange(depth, dtype="uint16")[None, None, :, None, None] + i * 100,
            (1, 1, depth, 8, 8),
        ).copy()
        write_tile(source, name, data, x=x, z=start)
        names.append(name)
        arrays.append(data[0, 0])
    composition = {
        "regions": "complete",
        "order": names,
        "z_references": dict.fromkeys(names, 0),
        "pyramid_reduction": MEAN_REDUCTION,
    }
    canvas = {"x_um": [0, 128], "y_um": [0, 8]}
    view = ViewSet(tmp_path / "view", acquisition="cost", piece=32)
    revisions = dict.fromkeys(names, 1)
    read = Composer._a_block_of
    counts = {"misses": 0, "decoded_bytes": 0}

    def observed(made, copy, at, outer):
        cold = (copy.held_in, outer, at) not in made._blocks
        block = read(made, copy, at, outer)
        if cold:
            counts["misses"] += 1
            counts["decoded_bytes"] += block.nbytes
        return block

    monkeypatch.setattr(Composer, "_a_block_of", observed)
    report = {"staggered": staggered, "positions": len(names), "views": {}}
    try:
        for bake in (False, True):
            started = time.perf_counter()
            view.publish(source, revisions, canvas, composition=composition, bake=bake)
            publish_ms = (time.perf_counter() - started) * 1000
            if not bake:
                assert not _chunks(view.folder)
            for mode in ("slice", "top"):
                output = view.outputs[f"cost_{mode}.zmartview.zarr"]
                cold = Composer(output.composer().mosaic, piece=32)
                counts.update(misses=0, decoded_bytes=0)
                started = time.perf_counter()
                representatives = set()
                for z in range(40):
                    expected = np.zeros((8, 128), dtype="uint16")
                    mask = np.zeros_like(expected, dtype=bool)
                    for data, (depth, start, x) in zip(arrays, layout):
                        plane = min(depth - 1, max(0, z - start)) if mode == "top" else z - start
                        if 0 <= plane < depth:
                            expected[:, x : x + 8] = data[plane]
                            mask[:, x : x + 8] = True
                    for _ in range(2):
                        h, w = expected.shape
                        expected = np.rint(
                            expected.reshape(h // 2, 2, w // 2, 2).mean((1, 3))
                        ).astype("uint16")
                        mask = mask.reshape(h // 2, 2, w // 2, 2).any((1, 3))
                    actual = cold.values_for(2, z, 0, 0)
                    if actual is None:
                        actual = np.zeros((32, 32), dtype="uint16")
                    np.testing.assert_array_equal(actual[:2, :32], expected)
                    np.testing.assert_array_equal(cold.coverage_for(2, z, 0, 0)[:2, :32], mask)
                    representatives.add(cold.canonical_plane(2, z, 0, 0))
                duration = (time.perf_counter() - started) * 1000
                before = dict(counts)
                for z in range(40):
                    cold.values_for(2, z, 0, 0)
                assert counts == before, "Warm Z sweep decoded original chunks again"
                physical = _chunks(output._shown)
                expected_bytes = [cold.bytes_for(2, z, 0, 0) for z in range(40)]
                started = time.perf_counter()
                served = [
                    pieces.built_bytes_behind(output._shown, f"2/c/{z}/0/0") for z in range(40)
                ]
                served_ms = (time.perf_counter() - started) * 1000
                assert served == expected_bytes
                report["views"][f"{mode}_{bake}"] = {
                    **before,
                    "served_misses": counts["misses"] - before["misses"],
                    "served_decoded_bytes": counts["decoded_bytes"] - before["decoded_bytes"],
                    "runs": len(representatives),
                    "lazy_composer_sweep_ms": round(duration, 2),
                    "served_sweep_ms": round(served_ms, 2),
                    "publication_ms": round(publish_ms, 2),
                    "retained_bytes": cold._blocks_weigh
                    + cold._weighs
                    + sum(a.nbytes for a in cold._pinned.values()),
                    "baked_files": len(physical),
                    "baked_bytes": sum(v[1] for v in physical.values()),
                }
                if mode == "top":
                    assert (
                        (len(representatives) >= 35) if staggered else (len(representatives) < 20)
                    )
                cold.close()
        # Shrink a contributor, retaining the overall 40-plane domain. No obsolete
        # former representative may remain as a duplicated physical bake chunk.
        chosen = 0 if staggered else 1
        depth, start, x = layout[chosen]
        write_tile(
            source, names[chosen], np.full((1, 1, 1, 8, 8), 77, dtype="uint16"), x=x, z=start
        )
        revisions[names[chosen]] = 2
        started = time.perf_counter()
        view.publish(source, revisions, canvas, composition=composition, bake=True)
        report["rewrite_ms"] = round((time.perf_counter() - started) * 1000, 2)
        output = view.outputs["cost_top.zmartview.zarr"]
        made = output.composer()
        for path in _chunks(output._shown):
            level, _, z, y, x = path.relative_to(output._shown).parts
            assert int(z) == made.canonical_plane(int(level), int(z), int(y), int(x))
        for output in view.outputs.values():
            made = output.composer()
            for level in range(made.mosaic.levels):
                for z in range(made.grid(level)[0]):
                    for y in range(made.grid(level)[1]):
                        for x in range(made.grid(level)[2]):
                            assert pieces.built_bytes_behind(
                                output._shown, f"{level}/c/{z}/{y}/{x}"
                            ) == made.bytes_for(level, z, y, x)
        print(json.dumps(report))
    finally:
        view.close()
        for output in view.outputs.values():
            pieces.forget(output._shown)


@pytest.mark.parametrize("count", [1, 10, 100])
def test_append_rewrite_costs_remain_local(tmp_path, count):
    source = tmp_path / "positions"
    source.mkdir()
    names = []
    for i in range(min(100, count + 1)):
        name = f"p{i:03}.ome.zarr"
        write_tile(source, name, np.full((1, 1, 1, 8, 8), i + 1, dtype="uint16"), x=i * 32)
        names.append(name)
    view = ViewSet(tmp_path / "view", acquisition="scale", piece=4)
    versions = dict.fromkeys(names[:count], 1)
    composition = {
        "regions": "complete",
        "order": names[:count],
        "pyramid_reduction": MEAN_REDUCTION,
    }
    canvas = {"x_um": [0, (count + 1) * 32], "y_um": [0, 8]}
    try:
        # 100 existing + append would exceed the agreed cap: start at 99 there.
        if count == 100:
            versions.pop(names[count - 1])
            composition["order"] = names[: count - 1]
        view.publish(source, versions, canvas, composition=composition)
        before = _chunks(view.folder)
        append = names[len(versions)]
        versions[append] = 1
        composition["order"] = list(versions)
        started = time.perf_counter()
        view.publish(source, versions, canvas, composition=composition)
        append_ms = (time.perf_counter() - started) * 1000
        after = _chunks(view.folder)
        append_x = (len(versions) - 1) * 32
        for path, stamp in before.items():
            level, _, _, _, col = path.relative_to(path.parents[4]).parts
            width = 4 * 2 ** int(level)
            if int(col) * width >= append_x + 8 or (int(col) + 1) * width <= append_x:
                assert after.get(path) == stamp, f"Unrelated chunk rewritten: {path}"
        write_tile(source, names[0], np.full((1, 1, 1, 8, 8), 500, dtype="uint16"))
        versions[names[0]] = 2
        started = time.perf_counter()
        view.publish(source, versions, canvas, composition=composition)
        rewrite_ms = (time.perf_counter() - started) * 1000
        final = _chunks(view.folder)
        changed = [p for p in after if final.get(p) != after[p]]
        levels = max(int(path.parts[-5]) for path in after)
        assert 0 < len(changed) <= 2 * levels
        assert all(int(path.name) == 0 for path in changed)
        assert len(view.sources) == 2
        print(
            json.dumps(
                {
                    "positions": len(versions),
                    "append_ms": round(append_ms, 2),
                    "rewrite_ms": round(rewrite_ms, 2),
                    "rewritten_chunks": len(changed),
                    "sources_per_view": 1,
                }
            )
        )
    finally:
        view.close()
