# Top, Slice and Projection data: isolated implementation plan

Date: 2026-09-09
Branch: `codex/view-modes-data`
Base: `b33a21cb8ff9c3edb6a49c75e46ee5e72790818b`
Status: Implemented on the isolated viewer branch; final verification recorded in
[view_modes_030_results.md](view_modes_030_results.md).
Target: **ZMART-viewer 0.3.0** on the feature branch, not a main-branch release.
The user's subsequent instruction explicitly defers operator/rig integration.
Leave the microscope fix branch and its pinned dependency unchanged.

## Implementation decisions (supersede alternatives below)

- Each named Top/Slice view uses one mixed aggregate, not separate flat/stack
  pictures. Composition therefore preserves the producer's order even when
  flat and stack positions interleave. Legacy two-picture publication is unchanged.
- Top uses one shared plane-selection rule for pixels and coverage. Its coarse
  bake stores one canonical chunk per constant sampling run. HTTP chunk requests
  resolve aliases; no hardlinks, repeated flat volume or slider-triggered publication.
  Staggered stacks can have no useful run compression; this is explicitly tested.
- Per-position Min/Max/Sum products use TCZYX singleton Z and immutable revision
  filenames. Old products are retained, not automatically garbage-collected.
- Completed revisions are recomputed, not assumed to be append-only plane events.
  The producer calls publication from its existing asynchronous/coalesced worker.
  This increment adds no second queue or polling protocol.
- Each view commits separately. Preflight/overflow errors leave the previous
  publication intact. Later I/O failures can leave another view committed; those
  successes are announced, failed views refuse reads until recovery, and retry
  completes the remainder. This is not acquisition-wide atomicity.
- Real browser failures justified contained Neuroglancer metadata-growth fixes:
  update shared geometry for every channel, invalidate reusable caches once, and
  retry failed metadata loads without requiring a later acquisition.
- The remaining sections retain the reviewed plan and its original gate rationale.
  Step 6's operator work is a separate future task, not a condition for delivering
  this viewer-only branch. See [the usage guide](../view_modes.md) for the API and limits.

## Scope and isolation

The microscope fixes remain on `codex/mixed-acquisition-depth` and operator
`codex/operator-relative-z-integration`, awaiting their real-microscope check.
Develop the new data views in a separate worktree. Do not change the rig, its
installed dependencies, the running session, or the operator's pinned viewer.
Any subsequently verified bug fixes can be brought into this branch separately.

Implement Top, Slice and Projection data views, with their correct coarse
representations. **3D rendering is excluded.** The selected UI direction is
design B: descriptive dropdown immediately left of Carrier. UI wiring comes
after the data behavior is proved, rather than enabling unfinished modes.
The agreed semantics are in [view_modes.md](view_modes.md).

## Verified starting points

- `PublishedAcquisition` accepts mixed acquisitions by routing contributors to
  at most two `PublishedTransfer` outputs. Each output is homogeneous: the
  low-level publisher rejects mixed flat/stack contributors and kind changes.
  Stacks use recorded references for relative-Z placement. Derived flat copies
  are placed at display Z=0 with unit Z spacing; this does not rewrite originals
  or preserve their physical Z as display geometry. The separate operator writer
  already uses a flat-Z convention, with acquisition height in provenance.
- The page marks flat outputs `persistentFlat`; `keepFlatDepthLocal` hides their
  Z axis from global slicing. This implements flat persistence, not per-stack
  boundary holding. New Slice views must not inherit that behavior.
- `Composer._build_slab` intersects requested Z with each contributor's bounds.
  `coverage_for` also checks Z membership. Top requires a shared sampling
  decision for both signal and coverage before composition.
- Existing materialized coarse data describes current slicing behavior. It is
  not automatically valid for boundary-held Top or Z-reduced Projection.
  Coverage is currently generated on demand by `coverage.py`, not baked beside
  the image. Equality between two calls to that same mask generator is not an
  independent correctness proof.
- Coverage serving is currently gated by `transparent_background`; dense opaque
  shaders are also coupled to that option. Named acquired views need coverage
  correctness with either an opaque or transparent page background.
- The existing composer uses Z/Y/X geometry and validates ZYX, CZYX or TCZYX
  acquired sources. For 0.3.0, save projections as T/C/Z/Y/X with singleton Z;
  a general native T/C/Y/X reader is not part of this increment.
