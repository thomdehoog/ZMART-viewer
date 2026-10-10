"""A position whose name holds a dot is repaired in the bake like any other.

Position names may contain dots; only the ending ``.generation-N`` is
reserved, for a replacement's folder. The bake's catch-up after a restart and
the patching of the composer's index both cut a folder name at its first dot
to find the position, so ``pos.B`` was looked for as ``pos``, never found,
and the bake was stamped as up to date while it still lacked ``pos.B``.
Zoomed out, that position stayed missing for good (finding M2 of the review
of 10 October 2026).

Byte equality with a fresh bake of the same run is what "repaired" means,
the same yardstick the other bake tests use.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

VIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VIZ))
sys.path.insert(0, str(VIZ.parent))

from record_fixtures import FRAME, some_specimen  # noqa: E402
from test_a_governed_picture_is_baked_per_commit import (  # noqa: E402
    a_fresh_bake_of,
    every_baked_file,
)
from test_the_composer_obeys_the_manifest import PIECE  # noqa: E402
from zmart_viewer.live.record.publisher import LivePublisher  # noqa: E402
from zmart_viewer.live.record.storage_plans import plan_the_writing  # noqa: E402
from zmart_viewer.live.record.vocabulary import GridCell, the_position_of  # noqa: E402
from zmart_viewer.picture.built_picture import declare_a_governed_picture  # noqa: E402
from zmart_viewer.serving import picture_pieces as served  # noqa: E402


def a_run_of(folder: Path, first: str, second: str) -> LivePublisher:
    profile, _ = plan_the_writing("overview", frame=FRAME, z_planes=1)
    return LivePublisher(
        folder,
        profile,
        run_id="dotted-names",
        cells={GridCell(0, 0): first, GridCell(0, 1): second},
        timepoints=1,
    )


@pytest.mark.parametrize("first, second", [("posA", "posB"), ("pos.A", "pos.B")])
def test_a_position_landing_while_the_viewer_was_stopped_is_baked(tmp_path, first, second):
    run = a_run_of(tmp_path, first, second)
    run.write_and_publish(first, some_specimen(700))
    store = declare_a_governed_picture(
        run.folder / "views" / "shown", run.folder, name="live", piece=PIECE, bake=True
    )
    served.forget(store)

    run.write_and_publish(second, some_specimen(4242))
    try:
        assert served.built_bytes_behind(store, "0/c/0/0/0") is not None
        after = every_baked_file(store)
    finally:
        served.forget(store)

    reference = a_fresh_bake_of(run.folder, tmp_path / "reference")
    differ = sorted(
        one for one in after.keys() | reference.keys() if after.get(one) != reference.get(one)
    )
    assert differ == [], f"the bake after the restart lacks {second}: {differ}"


@pytest.mark.parametrize(
    "folder, position",
    [
        ("posA", "posA"),
        ("pos.A", "pos.A"),
        ("pos.A.generation-3", "pos.A"),
        ("pos.A.GENERATION-12", "pos.A"),
        ("a.b.c", "a.b.c"),
    ],
)
def test_the_position_of_a_folder_drops_only_the_generation_ending(folder, position):
    assert the_position_of(folder) == position
