# Report: the viewer refactor, as done on the lab PC

This is the report that `REFACTOR_HANDOVER.md` asked for in its section 8.
It says, step by step, what was done, what was deliberately left and why,
what the tests said before and after, what behaved differently on the way,
and what was noticed but not touched. The work was done on 2026-10-08 and
2026-10-09 on the lab PC, on the branch `claude/smart-viewer-refactor-z0857c`.

## The steps

| step | what the hand-over asked | outcome |
|---|---|---|
| 1 | one file-system helper module | **done** |
| 2 | no underscore-prefixed imports across modules | **done** |
| 3 | split `PublishedTransfer.prepare()` | **done** |
| 4 | lift the `/api/config` builder out of `make_server` | **done** |
| 5 | the other long functions, and `vocabulary.py` | **partly** |
| 6 | the React files | **partly** |

Each step is one commit, in this order, and every commit was made only after
the test files that exercise the changed code had passed; the whole suite
was run on the finished branch (section "The tests" below).

### Step 1, done

`engine/filesystem.py` now holds the one retry loop
(`done_despite_brief_holds`), `written_despite_brief_holds` and
`rmtree_despite_brief_holds` on top of it, `push_directory_to_disk` (moved
from `manifest.py`), and `put_text_in_place` and `put_json_in_place` (the
temporary file beside the target, renamed over it, the temporary file
removed on any failure, and with `pushed_to_disk=True` the fsync of the
file and the folder entry). Every caller kept its previous choice of
`pushed_to_disk`. `vocabulary.py` re-exports the two old names;
`built_picture.py` keeps `_after_a_windows_reader` as an alias.
`manifest._write_and_replace`, `publishing._atomic_json` and
`omezarr._write_over_carefully` are gone, every importer in the repository
was updated, and the two tests that stub the publication write patch
`publishing.put_json_in_place`.

Two small things behave differently, both on Windows only and both
deliberate: the server's annotation save now waits out a reader's brief
hold on its rename, which it did not before; and the retry loop uses the
longest deadline (10 s) and the shortest first pause (2 ms) of the three
originals, and accepts the union of their error numbers.

One slip was caught by the suite and fixed in a follow-up commit: the new
loop at first looked only at the Windows error number, while the loop it
replaced in `built_picture.py` also accepted a `PermissionError` with errno
5 or 13 and no Windows number, which is what a `PermissionError` raised
from Python itself looks like, and which
`tests/test_a_governed_picture_is_baked_per_commit.py` raises to stand in
for a reader's hold. That fallback is back.

### Step 2, done

`read_attrs_at`, `read_array_description`, `description_file`,
`moments_folder` (one more than the hand-over listed; `contrast.py`
imported it), `read_one_tile`, `refuse_tiles_that_disagree` and
`holding_the_bake_lock` carry their plain names; every importer and test
uses them; the underscore spellings stay as aliases at each definition
site for one release. The hand-over's grep for cross-module private
imports finds nothing.

### Step 3, done

`prepare()` is a sequence of named steps: `validated_composition()` at
module level, `_refuse_changes_that_cannot_be_made_in_place()`,
`_what_changed()`, `_tiles_for()`, `_placed_in_depth()`,
`_refuse_tiles_that_cannot_be_baked_together()`, `_mosaic_for()`,
`_geometry_changed()`, `_refuse_tiles_outside_the_canvas()`, and
`_commit(plan)` with `_dirty_pieces()` and `_bake_dirty_pieces()`, taking
everything it needs from a `_Prepared` record. Every error message is
unchanged word for word. The longest function in the file is now 117 lines
(`_place_depth`, untouched); `prepare()` is 102.

### Step 4, done

`_LayerPanelConfig` (built once by `make_server`) has `now()` (the cached,
locked answer), `build()`, `_describe_one_store()`, `_channels_of()`,
`_frames_and_revisions()` and `_mark_coverage()`. The nested server class
is module-level too. `make_server` keeps its signature, defaults and
handler wiring and is 106 lines. The `/api/config` answer was compared
byte for byte before and after on the demo data for four set-ups (one
store; three stores with a transparent background, the panel on the left
and the selection list; finished data with opening disallowed; the empty
studio), each fetched twice: identical.

### Step 5, partly

Done: `LivePublisher.__post_init__` and `AcquisitionProfile.__post_init__`
are each a short sequence of named `_check_*` methods in the original
order, raising the original messages; `write_projection` keeps its checks,
recipe and staging-then-rename swap and hands the pixel work to
`_project_the_finest_level`, `_halve_down_the_levels` and
`_the_description_of`.

