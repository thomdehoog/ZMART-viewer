"""Virtual uint8 coverage alongside an image, never inferred from its intensity.

Composed views reuse their indexed position geometry and committed timepoints.
Ordinary position stores cover their declared array extent. Coverage is generated
only when requested, and never writes into the acquisition or reads image pixels.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np

from . import pieces
from .building import ComposedPicture
from .library import _read_array_description, _read_attrs_at
from .record.gateway import live_run_holding

MARKER = "__zmart_coverage__"


def source_url(source: str) -> str:
    return source.split("|", 1)[0].rstrip("/") + f"/{MARKER}/|zarr3:"


def requires_geometry(store: Path) -> bool:
    """A linked or governed view must never inherit dense-position opacity."""
    return (
        pieces.the_map_inside(store) is not None
        or live_run_holding(store) is not None
        or pieces._composer_for(store) is not None
    )


def answer(store: Path, inside: str) -> bytes | None:
    """Serve a Zarr 3 coverage group using the source's own axes and transforms."""
    held = pieces._composer_for(store)
    composer = held.composer() if isinstance(held, ComposedPicture) else held
    if composer is None and pieces.the_map_inside(store) is not None:
        raise ValueError("coverage unavailable for a refused or legacy linked image")
    if composer is None and live_run_holding(store) is not None:
        raise ValueError("live coverage requires the governed composed view")
    attrs = _read_attrs_at(store)
    multiscales = attrs.get("multiscales") or []
    if not multiscales:
        return None
    scale = multiscales[0]
    datasets = scale.get("datasets", [])
    multiscales = [{**scale, "datasets": datasets}]
    if inside == "zarr.json":
        return json.dumps(
            {
                "zarr_format": 3,
                "node_type": "group",
                "attributes": {
                    "ome": {"version": "0.5", "multiscales": multiscales},
                },
            }
        ).encode()

    for level, dataset in enumerate(datasets):
        prefix = dataset["path"].rstrip("/") + "/"
        if not inside.startswith(prefix):
            continue
        array_path = (store / dataset["path"]).resolve()
        if not array_path.is_relative_to(store.resolve()):
            raise ValueError("coverage dataset escapes its image store")
        metadata = _read_array_description(array_path)
        shape = metadata.get("shape", [])
        if len(shape) < 2:
            return None
        axes = [axis["name"] if isinstance(axis, dict) else axis for axis in scale["axes"]]
        # These are the spatial axes the compositor itself supports.
        if len(axes) != len(shape) or axes[-2:] != ["y", "x"]:
            return None
        if composer and axes[-3:] != ["z", "y", "x"]:
            return None
        side = composer.piece if composer else 256
        chunk_shape = [1] * (len(shape) - 2) + [side, side]
        tail = inside[len(prefix) :]
        if tail == "zarr.json":
            return json.dumps(
                {
                    "zarr_format": 3,
                    "node_type": "array",
                    "shape": shape,
                    "data_type": "uint8",
                    "dimension_names": axes,
                    "fill_value": 0,
                    "chunk_grid": {
                        "name": "regular",
                        "configuration": {"chunk_shape": chunk_shape},
                    },
                    "chunk_key_encoding": {"name": "default", "configuration": {"separator": "/"}},
                    "codecs": [{"name": "bytes"}, {"name": "gzip", "configuration": {"level": 1}}],
                    "attributes": {},
                }
            ).encode()
        parts = tail.split("/")
        if parts[0] != "c" or len(parts) != len(shape) + 1:
            return None
        if not all(part.isdecimal() for part in parts[1:]):
            return None
        indices = [int(part) for part in parts[1:]]
        if any(
            index * size >= extent
            for index, size, extent in zip(indices, chunk_shape, shape, strict=True)
        ):
            return None
        coordinates = dict(zip(axes, indices, strict=True))
        y, x = indices[-2:]
        if composer:
            mask = composer.coverage_for(
                level,
                coordinates.get("z", 0),
                y,
                x,
                coordinates.get("t", 0),
                coordinates.get("c", 0),
            )
            return gzip.compress(mask.tobytes(), compresslevel=1, mtime=0)
        # An ordinary position store is dense within its bounds, even if its
        # encoder omitted an all-zero chunk. Do not mistake that for a gap.
        mask = np.zeros((side, side), dtype=np.uint8)
        mask[: min(side, shape[-2] - y * side), : min(side, shape[-1] - x * side)] = 1
        return gzip.compress(mask.tobytes(), compresslevel=1, mtime=0)
    return None
