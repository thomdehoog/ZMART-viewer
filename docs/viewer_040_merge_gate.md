# Review corrections for the 0.4.0 candidate

2026-09-10, `codex/view-modes-data`, following the merge-gate review of `368ad41`.
This increment fixes that review; it does not claim the later launcher/format
roadmap gates, merge main, create a tag, or change the operator or microscope.
Version 0.4.0 follows the recorded decision not to reuse the 0.3.0 wheel name.

## Changes

- Bind each live publisher to its actual library dataset. A second legacy
  geometry group in the same folder keeps its own sources, depth and revisions;
  closing the owner cannot transfer its baked picture to the surviving group.
- Catch and log failures at each background publication boundary. A failed
  owner does not prevent other owners advancing or the watcher sampling changes.
  Status remains observational; no queue, polling loop or timeout was added.
- Keep the derived 2D mode and clear the old 3D request when the last legacy
  dataset closes. Opening legacy data later no longer hides the named views.
- Enforce source-folder ownership from committed/pending publications across
  restarts, under a source-scoped lock. Independent destinations remain allowed.
- Stage new projection products, validate actual projected geometry with the
  existing aggregate preparation, then promote and prepare final paths. This
  retains valid Sum inputs whose original dtypes/Z spacings differ. Validation
  failure removes temporary products and preserves committed products/publications.
- Coordinate immutable product promotion between independent view folders
  sharing projections. An identical concurrent winner is reused, not overwritten.
- Certify all frontend input directories except dependencies/build output;
  build the wheel regression in an isolated checkout copy. Whole-package exactness
  and install-outside-checkout assertions remain in place.

The extra projection preparation pass runs only for new products. Identical
announcements neither decode projection input pixels nor replace metadata/products
nor write chunks, checked by trapping those operations rather than timestamps.
Temporary computation on a rejected projection is allowed; zero permanent orphan
products is the guarantee. Later commit I/O failures still use per-view recovery,
not an all-views atomic transaction. Previously committed products are retained.

## Verification

Evidence: `C:/ProgramData/MinicondaZMB/home/t.de/viewer-040-merge-gate-20260910/`.

- `primary2.xml`: 44 passed, 58.42 seconds. Publication ownership (including the
  four formerly failing stack-address expectations), lifecycle, announcements,
  frontend certificate, two browser close/reopen cases and installed wheel.
- `data.xml`: 88 passed, 65.76 seconds. Named views, sampling, projections,
  recovery, independent bake/original oracles and cost fixtures capped at 100.
- `final.xml`: 21 passed, 14.22 seconds. Final lifecycle/concurrency/rejection
  additions and rebuilt installed wheel. Includes shared view/projection folders,
  a later projection method failing after an earlier one staged, and no-op traps.
- `browser.xml`: 20 passed, 458.71 seconds, ANGLE/NVIDIA T400. View switching,
  cross-acquisition coverage, distinct Z spacings, geometry refresh/retry, both
  flat/stack arrival orders, zero idle refetch assertions, reopening legacy data
  in 2D and settings propagation. Screenshots are in `browser/`, not committed.
- Independent reviewer: six targeted cases passed; no remaining blocker in the
  bounded staging, concurrency and dataset-owner review.
- Counterfactual: the dataset-isolation and failing-hook tests both fail with
  the corresponding classes loaded from `368ad41` (`old-code.xml`), and pass with
  the fixes. No checkout or branch was changed for this test.
- Scoped Ruff checks passed. The environment still warns about its uninstalled
  pytest timeout plugin; npm reports its existing alpha-Node compatibility and
  bundle-size warnings. No long acquisition/benchmark ladder was run.

Final wheel: `final/test_installed_wheel_serves_pa0/wheels/zmart_viewer-0.4.0-py3-none-any.whl`.
Size: 1,502,252 bytes. SHA256:
`9ace2fa1494e64f4219bedf10c419a821f87bffabfe58d6667c34e0289f94ca5`.
The wheel test compares every package file byte-for-byte, rejects orphan staging
modules, installs outside the source checkout and serves all five saved views.

Reproduce with the repository's configured environment and current built page:

```sh
npm --prefix app/page run build
python -m pytest tests/test_view_publication_lifecycle.py tests/test_publication_owner.py tests/test_announcements.py tests/test_build_frontend.py tests/test_view_wheel.py -q
python -m pytest tests/test_named_views.py tests/test_view_projections.py tests/test_view_sampling.py tests/test_view_release_contract.py tests/test_view_review_regressions.py tests/test_view_costs.py -q
python -m pytest tests/test_named_views_browser.py tests/test_no_setting_is_dropped_on_the_way_to_the_engine.py -q
python -m ruff check zmart_viewer tests build_support.py
```

On this machine source `operator-021-test-settings.ps1`, retain its `CONDA_PREFIX`
for Playwright, and set `PYTHONPATH` to this viewer checkout before running Python.