- Operator TIFFs are in `<run>/<acquisition>/data/`; converted original OME-Zarr
  positions are currently in `<run>/positions/<acquisition>/`. No files in either
  location should be changed by projection or mode selection.

## Stored view naming and bake ownership

Use `<name>_<view>.zmartview.zarr` in `view/`, with an underscore separating
the dataset name from the view suffix:

```text
view/
  overview_slice.zmartview.zarr/
  overview_top.zmartview.zarr/
  overview_min.zmartview.zarr/
  overview_max.zmartview.zarr/
  overview_sum.zmartview.zarr/
```

These are independent named views; any subset may be present. Metadata declares
the view type and projection method, rather than relying on filename parsing.
Each view owns its materialized coarse bake levels. Fine levels reference the
separate originals or per-position projection products. Bake off retains the
same naming and identity, computing coarse chunks on demand instead of storing
them. This is the target layout, not a claim that the current live publisher
already follows it; account for its existing internal names during implementation.
The store root supplies view identity in HTTP/cache addresses. Keep the existing
TCZYX chunk-key grammar inside each store; no extra view axis or path segment is
needed inside an array.

A UI view selection belongs to the acquisition, not to one internal component.
If separate flat/stack stores are retained, names such as
`overview_flat_top.zmartview.zarr` and `overview_stack_top.zmartview.zarr` identify
components of one Top choice. Metadata groups them; one selection switches all
components. The simpler names above describe single-component views. Choose the
physical split from the overlap proof, not from filename parsing.

## Proposed saved projection layout

Use `projections/` next to an acquisition's `data/` for durable derived products:

```text
run/
  targets/
    data/                         acquired TIFFs and metadata
    projections/
      max/                        all acquired Z planes, per position
        targets_P000001.ome.zarr
        targets_P000002.ome.zarr
      min/
        targets_P000001.ome.zarr
      sum/
        targets_P000001.ome.zarr
  positions/
    targets/                      separate original OME-Zarr positions
```

The diagram illustrates a proposal, not directories created in an actual run.
Preserve one projected store per input position, then use the shared aggregate
for bounded-source-count display. Do not create a full-resolution stitched
projection mosaic merely to feed the viewer.

Scope clarification, 2026-09-09: implement **Min, Max and Sum only**, each over
all acquired Z planes of its own position. No Focus preset or selected-range
controls in this increment. The directory names therefore need no range suffix.

Projections are logically T/C/Y/X, stored as T/C/Z/Y/X with Z length one. The
singleton is a storage/display axis, not a claim that the projection was acquired
at one physical height; preserve contributing heights in provenance. Save actual computed
values and their XY pyramids, not rendered RGB screenshots. Carry channel/time
metadata, specimen XY placement, authoritative coverage and provenance: source
identity/revision, reduction method, and the actual contributing Z extent.
Missing contributors remain distinct from acquired zero, including for Min.
Sum uses an appropriately wide accumulator/output type, to be tested against
storage and rendering support.

Separate persistent user-facing projection products from internal aggregate
cache/bake folders. The shared viewer accepts an explicit output location;
only the caller knows the operator run's directory convention. No workflow-name
or sibling-folder guessing belongs in the generic composer. Compute the requested
methods, not extra presets. Moving the Top/Slice Z slider must not regenerate
the all-Z projection products.

## Ownership and data flow

One renderer, one composition/baking implementation, three explicit data views:

```text
separate original position OME-Zarrs + authoritative acquired coverage
  |-- Top: relative-Z sampling, holding each stack's boundary planes
  |-- Slice: relative-Z sampling, only intersecting planes
  `-- Projection: per-position Min/Max/Sum -> separate saved OME-Zarrs
          |
          v
shared composition and overlap order -> view-correct coarse chunks
          |
          v
