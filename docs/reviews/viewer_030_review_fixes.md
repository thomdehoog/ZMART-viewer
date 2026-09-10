# 0.3.0 review corrections — 2026-09-10

Branch: `codex/view-modes-data`, following `4cc1fb0`. Viewer only; no operator,
rig, dependency pin, main merge, release tag or registry upload.

## Corrections

- One reducer-policy helper for composition and both bake paths. Unversioned
  legacy pictures retain rounded means; named views explicitly select fractional
  means from original data unless the producer certifies its input pyramid.
- Validate current chunk bounds before reading baked bytes. Geometry changes
  retire the owned chunk namespace when shape or chunk layout changes. Legacy
  extra coarse levels remain readable only where current metadata declares them.
- Validate original revision high-water marks before generating projections;
  persist them across reopen, partial commits and position retirement/re-add.
- Canonicalize coverage region lists and include the projection algorithm recipe
  in immutable product identity. Identical coverage does not rewrite products.
- Top uses native local-coordinate sampling to hold each aggregate's boundary
  outside its own range while retaining the shared global Z range. No duplicated
  volume, source replacement or publication on slider movement.
- HTTP publication ownership is per acquisition within a dataset folder. Owners
  advance independently; existing dataset-level status response shape is retained.
  Coverage/channel rendering blocks also respect acquisition identity: an upper
  acquired-black picture covers lower signal instead of adding it back on top of
  both underlays. Channels within an acquisition still mix additively.
- Legacy 3D remains selectable on mixed pages. Named products remain 2D-only and
  return when switching back; rendering, panel inclusion and framing agree.
- Successful frontend builds record input/output hashes. Wheel building rejects
  stale/incomplete output and replaces only its owned frontend staging tree.
  The installed-wheel test checks exact filenames and bytes, not just presence.

## Verification

Broad backend/contract rerun: **265 passed, 19 browser cases deselected** in 186.78 s.
This is the 18-file selector in `docs/design/view_modes_030_results.md`, plus
`test_view_review_regressions.py`, `test_build_frontend.py` and
`test_acquisition_coverage.py`. The later targeted review run passed **18 tests**,
including an additional independent shared-folder lifecycle case. Counts overlap.

Browser and artifact reruns: **12 passed** (all named-view browser cases plus the
installed wheel), **15 passed** (legacy transparency, empty-live and manifest
refresh browser suites), and **2 passed** in the final two-acquisition proof rerun.
At Z -2, 0, 1 and 4, Top retained 131,072 opaque pixels at fine zoom and 2,048 at
coarse zoom, bake off and on. The held acquired-black half retained 65,536 and
1,024 black pixels respectively at Z 1 and 4; partial alpha was zero. Each tested
idle window had zero image requests. Browser rendering used the NVIDIA T400.

After the final acquisition-block correction, **5 targeted browser cases passed**
(same-folder overlap, both bake choices, cross-acquisition Top and legacy 3D).
Slice, Top and Max each retained exactly 8,192 opaque black pixels over lower
bright signal, out of 16,384 acquired pixels. The final installed-wheel and build
certificate rerun passed **2 tests**; lint and `git diff --check` are clean.

Final tested wheel SHA256:
`36f4de77e3ade67114bd43244998342bccdf521c2aea53188bae066c40fa2761`.

The initial broad run caught a status-response-shape regression introduced by
the owner-key change; runtime code was corrected and the full run repeated.

New oracles use fresh composers for float bake comparisons, request removed
addresses, reject stale projections before and after reopen/retirement, and fail
on actual array/metadata writes during equivalent announcements. Timestamp or
hash equality alone is not used to establish no work.

The browser fixtures compare short/long acquisitions across both Z boundaries,
fine/coarse zoom and bake off/on, including acquired black, transparent gaps,
bounded rendered sources and zero idle image requests. Existing tests exercise
mode switching, both arrival orders, C/T, geometry refresh and retry after a
final metadata failure. Screenshots and the tested wheel are kept outside Git at
`C:/ProgramData/MinicondaZMB/home/t.de/viewer-030-review-fixes-20260910`.

Independent bounded reviewers found no remaining blocker in the reducer,
retired-address, local-Z or packaging fixes. Their additional retirement-history
and mixed-3D framing findings were fixed and covered by regressions.

## Explicit limits

Top holds **declared** array boundaries, not inferred acquired-depth boundaries;
unacquired boundary planes remain transparent. Off-lattice focus references are
rejected, not silently snapped. These semantics are documented in `docs/view_modes.md`.
No per-plane publication protocol, automatic projection garbage collection,
absolute-Z mode or operator deployment was added. Automated scaling remains
capped at 100 positions; synthetic tests are not microscope-throughput evidence.

Known environment warnings remain: pytest-timeout is unavailable; npm warns about
Node 26 alpha; Vite reports the existing large engine bundle.
