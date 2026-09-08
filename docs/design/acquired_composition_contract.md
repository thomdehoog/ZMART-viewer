# Acquired composition and completed publication

`Mosaic.with_acquired_regions(regions, order=...)` creates a new composition
snapshot from separate, unchanged original stores. The external completed-folder
publication API now uses this contract when `composition` is supplied. Operator
integration and rig Z behavior are unchanged.

Every source name must occur exactly once in `order` and have an explicit list
in `regions`. An empty list means no acquired content. Missing information is
refused. The list order is back to front: later acquired pixels win, including
zeros; unacquired holes do not overwrite earlier data.

Each region uses the existing storage tile-record vocabulary:

```json
{"frame": 0, "channel": 1,
 "origin": {"z": 2, "y": 40, "x": 80},
 "shape": {"z": 1, "y": 32, "x": 32}}
```

Coordinates are integer voxels in the original source's finest array. The
region denotes an actually acquired rectangular block in that T/C plane, not
a bounding box around unknown content. Larger sparse shapes use multiple
regions. The caller must obtain these facts from completed acquisition records;
the composer cannot verify that the microscope actually performed the capture.
It never substitutes brightness, chunk existence or array dimensions for them.

The existing mosaic ledger stores these regions beside the source descriptions;
its ordered tile list preserves compositing precedence. Committed timepoints
also survive serialization. Reopening validates coverage again. No metadata is
added to original stores, and a snapshot owns its copy of the supplied regions.

## Pixel and coverage semantics

- The spatial index includes only cells intersecting acquired regions.
- Fine composition reads original pixels only for acquired regions.
- Coarse pixels reduce the composed finer image on a common XY lattice. They
  reuse native means only when the declared reducer matches and acquired pixel
  ownership is constant within each reduction cell. Misaligned overlap edges
  still compose before reducing.
- The same mean reducer is used by composition and the existing baker, with
  edge padding and rounding preserved.
- Binary coarse coverage is the union of contributing fine acquired pixels,
  independent of the mean intensity. Subpixel holes cannot remain separately
  visible when reduced to one pixel.
- A zero-valued image chunk may be omitted even when acquired. Its independent
  coverage still distinguishes it from an entirely unacquired chunk.

## Deliberate boundary of this increment

The new contract supports unrotated ZYX/CZYX/TCZYX sources on one integer-aligned
voxel lattice, matching T/C room and dtype, and mean XY-halved output levels
with unchanged Z spacing. Unsupported geometry is refused, not silently rounded.
This does not yet implement mixed flat/stack aggregates or a growing Z domain.
The existing geometry-only path is retained for already-supported callers; it
must not be used as a fallback for a refused acquired-region snapshot.

Coarse composition uses the existing chunk/slab caches and reads finer composed
chunks as needed. This correctness increment is not a claim of optimized large
sparse-source throughput. It does not create another full-resolution mosaic.

## Completed-folder API

Open through `POST /api/stores/open`:

```json
{
  "path": "C:/runs/example/positions",
  "canvas": {"x_um": [0, 10000], "y_um": [0, 8000]},
  "bake": false,
  "source_revisions": {"a.ome.zarr": 1, "b.ome.zarr": 1},
  "composition": {"regions": "complete", "order": ["a.ome.zarr", "b.ome.zarr"]}
}
```

`"complete"` is the producer's explicit guarantee that every voxel in each
named store's T/C/Z room was acquired. The adapter derives rectangles from the
original geometry; the producer need not maintain a duplicate region list.
It is not a default for unknown stores. For sparse stores, replace `"complete"`
with a map from every source name to its acquired-region list as defined above.
Filenames, brightness and absent chunk files cannot establish completeness.

Announce the next complete snapshot using the existing `POST /api/announce`:
`{"publications": [{"path": ..., "source_revisions": ..., "composition": ...}]}`.
The canvas and bake mode are held by the open view. Explicit composition requires
explicit completed revisions; metadata polling is not a substitute. Source
revisions track pixel changes; `order` separately determines back-to-front
precedence. An order-only raise does not rewrite or reread original metadata.

The resulting source is `.zmart-viewer/overview.ome.zarr` in either bake mode;
the historical filename does not restrict it to overview workflows. Image and
coverage addresses stay stable as positions append or retire. Both modes declare
the same levels, extending XY reduction until the canvas fits one coarse chunk.
Bake off composes requested chunks without writing image chunks. Bake on uses
the existing chunk baker, retaining virtual full-resolution reads.

Pixel, coverage or order changes advance one aggregate revision. Identical
snapshots perform no image work. Dirty ground is conservatively the union of
old/new acquired footprints of affected sources, at chunk granularity; all
C/Z/T planes of those XY chunks are reconsidered. Unrelated baked chunks and
cached composition survive. This is not minimal per-voxel or per-C/Z/T dirtiness.
The existing whole-source client refresh updates image and coverage together.

Reopening with a different bake flag is supported without changing the aggregate
address or dataset identity. One server publication owner is retained per resolved
folder, including concurrent opens and an already-loaded folder. A rejected
reopen does not close the existing dataset. The open response supplies the updated
configuration; an external opener uses the existing announcement endpoint to
notify already-open browser sessions.

