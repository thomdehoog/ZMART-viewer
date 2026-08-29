# Positions land wherever they are put: the free-placement gate

> Written 2026-08-29, built the same day:
> `tests/test_positions_land_wherever_they_are_put.py`, 26 gates, all
> green. Two findings on the way in are recorded at the end. It designs one new test
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
view — decoded pixels identical, piece for piece; byte equality is
noted where it holds but is not the contract, which keeps the gate out
of the encoder's business. Every case is served once unbaked, then
baked and compared. This one check carries "with and without baking"
for the whole matrix.

**The oracle is anchored before it is trusted.** A reference written by
reading compose.py would share its bugs. So the reference is pinned
first against hand-computed micro-cases — one tile at offset 3.4 and
one at 3.6, expected voxel indices written out by hand at level 0 and
at a coarser level — and only then is it allowed to judge the random
sweep.

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
- **The one honest refusal.** The pointer-linked map must *refuse* to
  be written over tiles off the whole-chunk grid — a lying map would be
  worse than none. It is the only mode allowed to say no: scattered
  placement is required to work everywhere else, live included.

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

Columns — the four ways of showing, each run once with nominal
placements and once scattered:

1. **static, unbaked** — everything on disk, opened at once;
2. **static, baked** — equality with 1 is the assertion;
3. **sequential, unbaked** — positions appear one at a time through
   the contract path (the replay door and the live registry). The
   watched-foreign-folder arrival is deliberately not a column here:
   a foreign store joins as its own engine source, so overlap there is
   the engine's compositing, which no array oracle can judge — and the
   arrival itself is already gated elsewhere;
4. **sequential, baked** — the same arrival with the per-commit bake
   patching as each position lands.

Scattered must pass in every column, sequential included — that cell
is a placement check, not a refusal (the chapter below makes it so).
A plate growing live stays a named gap. And the matrix is pruned
where breadth would buy nothing: the awkward stores run the static
columns only (their difficulty is being read, not arriving), the broad
sweep uses one seed with three reserved for the flagship shape, and
at least one case per column is served through the real HTTP door —
the four-answer serving ladder is precisely where a stale baked file
could lie, and composer-level calls would never see it.

Placements — per case, drawn from a seeded generator (three fixed
seeds, the seed printed on failure): translations uniform over a canvas
smaller than tiles-times-count so overlap is guaranteed; plus, always,
the same hand-picked edge set — exact duplicate, full containment,
fractional offsets at each level's rounding boundary, a negative
corner, one far outlier.

## The chapter that opens the live cell

Scattered placement already works where the pixels are made: the
composer places every tile by its own translation, and the governed
picture resolves overlap by later-commit-wins — the per-commit patch
neither knows nor cares that landings share ground. What is grid-bound
is only the two entrances:

- the **replay planner** fits the dataset to a regular grid before
  writing, and refuses what does not fit. It learns to plan free
  placements: the plan *is* the list of each position's own
  translation, and the grid fit becomes one special case of it;
- the **live writer's location model** (zmart_live, the microscopy
  checkout) addresses positions as grid cells. It learns to carry an
  explicit place per position — `locations.json` already fixes every
  place before the first pixel, so this widens what a place may be,
  not when it is fixed.

First instrument, then change: feed scattered locations straight
through the publisher and record exactly what rejects them, so the
change is as small as the rejection and no smaller. The location model
lives in the microscopy checkout, not this one; if the rejection is
there, the change is made there on its own branch, and until it lands
the sequential-scattered cell is an xfail carrying the recorded
rejection word for word — the viewer side never blocks on it. The juicy new
assertion this opens: a scattered landing that overlaps committed
ground must patch exactly the union footprint, later commit winning
where they touch — the live half of the overlap rule, checked with the
same stamps and markers as the static half.

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
non-browser matrix in a few minutes, the flagship under the usual
browser-test minute — small enough to run on every change, which is the
point of a gate about a property everything else stands on.

## Built, and what building found

The gate exists and is green: the static matrix (both generations,
multichannel, timelapse, the awkward dtypes, nominal and scattered),
bake-equals-unbaked, one case through the real HTTP door, the plate
with overlapping recorded places, every live landing checked as it
commits, the scattered off-chunk replay end to end, the pointer map's
refusal pinned at both moments, and the photographed flagship.

Building it found and fixed two real faults: an eight-bit store could
not be composed at all (the codec honesty guard tripped over the
endianness a one-byte dtype does not have — the declaration now follows
the dtype), and a flat two-axis store crashed the tile reader instead
of being refused in words. It also settled two contract points the plan
had left open: the composer rounds halves up (floor of value plus one
half — the oracle's first random sweep caught the difference from
banker's rounding immediately), and a scattered run could not FINISH
(the deferred link map refused at run end). That last piece is now
designed, validated against this gate, and handed over:
`HANDOVER_the_pointer_map_decides_on_day_zero.md` beside this plan
carries the patch for the microscopy checkout — linkability decided at
construction, `per_publish` refused before the first pixel, finish
clean without the map.
