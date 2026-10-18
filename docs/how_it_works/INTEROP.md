# Reading other people's OME-Zarr, and writing ours so others can read it

The viewer opens images that other software wrote, and the images a ZMART run
writes should open in other software. This note collects what was learned by
opening light-sheet transfers from other instruments, and by reading how
[multiview-stitcher](https://github.com/multiview-stitcher/multiview-stitcher)
and [ngff-zarr](https://github.com/thewtex/ngff-zarr) do the same job. Claims
about their code name the file and function, so they can be checked against
their current source; they were read in source, not in rendered pages.

Sections 1 and 1a describe rules the viewer follows today. Sections 2 to 5 are
ideas that are not built, with the reasons they might be worth building.

---

## 1. Where an image says it is

OME-Zarr lets an image state its position in two places: beside each
resolution (in each dataset's `coordinateTransformations`) and once for the
image as a whole (beside the multiscale block). A reader applies the outer one
on top of the inner one.

**Reading.** The viewer applies both, in that order, because both are legal and
other instruments use either. Images from some instruments state their position
only beside each resolution; a reader that looked only at the outer place would
draw every tile of such a transfer at the stage's zero, on top of one another.
The viewer also reads only the axes a store declares: a light-sheet transfer
that declares `z, y, x` has no time axis, and asking it for one is refused.

**Writing.** A ZMART store states its position beside each resolution and
nowhere else (`engine/live/record/omezarr.py`). Two reasons:

- `ngff-zarr` reads the scale and the translation **only** from the datasets
  (`_from_zarr_attrs` in `v04/zarr_metadata.py`); the outer block is kept for
  validation and never applied. `multiview-stitcher` reads images through
  `ngff-zarr`, as does much of the Python imaging world. A position stated only
  in the outer block would put every acquisition at the origin there.
- Stating it in both places is not a safe middle way. The two add up, so the
  image would be placed twice as far from the origin as it really is.
  Neuroglancer composes them the same way (`parseOmeMultiscale` in
  `datasource/zarr/ome.js`), so this would break the viewer too.

`tests/make_test_stores.py` opens its test stores with `ngff-zarr` and checks
that grid positions sit apart. No test yet writes a *run* at a known place and
reads it back with `ngff-zarr`; such a test would hold this rule in place.

### 1a. The half-voxel question

Where does a translation point: at a voxel's corner or at its centre? OME-NGFF
RFC-5 (accepted 2025-12-01) settles it: "The pixel/voxel center is the origin of
the continuous coordinate system." A translation is the **centre of the first
voxel**.

ZMART writes that. A position's first voxel is centred half a voxel past the
corner the layout counts pixels from, in y and x. A level made by averaging 2x2
voxels begins with a voxel centred among the ones it averages, at
`first_centre + 0.5 * (2^level - 1) * base`, so the centres nest. That is also
what `multiview-stitcher` writes (`calc_ngff_coordinate_transformations_and_axes`
in `ngff_utils`) and what `ngff-zarr` computes for its own coarse levels. Depth
is a plane's own height and is written as it is. One function holds the rule:
`first_centre_of_a_level` in `engine/live/record/vocabulary.py`, used by the
live writer, the composer, the baked levels and the projections.

A canvas is a rectangle on the specimen, so its bounds are **edges**. A picture
baked onto a canvas has its first voxel centred half a voxel inside the low
edge, and tiles snap to the grid `low + step/2 + k * step`; a tile already on
that grid is not moved.

Nothing on disk is converted. A view an older viewer published for a request
that named `xy_origin: "corner"` already describes centres and opens as it was;
nothing new is published into it. A position store written as a corner is read
as a centre, half a voxel early.

---

## 2. Skipping empty ground by declared geometry

On a sparse survey most of the canvas has never been imaged, and a page that
asks for every piece on screen spends most of its requests on empty ground.

`multiview-stitcher` avoids those requests from declared geometry alone
(`_build_spatial_fusion_plan` in `fusion/_core.py`): it projects each tile's
corners through its transform, takes the bounding box, and turns it into a
range of chunk indices by integer division. A chunk no tile touches costs no
read at all. Its cost grows with the number of tiles, not with the number of
chunks.

The viewer could do the same with numbers it already reads when it opens a
store (its origin, shape and voxel size). Unlike a record of what was written,
this also works for a transfer from another instrument, which keeps no such
record, and for two acquisitions with different voxel sizes. It cannot remove
every wasted request, because a declared canvas is larger than the tiles in it,
but it removes every request for ground that no store claims.

---

## 3. Where tiles overlap

Two arrangements need different answers.

- **A detail scan inside a survey** (say 63× over 10×). The later, finer
  picture should simply win where it exists. Blending the survey into it would
  be wrong. The viewer does this.
- **Sibling tiles at the same magnification**, as in a light-sheet transfer of
  several tiles. Here "later wins" leaves a hard seam wherever two tiles
  overlap, and it throws away the second measurement, where averaging would
  have halved the noise.

`multiview-stitcher` blends siblings with a cosine ramp, `(cos((1-x)π)+1)/2`,
over a default of 10 µm from each tile's edge, and weights each tile only where
it has data. In a browser the same ramp is a few lines of shader: for an upright
rectangle, the distance to its edge is a subtraction, and the graphics card
already visits every pixel to draw it. Applied from each store's declared
rectangle, it would soften the seams between siblings while the nested case
keeps "later wins".

A related limit: deciding "was this imaged?" from how bright a pixel is cannot
tell ground that was never imaged from ground that is genuinely dark, which on
fluorescence is most of a field. Deciding it from geometry, as above, can.

---

## 4. One world, measured in whose voxels?

`multiview-stitcher` places every view in an abstract physical space that no
view's grid owns. A viewer that measures its world in the voxels of one chosen
acquisition is simpler, and adequate for an upright mosaic, which needs only a
scale and a shift. One thing follows from it all the same.

A store whose transform includes a rotation or a shear (which newer versions of
OME-Zarr and neuroglancer can express) would be drawn upright, in the right
place, without a word. A check that refuses or warns about a transform the
viewer cannot draw would fail loudly instead.

---

## 5. Stitching on the fly is slow, and why

`multiview-stitcher` can serve a set of tiles to a browser as if they were one
stitched image, without writing anything (`serve_virtual_ome_zarrs`). That is
attractive for a viewer whose difficulty is holding many sources at once. It was
far too slow to look at a specimen through: in one measurement a single piece
of picture took 647 ms to stitch, against 4.6 ms to read the same piece from
disk, and a piece covering the whole specimen took three seconds, because every
tile overlaps it.

**Where the time goes.** It detects when a tile is only shifted, and by whole
voxels (`_is_grid_aligned` and its neighbours in `fusion/_core.py`), but uses
that only to plan which tiles touch which piece. Every tile is then resampled
through a general affine transformation (`scipy.ndimage.affine_transform` in
`transformation.transform_sim`), where a copy would have done. That is the
difference between those 647 ms and the 6 ms that copying rectangles takes
([TILES_IN_ONE_STORE.md](TILES_IN_ONE_STORE.md)).

**Doing several at once does not rescue it.** On four processors, nine pieces
took 3.4 s one after another and 1.3 s four at a time, while a single piece went
from 266 ms to 306 ms, because the pieces compete. Working in parallel raises
how many pieces arrive per second and lengthens the wait for each one, and a
viewer that is drawing is waiting for one.

**What to take.** The blending formula of section 3, computed in the shader
rather than ahead of time. A shortcut for the whole-voxel case in
`transform_sim` would speed up exactly the mosaics most of their users have, and
is worth suggesting to them.
