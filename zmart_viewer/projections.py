"""Per-position acquired Z reductions; originals are read, never modified."""

from __future__ import annotations

import os
import shutil
import tempfile
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import numpy as np
import zarr

from .acquired import AcquiredRegion, canonical_regions
from .compose import MEAN_REDUCTION, _read_one_tile, halve_xy, the_frame_room_of
from .library import _read_attrs_at

METHODS = ("min", "max", "sum")
PROJECTION_RECIPE = 1


def projection_dtype(dtype, method):
    """A stable type independent of each position's depth, directly renderable."""
    if method not in METHODS:
        raise ValueError("Projection must be min, max or sum")
    dtype = np.dtype(dtype)
    if dtype.kind not in "iuf" or dtype.itemsize > 4:
        raise ValueError("Projection supports up to 32-bit integer and floating inputs")
    if method != "sum":
        return np.dtype("float32") if dtype.kind == "f" else dtype
    return np.dtype("float32" if dtype.kind == "f" else "int32" if dtype.kind == "i" else "uint32")


def reduce_z(values, acquired, method, *, output_dtype=None):
    """Reduce one ZYX window. A zero result and an unacquired result stay distinct."""
    values, acquired = np.asarray(values), np.asarray(acquired, dtype=bool)
    if values.ndim != 3 or values.shape != acquired.shape:
        raise ValueError("Projection needs matching ZYX values and acquired coverage")
    dtype = (
        projection_dtype(values.dtype, method) if output_dtype is None else np.dtype(output_dtype)
    )
    if method not in METHODS:
        raise ValueError("Projection must be min, max or sum")
    covered = acquired.any(axis=0)
    if values.dtype.kind == "f" and not np.isfinite(values[acquired]).all():
        raise ValueError("Acquired projection values must be finite")
    if method == "sum":
        accumulator = (
            "float64"
            if values.dtype.kind == "f"
            else "int64"
            if values.dtype.kind == "i"
            else "uint64"
        )
        result = np.sum(values, axis=0, where=acquired, initial=0, dtype=accumulator)
    else:
        limits = np.finfo(values.dtype) if values.dtype.kind == "f" else np.iinfo(values.dtype)
        initial = limits.max if method == "min" else limits.min
        operation = np.min if method == "min" else np.max
        result = operation(values, axis=0, where=acquired, initial=initial)
    result = np.where(covered, result, 0)
    limits = np.finfo(dtype) if dtype.kind == "f" else np.iinfo(dtype)
    if not np.isfinite(result).all() or np.any(result < limits.min) or np.any(result > limits.max):
        raise OverflowError(f"{method} projection exceeds {dtype}; previous publication retained")
    return result.astype(dtype), covered


def acquired_regions(tile, regions):
    """Validate the producer's coverage without inspecting image brightness."""
    base = tile.copies[0]
    frames, channels = the_frame_room_of(base.outer_shape)
    if regions == "complete":
        return tuple(
            AcquiredRegion(t, c, (0, 0, 0), base.shape)
            for t in range(frames)
            for c in range(channels)
        )
    if not isinstance(regions, list):
        raise ValueError("Projection coverage must be complete or an acquired-region list")
    result = tuple(AcquiredRegion.from_written(r) for r in regions)
    for region in result:
        if (
            region.frame >= frames
            or region.channel >= channels
            or any(
                a + b > size
                for a, b, size in zip(region.origin, region.shape, base.shape, strict=True)
            )
        ):
            raise ValueError("Projection coverage extends outside its source")
    return result


def _source_window(copy, t, c, y, x, height, width):
    outer = (t, c) if len(copy.outer_shape) == 2 else (c,) if copy.outer_shape else ()
    return np.asarray(copy.array[outer + (slice(None), slice(y, y + height), slice(x, x + width))])


