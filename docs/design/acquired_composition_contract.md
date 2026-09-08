# Acquired composition: first implementation increment

`Mosaic.with_acquired_regions(regions, order=...)` creates a new composition
snapshot from separate, unchanged original stores. This is a shared Python
composition contract, not yet the external live-publication API or operator
integration. Existing publication and rig Z behavior are unchanged.

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
  do not select between independently averaged source pixels at overlap edges.
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

Next, the existing completed-publication path must bind regions and source order
to its revision, compute dirty ground for region/order changes, and expose the
aggregate with baking either off or on. Order-only changes such as raising a
target must not rewrite originals. Do not enable sparse operator publication
before that wiring and its retry, refresh and browser tests pass.

`tests/test_acquired_composition.py` covers fine/coarse numerical pixels, opaque
black coverage, gap filling, partial/empty chunks, overlap order, C/Z/T,
snapshot validation, real workers, unchanged original bytes, odd canvas edges
and reuse of the existing chunk baker without a level-zero copy. These are
backend tests, not proof of mixed-Z operator rendering.
