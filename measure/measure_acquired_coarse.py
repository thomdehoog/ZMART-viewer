"""Cold acquired-composition reads: 100 separate 1024px positions, bake off/on.

Run with a new output directory. Viewer caches are cold for each request;
the operating system's file cache is not flushed. This is a single-channel,
single-plane/time fixture, not an acquisition-throughput benchmark.
"""

import argparse
import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import numpy as np
import zarr

from zmart_viewer import pieces
from zmart_viewer.compose import Composer
from zmart_viewer.published import STORE, PublishedTransfer


def positions(folder):
    rng = np.random.default_rng(20260908)
    names = []
    for index in range(100):
        name = f"p{index:03}.ome.zarr"
        names.append(name)
        group = zarr.open_group(str(folder / name), mode="w", zarr_format=3)
        values = rng.integers(0, 4096, (1, 1024, 1024), dtype=np.uint16)
        datasets = []
        y, x = divmod(index, 10)
        for level in range(6):
            factor = 2**level
            group.create_array(str(level), data=values, chunks=(1, 256, 256))
            datasets.append(
                {
                    "path": str(level),
                    "coordinateTransformations": [
                        {"type": "scale", "scale": [1, factor, factor]},
                        {
                            "type": "translation",
                            "translation": [
                                0,
                                y * 1024 + (factor - 1) / 2,
                                x * 1024 + (factor - 1) / 2,
                            ],
                        },
                    ],
                }
            )
            side = values.shape[-1] // 2
            values = values.reshape(1, side, 2, side, 2).mean(axis=(2, 4)).round().astype("uint16")
        group.attrs["ome"] = {
            "version": "0.5",
            "multiscales": [
                {
                    "type": "mean",
                    "datasets": datasets,
                    "axes": [
                        {"name": axis, "type": "space", "unit": "micrometer"} for axis in "zyx"
                    ],
                }
            ],
        }
        if (index + 1) % 10 == 0:
            print(f"Prepared {index + 1}/100 positions", flush=True)
    return names


def cold_chunk(store, level):
    pieces.forget(store)
    reads, sources = Counter(), set()
    read_from = Composer._read_from

    def counted(self, copy, low, high, outer):
        reads[str(copy.held_in.name)] += 1
        sources.add(str(copy.held_in.parent))
        return read_from(self, copy, low, high, outer)

    try:
        started = time.perf_counter()
        with patch.object(Composer, "_read_from", counted):
            data = pieces.built_bytes_behind(store, f"{level}/c/0/0/0")
        elapsed = time.perf_counter() - started
        assert data, "Expected an occupied image chunk"
        return {
            "level": level,
            "seconds": elapsed,
            "encoded_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "original_positions_read": len(sources),
            "tile_reads_by_level": dict(reads),
        }
    finally:
        pieces.forget(store)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="New scratch directory; must not exist")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    folder = args.output / "positions"
    names = positions(folder)
    canvas = {"x_um": [0, 10240], "y_um": [0, 10240]}
    view = PublishedTransfer(folder / STORE)
    results = {
        "positions": 100,
        "shape_zyx": [1, 1024, 1024],
        "source_levels": 6,
        "source_chunks_zyx": [1, 256, 256],
        "channels": 1,
        "timepoints": 1,
        "dtype": "uint16",
        "viewer_cache": "cold per request",
        "os_file_cache": "not flushed",
        "modes": [],
    }
    try:
        for bake in (False, True):
            started = time.perf_counter()
            view.publish(
                folder,
                dict.fromkeys(names, 1),
                canvas,
                composition={"regions": "complete", "order": names},
                bake=bake,
            )
            publication_seconds = time.perf_counter() - started
            metadata = json.loads((view._shown / "zarr.json").read_text())
            levels = metadata["attributes"]["zmart"]["baked"]
            row = {
                "bake": bake,
                "publication_seconds": publication_seconds,
                "baked_levels": levels,
                "requests": [],
            }
            print(
                json.dumps({key: value for key, value in row.items() if key != "requests"}),
                flush=True,
            )
            for level in (2, view.composer().mosaic.levels - 1):
                measured = cold_chunk(view._shown, level)
                row["requests"].append(measured)
                print(json.dumps(measured), flush=True)
            results["modes"].append(row)
        assert [r["sha256"] for r in results["modes"][0]["requests"]] == [
            r["sha256"] for r in results["modes"][1]["requests"]
        ], "Bake modes returned different encoded image chunks"
    finally:
        view.close()
    (args.output / "results.json").write_text(json.dumps(results, indent=2) + "\n")


if __name__ == "__main__":
    main()
