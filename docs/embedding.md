# Operator embedding API (v1)

The viewer serves `/embedding.js` as an ES module alongside its data server.
Hosts check `EMBEDDING_API_VERSION === 1`. It has no Neuroglancer dependency:
the host supplies its own `WatchableCoordinateSpaceTransform` constructor to
`keepDepthLocal(layer, viewer, makeTransform)`.

Use `viewChoices`, `selectedViews` and `inSelectedView` to select one product
per acquisition using declared metadata, not filenames. The standalone viewer
defaults to Slice; an embedding host may request Top. Missing products fall
back to an available product.

Top calls `keepDepthLocal` with the viewer and transform factory. It retains
native global Z bounds but clamps the sampled plane in the source's native
coordinates, even when another acquisition changes shared coordinate units.
Projections call it without a viewer and remain visible through Z. Slice uses
the ordinary spatial transform.
The binding expects one aggregate source per layer, not per-position sources.

For Z-slider transitions, `holdCompleteSlice(sliceView, changed)` can retain
the last complete framebuffer until Neuroglancer reports the requested slice
ready. Call `request()` immediately before changing Z, `cancel()` before changing
XY framing or layers, and `dispose()` when the slice view closes. Resizing cancels
the hold automatically. The `pending` property and `changed(pending)` callback
let the host label the old picture as loading rather than presenting it as the
requested plane. This adds no requests, pixel copies, polling, or timeout; the
engine's existing chunk arrivals drive rendering. An incomplete initial frame
is not held.

On a geometry revision, use `refreshGeometry` once per source and share the
refresh/metadata-dedup sets for that update. `geometryRefreshPending` reports
an outstanding read. Unchanged status does not request a refresh. Image-only
revisions use the existing source invalidation, not an additional polling loop.
Keep the last observed revisions across mode switches: retiring a layer does not
retire its decoded cache. Geometry refresh waits for a newly selected source to
bind before updating reusable chunks, and is cancelled if that source closes.

`neuroglancer-growth.mjs` exports the shared Neuroglancer metadata-growth patch
and `applyGrowthPatches(lib)`. Build both frontend and worker against this patch;
patching just the frontend leaves stale worker chunk bounds.
Apply the patches before compiling. Compile `workerEntry(lib, name)` into the
worker's original bundle path on every build; this preserves the original import
entry so rebuilding actually reads changed source modules. A stale patch body is
an explicit build error requiring `npm ci`, never a marker-only success.
The static `/embedding.js` route permits cross-origin imports and is revalidated
instead of cached as an immutable hashed asset.

This feature is isolated as `0.5.0.dev0`; it is not a release or a main-branch
deployment. The wheel build certificate includes both shared JavaScript files.
