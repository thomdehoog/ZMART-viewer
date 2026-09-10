# Viewer 0.3.0 verification

Date: 2026-09-10. Branch: `codex/view-modes-data`.
Base: `b33a21cb8ff9c3edb6a49c75e46ee5e72790818b`.
Baseline warmup/stale-marker fix: `aaf77c9`.

Scope is the viewer only. No operator integration, dependency-pin update, rig
deployment, main merge, release tag or package-registry publication was performed.

## Implemented

- Named Slice (default), Top and per-position Min/Max/Sum, accepting any subset.
- Shared ordered composition for mixed flats/stacks; authoritative sparse coverage,
  relative-Z references and independent C/T. Originals remain separate.
- Optional per-view coarse bakes, canonical Top Z-run aliases, and floating-point
  XY reduction without the old unconditional integer rounding.
- Immutable projection revisions and explicit output ownership; checked Sum
  overflow retains the previous valid publication.
- Revision-driven pixel refresh and metadata growth, including shared channels,
  same-shape placement changes and transient final-notification failures.
- Saved-view reopening, initial/interrupted-publication recovery, T/C layout
  growth/shrink, and a wheel containing the built frontend and workers.

See [usage and limitations](../view_modes.md) for the exact contract. Publication
remains a synchronous worker operation with per-view commits, not a new queue or
an acquisition-wide transaction.

## Test runs

The final broad backend/contract run passed **231 tests**, with 19 browser cases
deselected and exercised separately where relevant. Command:

```sh
python -m pytest tests/test_view_sampling.py tests/test_view_projections.py \
  tests/test_named_views.py tests/test_view_release_contract.py tests/test_view_costs.py \
  tests/test_published_acquired.py tests/test_published_depth.py tests/test_mixed_acquisition.py \
  tests/test_acquired_composition.py tests/test_acquired_native_pyramids.py \
  tests/test_published_transfer.py tests/test_bake_baseline_lifecycle.py \
  tests/test_library.py tests/test_server.py tests/test_contrast.py \
  tests/test_frontend_live_refresh_contract.py tests/test_frontend_geometry_refresh.py \
  tests/test_build_artifacts.py -q -k 'not browser'
```

Two subsequently added sparse-gap-fill cases passed with bake off and on. They
check partially occupied chunks, entirely empty chunks, acquired black, a later
filled gap, all three levels, masks and actual served bytes.

The final targeted named-view/recovery/legacy-bake run passed **22 tests**, including
a regression that retains access to legacy baked levels beyond an original
pyramid. The final installed-wheel rerun passed after that serving-path fix.

Separate browser runs:

- `test_named_views_browser.py`: **8 passed**. All modes, bake off/on, both arrival
  orders, two channels, same-shape Z shifts, growing T/Z and one injected metadata
  failure recovered without another acquisition.
- `test_empty_live_view_browser.py` + `test_manifest_refresh_browser.py`:
  **7 passed**, including empty manifest revision 0 and its first publication.
- Projection zoom-transition and visible-background proof: **2 passed** in the
  targeted rerun. At coarse zoom there are 2,048 opaque acquired pixels; Min
  retains 1,024 black pixels, with zero partial alpha.
- `test_transparent_2d_browser.py` + `test_view_wheel.py`: **9 passed**.
- Installed-wheel test runs in a fresh process outside the checkout and serves
  the page, nonempty worker assets and all five saved views. Actual HTTP bytes
  match precomputed chunk bytes; Sum's initial window exceeds 65535 when needed.

The earlier combined native-pyramid, publication, plane-stamp and contrast run
passed **55 tests**. Counts above overlap; they are not a sum of unique tests.
Long replay/benchmark ladders were not run. Automated scaling never publishes
more than 100 positions.

Build: `npm ci --prefix app/page`, `npm run build --prefix app/page`.
Lint: `python -m ruff check zmart_viewer tests/test_view_*.py tests/test_named_views*.py
tests/test_empty_live_view_browser.py tests/test_frontend_geometry_refresh.py`.

Environment warnings: pytest-timeout is absent, so pytest warns about its `timeout`
setting; that timeout was not enforced. npm warns about the machine's Node 26 alpha
version, and Vite reports the existing large engine bundle. Build/tests succeeded.
Browser rendering used ANGLE on NVIDIA T400, not a software-only metadata mock.

## Pixel and request evidence

- Slice -> Top at Z=2 changes **25,856 screenshot pixels**. Acquired opacity grows
  from **65,536 to 131,072 pixels**, with zero partial-alpha pixels.
- Acquired Min zeros stay black and opaque; empty ground stays transparent.
- Geometry rewrites change **65,536 screenshot pixels** while retaining zoom.
- Both arrival orders finish with **131,072 opaque pixels** and no partial alpha.
  Two-channel updates requested 24 or 36 chunks respectively, with no duplicate
  chunk URL during those measured update windows.
- Each tested idle window of 2.3 seconds has **zero image-data requests**, including
  after an identical announcement. No position-store image URLs reach the browser.
- Independent reviewer compared **3,840 served addresses before and 3,840 after**
  a stack-to-singleton rewrite: Slice and Top, 40 Z planes, 2 T, 2 C, every level
  and XY chunk matched fresh-composer bytes. Committed tests now retain served-byte
  assertions rather than merely exercising the address.

Generated screenshots are kept outside Git under the local evidence directory
`C:/ProgramData/MinicondaZMB/home/t.de/viewer-030-evidence-20260910`.
The numeric assertions, not the screenshots alone, establish correctness.

## Top cost and locality

`test_view_costs.py` instruments actual source-block cache misses and decoded bytes
in the fixture, without production telemetry. It reports lazy-composer sweep time
separately from served-chunk time, physical bake size and publication/rewrite time.

- Four-position, 40-plane fixture: Top has **17 sampling runs**, 17 physical coarse
  files / 595 bytes, versus Slice's 18 files / 502 bytes. Both decode 8 source blocks
  / 160 bytes at the measured level. The second sweep decodes no additional blocks.
- Thirty-eight staggered shallow stacks: Top has **40 runs** and no useful Z-run
  compression. Both decode 38 blocks / 3,648 bytes; Top stores 2,640 bytes versus
  Slice's 1,303 bytes. The test intentionally disproves universal compression.
- Tiny 8x8 append/rewrite fixtures at 2, 11 and 100 published positions verify that
  only intersecting coarse chunks/ancestors change. Rewriting the first position
  touches 6, 12 and 18 chunks respectively across the two named views.

These are synthetic cost/locality measurements, **not microscope throughput or
100 rig-sized-position benchmarks**. Top can cost more than Slice; no constant-time
publication claim is made. Source count stays one per named view (with channel
and coverage rendering layers), independent of position count.

One measured serving sweep of 40 logical Z addresses at level 2 took Slice/Top
43.16/60.85 ms with bake off, and 26.12/21.25 ms with bake on (four-position
fixture). The staggered case took 162.01/187.56 ms off and 60.19/58.84 ms on.
These single-run local measurements are diagnostic, not timing thresholds or
statistically controlled throughput claims.

## Independent reviews

Separate data/cost and release/lifecycle reviewers found and helped close:
empty-live-run calibration, persisted chunk-size restoration, T/C chunk-layout
collisions, recovery after a failed geometry declaration, projection coverage on
standalone reopen, and missing packaged frontend assets. The final bounded data
review reported no remaining blocker and independently reran 17 contract tests.

Remaining limits are explicit: relative Z only; aligned unrotated inputs;
completed-position revision publication; no automatic projection-revision garbage
collection; no arbitrary dataset relocation; no operator/rig adoption in this branch.
