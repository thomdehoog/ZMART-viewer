# Review: transparent 2D acquisition footprints

Repository: https://github.com/thomdehoog/zmart-viewer
Branch: `codex/transparent-2d-footprints`
Base: `9ff10b04e803fbe2a71a1735a8065a845ea803dd` (version 0.2.0).
Review `git diff 9ff10b04...HEAD`. No PR has been opened.

Companion operator change:
https://github.com/thomdehoog/ZMART-microscopy/tree/codex/transparent-operator-layers

## Required behaviour

Only the 2D C/Z/T canvas is transparent outside acquired footprints. Acquired
black pixels stay opaque. Published new positions become opaque; an update must
not expose the application underneath an already acquired image. Defaults and
3D volume rendering retain their existing behaviour.

## Review emphasis

Prioritise simple, clear, maintainable fixes at the correct abstraction boundary.
Reject unnecessary mechanisms, duplicated geometry, broad refactors, silent
fallbacks, per-position layer growth, or assumptions fitted only to the tests.
Read `docs/TRANSPARENT_2D.md` for the implementation and supported input contract.

Check particularly:

1. The pinned Neuroglancer patch: premultiplied alpha, zero background RGB,
   channel mixing, annotations/scale bars, default and volume isolation,
   fresh-install application and idempotence. Engine edits belong in the existing
   patch script, not runtime monkey-patching or arbitrary CSS holes.
2. Coverage must agree with `Composer._build_slab` placement, pyramid levels,
   Z bounds, channel bounds and published timepoints. It must never infer
   acquisition from image intensity or read specimen pixels to compute coverage.
3. A refused composed view must not silently become one opaque bounding box.
   Check paths, malformed metadata, legacy linked views, and publication races.
4. Coverage sources must track image visibility, local C position, source IDs,
   revisions and refresh lifetime. Check opaque black underpaint does not alter
   channel colour/mixing or grow the layer count per mosaic position.
5. The opt-in flag crosses server/config/render/CSS boundaries. Check each is
   necessary; suggest a smaller contained alternative if equally correct.
6. Baked pyramids may have more image levels than compositor tile copies;
   coverage advertises only supported levels. Fixed single-source dense rows
   use constant alpha; watched or covering multi-source rows retain coverage
   to avoid changing opacity strategy mid-publication or erasing channel colour.
   The wrapper background is separate from the engine's theme background.
7. The bounds-only refresh in `patch_neuroglancer_growth.mjs`: compatibility
   before mutation, non-owning source cache, preserved GPU chunk ownership,
   frontend/worker bounds, partially filled chunks, cancellation and errors.
   Packed T chunks exercise the engine API directly; the application's existing
   watcher supports one-frame chunks. Shrink/layout changes still replace sources.

## Verification performed

Round-three build and targeted qualification passed 78 tests:

    python -m pytest tests/test_acquisition_coverage.py tests/test_transparent_2d_browser.py tests/test_manifest_refresh_browser.py tests/test_the_screen_never_goes_black.py tests/test_server.py tests/test_timelapse.py -q

The new dense-growth test failed before the repair with a fully transparent
rendered frame, then passed for repeated growth and packed boundary chunks.
Ruff passed on changed Python files. Browser rendering used installed Playwright
Chromium on NVIDIA T400.
Regression checks now also cover baked overview levels, fixed versus watched
dense rows, and light-theme volume backgrounds after transparent 2D embedding.
Pytest warned that this environment lacks the plugin for its `timeout` option.
No Firefox/Edge rendering qualification or large-scale coverage benchmark is
claimed. Normal dense position stores use their declared extents; arbitrary
third-party sparse geometry and legacy pointer-only views are not supported.

## Reproduce on this machine

Worktree: `C:\ProgramData\MinicondaZMB\home\t.de\zmart-viewer-transparency-20260907`.
Python: `C:\ProgramData\MinicondaZMB\envs\zmart-viz\python.exe`.
Build from `app/page`: `npm ci`, then `npm run build`.
Set `ZMART_REQUIRE_BROWSER=1` and `ZMART_CHROMIUM` to
`C:\ProgramData\MinicondaZMB\home\t.de\ms-playwright\chromium-1234\chrome-win64\chrome.exe`.
Keep executable files, caches and temporary data under the whitelisted
`C:\ProgramData\MinicondaZMB` tree; npm cache is its `home\t.de\npm-cache`.
Never use conda defaults, touch Leica driver folders, or modify acquisition data.

Report findings as severity, file:line, concrete failure scenario, and the
smallest principled correction. Separate demonstrated defects from questions.
Do not fix, commit, push, or open a PR during this review.
