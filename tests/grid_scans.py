"""Small raw grid scans, and a way to talk to a running server, for the tests.

A grid scan here is what a microscope leaves after a tiled survey: one
OME-Zarr per position, each carrying its own stage offset, overlapping its
neighbours a little. Several tests open one through the server's doors, so
the writer lives here rather than inside any one of them.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import zarr

# One tile: two planes deep, one camera frame square. 384 is the same frame
# the live fixtures elsewhere use -- small enough to write quickly, large
# enough that the planner can give it a real pyramid.
FRAME = 384
PLANES = 2
# How far the stage steps between tiles, in micrometres at one micrometre a
# voxel. 320 leaves 64 pixels of overlap, which is the overlap the live
# planner itself chooses for this frame.
STEP_UM = 320.0

def _write_a_grid_tile(
    store: Path,
    number: int,
    at_um: tuple[float, float],
    *,
    across: int | None = None,
    reduction: str = "nearest",
) -> None:
    """One position of a raw grid scan, bright enough to tell apart.

    ``across`` makes the frame a rectangle rather than a square. ``reduction``
    is how the smaller copies are said to be made: ``"nearest"`` picks pixels,
    ``"mean"`` averages them, which is what the engine's baked overview needs.
    """
    store.mkdir(parents=True)
    picture = np.full((PLANES, FRAME, across or FRAME), 1500 + number * 800, "uint16")
    datasets = []
    for level in range(2):
        shrink = 2**level
        wide = (across or FRAME) // shrink
        array = zarr.create_array(
            store=str(store / str(level)),
            shape=(PLANES, FRAME // shrink, wide),
            chunks=(PLANES, FRAME // shrink, wide),
            dtype="uint16",
            zarr_format=3,
            dimension_names=["z", "y", "x"],
            overwrite=True,
        )
        array[:] = picture[:, ::shrink, ::shrink]
        datasets.append(
            {
                "path": str(level),
                "coordinateTransformations": [
                    {"type": "scale", "scale": [1.0, 1.0 * shrink, 1.0 * shrink]},
                    {"type": "translation", "translation": [0.0, at_um[0], at_um[1]]},
                ],
            }
        )
    (store / "zarr.json").write_text(
        json.dumps(
            {
                "attributes": {
                    "ome": {
                        "version": "0.5",
                        "multiscales": [
                            {
                                "name": store.name,
                                "type": reduction,
                                "axes": [
                                    {"name": one, "type": "space", "unit": "micrometer"}
                                    for one in ("z", "y", "x")
                                ],
                                "datasets": datasets,
                            }
                        ],
                    }
                },
                "zarr_format": 3,
                "node_type": "group",
            }
        ),
        encoding="utf-8",
    )


def _a_grid_scan(folder: Path, *, across: int = 2, reduction: str = "nearest") -> Path:
    """A raw dataset of ``across``-squared positions on a regular grid."""
    folder.mkdir(parents=True)
    number = 0
    for row in range(across):
        for column in range(across):
            _write_a_grid_tile(
                folder / f"pos{number:02d}.ome.zarr",
                number,
                (row * STEP_UM, column * STEP_UM),
                reduction=reduction,
            )
            number += 1
    return folder


def _post(address: str, route: str, payload: dict) -> tuple[int, dict]:
    """One JSON request straight to the server, no browser in between."""
    import urllib.error
    import urllib.request

    asked = urllib.request.Request(
        f"{address}{route}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(asked, timeout=180) as answer:
            return answer.status, json.loads(answer.read() or b"{}")
    except urllib.error.HTTPError as refusal:
        return refusal.code, json.loads(refusal.read() or b"{}")