viewer HTTP sources -> one Neuroglancer renderer
```

`zmart_viewer/compose.py` owns sampling, composition and coverage. Extend its
existing paths; do not create a second composer for each view. A small projection
module should own numeric reduction and derived-store writing, not publication
or operator directory conventions. Persist view identity and sampling settings
in the existing description/ledger contract; `zmart_viewer/published.py` owns
live revisions and publication through the existing snapshot machinery.
`zmart_viewer/server.py` exposes the selected view; it must not perform expensive
projection or bake work inside status requests.
`loading.py`, `library.py`, `building.py` and `pieces.py` must discover and reopen
the same named views without an active publication session. Use existing
serialization and dispatch, not a separate registry or live-only mode state.

The page requests a view and displays its controls. It must not calculate
coverage, choose per-position placement or build mosaics. Keep Neuroglancer
patches out of this increment unless a rendered test demonstrates a missing
capability that cannot be supplied at the shared data boundary.

The number of engine sources must remain bounded by acquisition/view structure,
not position count. Fine reads remain backed by separate originals (or the
separate projected positions). Bake off must not restore per-position engine
sources. Only coarse aggregate levels are materialized.

## Incremental implementation and gates

Each increment should be independently reviewed and committed after its gate
passes. Do not enable an unfinished mode in the UI.

### 1. Specify the numeric and storage contracts

Start with tiny, independent array oracles, not a long acquisition replay.
Document exact behavior for Top, Slice and each projection method in the tests.

- Top maps the requested relative Z into each contributor's own depth range.
  Outside that range it selects the nearest boundary plane; a singleton always
  selects its one plane. Signal and coverage use the same selection. Missing
  interior planes and XY holes remain missing, not boundary-filled.
- Slice samples only actual intersections. Keep the current reference-height
  mapping and explicitly test ascending and descending input plane order.
  For this relative-Z release, flats have one declared display plane at Z=0;
  that is not a recovered specimen height. Keep source geometry/provenance
  distinct from display placement, without changing the operator writer here.
  Plain/default composition remains ordinary slicing: retain the existing
  plane-identity tests and add Top-specific expected selections separately.
- Project each position before resolving overlap between positions. Reduce
  acquired samples independently for each T/C. With no acquired contributors,
  coverage is zero; acquired zeros participate in Min/Max/Sum normally.
- Round-trip singleton-Z T/C/Z/Y/X projection stores with correct metadata.
  Do not let a 4D array silently enter a reader assuming its last three axes
  are Z/Y/X. Native four-dimensional storage support is deferred.
- Specify Sum accumulation precision and one stable saved dtype policy per
  projection aggregate before writing its first product. Contributors must
  agree on dtype; do not choose a minimal type independently for each position
  or silently promote one after an append. Define supported input/depth limits;
  an unrepresentable Sum update is visibly refused before publication, preserving
  the last valid result. Never saturate, wrap or silently drop a position. Prove
  no integer overflow and verify renderer
  support; document display conversion separately from the saved numeric result.
- Specify projection XY mean-reduction precision, edge handling and rounding
  for each supported dtype. Floating output must retain fractional signal;
  wide integer arithmetic must not silently lose precision through a floating
  intermediate. The saved pyramid's declared reducer must match the composer.

Gate: small expected arrays for unequal stack ranges, singletons, overlap order,
black pixels, sparse holes and independent C/T; a projection-store metadata
round trip. Include unequal projection depths, singleton-plus-stack products,
growth across a dtype capacity boundary, fractional floats and high integer
values. Retain existing aligned-Z-lattice requirements unless a separate
resampling design is explicitly approved. Unequal ranges are not permission to
silently resample unequal Z spacings.

Distinguish a mixed acquisition from a physically mixed aggregate. Existing
two-output publication is a valid baseline for the former; do not remove its
guards just to construct a test fixture. Every per-position projection product
has Z length one, even when its original was a stack. During increment 2, choose
the bounded internal source arrangement for each named view and prove overlap
order across flat/stack inputs, including interleaved order and acquired black.
If two outputs cannot preserve the required pixels, change the shared layout
there; a compulsory mixed-store rewrite is not a prerequisite to the oracles.
Source/chunk iteration order must not choose output slab depth accidentally;
test reversed contributor order and both single-plane and multi-plane chunks.

### 2. Implement Top and Slice with baking off

Put the per-contributor sampling decision in the shared composer, used by both
signal and coverage. Reuse readers and spatial indexing. Clamping the already
composed mosaic is incorrect where contributors have different depth ranges.

Use one shared sampling helper for signal and coverage. Preserve contiguous
interior reads and group repeated boundary selections instead of forcing one
disk read per output plane. Reuse must be keyed by source revision, C/T, level
and source window so that rewritten boundaries do not return stale data.

Keep originals unchanged. Do not store repeated flat or boundary planes across
Z. Extend publication/source identity sufficiently to request either view
without cross-contaminating caches; defer coarse materialization to increment 4.
Specify display-axis behavior in the named-view contract. Slice uses the shared
Z axis, including singletons. Top holds boundaries; Projection uses its one
projected plane. Reuse or adapt flat-local-axis handling only where equivalent
to that contract, rather than deleting it globally. Test switching both ways
on already loaded layers so an old private depth axis cannot leak into Slice.

Gate: actual chunk pixels match the oracles below, within and above stack ranges;
both arrival orders of flat and stack inputs work. Original files remain
byte-identical and source count stays bounded. Add a small rendered proof before
building further features on the sampling contract.

The early rendered proof uses a test fixture through the existing HTTP/renderer
path, not the production mode menu scheduled for increment 5.

**Top representation/cost gate, before increment 4:** measure mixed singleton
and stack contributors over a 40-plane display range. Count unique source
chunks read, decoded bytes, memory and coarse chunks written; compare cold reads,
Z sweeps and a single contributor rewrite against Slice. Top can expand coverage
across depth, but a fixed 40x cost increase is not universal: it depends on stack
ranges, overlap and occupied chunks.
First count constant Z runs of the per-contributor sampling/coverage state for
each XY chunk, level and C/T. Use placements and authoritative coverage to find
safe equivalence groups, then verify their pixel/mask equality numerically.
These groups bound distinct compositions; they do not count distinct images
exactly, since different selections can contain equal pixels or be occluded.
Interior stack planes can change on every Z step; constant runs also occur
between disjoint depth ranges, not only at the two outer ends. Do not assume a
universal Slice-plus-two physical chunk count, especially with multi-plane chunks.

Include two small fixtures: overlapping stack ranges with long constant tails,
and shallow stacks staggered across the display range so that nearly every Z
selects different data. Define both in relative display coordinates after
reference alignment, not from absolute specimen heights. Predict sampling runs
from metadata before implementing Top, then verify them through the minimal Top
path and independent pixel/coverage oracles. Do not count a held tail as a new
state when it repeats the last interior selection. Record useful run-based reuse
as a hypothesis that the measurements may reject, not a required outcome.

In the existing two-component representation, the flat bake remains one plane;
there is no materialized flat-times-stack volume. Measure repeated Z states in
the stack component separately, while retaining the mixed-view overlap test.
Reuse that split only if it preserves the required cross-component pixel order.
Run counts guide the prototype but do not choose its storage format alone.
Copies still duplicate storage, and hardlink candidates must prove rewrite,
alias-update and crash-recovery correctness with the existing atomic-replacement
writers. Compare physical bytes, logical chunk addresses, update work and elapsed
time before accepting either strategy; do not make hardlinks a dependency now.

Instrument these measurements in the small test harness where counters do not
yet exist: distinguish requested rectangles, actual decoded chunk misses,
decoded bytes, retained cache memory and written coarse chunks. Include workers
where used. Measure elapsed read/compose/write/publication phases as well as
counts; a lower read count alone is not a performance win. The reverted
optimization documented in `tests/test_the_bake_patch_stays_honest.py` is the
reason to retain wall-clock evidence. Do not build a new telemetry subsystem.

Prefer a stable logical-Z source with request-driven sampling and bounded reuse;
do not treat slider movement as an acquisition publication. Do not blindly bake
the entire expanded Z range or duplicate held planes on disk. Before enabling
Top baking, specify how coarse results are materialized/reused without that
duplication and prove it with the counters above. If this cannot be achieved
simply, resolve the representation here before proceeding. A single-plane source
republished on every Z movement is not an automatic solution: it adds source
refreshes and shared-session state and must be compared, not assumed cheaper.

### 3. Save per-position Min, Max and Sum projections

Reduce original acquired samples over Z in bounded-memory windows, then generate
the projection's XY pyramid. Projection of an averaged coarse source is not
assumed equal to a pyramid of its full-resolution projection, especially for
Min/Max. Carry derived coverage through both reduction and downsampling.

Use an explicit output directory and reuse existing OME-Zarr writing/pyramid
facilities where their semantics match. Record method, source revision, coverage
contract, axes, calibration and provenance. Ensure discovery of original stores
cannot accidentally ingest derived projection stores and project them again.

Compute only requested methods. Initially reduce a newly published position
once, recompute a changed position and reuse unchanged products. A completed
publication may contain sparse acquired regions: completed means the announced
writes are ready, not that the store's bounding rectangle is fully acquired.
Both `regions: "complete"` and explicit region maps can accompany rewrites.

Per-plane incremental projection updates are conditional on identifying an
in-scope producer with an explicit append-only guarantee since the consumed
revision. If none exists, defer that API rather than inventing a new publication
protocol for 0.3.0. With such a guarantee, update Min/Max/Sum from new samples
and coverage exactly once, including after retries/restarts. Rewrites, removals
and revisions without the guarantee require recomputation. Never infer append
semantics from increasing shape, coverage or revision alone. Publish only
complete products; interrupted writes must not become a current revision.

The current operator writer publishes complete position stores, not individual
arriving stack planes (`application/parts/storage/zarr_positions.py`). Therefore
plane-by-plane append is a shared producer capability to verify, not a measured
property of the current operator path.

Gate: reopen saved arrays and compare values, coverage and every pyramid level
against an independent reduction. Include a black chunk physically omitted by
Zarr, entirely empty chunks, a later gap fill, rewritten extrema, Sum overflow
cases and separate C/T. No original data or full-resolution stitched mosaic is
written.

Test repeated announcements, interrupted commits, newly covered pixels and
overwrites against a from-scratch reduction. If the conditional append-only
path is implemented, compare its incremental results against the same oracle.
Use exact equality for integer reductions and an explicit numerical tolerance
for floating Sum, whose addition order can differ.

Prove aligned uint32 Sum products actually read their native coarse levels;
measure source reads with baking off rather than checking only output pixels.
The existing `halve_xy` rounds floating means, and the `MEAN_CROP_REDUCTION`
native-read guard excludes floats and integers wider than 16 bits. Reuse these
paths only where their semantics fit the chosen projection dtype/reducer.
Preserve correct sparse/misaligned fallbacks; do not weaken the eligibility
checks merely to make a performance test pass.

Include `zmart_viewer/contrast.py` and `app/page/src/scene.js` in this gate:
Sum must open with an appropriate initial/auto window, preserve values above
65535 and keep covered black opaque when the window changes. The current 16-bit
fallback is used when samples are unavailable, not as the normal measured
window. When enabled, the 2D coverage underlay derives alpha independently of
contrast; the missing case is availability when page transparency is off. For
named acquired views, select coverage/constant opacity from the acquisition
contract, independently of the page-background option. Do not substitute raw
nonzero intensity for coverage or globally enable a transparent page as a fix.
Test Min, Max and Sum with both background settings and a distinguishable image
beneath: acquired zero must cover it, while missing ground must reveal it.

### 4. Extend the shared bake machinery for each view

Use the same view sampling/composition semantics for lazy chunks and materialized
coarse chunks. Establish each view's fine-to-baked transition explicitly. Top
must not reuse Slice bakes after contributor depth information has been lost.
Projection bakes compose the saved projected positions, not the original volume.

Distinguish cached products by view, projection method where applicable,
placement/settings and source revisions. Keep invalidation dependencies local:
a changed contributor affects only its derived product and intersecting coarse
chunks, including Top's boundary-held contribution where applicable. Reuse the
existing spatial index and publication mechanism instead of adding a parallel
dependency framework without demonstrated need.

Gate: compare baked and lazy pixels, and served coverage, to independent
expected arrays derived from acquired regions and per-contributor selections.
Use stamped source planes to identify pixel ownership and an independent mask
oracle, including acquired black and empty ground in the same chunk. Never
infer expected coverage from nonzero signal. Bake-on/off equality supplements
this oracle; comparing live coverage to itself cannot replace it. Exercise
updates while baking is pending so future coverage cannot accompany stale
pixels. Also test zoom transitions, no writes to unrelated coarse chunks and
cold/warm request and source-read counts.
Measure a one-position append and rewrite with 1, 10 and 100 existing positions.
Separate metadata cost from image reads and bake writes; do not claim constant
total update time simply because only affected chunks are rewritten.

### 5. Connect live updates and the standalone controls

Extend the existing asynchronous, coalesced publisher. Reuse its failure and
snapshot handling so image, coverage and advertised revision agree. A slow view
must not block acquisition callbacks or bridge status; pending changes must
eventually publish even after acquisition stops. Do not add a second polling
loop or rebuild all views on every tick.

Wire the shared page in `app/page/src` through its existing scene, engine and
refresh boundaries. Expose Top, Slice and Projection, with Min/Max/Sum under
Projection. Preserve pan/zoom, C/T, contrast and remembered Z when switching
views. A view switch may request different data, but must not falsely announce
an acquisition rewrite. Z movement does not regenerate an all-Z projection.

The standalone viewer defaults to **Slice**, if available. Support any nonempty
subset of the three views and offer only available projection methods. If Slice
is absent, select an available view; opening must not generate missing views.
Keep the operator's Top default separate from this standalone policy. Test all
seven nonempty view combinations, including projection-only datasets.

Add a saved-view gate: in a fresh process open each named store and its containing
`view/` folder, without an active publisher. Persist acquisition identity, view
type/method, sampling/placement and source/bake dependencies through the existing
ledger and snapshot round trips. Classify named alternatives before ordinary
same-acquisition checks, which currently compare Z spacing/channel declarations.
Correctly suffixed views are already excluded from position composition; the
missing behavior is named-alternative discovery, not another file reader.
Verify selection/defaults, fine/coarse pixels, coverage and zero generation of
missing views after reopening, including different Slice/projection Z metadata.

Use the selected descriptive design B. The later operator integration places
the dropdown immediately left of Carrier; standalone placement follows its own
toolbar. Omit the earlier mockup's selected-Z-range controls and all 3D controls
from this release.

Gate: browser pixel assertions and screenshots for every mode, both arrival
orders and zoom transitions. Count requests: idle polling causes zero image
refetches; each published change has one effective whole-source refresh, with
no second invalidation at the next tick. Exercise slow publication, failure and
retry, view switching during publication and final catch-up. Chunk-selective
Neuroglancer invalidation is not required.

### 6. Review and prepare viewer 0.3.0; defer operator integration

Run the relevant viewer backend and frontend regressions, plus the new numeric,
storage, bake and browser suites. Keep scaling at 100 positions and use the
small append/rewrite reproducer for iteration, not a 15-minute full scan.
Request independent correctness/performance and architecture/test-gap reviews.
Resolve findings before preparing the release.

When separately requested, create an operator integration branch from the latest agreed
operator baseline, preserving the microscope fixes. Pin the exact tested viewer
commit, pass projection output locations through the shared service contract,
adopt design B and rebuild the tracked operator page. The bridge announces writes;
it does not implement projection or baking. Test overview, focus and target
publication through the same mechanism, including mixed flat/stack inputs.

Viewer gate: numeric correctness, sparse coverage, bounded sources, bake
equivalence and standalone live catch-up pass; reviews are
resolved and limitations documented. Hardware verification of microscope fixes
is tracked separately and must not be claimed from mock tests. Bump package
metadata to 0.3.0, write release notes and verify a clean package installation
only when preparing the release. Publishing/tagging and updating the rig are
separate actions, not part of this planning change.

## Baseline regressions to reproduce separately

The final review found two concrete code hazards in the baseline. Do not assume
these caches are working when measuring the new modes. Add small red tests and
contained fixes in separate commits before depending on the affected paths;
neither justifies a general publication redesign or changes to the rig branch.

- `warm_the_coarse_levels` calls `_a_block_of` without its required `outer`
  argument, catches the resulting exception and eventually marks prefill done.
  Verify actual cache population for the C/T contract, not just the completion
  flag. Ordinary on-demand block reads pass `outer`; this is a warm-path issue.
- Built-picture redeclaration removes numeric chunk directories but can retain
  `baked.json`; `_already_this_runs_picture` accepts that marker as bake presence.
  Reproduce bake-on -> redeclare/bake-off -> bake-on and interrupted redeclaration
  on a tiny governed fixture. A surviving marker must not certify deleted chunks.

The unused `ACQUISITION_RENDERING_VERSION` constant is not a feature blocker.
Do not create version plumbing merely to consume it; use explicit persisted
view semantics where the saved-view contract requires them.

## Evidence to retain

- Small array fixtures and independent expected results, including saved-file
  reopen checks rather than metadata assertions alone.
- Rendered pixel assertions and screenshots showing acquired black, transparent
  holes, differing stack ranges, mode changes and later gap filling.
- Request/read/write counts plus separate publication and rendering timings for
  idle, append, rewrite and final catch-up; record dataset size and bake setting.
- Test commands/results and the reviewed commit pair for viewer/operator.

Keep generated evidence outside the source tree unless it is a small intentional
test fixture. Report failures and unsupported cases, not just passing counts.

Absolute-Z storage/display mapping remains a separate decision from enabling
3D rendering; do not silently expand this increment to implement either.
First deliver a correct Top/Slice comparison on the existing relative-Z basis.

Only viewer code and synthetic test data were changed during implementation.
No operator dependencies, microscope data, rig worktrees or main branch were changed.
