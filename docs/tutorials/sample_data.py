"""Small synthetic OME-Zarr images for the tutorials.

The tutorials need something to look at, and you may not have a microscope at
hand. These two functions make a small pretend specimen and write it in the
same form a microscope writes: OME-Zarr version 0.5, with a pyramid of
averaged copies. Nothing here is needed to use the viewer on real data.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import zarr

BACKGROUND = 800  # counts, like a camera's dark level
SIGNAL = 20000  # counts, like a bright stain


def a_specimen(shape=(16, 256, 256), *, cells=40, seed=0, frame=0):
    """A (channel, z, y, x) volume of blob-like cells, 16-bit, two channels.

    Channel 0 is a stain in every cell. Channel 1 marks about a third of
    them. ``frame`` drifts the cells a little, for a timelapse.
    """
    rng = np.random.default_rng(seed)
    depth, height, width = shape
    zz, yy, xx = np.indices(shape, dtype=np.float32)
    stain = np.zeros(shape, np.float32)
    marker = np.zeros(shape, np.float32)
    centres = rng.uniform(0, 1, (cells, 3)) * np.array(shape) + 1.5 * frame
    marked = rng.uniform(0, 1, cells) < 0.35
    for (cz, cy, cx), is_marked in zip(centres, marked, strict=True):
        blob = np.exp(-(((zz - cz) / 3) ** 2 + ((yy - cy) / 12) ** 2 + ((xx - cx) / 12) ** 2))
        stain += blob
        if is_marked:
            marker += blob
    stack = np.stack([stain, marker])
    stack = BACKGROUND + SIGNAL * np.clip(stack, 0, 1)
    stack += rng.normal(0, 60, stack.shape)
    return np.clip(stack, 0, 65535).astype(np.uint16)


def write_image(
    path,
    pixels,
    *,
    step_um=1.0,
    origin_um=(0.0, 0.0),
    channels=("stain", "marker"),
    colors=("FFFFFF", "00FF66"),
    levels=3,
):
    """Write ``pixels`` as one OME-Zarr image at ``path`` and return the path.

    ``pixels`` is shaped (t, c, z, y, x), (c, z, y, x) or (z, y, x).
    ``step_um`` is the size of one pixel in micrometres. ``origin_um`` is
    where the image's corner sits on the stage, as (y, x), so that many
    images can be placed next to each other.
    """
    path = Path(path)
    pixels = np.asarray(pixels)
    names = {5: ["t", "c", "z", "y", "x"], 4: ["c", "z", "y", "x"], 3: ["z", "y", "x"]}[pixels.ndim]
    axes = []
    for name in names:
        if name == "t":
            axes.append({"name": "t", "type": "time", "unit": "second"})
        elif name == "c":
            axes.append({"name": "c", "type": "channel"})
        else:
            axes.append({"name": name, "type": "space", "unit": "micrometer"})

    group = zarr.open_group(str(path), mode="w", zarr_format=3)
    datasets = []
    copy = pixels
    for level in range(levels):
        shrink = 2**level
        chunks = (1,) * (copy.ndim - 2) + copy.shape[-2:]
        array = group.create_array(
            str(level), shape=copy.shape, chunks=chunks, dtype=copy.dtype, dimension_names=names
        )
        array[:] = copy
        scale = [1.0] * (copy.ndim - 3) + [step_um, step_um * shrink, step_um * shrink]
        shift = [0.0] * (copy.ndim - 3) + [0.0, float(origin_um[0]), float(origin_um[1])]
        datasets.append(
            {
                "path": str(level),
                "coordinateTransformations": [
                    {"type": "scale", "scale": scale},
                    {"type": "translation", "translation": shift},
                ],
            }
        )
        if min(copy.shape[-2:]) < 64:
            break
        # The next copy averages 2 x 2 pixels, which is what "type": "mean" promises.
        copy = copy.reshape(*copy.shape[:-2], copy.shape[-2] // 2, 2, copy.shape[-1] // 2, 2)
        copy = copy.mean(axis=(-1, -3)).astype(pixels.dtype)

    channel_count = pixels.shape[-4] if pixels.ndim >= 4 else 1
    omero = {
        "channels": [
            {
                "label": channels[i],
                "color": colors[i],
                "active": True,
                "window": {"min": 0, "max": 65535, "start": BACKGROUND, "end": BACKGROUND + SIGNAL},
            }
            for i in range(channel_count)
        ]
    }
    group.attrs["ome"] = {
        "version": "0.5",
        "multiscales": [{"name": path.name, "type": "mean", "axes": axes, "datasets": datasets}],
        "omero": omero,
    }
    return path
