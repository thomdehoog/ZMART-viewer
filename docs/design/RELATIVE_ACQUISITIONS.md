# Shared relative-Z acquisition rendering

An explicit acquired publication produces at most two stable, chunked sources:
`.zmart-viewer/overview.ome.zarr` for flat captures and
`.zmart-viewer/stack.ome.zarr` for stacks. Baking does not change source count.
Original OME-Zarr stores remain separate and unmodified.

`composition.regions: "complete"` derives acquired coverage from each original
store's geometry. Sparse producers instead supply source-local acquired regions,
including C and T. Coverage never depends on brightness or the presence of a Zarr
chunk: acquired zero remains opaque even when its chunk is absent on disk.
`composition.order` names all originals and sets bottom-to-top overlap order
within each depth kind. Stacks always render above flats; cross-kind interleaving
or raising a flat above a stack is not supported by this two-source display.
Reordering does not rewrite originals.

Flat placement is a display convention in the aggregate: Z = 0 on a private
depth axis, visible through stack navigation. Stacks subtract their specimen-frame
`requested_stage_focus_z_um` provenance value. An explicit `z_references` map can
override it; without either reference, the lowest stored plane is used. The
reference need not coincide with a plane. Canonical positive-spacing plane order
is preserved; Leica acquisition order is normalized by the writer, not reversed
again here. No absolute-Z viewing mode is introduced.

XY is sampled onto the fixed specimen canvas's finest grid by nearest neighbour.
For a fractional-pixel origin, the footprint moves by at most half a finest voxel
per axis. The identical translation is applied to every native level and coverage;
original coordinates and pixels do not change. This is raster sampling, not
subpixel interpolation or geometric warping.

Fine chunks are composed from originals. Compatible native pyramids are reused;
unaligned or sparse coarse cells compose before reduction. Baking materializes
only affected coarse chunks. Single-level inputs generate coarse levels by the
same bounded recursive reduction, never by copying a full-resolution mosaic.
With baking off, no aggregate pixel chunks are stored. Empty coverage requires no
original tile reads.

Both depth outputs are validated before either commits. Retiring a depth kind
clears its acquired regions while retaining its address. Whole-source client
refresh remains revision-driven; an identical publication does no invalidation.

## Supported geometry

Flat and stack outputs of one acquisition must have the same channel count;
their time lengths may differ. Sources within each aggregate must have compatible C/T axes, spatial sampling,
native pyramid layouts and a common relative Z lattice. The canvas and published
Z domain are fixed for that acquisition. A later stack extending that domain, or
changing sampling, is refused explicitly instead of silently misplaced; changing
geometry requires a new acquisition. Flats and stacks may arrive in either order.

Auto measures a sampled channel distribution across the common-canvas sources,
not an exact current-plane/occlusion histogram. It retains the existing nonzero
sampling policy; an all-black image keeps its current window. This measurement
policy does not determine acquired coverage or displayed opacity.

`tests/test_mixed_acquisition.py` verifies both baking modes, both arrival orders,
original-byte preservation, C/T values, relative planes, acquired black, sparse
coverage changes, retirement, reopen/preflight, and real-browser pixels/refetches.