Old baked files are ignored while baking is off; accumulated dirty
ground, including retired positions, is rebuilt when baking resumes. Existing
original stores are never edited. Interrupted publication fails closed until
retry, including interrupted order changes.

This API still requires at least one source, fixed matching C/Z/T geometry,
at least two mean-pyramid levels and an integer-aligned common voxel lattice.
Mixed flat/stack sources and growing Z domains are the next increment. Legacy
folder opening and the earlier complete-rectangle bake API remain unchanged;
they are not a fallback for a refused explicit composition snapshot.

Mandatory aggregation is currently a guarantee of the explicit `composition`
API, not of legacy folder opening. Operator adoption must use this API in both
bake modes, explicitly guarantee complete stores (or supply authoritative sparse
regions), and publish the complete back-to-front order independently of revisions.
No completeness guarantee may be inferred from a workflow name or bounding box.

`tests/test_acquired_composition.py` covers fine/coarse numerical pixels, opaque
black coverage, gap filling, partial/empty chunks, overlap order, C/Z/T,
snapshot validation, real workers, unchanged original bytes, odd canvas edges
and reuse of the existing chunk baker without a level-zero copy. These are
backend tests, not proof of mixed-Z operator rendering.

`tests/test_published_acquired.py` adds publication/recovery and bake-switch tests,
100 small-position source-count checks, and real-browser fine/coarse alpha,
pixel-change, request and idle-refresh assertions in both bake modes. The
100-position fixture uses 8x8 images with two T/C/Z values; it is a capability
check, not evidence of production acquisition throughput. Cold coarse requests
may read substantial fine data. No release default or operator installation is
changed by this increment.

## Cold coarse-read cost

The measurements below describe the initial L0-only implementation at `c3b1e00`;
the following section records the guarded native-pyramid improvement.

`measure/measure_acquired_coarse.py NEW_DIRECTORY` measures a fixed 100-position
fixture: separate 1024x1024 uint16 images on a 10x10 grid, six native mean-pyramid
levels, one channel, plane and timepoint. Every request starts with empty viewer
caches; the operating-system file cache is not flushed. It compares encoded
chunk hashes between bake modes as well as recording reads and wall time.

On the test workstation (2026-09-08), one L2 chunk took 174 ms with bake off and
172 ms with bake on, reading L0 from four originals in either mode. One L5 chunk
covering the entire canvas took 3.429 s with bake off (all 100 originals at L0)
and 12.6 ms with bake on (no original reads). L4 and L5 were baked; L2 was virtual.
Initial virtual publication took 0.317 s; enabling baking took 3.578 s.

These are single cold-request measurements, not acquisition throughput or bridge
latency. They established a real cost gap: acquired composition reduced L0 even
when complete original pyramids existed.

## Guarded native-pyramid reuse

A producer may add `"pyramid_reduction": "mean-xy2-edge-round"` to `composition`
on both open and subsequent announcements. This explicitly guarantees that every
source's native levels are consecutive applications of the shared `halve_xy`
reducer: 2x2 XY mean, edge replication for odd dimensions, NumPy nearest-even
rounding, cast back to the original dtype. Z, C and T are not reduced. A completed
source revision must include all these native levels. Originals remain untouched.

The OME `type: mean` label alone does not establish this rounding convention.
For example, the record writer's `_halve` truncates integer means. Undeclared
pyramids retain L0-derived composition, and unsupported declarations are refused.
Do not set this declaration merely because a producer writes a mean pyramid.

The existing composer reads native levels only if source placement and acquired
region XY edges are aligned to the requested reduction lattice, and native shape,
dtype, T/C room and sample-center translations match the expected pyramid.
Aligned sparse regions, black pixels and aligned overlaps use the same path as
complete stores. Ineligible chunks reduce finer composed chunks until safe native
levels are reached, or L0 if necessary. Levels beyond a source's pyramid use that
same reduction path. An ineligible source does not disable reuse in other chunks.
Eligibility conservatively considers every region of each intersecting source.

The declaration is persisted in the existing composition snapshot and travels to
workers. Changing it dirties the aggregate so cached slabs and baked chunks cannot
retain the previous interpretation. Idle snapshots and whole-source client refresh
are otherwise unchanged; no notification or operator-specific mechanism is added.

Repeating the same 100-position fixture with this declaration measured L2 at
45.3 ms (bake off) / 26.1 ms (on), reading four originals at L2 rather than L0.
Virtual L5 took 250.6 ms and read 100 originals at L5; baked L5 took 12.1 ms and
read none. Enabling baking took 570.2 ms. All encoded hashes match the L0-only
baseline and both bake modes. These remain single samples, not a latency bound.
The browser still sees one aggregate; 100 original stores are opened internally
on the cold virtual L5 request. Baking continues to avoid that fan-out.

`tests/test_acquired_native_pyramids.py` compares against independent fine-image
reductions and coverage for aligned and misaligned overlaps, sparse gaps, black
pixels, C/Z/T, native/extended levels, bad native transforms, per-chunk eligibility,
undeclared truncating means, publication rewrites, declaration changes and reopen.
The sparse browser cases run both with and without the declaration, bake off/on.
Operator adoption and producer qualification are not part of this increment.
