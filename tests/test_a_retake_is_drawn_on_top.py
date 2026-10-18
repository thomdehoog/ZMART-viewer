"""A position taken again is drawn on top: the last one that comes in covers.

Where two positions image the same ground, the picture shows the one that came
in last. A re-take is a position coming in again, so after a re-take the
overlap shows the re-taken pixels -- in the picture composed for the viewer
and in the linked view the server answers from, alike. A later moment of a
timelapse is not a re-take: the tile only grows, and keeps its place.

Runs recorded before this rule (links schema 3, where a position kept its
first-arrival place for good) are still read as they were written.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


VIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(VIZ))

from record_fixtures import FRAME, some_specimen  # noqa: E402
from test_the_composer_obeys_the_manifest import PIECE, pixels_of, the_columns_of  # noqa: E402
from zmart_viewer.live.record.live_serving import answer_from_a_live_run, forget_live_run  # noqa: E402
from zmart_viewer.live.record.storage_plans import plan_the_writing  # noqa: E402
from zmart_viewer.live.record.vocabulary import GridCell  # noqa: E402
from zmart_viewer.picture.built_picture import GovernedRun  # noqa: E402


def a_governed_run(folder: Path):
    """posA at the origin and posB to its right, overlapping; room for two moments."""
    from zmart_viewer.live.record.publisher import LivePublisher

    profile, _ = plan_the_writing("overview", frame=FRAME, z_planes=1, timepoints=2)
    return LivePublisher(
        folder,
        profile,
        run_id="retake-run",
        cells={GridCell(0, 0): "posA", GridCell(0, 1): "posB"},
    )


def inside_the_overlap(run) -> int:
    """A full-resolution column both positions image."""
    b_starts = int(run.layout.placement("posB").origin["x"])
    return (b_starts + FRAME) // 2


def a_retaken_run(tmp_path):
    """posA, then posB over part of it, then posA taken again."""
    run = a_governed_run(tmp_path)
    run.write_and_publish("posA", some_specimen(700))
    run.write_and_publish("posB", some_specimen(1100))
    run.replace_a_position("posA", some_specimen(2200))
    return run


def test_the_composed_picture_shows_the_retake_over_its_neighbour(tmp_path):
    run = a_retaken_run(tmp_path)

    composer = GovernedRun(run.folder, piece=PIECE).composer()
    _, shared, _ = the_columns_of(run)
    piece = pixels_of(composer, 0, 0, 0, shared)

    assert piece[..., inside_the_overlap(run) % PIECE].max() == 2200, (
        "ground posA was taken again over must show the re-take, not posB, "
        "which came in before it"
    )


def test_the_draw_order_puts_the_retake_last(tmp_path):
    run = a_retaken_run(tmp_path)

    assert run._positions_in_commit_order() == ["posB", "posA"]
    held = json.loads(run.link_map_file.read_text(encoding="utf-8"))
    assert held["schema"] == "zmart-live-links/4"
    for level in held["levels"]:
        assert [entry["position_id"] for entry in level["positions"]] == ["posB", "posA"]


def test_a_later_moment_keeps_its_place(tmp_path):
    """A timelapse frame is the same tile growing, not a re-take."""
    run = a_governed_run(tmp_path)
    run.write_and_publish("posA", some_specimen(700))
    run.write_and_publish("posB", some_specimen(1100))
    run.write_and_publish("posA", some_specimen(800), timepoint=1)

    assert run._positions_in_commit_order() == ["posA", "posB"]


def the_linked_answer_in_the_overlap(run):
    """Which store the server reads for a view chunk inside the overlap."""
    described = json.loads((run.view_level() / "zarr.json").read_text(encoding="utf-8"))
    chunk = described["chunk_grid"]["configuration"]["chunk_shape"]
    column = inside_the_overlap(run) // chunk[-1]
    key = "/".join(["c"] + ["0"] * (len(chunk) - 1) + [str(column)])
    forget_live_run(run.folder)
    answered = answer_from_a_live_run(run.view_level() / key)
    assert answered is not None and answered.serving is not None
    return answered.serving.path


def test_the_linked_view_answers_the_overlap_from_the_retake(tmp_path):
    run = a_retaken_run(tmp_path)

    served_from = the_linked_answer_in_the_overlap(run)

    assert served_from.is_relative_to(run.position_store("posA", generation=1)), (
        f"the overlap was answered from {served_from}, not from the re-take of posA"
    )


def test_a_run_recorded_under_the_old_order_is_still_served(tmp_path):
    """Schema 3 kept a re-taken position in its first-arrival place."""
    run = a_retaken_run(tmp_path)
    held = json.loads(run.link_map_file.read_text(encoding="utf-8"))
    held["schema"] = "zmart-live-links/3"
    for level in held["levels"]:
        level["positions"].sort(key=lambda entry: ["posA", "posB"].index(entry["position_id"]))
    run.link_map_file.write_text(json.dumps(held, indent=2), encoding="utf-8")

    served_from = the_linked_answer_in_the_overlap(run)

    assert served_from.is_relative_to(run.position_store("posB")), (
        "an old run draws as it was recorded: posB, the later first arrival, on top"
    )
