# Handover: the pointer map's fate is decided on day zero

> For the ZMART-microscopy checkout (`zmart_live`), branch
> `claude/thy1-linked-spiral` — the branch this viewer's pyproject pins.
> Written 2026-08-29, validated end to end against this viewer's
> free-placement gate with the patch applied to the installed package.

A run's placements are known at the publisher's construction, so whether
the pointer-linked view can ever be written is known then too. Today that
fact surfaces as a refusal at the FIRST PUBLISH (`per_publish`) or at
`finish_the_run()` (`at_run_end`) — the end of an experiment discovering
what was knowable before its first pixel, and a deferred-view run that can
never finish cleanly.

The change, in `zmart_live/coordinator.py`:

- at construction, compute whether every placement lands on whole level-0
  chunks and expose it as `publisher.pointer_linkable`;
- `per_publish` over off-chunk placements is refused there and then, in
  plain words, before the first pixel and before any record;
- `finish_the_run()` on a non-linkable run records the layout and skips
  the map and view deliberately — the governed picture is how such a run
  is served — and returns normally.

The viewer side is already flipped and holds these gates (held back from
its remote until this lands, so fresh installs stay coherent):
`test_the_pointer_map_refuses_off_chunk_placements_on_day_zero` and
`test_a_scattered_run_finishes_cleanly_without_the_pointer_map` in
`tests/test_positions_land_wherever_they_are_put.py`.

A test worth adding beside the writer's own:

```python
def test_off_chunk_places_are_refused_before_the_first_pixel(tmp_path):
    profile, _ = plan_the_writing("overview", frame=384, channels=("channel 0",))
    with pytest.raises(ZmartLiveError, match="whole chunks"):
        LivePublisher(tmp_path / "run", profile, run_id="x",
                      positions={"p": {"y": 190, "x": 117}})
    written = [p for p in (tmp_path / "run").rglob("*") if p.is_file()]
    assert all(p.name == "zarr.json" for p in written)


def test_a_non_linkable_run_finishes_cleanly(tmp_path):
    profile, _ = plan_the_writing("overview", frame=384, channels=("channel 0",))
    publisher = LivePublisher(tmp_path / "run", profile, run_id="x",
                              positions={"p": {"y": 190, "x": 117}},
                              linked_view="at_run_end")
    assert publisher.pointer_linkable is False
    publisher.write_and_publish("p", np.full((1, 384, 384), 7, dtype=np.uint16))
    publisher.finish_the_run()
    assert not any((tmp_path / "run").rglob("links.json"))
```

The patch, against the branch as installed here:

```diff
--- a/zmart_live/coordinator.py	2026-08-29 12:48:34.192270319 +0000
+++ b/zmart_live/coordinator.py	2026-08-29 12:49:04.461240657 +0000
@@ -354,6 +354,24 @@
                 profile_id=self.profile.profile_id,
                 complete=True,
             )
+        chunk = self.profile.levels[0].inner_chunk
+        off_chunk = sorted(
+            name
+            for name, place in self.positions.items()
+            if place["y"] % chunk["y"] or place["x"] % chunk["x"]
+        )
+        object.__setattr__(self, "pointer_linkable", not off_chunk)
+        if off_chunk and self.linked_view == "per_publish":
+            raise ZmartLiveError(
+                f"{len(off_chunk)} of this run's placements (first: {off_chunk[0]!r}) "
+                f"do not land on whole chunks of {chunk['y']} by {chunk['x']} pixels, "
+                "so the pointer-linked view can never be written for it. The places "
+                "are known now, before the first pixel, so it is said now rather "
+                "than at the first publish: open this run with "
+                "linked_view='at_run_end'. The governed picture serves it either "
+                "way, and the run then finishes without the linked plain-file view."
+            )
+
         placements = place_the_positions(
             self.profile, self.positions,
             cells=({name: cell for cell, name in self.cells.items()}
@@ -1826,8 +1844,17 @@
 
         Harmless on a per-publish run, whose products are already current;
         calling it twice simply restates what is there.
+
+        A run whose placements do not land on whole chunks
+        (``pointer_linkable`` is False, decided at construction) has no
+        pointer-linked view to write: the layout is recorded and the map and
+        view are skipped, deliberately and finally — the governed picture is
+        how such a run is served.
         """
         self.write_the_layout()
+
+        if not getattr(self, "pointer_linkable", True):
+            return
         self.write_the_link_map(frozenset(self._committed_units()))
         self.write_the_view()
 
```

## The growth items, same branch

A live experiment grows two ways the writer does not yet allow, and both
are day-zero questions in the same sense as the pointer map: the canvas
is declared upfront, what fills it is not.

**A position may join a running run.** Today `positions` is fixed at
construction and there is no way to add one; smart microscopy points a
target scan mid-run at ground nobody declared. Wanted:

```python
publisher.add_a_position(position_id, {"y": ..., "x": ...})
```

recording a new layout revision (the machinery is already versioned —
`record_the_layout` reuses a revision only for an unchanged arrangement),
validating the place against the same whole-chunk rule at the same
moment (an off-chunk addition to a `per_publish` run is refused in the
day-zero words above; on an `at_run_end` run it simply keeps
`pointer_linkable` honest), and leaving every published revision meaning
what it meant. The viewer's gate is already in and waits on exactly this
name: `test_a_position_joins_a_running_survey_where_it_is_put` in
`tests/test_positions_land_wherever_they_are_put.py` skips while
`LivePublisher` lacks `add_a_position` and runs the day it appears.

**Time room should be roomy by default.** `timepoints=None` today means
"the profile's count", and a write past it is refused. The viewer side
has already proven that generous declared room is metadata-only — a room
of 500 moments costs nothing until pixels arrive, and the slider follows
`committed_time_ranges`, never the declared room. So let `timepoints=None`
mean a generous default room (hundreds), not the profile's minimum: an
open-ended experiment then never hits the wall, and nothing is allocated
for moments that never come.

**The gateway answers one piece at O(events).** `answer_from_a_live_run`
costs ~9 ms per answer at 400 committed positions, ~261 ms at 2,500 and
~5.9 s at 10,000 (measured in the viewer's
`measure/measure_the_four_ways_of_serving.py`): each call walks the run's
whole history. An external reader of a big run's linked view pays that on
every chunk request. The viewer's own governed serving is unaffected (a
non-live path answers `None` in ~1 ms), but the linked view's promise —
outside tools read it cheaply — inverts at scale. An incremental reader
(the manifest's own `events()` already reads only what is new; the
gateway's per-call state could be held the same way) restores it.

**What one more landing costs, measured.** The viewer's derive after a
landing is O(change): at most one tile read, never a survey re-read
(gated count-wise in `test_one_more_landing_reads_one_tile_no_matter_the_survey`).
The writer's own landing is where survey size can leak in: `publish()`
walks the full event history three times (`_committed_units`,
`next_revision`, `_declare_the_current_members`) and rewrites the whole
members list per commit. `manifest.events()` parses incrementally, so
these are pure in-memory sweeps plus one O(positions) JSON write — but
they run on every landing. If landing cost is found to grow with the
survey (measured numbers live in the viewer's
`docs/open/PLAN_positions_land_wherever_they_are_put.md` growth note),
this is the place to look first.
