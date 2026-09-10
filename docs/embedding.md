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

On a geometry revision, use `refreshGeometry` once per source and share the
refresh/metadata-dedup sets for that update. `geometryRefreshPending` reports
an outstanding read. Unchanged status does not request a refresh. Image-only
revisions use the existing source invalidation, not an additional polling loop.

`neuroglancer-growth.mjs` exports the shared Neuroglancer metadata-growth patch
and `applyGrowthPatches(lib)`. Build both frontend and worker against this patch;
patching just the frontend leaves stale worker chunk bounds.

This feature is isolated as `0.5.0.dev0`; it is not a release or a main-branch
deployment. The wheel build certificate includes both shared JavaScript files.
