# Transparent 2D embedding (viewer 0.2.0)

Opt in with `make_server(..., transparent_background=True)` or
`open_window(..., transparent_background=True)`. The default stays opaque.
This is for embedding the 2D C/Z/T viewer between application surfaces, not
for changing volume rendering. The embedding parent's background must also
permit transparency; the viewer's toolbar and controls remain ordinary UI.

Outside image footprints the canvas alpha is zero. Inside a visible acquired
footprint it is one, including exact-zero image pixels. Channel weights still
control image colour/brightness; they do not fade the embedding background
through the specimen. Switching a channel off also switches off its coverage.

## Small engine change, explicit coverage

Neuroglancer 2.41.2 normally paints the slice background opaque and finally
forces the whole display alpha to one. The patch script makes those operations
conditional on `display.transparentBackground`, clears transparent RGB to zero,
and normalizes the flat image composite to binary alpha. Separate alpha blending
preserves an opaque destination under translucent annotations. All changes are
opt-in; the application disables the flag in volume mode.

Image intensity cannot identify an unacquired position: both empty mosaic gaps
and real black pixels can be zero. A virtual uint8 Zarr coverage source therefore
uses the compositor's existing position index and published timepoint selection.
It generates only requested chunks, reads no specimen pixels, and writes no
acquisition files. Gzip keeps mostly uniform coverage chunks small. Black
coverage layers sit beneath the ordinary image channels; their identities,
revisions, local channel positions and visibility follow the image sources.
Layer count follows channel rows, not the number of mosaic positions.
Only levels backed by compositor tile copies are advertised for coverage;
Neuroglancer samples those levels when a baked image has extra overview levels.

An ordinary position store is dense within its declared array bounds, even if
its writer omitted zero-valued chunks. Its writer must expose only acquired
extents/timepoints. Use governed composed views for publication-gated sparse
acquisitions. A refused compositor must never fall back to dense coverage.
Legacy pointer-only linked mosaics are not supported by this opt-in feature.
It does not infer arbitrary third-party sparse geometry from pixel values.

A fixed (`live=False`) single dense source uses constant shader alpha, without a coverage layer.
Watched rows retain coverage so adding sources cannot change their alpha strategy.
Multi-source rows retain underpainting: their existing covering blend avoids
overlap seams, and making every channel opaque would erase lower-channel colour.
Transparency styling affects only the embedding surfaces, not the engine's
theme colours. The image has no CSS opacity fade, which would expose the host
through acquired pixels during arrival.

## Checks

Build `app/page` with `npm ci && npm run build`, then run:

    python -m pytest tests/test_acquisition_coverage.py tests/test_transparent_2d_browser.py tests/test_manifest_refresh_browser.py tests/test_the_screen_never_goes_black.py

Set `ZMART_REQUIRE_BROWSER=1` to forbid silent browser skips and `ZMART_CHROMIUM`
to the installed Chromium executable when required. The tests cover acquired
black pixels, gaps inside one chunk, pyramid geometry, C/Z/T availability,
publication, DOM surfaces beneath/above, and the unchanged opaque/volume modes.

The companion microscopy integration uses its writer's individual dense position
stores, so its image shader can emit constant alpha inside those stores without
the extra virtual mosaic coverage. That adapter and the operator's layer split
belong to the microscopy repository, not this package.
