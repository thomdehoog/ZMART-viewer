# The writer keeps the record; the viewer owns every way of reading it

> Written 2026-08-31, agreed with the operator — and then taken one step
> further the same day, at the operator's word: the viewer is now
> **self-contained**. The record machinery lives inside this repository as
> `zmart_viewer/record/` (formerly the `zmart_live` package), the three
> small utilities it needed from `zmart_storage` are folded into
> `record/model.py`, the run fixtures live with the tests
> (`tests/record_fixtures.py`), and the elder writers the compatibility
> gates exercise live in `testdata/oldwriter/`. The external
> `zmart-microscopy` dependency is gone from `pyproject.toml`. The plan
> below is kept as the reasoning; its "microscopy steps" now read as the
> other repo's migration: delete its copy of this machinery and drive the
> hardware through this package instead.

## The boundary

**The microscope repo (`zmart_live`) keeps the record** — everything that
must survive with the instrument and mean the same thing forever:

- writing a position's pixels and pyramids (`write_a_position`,
  `replace_a_position`, the profiles and geometry that plan them);
- the manifest — events, signed revisions, generations — and the layout
  (`locations.json`): what happened, when, in what order;
- publishing: `publish`, `write_the_layout`. The ~2.3 s per landing is
  flat and acquisition-bound; a leaner or parallel publish stays a
  microscope-side option, wanted only above ~0.4 landings/s.

**The viewer (`zmart_viewer`) owns every way of reading it** — anything
that exists so the run can be shown:

- the governed picture (already here): the mid-run and post-run serving
  path, derive, bake;
- the linked zero-copy view: the pointer map from view pieces to the
  positions' own bytes — today written by the writer
  (`write_the_link_map`, `write_the_view`, `links.json`,
  `live.ome.zarr`) and served by the writer's gateway
  (`answer_from_a_live_run`);
- the shard-index knowledge those pointers need (`shardlink`) — reading
  where an inner chunk sits inside a shard file is a *reading* concern.

## Why

One machinery per concern. The viewer already serves a run from nothing
but data + manifest (proved by the governed picture and the scale
benchmarks). The writer's view-making duplicates that concern inside the
acquisition code, and every writer-side pain found this chapter lives in
that half: the day-zero pointer-map refusal, the finish that could not
complete, the gateway's O(events) cold open (~60 s at 10,000 for a fresh
reader). Moving the reading story here puts those fixes in code this
repo controls.

## What the viewer builds now (this repo, done in this chapter)

`pieces.link_a_finished_run(run_root)`: build the pointer map for a
run's committed truth **into the governed picture's own store** — no new
view, no second store. The map extends the viewer's own links format
with shard entries: per tile, the shard file and its inner-chunk index
(offsets and lengths, read once at link time), so serving needs no shard
parsing and no history walk — the minute-long cold open becomes a
one-time map read. Honesty rules, refusals in words:

- off-chunk placements cannot be pointer-linked (the day-zero rule,
  said here too);
- the map records the signed revision it was built at; a commit after
  linking makes the pointers answer absent and the governed picture
  serves — a stale map never lies. Re-linking refreshes it;
- byte handover requires the view's declared level-0 encoding to be the
  tiles' own; a mismatch refuses rather than hands over bytes the
  reader would mis-decode.

The serving ladder is unchanged: pointed → baked file → composed, with
the gateway's guard answering `None` for the picture store as it always
did.

## The microscopy repo's steps (when access arrives)

1. Land `HANDOVER_the_pointer_map_decides_on_day_zero.md` (the day-zero
   patch and its two tests) — still worth it while the writer's view
   exists.
2. Grow the record API: `add_a_position(position_id, origin)` and
   `timepoints=None` as generous room (same handover, growth items).
3. Retire the writer's view-making: `write_the_view`,
   `write_the_link_map`, the links schema, and the gateway's serving
   half, once callers read runs through the viewer. `finish_the_run`
   shrinks to `write_the_layout` plus a completion mark.
4. Move `shardlink`'s reading half into the viewer (the viewer carries
   its own shard-index reader from this chapter; delete the duplicate).
5. Outside tools that read a run without this viewer keep working
   through the viewer's server or a plain export — the linked view was
   never plain-readable anyway (its chunks are virtual and need a
   resolver); the resolver's home is the viewer.

## What deliberately does not move

- The record's integrity machinery (signed revisions, recovery,
  generations) — that is the experiment's truth, not a view of it.
- Replay (`rehearsal.py`) keeps writing through the real writer: a
  replay *is* an acquisition.
