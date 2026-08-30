"""Pointed-at views written by hand, straight from the format's own contract.

The map format belongs to the viewer (`zmart_viewer.pieces` reads it), so the
gates that pin it write it themselves: a few plain tile stores and a small
JSON map, no writer machinery. What these helpers produce is the shape every
pointed view shares -- a store that holds no pixels, whose pieces are the
tiles' own files.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import zarr

TILE = 64


def a_tile(
    store: Path,
    value: int,
    *,
    side: int = TILE,
    pieces: int = 1,
    body: np.ndarray | None = None,
    voxel: tuple[float, float, float] = (2.0, 0.5, 0.5),
) -> None:
    """One five-axis tile store whose every piece is a file of its own."""
    group = zarr.open_group(str(store), mode="w", zarr_format=3)
    shape = (1, 1, 1, side * pieces, side * pieces)
    made = group.create_array(
        "0",
        shape=shape,
        chunks=(1, 1, 1, side, side),
        dtype="uint16",
        dimension_names=("t", "c", "z", "y", "x"),
    )
    made[:] = value if body is None else body
    _describe(store, shape, voxel=voxel)


def _describe(
    store: Path,
    shape: tuple[int, ...],
    *,
    omero: dict | None = None,
    voxel: tuple[float, float, float] = (2.0, 0.5, 0.5),
) -> None:
    axes = [
        {"name": "t", "type": "time"},
        {"name": "c", "type": "channel"},
        {"name": "z", "type": "space", "unit": "micrometer"},
        {"name": "y", "type": "space", "unit": "micrometer"},
        {"name": "x", "type": "space", "unit": "micrometer"},
    ]
    described = json.loads((store / "zarr.json").read_text())
    ome: dict = {
        "version": "0.5",
        "multiscales": [
            {
                "axes": axes,
                "datasets": [
                    {
                        "path": "0",
                        "coordinateTransformations": [
                            {"type": "scale", "scale": [1.0, 1.0, *voxel]}
                        ],
                    }
                ],
            }
        ],
    }

    if omero is not None:
        ome["omero"] = omero
    described.setdefault("attributes", {})["ome"] = ome
    (store / "zarr.json").write_text(json.dumps(described, indent=1), encoding="utf-8")


def a_pointed_view(
    view: Path,
    placed: list[tuple[str, tuple[int, int]]],
    *,
    canvas_chunks: tuple[int, int],
    omero: dict | None = None,
) -> Path:
    """A view holding no pixels: level 0 declared, every piece a pointer.

    ``placed`` names tile stores (beside the view, one chunk each) and the
    chunk each sits at; ``canvas_chunks`` is the declared room, in chunks.
    """
    view.mkdir(parents=True)
    shape = (1, 1, 1, canvas_chunks[0] * TILE, canvas_chunks[1] * TILE)
    (view / "zarr.json").write_text(json.dumps({"zarr_format": 3, "node_type": "group"}))
    level = view / "0"
    level.mkdir()
    (level / "zarr.json").write_text(
        json.dumps(
            {
                "zarr_format": 3,
                "node_type": "array",
                "shape": list(shape),
                "data_type": "uint16",
                "chunk_grid": {
                    "name": "regular",
                    "configuration": {"chunk_shape": [1, 1, 1, TILE, TILE]},
                },
                "chunk_key_encoding": {"name": "default"},
                "fill_value": 0,
                "codecs": [
                    {"name": "bytes", "configuration": {"endian": "little"}},
                    {"name": "zstd", "configuration": {"level": 0, "checksum": False}},
                ],
            },
            indent=1,
        )
    )
    _describe(view, shape, omero=omero)
    described = json.loads((view / "zarr.json").read_text())
    described["attributes"]["zmart"] = {
        "version": 3,
        "level": "0",
        "pointed_levels": 1,
        "separator": "/",
        "prefix": "c",
        "tiles": [
            {
                "store": name,
                "at": [0, at[0], at[1]],
                "size": [1, 1, 1],
                "from": [0, 0, 0],
                "held_as": "file",
            }
            for name, at in placed
        ],
    }
    (view / "zarr.json").write_text(json.dumps(described, indent=1), encoding="utf-8")
    return view


def a_small_scene(folder: Path) -> Path:
    """Two tiles side by side and the view that points at them."""
    folder.mkdir(parents=True, exist_ok=True)
    a_tile(folder / "pos_a.zarr", 1000)
    a_tile(folder / "pos_b.zarr", 2000)
    return a_pointed_view(
        folder / "picture.zarr",
        [("pos_a.zarr", (0, 0)), ("pos_b.zarr", (0, 1))],
        canvas_chunks=(1, 3),
    )


def the_tiles_bytes(folder: Path, name: str) -> bytes:
    """What one tile's only piece holds on disk, byte for byte."""
    return (folder / name / "0" / "c" / "0" / "0" / "0" / "0" / "0").read_bytes()


def decoded(raw: bytes) -> np.ndarray:
    import numcodecs

    return np.frombuffer(numcodecs.Zstd().decode(raw), dtype=np.uint16).reshape(TILE, TILE)