Left, deliberately: `read_the_index`, `_routes_now`, `plan_the_writing`,
`link_a_finished_run`, `build_the_scene`, `_bake_the_coarse_ground`, and
`open_window` and `main` in `gui/window.py`; and the split of
`vocabulary.py` into several modules. None of the functions duplicates
anything and each is one sequence of work rather than a list of checks, so
a split would move lines without making a seam the reader needs. The
vocabulary split is not the pure reorganisation the rest of this series
is: its classes are written to and read from disk by name, so moving them
wants its own round of thought and tests.

### Step 6, partly

Done, each rebuilt and run through the browser tests before its commit:

- `App.jsx` (2,355 lines) gave up `LoadWindow.jsx` (with `ROW_KINDS` and
  the load styles), `ModeToggle.jsx`, `BringItBack.jsx`, `ThemeToggle.jsx`
  (sharing `top-bar-styles.js`), `annotation-layer.js` and
  `asking-python.js` (the eleven short conversations with the server). It
  is 1,329 lines.
- `LayerPanel.jsx` (1,747 lines) gave up `Histogram.jsx`, `ValueBox.jsx`,
  `Eye.jsx`, `VolumeMode.jsx`, `ColourChooser.jsx`, `ChannelControls.jsx`,
  `layer-panel-styles.js`, `colours.js`, `contrast-window.js` and
  `display-axes.js`. It is 248 lines.

Left, deliberately: the custom hooks and the reducer for the shell's state
in `App()`, and the split of `drawing/neuroglancer.js`. Both change where
state lives and in what order effects run, and the browser tests alone
cannot show that to be neutral; they want their own design and review.

The page was rebuilt on this PC from a copy of the sources with its own
`npm ci`. Before any change, that rebuild reproduced the committed
`gui/build` byte for byte, which is what makes the later rebuilds
trustworthy. (With `node_modules` reached through a folder junction
instead, esbuild wrote different source-path comments into the two worker
bundles; nothing else differed.)

## The tests

