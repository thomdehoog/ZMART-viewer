"""Pretend mesoSPIM tiles, written with numpy alone, and a demo that shows them.

    python -m mesospim_view.demo            # writes tiles, opens a browser
    python -m mesospim_view.demo --no-open  # just serve, print the address

Four two-channel tiles are laid out two by two with a small overlap, each an
ordinary OME-Zarr 0.4 store (zarr v2, uncompressed chunks) carrying its stage
position as a translation. The demo places them once by that metadata and once
more shifted, to show both ways of putting a tile somewhere.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

VOXEL_UM = (5.0, 1.0, 1.0)  # z, y, x
TILE = (24, 160, 160)  # z, y, x voxels
OVERLAP_UM = 16.0


def _pyramid(volume):
    import numpy as np

    levels = [volume]
    while min(levels[-1].shape[-2:]) >= 32:
        held = levels[-1]
        y, x = (held.shape[-2] // 2) * 2, (held.shape[-1] // 2) * 2
        trimmed = held[..., :y, :x].astype(np.float32)
        smaller = trimmed.reshape(*held.shape[:-2], y // 2, 2, x // 2, 2).mean(axis=(-3, -1))
        levels.append(smaller.astype(np.uint16))
    return levels


def _write_array(folder: Path, data, chunk_shape) -> None:
    """A zarr v2 array with no compression: one file per chunk, C order."""
    import numpy as np

    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".zarray").write_text(
        json.dumps(
            {
                "zarr_format": 2,
                "shape": list(data.shape),
                "chunks": list(chunk_shape),
                "dtype": "<u2",
                "compressor": None,
                "filters": None,
                "fill_value": 0,
                "order": "C",
                "dimension_separator": ".",
            }
        )
    )
    counts = [-(-n // c) for n, c in zip(data.shape, chunk_shape, strict=True)]
    for index in np.ndindex(*counts):
        window = tuple(slice(i * c, (i + 1) * c) for i, c in zip(index, chunk_shape, strict=True))
        piece = data[window]
        if piece.shape != tuple(chunk_shape):
            full = np.zeros(chunk_shape, dtype=np.uint16)
            full[tuple(slice(0, n) for n in piece.shape)] = piece
            piece = full
        (folder / ".".join(str(i) for i in index)).write_bytes(
            piece.astype("<u2").tobytes(order="C")
        )


def write_tile(path: Path, *, origin_um: tuple[float, float, float], seed: int) -> Path:
    """One two-channel tile at ``origin_um`` (z, y, x), as OME-Zarr 0.4 with axes t, c, z, y, x."""
    import numpy as np

    rng = np.random.default_rng(seed)
    z, y, x = TILE
    zz, yy, xx = np.ogrid[0:z, 0:y, 0:x]
    volume = np.zeros((2, z, y, x), dtype=np.float32)
    for centre in rng.uniform([2, 10, 10], [z - 2, y - 10, x - 10], size=(40, 3)):
        blob = np.exp(
            -0.5
            * (
                ((zz - centre[0]) / 2.0) ** 2
                + ((yy - centre[1]) / 7.0) ** 2
                + ((xx - centre[2]) / 7.0) ** 2
            )
        )
        volume[0] += blob
        if rng.random() < 0.5:
            volume[1] += blob * rng.uniform(0.5, 1.0)
    # A bright frame around each tile, so its edges and overlaps are visible.
    volume[0, :, :3, :] = volume[0, :, -3:, :] = volume[0, :, :, :3] = volume[0, :, :, -3:] = 0.6
    out = np.empty_like(volume, dtype=np.uint16)
    for c in range(2):
        peak = float(volume[c].max()) or 1.0
        out[c] = np.clip(400 + volume[c] / peak * 12000, 0, 65535).astype(np.uint16)
    out = out[np.newaxis]  # t, c, z, y, x

    if path.exists():
        shutil.rmtree(path)
    levels = _pyramid(out)
    datasets = []
    for level, data in enumerate(levels):
        factor = 2**level
        _write_array(
            path / str(level),
            data,
            (1, data.shape[1], 1, min(64, data.shape[-2]), min(64, data.shape[-1])),
        )
        datasets.append(
            {
                "path": str(level),
                "coordinateTransformations": [
                    {
                        "type": "scale",
                        "scale": [
                            1.0,
                            1.0,
                            VOXEL_UM[0],
                            VOXEL_UM[1] * factor,
                            VOXEL_UM[2] * factor,
                        ],
                    },
                    {"type": "translation", "translation": [0.0, 0.0, *origin_um]},
                ],
            }
        )
    (path / ".zgroup").write_text(json.dumps({"zarr_format": 2}))
    (path / ".zattrs").write_text(
        json.dumps(
            {
                "multiscales": [
                    {
                        "version": "0.4",
                        "name": path.name,
                        "axes": [
                            {"name": "t", "type": "time", "unit": "second"},
                            {"name": "c", "type": "channel"},
                            {"name": "z", "type": "space", "unit": "micrometer"},
                            {"name": "y", "type": "space", "unit": "micrometer"},
                            {"name": "x", "type": "space", "unit": "micrometer"},
                        ],
                        "datasets": datasets,
                    }
                ],
                "omero": {
                    "channels": [
                        {
                            "label": "488",
                            "color": "00FF66",
                            "active": True,
                            "window": {"min": 0, "max": 65535, "start": 380, "end": 12400},
                        },
                        {
                            "label": "561",
                            "color": "FF33FF",
                            "active": True,
                            "window": {"min": 0, "max": 65535, "start": 380, "end": 12400},
                        },
                    ]
                },
            },
            indent=1,
        )
    )
    return path


def write_tiles(folder: Path, *, across: int = 2, down: int = 2) -> list[Path]:
    """A grid of tiles, each placed by its own metadata, overlapping a little."""
    folder.mkdir(parents=True, exist_ok=True)
    step_y = TILE[1] * VOXEL_UM[1] - OVERLAP_UM
    step_x = TILE[2] * VOXEL_UM[2] - OVERLAP_UM
    tiles = []
    for row in range(down):
        for column in range(across):
            seed = row * across + column
            tiles.append(
                write_tile(
                    folder / f"tile_{row}{column}.ome.zarr",
                    origin_um=(0.0, row * step_y, column * step_x),
                    seed=seed,
                )
            )
    return tiles


def main(argv: list[str] | None = None) -> int:
    from .viewer import Viewer

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--folder", default="testdata/mesospim_demo", help="where the tiles are written"
    )
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-open", action="store_true", help="do not open a browser")
    parser.add_argument("--transparent", action="store_true", help="transparent 2D ground")
    parser.add_argument("--ui", choices=("full", "bare"), default="full")
    args = parser.parse_args(argv)

    folder = Path(args.folder)
    tiles = write_tiles(folder)
    view = Viewer(port=args.port, transparent=args.transparent, ui=args.ui)
    for tile in tiles:
        view.add(tile, layer="overview")
    # The same tiles again, shifted aside, as a second acquisition placed by hand.
    shift = TILE[2] * VOXEL_UM[2] * 2 + 200
    for tile in tiles:
        view.add(tile, layer="shifted copy", offset={"x": shift}, colours=["#33ccff", "#ffbf1a"])
    view.set_visible("shifted copy", False)
    view.fit()
    url = view.start()
    if not view.page_built:
        print("the page is not built: run `npm ci && npm run build` in app/mesospim first")
    print(f"serving {len(tiles)} tiles at {url}")
    if not args.no_open:
        view.open_in_browser()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        view.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