def write_projection(source, destination, method, *, regions="complete", revision=0, piece=256):
    """Write one complete derived product transactionally; unchanged inputs are no-ops.

    The caller supplies a completed revision and the output location. No folder
    scanning, workflow inference, notification or projection of a stitched volume.
    """
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if regions != "complete":
        regions = canonical_regions(regions)
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("Projection output must be separate from the source store")
    if not destination.name.endswith(".ome.zarr"):
        raise ValueError("Projection output must name an OME-Zarr store, not a run folder")
    if destination.exists():
        owner = _read_attrs_at(destination).get("zmart_projection", {})
        if owner.get("source") != str(source) or owner.get("method") != method:
            raise ValueError("Refusing to replace output not owned by this position projection")
        if (
            owner.get("revision") == revision
            and owner.get("declared_regions") == regions
            and owner.get("recipe") == PROJECTION_RECIPE
            and owner.get("pyramid_reduction") == MEAN_REDUCTION
        ):
            return destination
    attrs = _read_attrs_at(source)
    multiscale = attrs["multiscales"][0]
    source_axes = {axis["name"]: axis for axis in multiscale["axes"]}
    if any(source_axes[a].get("unit", "micrometer") != "micrometer" for a in "zyx"):
        raise ValueError("Projection currently requires spatial coordinates in micrometres")
    if any(t.get("type") != "translation" for t in multiscale.get("coordinateTransformations", [])):
        raise ValueError("Projection does not support shared non-translation transforms")
    tile = _read_one_tile(source)
    if tile.turned or tile.axes not in (
        ("z", "y", "x"),
        ("c", "z", "y", "x"),
        ("t", "c", "z", "y", "x"),
    ):
        raise ValueError("Projection requires unrotated ZYX, CZYX or TCZYX inputs")
    base = tile.copies[0]
    dtype = projection_dtype(base.dtype, method)
    acquired = acquired_regions(tile, regions)
    projected = tuple(
        dict.fromkeys(
            replace(r, origin=(0, *r.origin[1:]), shape=(1, *r.shape[1:])) for r in acquired
        )
    )
    recipe = {
        "recipe": PROJECTION_RECIPE,
        "source": str(source),
        "revision": revision,
        "method": method,
        "declared_regions": regions,
        "input_regions": [r.as_written() for r in acquired],
        "regions": [r.as_written() for r in projected],
        "pyramid_reduction": MEAN_REDUCTION,
        "source_z_um": [base.corner_um[0], base.voxel_um[0], base.shape[0]],
    }
    frames, channels = the_frame_room_of(base.outer_shape)
    depth, height, width = base.shape
    # Bound Z-window allocation too, not only output XY size.
    side = max(
        1,
        min(
            piece, int((32 * 1024**2 / max(1, depth * (np.dtype(base.dtype).itemsize + 1))) ** 0.5)
        ),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.building-", dir=destination.parent)
    )
    retired = None
    try:
        group = zarr.open_group(str(staging), mode="w", zarr_format=3)
        output = group.create_array(
            "0",
            shape=(frames, channels, 1, height, width),
            dtype=dtype,
            chunks=(1, 1, 1, piece, piece),
            dimension_names=list("tczyx"),
        )
        for t in range(frames):
            for c in range(channels):
                relevant = [r for r in acquired if (r.frame, r.channel) == (t, c)]
                for y in range(0, height, side):
                    for x in range(0, width, side):
                        h, w = min(side, height - y), min(side, width - x)
                        mask = np.zeros((depth, h, w), dtype=bool)
                        for r in relevant:
                            low, high = r.bounds()
                            y0, y1 = max(y, low[1]), min(y + h, high[1])
                            x0, x1 = max(x, low[2]), min(x + w, high[2])
                            if y0 < y1 and x0 < x1:
                                mask[low[0] : high[0], y0 - y : y1 - y, x0 - x : x1 - x] = True
                        if not mask.any():
                            continue
                        values = _source_window(base, t, c, y, x, h, w)
                        image, _ = reduce_z(values, mask, method, output_dtype=dtype)
                        output[t, c, 0, y : y + h, x : x + w] = image
        arrays = [output]
        for level in range(1, max(1, tile.keeps)):
            previous = arrays[-1]
            h, w = previous.shape[-2:]
            following = group.create_array(
                str(level),
                shape=(frames, channels, 1, (h + 1) // 2, (w + 1) // 2),
                dtype=dtype,
                chunks=(1, 1, 1, piece, piece),
                dimension_names=list("tczyx"),
            )
            for t in range(frames):
                for c in range(channels):
                    for y in range(0, following.shape[-2], piece):
                        for x in range(0, following.shape[-1], piece):
                            block = np.asarray(
                                previous[
                                    t,
                                    c,
                                    0,
                                    2 * y : min(2 * (y + piece), h),
                                    2 * x : min(2 * (x + piece), w),
                                ]
                            )
                            reduced = halve_xy(block)
                            following[
                                t, c, 0, y : y + reduced.shape[0], x : x + reduced.shape[1]
                            ] = reduced
            arrays.append(following)
        outer_scale, outer_offset = [1, 1], [0, 0]
        for transform in multiscale["datasets"][0].get(
            "coordinateTransformations", []
        ) + multiscale.get("coordinateTransformations", []):
            for i, axis in enumerate("tc"):
                if axis not in tile.axes:
                    continue
                at = tile.axes.index(axis)
                if transform["type"] == "scale":
                    outer_scale[i] = transform["scale"][at]
                elif transform["type"] == "translation":
                    outer_offset[i] += transform["translation"][at]
        datasets = []
        for level in range(len(arrays)):
            factor = 2**level
            datasets.append(
                {
                    "path": str(level),
                    "coordinateTransformations": [
                        {
                            "type": "scale",
                            "scale": [
                                *outer_scale,
                                1,
                                base.voxel_um[1] * factor,
                                base.voxel_um[2] * factor,
                            ],
                        },
                        {
                            "type": "translation",
                            "translation": [
                                *outer_offset,
                                0,
                                base.corner_um[1] + base.voxel_um[1] * (factor - 1) / 2,
                                base.corner_um[2] + base.voxel_um[2] * (factor - 1) / 2,
                            ],
                        },
                    ],
                }
            )
        axes = [
            {
                "name": a,
                "type": "time" if a == "t" else "channel" if a == "c" else "space",
                **({"unit": "micrometer"} if a in "zyx" else {}),
            }
            for a in "tczyx"
        ]
        if "t" in source_axes:
            axes[0] = deepcopy(source_axes["t"])
        omero = attrs.get("omero")
        if omero and method == "sum":
            omero = deepcopy(omero)
            for channel in omero.get("channels", []):
                channel.pop("window", None)
        group.attrs["ome"] = {
            "version": "0.5",
            "multiscales": [{"type": "mean", "axes": axes, "datasets": datasets}],
            **({"omero": omero} if omero else {}),
        }
        group.attrs["zmart_projection"] = recipe
        if destination.exists():
            retired = staging.with_name(staging.name + ".previous")
            os.replace(destination, retired)
        try:
            os.replace(staging, destination)
        except BaseException:
            if retired is not None:
                os.replace(retired, destination)
                retired = None
            raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if retired is not None:
            shutil.rmtree(retired)
    return destination
