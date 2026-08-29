"""Relive a finished dataset as a live run, one position at a time.

The positions go through the real live writer — sealed profile, manifest,
one commit each, sweep by sweep for a timelapse — so the picture assembles
on screen exactly as during an acquisition: a dress rehearsal for smart
microscopy on data already on disk.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import zarr
from zmart_live.coordinator import LivePublisher
from zmart_live.profiles import DEFAULTS, plan_the_writing

from .compose import read_the_transfer, the_front_axes

GRID_TOLERANCE_UM = 0.1


@dataclass(frozen=True)
class ReplayPlan:
    """Everything a replay needs, worked out once before the first beat."""

    profile: object
    geometry: object
    #: Where each position sits, by name, in the run's own pixels.
    positions: dict
    beats: list
    z_planes: int
    channel_count: int

    @property
    def total(self) -> int:
        return len(self.beats)


def _evenly_spaced(offsets: list[float]) -> bool:
    """Do these offsets sit a single fixed step apart?"""
    distinct = sorted(set(offsets))
    if len(distinct) < 2:
        return True
    steps = [b - a for a, b in zip(distinct, distinct[1:], strict=False)]
    return all(abs(step - steps[0]) <= GRID_TOLERANCE_UM for step in steps)


def _the_one_step(offsets: list[float]) -> float | None:
    """The single step these offsets sit at, or None when there is not one."""
    distinct = sorted(set(offsets))
    if len(distinct) < 2 or not _evenly_spaced(offsets):
        return None
    return distinct[1] - distinct[0]


def plan_a_replay(transfer: str | Path) -> ReplayPlan:
    """Map a dataset onto the live writer's grid, or say plainly why not."""
    mosaic = read_the_transfer(Path(transfer))
    moments, channel_count = mosaic.frame_room
    first = mosaic.tiles[0].copies[0]
    front = the_front_axes(first.outer_shape)
    # The spatial extent of one tile, by axis name rather than position, so a
    # tile with or without front axes reads the same way.
    spatial = dict(zip(("z", "y", "x"), first.shape[-3:], strict=True))
    voxel = dict(zip(("z", "y", "x"), first.voxel_um[-3:], strict=True))
    z_planes = spatial["z"]
    frame = (spatial["y"], spatial["x"])

    corners = {tile.name: tile.copies[0].corner_um[-2:] for tile in mosaic.tiles}
    down = [corner[0] for corner in corners.values()]
    across = [corner[1] for corner in corners.values()]
    on_a_grid = _evenly_spaced(down) and _evenly_spaced(across)
    steps_um = (
        _the_one_step(down) if on_a_grid else None,
        _the_one_step(across) if on_a_grid else None,
    )
    steps_px = tuple(
        None if step is None else step / voxel[axis]
        for step, axis in zip(steps_um, ("y", "x"), strict=True)
    )

    shares = []
    for step, side, axis in zip(steps_px, frame, ("down", "across"), strict=True):
        if step is None:
            continue
        if step > side:
            raise ValueError(
                f"these positions sit {step * voxel['y']:.1f} micrometres apart "
                f"{axis} but one frame only spans {side * voxel['y']:.1f} that "
                "way, leaving gaps between tiles. The live grid lays tiles edge "
                "to edge or overlapping, so this dataset cannot be replayed."
            )
        shares.append((side - step) / side)

    if shares and max(shares) - min(shares) > 0.01:
        raise ValueError(
            f"these positions overlap by {min(shares):.0%} one way and "
            f"{max(shares):.0%} the other, and a live profile declares one "
            "overlap for both. A replay cannot reproduce that layout; open "
            "the dataset instead."
        )

    if shares:
        overlap = shares[0]
        defaults = DEFAULTS["overview"].with_overlap(overlap, overlap, overlap)
    else:
        # A single position has no neighbours, so any overlap will do and the
        # stock plan is the simplest true one.
        defaults = DEFAULTS["overview"]

    channels = tuple(f"channel {index}" for index in range(channel_count))
    profile, geometry = plan_the_writing(
        "overview",
        frame=frame,
        dtype=mosaic.dtype,
        z_planes=z_planes,
        timepoints=moments,
        channels=channels,
        voxel_size=(voxel["z"], voxel["y"], voxel["x"]),
        readable_prefix="replay",
        defaults=defaults,
    )
    wanted = tuple(None if step is None else round(step) for step in steps_px)
    if any(
        step is not None and step != planned
        for step, planned in zip(wanted, geometry.step_shape, strict=True)
    ):
        raise ValueError(
            f"these positions sit {wanted} pixels apart, but the live planner "
            f"can only place this frame {geometry.step_shape} pixels apart. The "
            "replay would draw the tiles somewhere the dataset does not put "
            "them, so it is refused; open the dataset instead."
        )

    least = (
        min(corner[0] for corner in corners.values()),
        min(corner[1] for corner in corners.values()),
    )
    ordered, positions = [], {}
    for tile in sorted(mosaic.tiles, key=lambda t: corners[t.name]):
        name = tile.name.removesuffix(".ome.zarr")
        corner = corners[tile.name]
        positions[name] = {
            "y": round((corner[0] - least[0]) / voxel["y"]),
            "x": round((corner[1] - least[1]) / voxel["x"]),
        }
        ordered.append((name, tile.copies[0].held_in))

    beats = [
        (name, held_in, front, moment) for moment in range(moments) for name, held_in in ordered
    ]
    return ReplayPlan(
        profile=profile,
        geometry=geometry,
        positions=positions,
        beats=beats,
        z_planes=z_planes,
        channel_count=channel_count,
    )


def replay_the_dataset(
    transfer: str | Path, folder: str | Path, *, every_s: float = 0.7, told=None, announce=None
) -> Path:
    """Publish every position of the dataset through the live writer, paced."""
    plan = plan_a_replay(transfer)
    publisher = LivePublisher(
        Path(folder), plan.profile, run_id=Path(transfer).name, positions=plan.positions
    )
    if told:
        told(0, plan.total)
    try:
        for number, (name, held_in, front, moment) in enumerate(plan.beats):
            if number and every_s:
                time.sleep(every_s)
            held = zarr.open_array(str(held_in), mode="r")
            pixels = held[moment] if "t" in front else held[:]
            if "c" not in front:
                pixels = pixels[None]
            publisher.write_and_publish(name, pixels, timepoint=moment)
            if told:
                told(number + 1, plan.total)
            if announce:
                announce()
    finally:
        publisher.finish_the_run()
    return publisher.folder / "views" / "live" / "live.ome.zarr"