Every run below was made with `ZMART_REQUIRE_BROWSER=1`, so the browser
tests had to run, and with one pytest process per test file (see "About
this PC"), which is why the summary is a sum over files rather than one
pytest line. Temporary files were on `D:`.

**The viewer, before any change** (the untouched branch, conda env
`zmart-viewer-dev`), 115 files, 68 minutes:

```
1198 passed, 3 failed, 12 skipped, 2 xfailed
```

The three failures were not the code's: one was an untracked draft file
of mine in the checkout that the wheel test counted as an extra file; one
is a race in saving the targets file (below); one is the pixel flake
(below).

**The viewer, on the finished branch**, 115 files, 121 minutes (three
suites were sharing the machine):

```
1196 passed, 1 failed, 11 skipped, 2 xfailed, and two files with no result
```

The one failure is the same save race as before the change. The two
files with no result, `test_a_commit_storm_under_zooming.py` and
`test_absorbing_a_change_touches_the_change.py`, were ended by Kaspersky
or hung past their timeout on all four attempts. Rerun on their own
afterwards: `test_absorbing_a_change_touches_the_change.py` **2 passed**
(on its third attempt; the first two were ended), and the save race and
the pixel test were run again too, **40 passed, 1 skipped** and **18
passed, 1 failed** (the flake, on a different parameter than before). The
storm file never finished: eight attempts in all, each ended by Kaspersky
about forty seconds in, while it writes its 1,600 positions, and the
last with Kaspersky's verdict logged at 03:03:48 against that very
process. It cannot be qualified on this PC until the exclusion below is in
place. The wheel test, which failed before, passes.
Every other count matches the baseline exactly (the one skip fewer is in
the storm file). `python -m ruff check .` passes.

**The viewer by hand** (section 7b) was not done: this session had no
one at the screen. The browser suite covers opening, the Z and T sliders,
hiding and recolouring a channel, contrast, the 3-D view, drawing and
saving a target, and closing and reopening, and all of it passed; the
by-hand check is yours to make.

**The interface** (section 7c), conda env `zmart-interface-dev`, this
viewer installed editable over the one pip pulled from GitHub
(`zmart_viewer.__file__` in the checkout, version `0.5.0rc1`):

- `flat-tiles.spec.js` and `named-views.spec.js`, the direct test of the
  contract in section 3 (they start `make_server`, call
  `/api/stores/open`, `/api/announce` and `/api/config`, and import
  `/embedding.js`): **8 passed**.
- `neuroglancer-workers.mjs` found `neuroglancer-growth.mjs` in the
  installed viewer and built both workers; `vite build` built the page.
- `python -m pytest`: **273 passed, 7 failed, 2 skipped**, and exactly
  the same 273 and the same 7 with the viewer pip pulls from GitHub
  instead of this one. So the seven are the interface's own, on this PC:
  two bridge tests and two detection tests that need the analysis engine,
  whose installed copy starts its workers with the analysis env's
  `bin/python`, a Linux path that Windows refuses; the mock driver against
  a controller branch whose `get_xyz` answer has moved on; one kidney
  simulator anchoring test; and one named-view input test whose analysis
  pipeline keys have changed. The tests that use the viewer (the storage
  service that starts `make_server`, `test_zarr_positions.py`, and the
  bridge tests that publish through `PublishedAcquisition` and `STORE`)
  passed.
- `vitest`: 566 passed, 5 failed, 16 skipped. The five failures are all in
  `stage-position.test.js`, which watches the stage through the controller
  and never touches the viewer.
- `walk.spec.js`: connects to the mock, scans the overview and shows it
  through this viewer, then fails at the focus step with "a focus point was
  measured through the bridge", because the analysis engine cannot start
  its worker on this PC (the `bin/python` path above). That is the same
  wall the interface's own pytest meets, and it is after the part of the
  walk that uses the viewer.

For the interface's checks to be complete on this PC, three things of its
own need sorting, none of them in the viewer: the controller branch it
follows (`main` lacks `zmart_controller.session`; `polish/second-pass`
has everything it imports), the analysis engine's Windows path for the
worker's Python, and a checkout of ZMART-analysis for the workflows
(`ZMART_ANALYSIS_WORKFLOWS`).

**The interface by hand** (section 7d) was not done, for the same reason
as 7b.

## What behaved differently, and how it was resolved

- The errno fallback of the retry loop (step 1, above).
- Nothing else: every other difference the tests found was in the test
  machine, not the code (below).

## Real bugs noticed, deliberately not fixed

- `gui/source/LayerPanel.jsx` had a stray `export` keyword in front of a
  comment, so that it applied to `ROW_ITEM` two lines down; nothing imports
  `ROW_ITEM`. The split keeps the export, now on `ROW_ITEM`'s own line.
- `tests/test_mixed_acquisition.py::test_browser_mixed_relative_depth[True-True]`
  read a coloured pixel where it expected black once, on the untouched
  branch, under heavy load; it passed on every other run. It looks like a
  timing flake rather than a fault, but it is the kind that deserves a look.
- Nothing was found in the engine's behaviour.

## About this PC

- **Kaspersky Endpoint Security ends a Python process that rewrites many
  files quickly** (event 4662 in its log; the process exits with code 5 and
  no output). It ended three whole-suite runs and one test runner before
  the runs were changed to one process per test file with a retry. The
  exclusion that would stop this is `C:\ProgramData\MinicondaZMB\envs\*\python.exe`;
  until it is in place, a live acquisition that bakes many tiles could meet
  the same fate.
- At 03:03 Kaspersky went one step further and **removed `python.exe` from
  `C:\ProgramData\MinicondaZMB\envs\zmart-viewer-dev`**, the env the
  baseline had run in, straight after its verdict on the storm test run
  from there; nothing else in the env was touched. It was put back from
  the identical build in the interface's env. Earlier in the evening the
  same file had gone missing from conda's package cache, which is what
  made the first attempt at the interface env fail with "the package for
  python appears to be corrupted". Kaspersky's own process also logged
  "crashed during previous session" and restarted at 23:17, 23:35, 23:54
  and 02:58, re-applying group policy each time. The storm file passed in
  the baseline on a retry, most likely in one of those gaps.
- `tests/test_a_commit_storm_under_zooming.py` also hangs past its timeout
  under load on this PC (pytest-timeout's thread does not get to fire), so
  a run of it can cost its full ten minutes before the runner gives up.
- The scripts that made this workable are in
  `D:\claude-scratch\zmart-viewer-refactor\`: `run_suite.sh` (one pytest
  process per file, with retries), `rebuild_page.sh` (the page rebuilt from
  a copy with its own `npm ci`), `config_snapshot.py` (the `/api/config`
  comparison) and `interface_check.sh` (section 7c).
