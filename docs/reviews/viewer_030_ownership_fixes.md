# 0.3.0 ownership and lifecycle corrections — 2026-09-10

Feature branch: `codex/view-modes-data`, following `b1c5db6`.
No main merge, release tag, package upload, operator integration or rig changes.

## Changes

- `95c59df`: named acquisition identity is independent of geometry. Saved
  acquisitions at different objectives reopen as separate panel datasets;
  closing one does not retire another publisher. Owners use resolved destination
  and acquisition identity, not the library's transient dataset number. Legacy
  geometry grouping remains unchanged, including unknown legacy geometry.
- Original revision history is read from committed publications under an
  acquisition-scoped, cross-process lock. An older handle or a failure after the
  publication file lands cannot lower the committed revision. History includes
  retired originals and outputs not selected by the current handle.
- A second live owner for the same original folder in one destination is refused
  before publication. Independent destinations are allowed. Changed/invalid
  canvases and previews without durable history fail before projection writes.
  Those previews remain readable; live updates require a new view folder.
- Coverage canonicalization also lives at the shared publication boundary.
  Duplicate/reordered region announcements do not write arrays or metadata.
- Automatic publication runs in the existing folder watcher. Config and revision
  requests observe committed state without performing or waiting for a bake.
  Failures are logged per automatic owner; other owners continue. The watcher's
  existing explicit-announcement suppression identity is preserved.
- `6d52268`: Top's native global bounds use Neuroglancer's transform-aware
  coordinate binding, so bounds scale with the combined coordinate system.
  Metadata replacement updates the binding; disposal releases it.
- Effective rendering mode is derived from available datasets. Closing the last
  legacy acquisition restores named 2D views without an unsupported intermediate
  scene. The existing deliberate GL-context restart preserves position and zoom
  in physical units through the existing first-fit completion callback. Failed
  closes and closing everything do not retain a camera for an unrelated load.
- `c6fbc03`: each wheel uses fresh, owned temporary staging for the entire package.
  Existing build directories are not removed. Frontend certificates include
  public assets, and the wheel test compares the complete package byte-for-byte
  while a deliberately planted retired Python module exists in old staging.

## Evidence

External evidence directory (not committed):
`C:/ProgramData/MinicondaZMB/home/t.de/viewer-030-owner-fixes-20260910/`.

- Broad backend/contract selector: **296 passed, 19 browser cases deselected**, in
  206.33 s (`backend.xml`). It includes the previous review selector plus
  publication-lifecycle and announcement tests. A subsequently added
  unknown-legacy-identity case passed in the targeted run below.
- Targeted ownership, library, geometry, build and installed-wheel run:
  **49 passed** (`targeted-and-wheel.xml`). Counts overlap the broad run.
- Named-view browser and setting-accounting checks: the initial run passed
  15 cases, with four new Top assertions failing because the test used voxel
  edges where the format declares centers. The corrected four cases passed;
  the added physical-bounds oracle expects `[-3, 11]` micrometers for the coarse
  seven-plane stack at centers `-2, 0, 2, 4, 6, 8, 10`.
- Unequal-Z Top proof repeated with baking off and on: **2 passed**
  (`top-proof.xml`). All seven plane selections were reached. At fine zoom,
  131,072 pixels remained opaque; 65,536 were acquired black at the held boundary.
  At coarse zoom those counts were 2,048 and 1,024. Partial alpha was zero.
  Every tested idle window had zero image requests. Screenshots are in
  `top-proof/test_top_holds_each_acquisitio*/`.
- Legacy transparency, empty-live, manifest refresh and sparse aggregate browser
  suites: **19 passed** (`browser-regressions.xml`).
- Closing the last legacy acquisition initially exposed a real zoom reset.
  After the camera-lifecycle correction, both same-spacing and half-spacing
  legacy fixtures preserve physical magnification. The final rebuilt frontend
  and installed wheel passed **3 targeted cases** (`final-artifacts.xml`).
- Setting-accounting, wheel, frontend certificate and node geometry rerun:
  **8 passed** (`final-wheel-settings.xml`); the final wheel and camera rerun
  above followed the last unready-coordinate guard. Counts overlap.

The strengthened fractional reducer oracle uses 0.75 as well as 0.25 and asserts
that real coarse baked files exist. Running the 0.75 bake case against imported
`4cc1fb06` code fails at byte equality; it passes against the corrected code.
This closes the former all-zero/fallback false positive. No-write tests intercept
actual array and metadata writes rather than relying on timestamp uniqueness.

Final installed-wheel SHA256:
`b8ff424636c7a6da9f46a7e7c9d6b21ef19151481328c31a79cc8841f003a64e`.
Wheel: `final-artifacts/test_installed_wheel_serves_pa0/wheels/`.

Two independent reviewers checked backend ownership/durable history and
renderer/packaging respectively. Backend review reproduced the two old rollback
scenarios and confirmed revision-2 retries recover Sum 14. Its additional
watcher-isolation and mixed-root findings now have regression tests. Renderer
review confirmed transform-aware bounds and physical camera restoration.

`ruff check zmart_viewer tests build_support.py` and `git diff --check` pass.
Whole-repository lint still reports four pre-existing import-order findings in
unmodified demo scripts. Environment warnings remain: pytest-timeout is absent,
npm warns about this machine's alpha Node version, and Vite warns about bundle
size. Browser tests ran on ANGLE/NVIDIA T400. No long benchmark ladder was run;
automated scaling remains capped at 100 positions.

## Unchanged limitations

Commits are atomic per view, not across every product in an acquisition. An
interrupted pending view may require retry before it can be read. Old previews
without durable original revision history are read-only. Immutable projection
products are retained; garbage collection and relocation are separate work.
Whole-source client refresh remains intentional. Top's sparse-depth semantics
and the 0.3.0 two-dimensional scope are unchanged.
