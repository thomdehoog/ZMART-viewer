# Named-view format verification

2026-09-10. Production baseline: `ddb2851`, `codex/view-modes-data`, 0.4.0 candidate.

**Result: passed for the tested inputs.** No production code, dependencies,
machine settings, operator files or rig configuration changed. This is the
format-verification increment, not release approval or launcher completion.

## Matrix and evidence

All five named views (Top, Slice, Min, Max, Sum) were exercised with baking off
and on for each representation:

| Original representation | Independent data/coverage oracle | Browser switching and live updates |
| --- | --- | --- |
| OME-Zarr 0.4, Zarr v2 | Passed | Passed |
| OME-Zarr 0.5, Zarr v3 | Passed | Passed |
| OME-Zarr 0.5, sharded Zarr v3 | Passed | Passed |

Fixtures use TCZYX uint16 originals with three XY pyramid levels. The v2 fixture
uses Blosc/LZ4 and dotted chunk keys; v3 uses the installed writer's default codecs.
Sharded inputs contain multiple inner chunks per shard. This is not a claim to
have tested every codec, axis layout or optional OME-Zarr feature.

- **17,424 image/coverage chunk pairs** checked against an independent NumPy
  oracle: 2,904 per format/bake combination, across initial publication, append
  and rewrite. Shapes are independently asserted, including two T and two C
  entries, singleton images and four-plane stacks.
- Sparse acquired regions, missing planes in one T/C pair, gaps within chunks,
  entirely empty chunks, acquired black, and a later write filling a gap are
  checked. The all-black original has **no stored image chunks or shards**;
  coverage still makes it opaque.
- Every expected nonzero coarse chunk in bake-on mode must have a real baked
  file, decoded and compared directly. A compositor fallback cannot satisfy it.
  Baking off produces no coarse chunk files. Fresh readers and saved-view
  reopening preserve the expected pixels and coverage.
- Original file hashes are unchanged by publication, projection and reading.
  Appending a third position keeps five published view sources; the active
  browser view keeps two image layers (signal and coverage), with no requests
  to individual position-source URLs.
- Twelve owner cases cover all formats, baking off/on, equal/different voxel
  sizes across acquisitions, saved-folder reopening, closing one acquisition
  and continuing to publish the other.
- All six browser cases measured the same results: **25,856 changed pixels**
  between Slice and Top; **59,136** on rewrite; **32,768** on append.
  Each live update made **six unique chunk requests**, with no duplicate chunk
  requests and **zero idle data refetches** during the subsequent 2.3-second
  window. Projection zoom transitions preserve opacity, including acquired
  black in Min. Screenshots accompany the assertions.

## Runs

47 distinct tests passed across the runs below; repeated cases are not added
together. Ruff and diff whitespace checks also passed.

Reports: `C:/ProgramData/MinicondaZMB/home/t.de/viewer-040-format-gate-20260910/`.

- `data.xml`: 18 passed, 66.78 s (six format cases and twelve owner cases).
- `final-data-short.xml`: 29 passed, 55.62 s (stronger omitted-black fixture,
  plus sampling and projection helper regressions).
- `exact.xml`: final six format cases passed, 63.21 s (independent shape and
  mandatory physical baked-file checks added).
- `browser2.xml`: six passed, 276.07 s, ANGLE/NVIDIA T400.

Final data fixtures: `C:/ProgramData/MinicondaZMB/home/t.de/_fmt040/exact/`.
Browser screenshots: `C:/ProgramData/MinicondaZMB/home/t.de/_fmt040/browser2/`.
The sharded bake-on append proof is
`test_named_view_switching_pixe5/v3-sharded-append-True.png` in that directory.
Evidence images and generated data are not committed into the source tree.

## Limitations observed

This machine has `LongPathsEnabled=0`. A longer pytest evidence path made
nested projection staging plus Zarr's temporary chunk filename exceed the
Windows path limit, producing `FileNotFoundError` for every format. The same
cases pass under the shorter `_fmt040` root. Long-path handling was not changed;
keep dataset/output paths short on this configuration. The failed run is retained
as `final-data.xml`, not counted as a passing run.

An initial browser assertion photographed the old image after config delivery
but before replacement pixels arrived. The corrected test waits for an actual
pixel change, with a bounded ten-second assertion deadline, then measures
requests and idle behavior. It does not alter production refresh or timing.

No microscope acquisition or long scaling ladder was run. New format fixtures
use at most three positions; the earlier 100-position cost gate was not repeated.
The existing warning about the unavailable pytest timeout plugin remains.

## Reproduce

Use the configured `operator-021-test-settings.ps1`, retain `CONDA_PREFIX` for
Playwright, and set `PYTHONPATH` to this checkout. Use a fresh, short `--basetemp`
inside the whitelisted home and an up-to-date frontend build.

```sh
python -m pytest tests/test_named_view_formats.py tests/test_view_review_regressions.py::test_shared_folder_publication_owners_advance_independently -q
python -m pytest tests/test_view_sampling.py tests/test_view_projections.py -q
python -m pytest tests/test_named_views_browser.py::test_named_view_switching_pixels_requests_and_reopen -q -s
```
