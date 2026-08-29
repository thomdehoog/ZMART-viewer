"""Write down what a built picture is, so a viewer opens it like any store.

The description — axes, sizes, levels, voxel size, provenance — is written
as an OME-Zarr holding no pixels; served.py makes the pieces on request.
``bake`` computes the coarse levels once and keeps them as files.
"""

from __future__ import annotations

import json
import os
import shutil
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from multiprocessing import get_context
from pathlib import Path

import numpy as np
import zarr

from .composer import PIECE, Composer
from .mosaic import read_the_transfer, the_mosaic_written_down

OURS = "zmart"

_BAKE_PROCESSES = min(4, os.cpu_count() or 1)

_BAKING: tuple | None = None


def _start_baking(run: Path, piece: int) -> None:
    """Give this bake worker its own reading of the governed run."""
    global _BAKING
    from .governed import GovernedRun

    governed = GovernedRun(run, piece=piece)
    composer = governed.composer()
    composer.stop_warming()
    _BAKING = (governed, composer)


def _bake_one_stripe(store: Path, level: int, rows: tuple[int, ...]) -> int:
    """One worker's share of a bake: whole rows of one level, written as files."""
    assert _BAKING is not None, "a bake worker must be started before use"
    _, composer = _BAKING
    deep = composer.grid(level)[0]
    across = composer.grid(level)[2]
    moments, channels = composer.mosaic.frame_room
    grown = (moments, channels) != (1, 1)
    written = 0
    for row in rows:
        for moment in range(moments):
            for channel in range(channels):
                frame = (str(moment), str(channel)) if grown else ()
                for plane in range(deep):
                    inside = store.joinpath(str(level), "c", *frame, str(plane), str(row))
                    for column in range(across):
                        body = composer.bytes_for(
                            level, plane, row, column, moment=moment, channel=channel
                        )
                        if body is None:
                            continue
                        inside.mkdir(parents=True, exist_ok=True)
                        (inside / str(column)).write_bytes(body)
                        written += 1
    return written


def the_scene_folder_name(name: str) -> str:
    """The scene folder's name: the given name wearing ``.zmartview.zarr`` once."""
    bare = name.removesuffix(".zarr").removesuffix(".ome").removesuffix(".zmartview")
    return f"{bare}.zmartview.zarr"


def declare_a_built_picture(
    where: str | Path,
    transfer: str | Path,
    *,
    name: str = "built",
    piece: int = PIECE,
    bake: bool = False,
    workers: int = 1,
    told=None,
) -> Path:
    """Write the description of a picture built from a transfer."""
    where, transfer = Path(where), Path(transfer).resolve()
    mosaic = read_the_transfer(transfer)
    composer = Composer(mosaic, piece=piece, workers=workers)

    store = where / the_scene_folder_name(name)
    store.mkdir(parents=True, exist_ok=True)

    for kept in sorted(store.glob("[0-9]*")):
        if kept.is_dir() and (int(kept.name) >= mosaic.levels or (kept / "c").exists()):
            shutil.rmtree(kept)

    for level in range(mosaic.levels):
        inside = store / str(level)
        inside.mkdir(exist_ok=True)
        (inside / "zarr.json").write_text(
            json.dumps(json.loads(composer.array_json(level)), indent=1), encoding="utf-8"
        )

    described = json.loads(composer.group_json())
    baked: list[int] = []
    if bake:
        try:
            baked = _bake_the_coarse_ground(store, composer, described, told=told)
        finally:
            composer.close()

    described["attributes"][OURS] = {
        "what": (
            "A picture that holds no pixels beyond its baked coarse ground. "
            "Every other piece of it is built when it is asked for, out of the "
            "tiles of the transfer named below, which are read and never "
            "changed."
        ),
        "built_from": transfer.as_posix(),
        "piece": composer.piece,
        "tiles": len(mosaic.tiles),
        "baked": baked,
    }
    (store / "zarr.json").write_text(json.dumps(described, indent=1), encoding="utf-8")

    (store / "tiles.json").write_text(json.dumps(the_mosaic_written_down(mosaic)), encoding="utf-8")

    return store


def declare_a_governed_picture(
    where: str | Path,
    run: str | Path,
    *,
    name: str = "live",
    piece: int = PIECE,
    bake: bool = False,
) -> Path:
    """Write the description of a picture built from a manifest-governed run."""
    from .governed import GovernedRun

    where, run = Path(where), Path(run).resolve()
    governed = GovernedRun(run, piece=piece)
    try:
        composer = governed.composer()
        folded = governed._run._folded
        tail = governed._run._last_folded_revision
        revision = governed._run._geometry()[0].revision

        store = where / the_scene_folder_name(name)
        store.mkdir(parents=True, exist_ok=True)
        for kept in sorted(store.glob("[0-9]*")):
            if kept.is_dir() and (
                int(kept.name) >= composer.mosaic.levels or (kept / "c").exists()
            ):
                shutil.rmtree(kept)
        for level in range(composer.mosaic.levels):
            inside = store / str(level)
            inside.mkdir(exist_ok=True)
            (inside / "zarr.json").write_text(
                json.dumps(json.loads(composer.array_json(level)), indent=1), encoding="utf-8"
            )

        described = json.loads(composer.group_json())
        baked: list[int] = []
        if bake:
            from .governed import _holding_the_bake_lock

            with _holding_the_bake_lock(store):
                baked = _bake_the_coarse_ground(store, composer, described, governed_run=run)
        described["attributes"][OURS] = {
            "what": (
                "A picture of a live, manifest-governed run. It holds no "
                "pixels beyond its baked coarse ground, kept true per "
                "commit; every other piece is built when asked for, from "
                "the positions the run's manifest has published as of that "
                "request, and nothing else."
            ),
            "governed_from": run.as_posix(),
            "piece": composer.piece,
            "baked": baked,
        }
        (store / "zarr.json").write_text(json.dumps(described, indent=1), encoding="utf-8")
        if bake:
            governed.stamp_the_bake(store, events=folded, tail=tail, layout=revision)
        return store
    finally:
        governed.close()


