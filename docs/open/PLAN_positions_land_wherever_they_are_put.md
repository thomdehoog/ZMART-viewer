# Positions land wherever they are put: the free-placement gate

> Written 2026-08-29. A plan, not yet built. It designs one new test
> file that pins a property the suite only grazes today: a position's
> pixels appear exactly where that position's own translation says, for
> translations chosen adversarially — random, fractional, overlapping,
> negative — across both OME-Zarr generations, the awkward stores, and
> every loading mode, baked and unbaked. And when a position does not
> land right, the failure names where it actually ended up.

## The want

Today's placement gates put positions where a microscope would: on a
grid, at recorded plate places, or drifted a little off a grid. Free
placement is the stronger claim the viewer actually makes — every store
carries its own translation and the arrangement follows — and smart
microscopy will lean on it: a target scan lands wherever the operator
pointed, on top of whatever is already there. So the gate scatters
positions on purpose and checks the picture, not the plumbing.

## The property, and the oracle that checks it

**Property.** For any set of positions with arbitrary translations, the
served picture equals the reference paste: each tile placed at its
translation rounded to the nearest voxel *of the level being drawn*
(the documented rule in compose.py), later tiles pasted over earlier
ones where they overlap, ground no tile covers absent.

**Oracle.** The reference paste is computed in the test with plain
numpy, from the same translations, at every level. Two devices make a
mismatch tell its own story:

- **Stamped bodies.** Each tile's pixels carry its identity (the
  `the_stamp` pattern the suite already uses), so wrong ground shows as
  the wrong stamp, not as a subtle brightness difference.
- **Corner markers.** Each tile's origin voxel carries a unique marker
  value. On failure, a diagnostic scans the served picture for every
  marker and reports, per position, *asked-for corner → found corner*
  (or "not found at all"), so the answer to "where did placing go
  wrong" is in the failure text, not in a debugger.

Comparison is exact array equality at level 0. At coarser levels the
reference applies the same per-level rounding rule rather than
downsampling its own level-0 paste — the two differ by design, and the
composer's rule is the contract.

**The second oracle, nearly free.** A baked view must equal the unbaked
view bit for bit — the bake writes what the composer would have served.
Every case below is served once unbaked, then baked and compared piece
for piece. This one check carries "with and without baking" for the
whole matrix.

## What is pinned besides pixels

- **The declared room.** The view's description must declare the exact
  bounding box of the scattered placements, per level under the
  rounding rule — negative translations shift the origin rather than
  clipping a tile.
- **Overlap order.** Two tiles at the *same* translation with different
  stamps pin which one wins, in the composer's documented order. This
  is asserted once, explicitly, so the rule cannot drift silently.
- **Absence stays absence.** A far outlier makes the canvas sparse; the
  empty pieces between must answer as absent, never as written zeros.
- **The refusals of the grid-bound modes.** Free placement is not every
  mode's promise, and the gate pins the boundary rather than ignoring
  it: the pointer-linked map must *refuse* to be written over tiles off
  the whole-chunk grid (a lying map would be worse than none), and the
  replay planner already refuses off-grid datasets in plain words —
  asserted here with a scattered input, so the refusal survives.

## The matrix

Rows — the inputs, all written by the existing fixture writers plus one
new scatter helper:

| input | from |
|---|---|
| positions, 0.4 (zarr v2) | `make_test_stores` shapes |
| positions, 0.5 (zarr v3), plain and sharded | the v3 writer in `test_zarr_v3` |
| multi-channel, and a short timelapse (t > 1) | same writers — placement must be identical for every (t, c) |
| a plate whose fields carry random recorded places, overlapping | the plate writer in `test_a_plate_lays_itself_out` |
| every awkward store: t-of-one, no pyramid, one channel with the axis kept, one plane, flat 2-D, 8-bit, float | `testdata/make_awkward_stores` — each in both generations |

Columns — the modes:

1. composed, unbaked (the default door);
2. composed, baked (equality with 1 is the assertion);
3. opened through `loading.load` as a folder, and through the build
   door — one view either way, already gated elsewhere, spot-checked
   here once;
4. the grid-bound modes, as refusals (above), not as placements.

Placements — per case, drawn from a seeded generator (three fixed
seeds, the seed printed on failure): translations uniform over a canvas
smaller than tiles-times-count so overlap is guaranteed; plus, always,
the same hand-picked edge set — exact duplicate, full containment,
fractional offsets at each level's rounding boundary, a negative
corner, one far outlier.

## One photographed flagship

Everything above runs against the composer and the serving door — fast,
no browser, a few seconds per case. One case only (0.5, multi-channel,
scattered with overlaps, baked) also goes through the real page and is
photographed, asserting the marker corners land within one coarse voxel
on screen — proof the translations survive the engine's own transform,
which no array comparison can give.

## Instruments before assertions

Three contract points are read out of the code (or measured) before the
assertions are written, and the plan is wrong wherever they disagree:

1. the composer's overlap order — which tile is "later";
2. how `declare` treats a negative bounding corner today — supported,
   or an honest refusal to pin instead;
3. whether the plate layout accepts overlapping recorded field places
   or separates them — the gate pins whichever the code intends.

## Where it lives, and what it costs

One new file, `tests/test_positions_land_wherever_they_are_put.py`,
with one scatter helper and one marker-scanning diagnostic; fixture
writers are imported from where they already live. Budget: the whole
non-browser matrix inside a minute, the flagship under the usual
browser-test minute — small enough to run on every change, which is the
point of a gate about a property everything else stands on.
