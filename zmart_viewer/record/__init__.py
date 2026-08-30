"""The record of an acquisition: how a run writes itself, and what that means.

This is the machinery a smart-microscopy run stands on -- profiles and
geometry, placement and seam ownership, the manifest of signed commits, the
layout, and the publisher that writes pixels and pyramids as the microscope
acquires. It lives inside the viewer so the whole story of a run -- writing
its record and every way of reading it -- is one codebase, tested against
itself; a controller drives real hardware through this same package rather
than through a copy that could drift.

:mod:`~zmart_viewer.record.model` is the vocabulary the rest of it speaks in.
:mod:`~zmart_viewer.record.profiles` chooses how one kind of acquisition is
written. :mod:`~zmart_viewer.record.identity` names those choices after their
own contents and keeps them, and the run's layout snapshots, where they can
be found again.
"""

from .identity import (
    load_the_profile,
    record_the_layout,
    store_the_profile,
)
from .model import (
    AcquisitionProfile,
    Box,
    CommitEvent,
    GridCell,
    Interval,
    LevelGeometry,
    MosaicComponent,
    OverlapBand,
    PositionPlacement,
    SceneLayoutRevision,
    ZmartLiveError,
    check_the_name_is_safe,
    is_a_safe_name,
)
from .profiles import DEFAULTS, AcquisitionDefaults, Geometry, plan_the_writing

__all__ = [
    "DEFAULTS",
    "AcquisitionDefaults",
    "AcquisitionProfile",
    "Box",
    "CommitEvent",
    "Geometry",
    "GridCell",
    "Interval",
    "LevelGeometry",
    "MosaicComponent",
    "OverlapBand",
    "PositionPlacement",
    "SceneLayoutRevision",
    "ZmartLiveError",
    "check_the_name_is_safe",
    "is_a_safe_name",
    "load_the_profile",
    "plan_the_writing",
    "record_the_layout",
    "store_the_profile",
]