def _bake_the_coarse_ground(
    store: Path, composer: Composer, described: dict, *, governed_run: Path | None = None, told=None
) -> list[int]:
    """Build the coarse ground once, into real files, and extend the pyramid."""
    coarsest = composer.mosaic.levels - 1
    pinned = sorted(composer.pinned_levels)
    datasets = described["attributes"]["ome"]["multiscales"][0]["datasets"]

    built_by_workers = False
    if governed_run is not None and _BAKE_PROCESSES > 1:
        composer.stop_warming()
        try:
            working = ProcessPoolExecutor(
                max_workers=_BAKE_PROCESSES,
                mp_context=get_context("spawn"),
                initializer=_start_baking,
                initargs=(governed_run, composer.piece),
            )
            try:
                stripes = []
                for level in pinned:
                    down = composer.grid(level)[1]
                    for worker in range(min(_BAKE_PROCESSES, down)):
                        rows = tuple(range(worker, down, _BAKE_PROCESSES))
                        if rows:
                            stripes.append(working.submit(_bake_one_stripe, store, level, rows))
                # Consumed in order; a stripe that cannot be built stops the
                # bake, exactly as the serial loop's first failure would.
                for stripe in stripes:
                    stripe.result()
            finally:
                working.shutdown(wait=True, cancel_futures=True)
            built_by_workers = True
        except BrokenProcessPool:
            print(
                "The bake's worker processes could not start (usually: "
                "the calling script runs its work at import time, and a "
                "worker re-imports it -- guard the script with "
                "'if __name__ == \"__main__\":'). Baking serially "
                "instead, which is slower and otherwise identical."
            )
    if not built_by_workers:
        moments, channels = composer.mosaic.frame_room
        grown = (moments, channels) != (1, 1)
        # One unit of progress per row of pieces per frame, counted up front
        # so the ratio is honest from the first report.
        total = (
            moments
            * channels
            * sum(composer.grid(level)[0] * composer.grid(level)[1] for level in pinned)
        )
        done = 0
        for level in pinned:
            deep, down, across = composer.grid(level)
            for moment in range(moments):
                for channel in range(channels):
                    frame = (str(moment), str(channel)) if grown else ()
                    for plane in range(deep):
                        for row in range(down):
                            done += 1
                            if told is not None:
                                told(done, total)
                            inside = store.joinpath(str(level), "c", *frame, str(plane), str(row))
                            for column in range(across):
                                body = composer.bytes_for(
                                    level, plane, row, column, moment=moment, channel=channel
                                )
                                if body is None:
                                    continue
                                inside.mkdir(parents=True, exist_ok=True)
                                (inside / str(column)).write_bytes(body)

    whole = np.asarray(zarr.open_array(str(store / str(coarsest)), mode="r"))
    room = whole.shape[:-3]
    depth, height, width = whole.shape[-3:]
    voxel = list(composer.mosaic.voxel_um(coarsest))
    level = coarsest
    while height > composer.piece or width > composer.piece:
        level += 1
        height, width = -(-height // 2), -(-width // 2)
        voxel = [voxel[0], voxel[1] * 2, voxel[2] * 2]
        evened = np.pad(
            whole,
            [(0, 0)] * (len(room) + 1)
            + [(0, height * 2 - whole.shape[-2]), (0, width * 2 - whole.shape[-1])],
            mode="edge",
        )
        whole = (
            evened.reshape(*room, depth, height, 2, width, 2)
            .mean(axis=(-3, -1))
            .round()
            .astype(composer.mosaic.dtype)
        )
        made = zarr.create_array(
            store=str(store / str(level)),
            shape=(*room, depth, height, width),
            chunks=(1,) * len(room) + (1, composer.piece, composer.piece),
            dtype=composer.mosaic.dtype,
            zarr_format=3,
            dimension_names=(["t", "c"] if room else []) + list(composer.mosaic.axes),
            overwrite=True,
        )
        made[:] = whole
        datasets.append(
            {
                "path": str(level),
                "coordinateTransformations": [
                    {"type": "scale", "scale": [1.0] * len(room) + list(voxel)},
                    {
                        "type": "translation",
                        "translation": [0.0] * len(room) + list(composer.mosaic.corner_um),
                    },
                ],
            }
        )

    return pinned + list(range(coarsest + 1, level + 1))


def main() -> None:
    import argparse

    parsed = argparse.ArgumentParser(description=__doc__)
    parsed.add_argument("transfer", type=Path)
    parsed.add_argument("where", type=Path)
    parsed.add_argument("--name", default="built")
    parsed.add_argument("--piece", type=int, default=PIECE)
    parsed.add_argument(
        "--bake",
        action="store_true",
        help="also build the coarse ground now, once, into "
        "real files, so opening never builds it again. "
        "Declaring without this removes any earlier bake.",
    )
    parsed.add_argument(
        "--workers",
        type=int,
        default=1,
        help="how many processes build while baking; one builds in place",
    )
    given = parsed.parse_args()

    store = declare_a_built_picture(
        given.where,
        given.transfer,
        name=given.name,
        piece=given.piece,
        bake=given.bake,
        workers=given.workers,
    )
    print(f"\n  declared {store}")
    print("  it holds no pixels; open the folder above it in the viewer.\n")


if __name__ == "__main__":
    main()
