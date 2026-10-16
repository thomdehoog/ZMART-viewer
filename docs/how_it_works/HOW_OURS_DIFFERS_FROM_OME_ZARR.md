# How what we write differs from plain OME-Zarr 0.5

Written 5 August 2026. Short, because the honest answer is "hardly at all, in one
important way".

Every image this project writes is an ordinary OME-Zarr image. Open one in napari,
Fiji, `ngff-zarr` or anything else that reads the format and it opens, with the right
axes, the right voxel size and the right place on the stage. Nothing here is a
private format wearing OME-Zarr's clothes.

There is **one** real divergence, and four things that look like divergences and are
not. Knowing which is which matters, because the first one will surprise a colleague
and the others will not.

---

## The one that matters: a view is an image with its pixels somewhere else

A **view** — `overview.ome.zarr` in the layout below — is a complete and valid
OME-Zarr image. It declares its shape, its chunks, its number type, its compression,
its axes, its voxel size and where it sits on the stage. What it does not contain is
the chunk files.

Those live in the positions, and a small file beside the image says which chunk of
the picture is which chunk of which position. The viewer's server reads that and
hands over the position's own file, untouched.

**What another program sees.** Zarr is entitled to treat a missing chunk as unwritten
and fill it from the fill value, and that is exactly what it does. So opening a view
in napari gives you a correct-looking image that is **blank wherever the pixels are
pointed at** — which today is every level, because the positions carry their own
zoomed-out copies and all of them are pointed at.

**This is not a violation.** A zarr array with no chunks written is a legal zarr array
that has had nothing written to it, and every reader handles that gracefully. It is
simply not what a person expects when the folder is named after their experiment.

**So the rule to tell people is short: point other software at the positions, not at
the view.** The positions are ordinary images holding real pixels, each one openable
on its own, and they are where all the data is. The view exists for one purpose —
letting a drawing engine treat ten thousand positions as a single image — and outside
that purpose it has nothing to offer.

---

## The four that look like divergences and are not

**Our own files sit beside the images, never inside one.**

```
experiment/
  overview.ome.zarr/      a view — an ordinary OME-Zarr image, and nothing else
  positions/
    overview_pos00000.ome.zarr    ordinary OME-Zarr images holding the real data
    overview_pos00001.ome.zarr
  zmart-links/            ours — which piece of the picture is which position
  zmart-coverage/         ours — where the run has actually imaged so far
```

`zmart-links` and `zmart-coverage` are not part of OME-Zarr and are not pretending to
be. They are kept **outside** every `.ome.zarr` folder precisely so that the images
stay pure: put a file of your own inside one and zarr tells whoever opens it *"Object
at zmart-links.json is not recognized as a component of a Zarr hierarchy"*, and a
colleague meets a warning about a file they have never heard of. Reading the images
never requires reading ours.

**We write both the scale and the position beside each resolution, and nowhere
else.** OME-Zarr allows `coordinateTransformations` on the multiscales as a whole
*or* on each dataset, and a reader applies the outer one on top of the inner one.
A store of ours carries a `scale` and a `translation` in each dataset and no outer
block (`engine/live/record/omezarr.py`; built pictures and projections follow the
same rule).

**Why only there.** A large part of the ecosystem reads the position from the
per-dataset block alone: `ngff-zarr`, and so `multiview-stitcher` and much of the
Python imaging world with it, never applies the outer one. A store that put its
position only in the outer block would open there with every acquisition stacked
on the origin, and nothing would say so. Writing it in *both* places is no cure:
the two add up, and the specimen would be placed twice as far from the origin as it
really is, in neuroglancer as well. Our own reader applies both blocks, so it opens
other people's files either way. [INTEROP.md](INTEROP.md) has the reading of their
source.

**We shrink by taking every second voxel rather than averaging.** The format says
nothing about how the smaller copies are made, so this is within it, and the writer
declares it — `"type": "nearest"`, with a `metadata` block saying every second
voxel is kept along y and x. It is worth stating because it is deliberate and
load-bearing rather than lazy: because no voxels are combined, a zoomed-out voxel
comes from exactly one position, which is what lets a view point at the positions'
own zoomed-out copies instead of writing its own. Averaging would look smoother and
would quietly make that impossible.

The catch that goes with it is written up in the voxel-placement note carried with the elder writer
§3a: a coarse voxel's value is the fine voxel at the *low corner* of the block it
covers, not the block's average, so a reader that assumes averaging places it
`(2^k − 1)/2` fine voxels too far along in y and x. Nothing is out along z, because
z is not shrunk at all.

**We use the `omero` block for channel names, colours and brightness.** That block is
a transitional part of the specification rather than a permanent one, and a future
version of OME-Zarr may put this somewhere else. It is what readers understand today.

---

## Version, and what we ask of a run

**We write OME-Zarr 0.4 by default and 0.5 on request.** 0.4 because almost
everything reads it today; 0.5 because it is where the format is going, and because
it allows several chunks to be bundled into one file, which matters when a run would
otherwise leave millions of small files behind. Both are fully readable here, and
nothing an operator sees changes between them.

**The constraints a run has to satisfy are ours, not the format's.** Positions must
begin on a multiple of the chunk size times the largest shrink, and must all be
written the same way — the same number type, compression, chunk size, axis order and
number of levels. None of that is required by OME-Zarr. It is required for a
position's own file to be handed over as a piece of the picture without touching a
single pixel, and a run that breaks it is refused when the view is built, with a
message saying what would work.

---

## In one paragraph

We write plain OME-Zarr. The positions are ordinary images and hold everything. The
view is also an ordinary image, correct in every respect except that its pixels are
somewhere else — so it draws perfectly in our viewer and comes out blank in anyone
else's. Point other software at the positions. Everything else we add sits beside the
images rather than inside them, so nothing we do makes an image harder for anybody
else to open.
