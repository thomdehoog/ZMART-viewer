# Hand-over: refactoring the ZMART Viewer on the lab PC

This file is for a Claude Code session running on the lab PC. It holds
everything that session needs: what to refactor and why, in what order, the
rules that keep it safe, how the PC is set up, how to prove at the end that
the viewer still works on its own *and* plugged into the ZMART interface, and
a ready-to-paste prompt (at the very end).

Delete this file once the work is merged.

## 1. What this is, in one paragraph

The ZMART Viewer (this repository) works and is well tested, but several of
its files and functions have grown very large, and a few small helpers are
duplicated across modules. The job is a **pure refactor**: reorganise the
code so that it is easier to read and change, with **no change in what it
does**. Every public name and every HTTP route the ZMART interface relies on
stays exactly as it is. The test suite is the safety net, and it is run after
every step. Nothing is pushed until it is green.

The plan below was written after reading the code, not after running it, so
treat each step as a well-founded suggestion: if, on reading the code, a step
turns out to be riskier than it looks, do the safe part and write down why
the rest was left.

## 2. The repository, briefly

Read `docs/how_it_works/ARCHITECTURE.md` first; it is the map. In short:

- `engine/` is the Python package `zmart_viewer`: reading OME-Zarr, placing
  positions, following a folder while it is written, serving the picture over
  HTTP. Its public names are in `engine/__init__.py`.
- `gui/source/` is the viewer's own window (React, built with Vite), and
  `gui/build/` is the built copy, committed, because a pip install cannot
  build it.
- `tests/` is a large pytest suite (about 1,200 tests). About a third open a
  real browser; they skip without one, and `ZMART_REQUIRE_BROWSER=1` turns a
  skip into a failure. `docs/how_it_works/TESTING.md` explains it.
- `CLAUDE.md` sets the writing style for every docstring and comment: full,
  gentle sentences for biologists who are learning, not for engineers. Keep
  to it in every line you touch.

## 3. The contract that must not move

`ZMART-interface` (a separate repository, in the same folder on the PC) uses
the viewer only through the names and routes below. Each one stays exactly as
it is; the final check is that each one still behaves the same.

- `from zmart_viewer import make_server, PublishedAcquisition, STORE, write_projection`.
- The keyword arguments of `make_server`, in particular
  `make_server(port=0, data_dir=..., live=True, allow_open=True, panel_side="left", canvas=..., transparent_background=True)`.
- `zmart_viewer.views.publishing.ACQUISITION_RENDERING_VERSION == 1`.
- `PublishedAcquisition(folder, piece=...)`, its `.outputs[STORE].composer()`,
  `.publish(...)`, `.revision`, `.close()`.
- The HTTP routes served by `engine/serving/server.py`: `/api/config`,
  `/api/stores/open`, `/api/stores/close`, `/api/stores/list`,
  `/api/stores/construct`, `/api/stores/construct-status`,
  `/api/stores/construct-cancel`, `/api/announce`, `/api/measure`,
  `/api/live-state`, `/api/events`, `/api/health`, `/api/annotations`,
  `/api/browse`, `/data/...`, `/embedding.js`, and the shape of what each
  answers.
- The file `engine/drawing/neuroglancer-growth.mjs` at that path: the
  interface's `neuroglancer-workers.mjs` reads it from the installed package
  (`Path(zmart_viewer.__file__).parent / "drawing" / "neuroglancer-growth.mjs"`).
- `engine/drawing/embedding.js`, served as `/embedding.js`.
- The version `0.5.0rc1` in `pyproject.toml` (the interface accepts
  `>=0.5.0rc1,<0.6`).
- The package list in `pyproject.toml` (`[tool.setuptools] packages = [...]`)
  is **explicit**. A new *module* inside an existing package needs nothing;
  a new *sub-package* (a new folder with `__init__.py`) must be added there,
  or the wheel ships without it and the interface's install breaks quietly.

## 4. The lab PC: how things run there

These constraints come from the owner of the machine. Follow them.

- The PC has **AppLocker**: executables run only from
  `C:\ProgramData\MinicondaZMB` (the conda root). Nothing runs from
  `C:\Users` or from `D:`.
- `HOME` is `C:\ProgramData\MinicondaZMB\home\t.de`.
- The repositories (ZMART-viewer, ZMART-interface, and the rest) are checked
  out under `C:\ProgramData\MinicondaZMB\home\t.de\`.
- Node is `C:\ProgramData\MinicondaZMB\envs\lasxapi_extended\node.exe`.
- Make **one conda env per project** under the conda root (for example
  `zmart-viewer-dev` and `zmart-interface-dev`, Python 3.12).
- Call node tools **directly**, never through `npm run`:
  `node node_modules\<tool>\bin\<tool>.js`. For example Vite is
  `node node_modules\vite\bin\vite.js build`, Playwright is
  `node node_modules\@playwright\test\cli.js test`, and npm itself is
  `node <node's folder>\node_modules\npm\bin\npm-cli.js ci`.
- **Build JavaScript from a copy** of the sources under
  `C:\ProgramData\MinicondaZMB\home\t.de\build\`, not inside the repository
  checkout. Copy `gui/source` (without `node_modules`) and `engine/drawing`
  there, keeping the same relative layout (`<copy>/gui/source`,
  `<copy>/engine/drawing`, `<copy>/gui/build`), because the build stamp
  records paths relative to the repository root. Copy `gui/build` back into
  the checkout when it is done.
- Playwright's browser download needs `NODE_TLS_REJECT_UNAUTHORIZED=0`. Put
  the browsers somewhere allowed, for example
  `PLAYWRIGHT_BROWSERS_PATH=C:\ProgramData\MinicondaZMB\ms-playwright`.
- Put **data and scratch files on `D:`**, for example test data and
  `TMP`/`TEMP`. (pytest's `tmp_path` follows `TEMP`.)

Setting up the viewer's environment, from the viewer checkout:

```bat
conda create -n zmart-viewer-dev -c conda-forge python=3.12 pip git
conda activate zmart-viewer-dev
python -m pip install -e ".[dev]"
set PLAYWRIGHT_BROWSERS_PATH=C:\ProgramData\MinicondaZMB\ms-playwright
set NODE_TLS_REJECT_UNAUTHORIZED=0
python -m playwright install chromium
:: In the build copy of gui/source (see above):
node <npm-cli.js> ci
```

## 5. Before touching anything: the baseline

Run the whole suite on the untouched branch and keep the summary line. This
is what "green" means on this machine, and the number of skips is part of it.

```bat
set ZMART_REQUIRE_BROWSER=1
python -m pytest tests -q -p no:cacheprovider --timeout=600
```

If a test fails *before* any change, it is not the refactor's fault; note it
and move on, but keep an eye on it.

## 6. The refactors, in order

Do them in this order: the first two are small and mechanical and make the
later ones safer; the Python ones come before the JavaScript one because the
Python suite is the stronger net. **One refactor, one commit**, with the
tests green before each commit. Keep each commit message plain about what
moved and that nothing changed in behaviour.

General rules for every step:

- Behaviour stays identical. If you find a real bug on the way, write it down
  in the final report; do not fix it inside the refactor commit.
- When a name moves, keep the old name importable from where it was (an alias
  or a re-export) unless you have updated every importer *and* every test,
  and say so in the commit message.
- Tests that `monkeypatch` a module attribute keep working only if the
  attribute still exists on that module *and* the code still looks it up
  there. Check before moving such a name.
- Docstrings and comments follow `CLAUDE.md`. Moved code keeps its
  explanations; new helpers get a docstring a biologist could follow.
- Run `python -m ruff check .` (configured in `pyproject.toml`) as well as
  the tests.

### Step 1 — one file-system helper module

**Where:** `engine/picture/built_picture.py`, `engine/live/record/vocabulary.py`,
`engine/live/record/manifest.py`, `engine/views/publishing.py`,
`engine/live/record/omezarr.py`, `engine/serving/server.py`.

**What is duplicated.** Three functions retry a file operation while Windows
briefly holds the file, each with different timings and error codes:

| function | file | deadline | first pause / cap | codes |
|---|---|---|---|---|
| `_after_a_windows_reader(operation, *paths)` | `built_picture.py` ~line 465 | 5 s | 2 ms / 50 ms | winerror 5, 32, 33 or errno 5, 13; only when `os.name == "nt"` |
| `written_despite_brief_holds(write)` | `vocabulary.py` ~line 1587 | 10 s | 50 ms / 1 s | winerror 5, 32 |
| `rmtree_despite_brief_holds(tree)` | `vocabulary.py` ~line 1620 | 10 s | 50 ms / 1 s | winerror 5, 32, 145; `FileNotFoundError` is fine |

And four pieces of code write a file "atomically" (to a temporary name beside
the target, then rename over it), each slightly differently:

- `_write_and_replace(destination, text)` in `manifest.py` ~line 234: temp
  file in the same folder, flush, `fsync`, rename with retry, then
  `_push_directory_to_disk`. Used by `manifest.py`, `publisher.py`, and
  `identity.py` (imported there as `_put_in_place_in_one_step`).
- `_atomic_json(path, value)` in `publishing.py` ~line 449: `.publishing`
  sibling, rename with retry, no `fsync`. **Two tests monkeypatch it**:
  `tests/test_named_view_formats.py` ~line 244 and
  `tests/test_view_review_regressions.py` ~line 49 (`patch.setattr(published, "_atomic_json", ...)`).
- `_write_over_carefully(target, text)` in `omezarr.py` ~line 500:
  `.writing` sibling, rename with retry, no `fsync`.
- `_save_annotations` in `server.py` ~line 1300: `mkstemp`, `fsync`, rename
  **without** retry, cleans up on failure.

**What to do.** Create `engine/filesystem.py` (a module in the top-level
package, so no change to the package list) holding:

- `done_despite_brief_holds(operation, *arguments)`: the one retry loop.
  Catch `OSError`, retry only when `os.name == "nt"` and `winerror` is in
  `(5, 32, 33, 145)`, deadline 10 s, pause from 2 ms doubling to a 1 s cap.
  (This takes the longest deadline and the shortest first pause of the three
  originals; the retry can only ever fire on Windows, so Linux behaviour is
  unchanged.)
- `rmtree_despite_brief_holds(tree)`: `shutil.rmtree` through the above,
  swallowing `FileNotFoundError`.
- `put_text_in_place(destination, text, *, pushed_to_disk=False)`: the
  temp-file-then-rename write, with `fsync` and the directory push only when
  `pushed_to_disk=True`, and cleanup of the temp file on any failure.
- `put_json_in_place(destination, value, *, pushed_to_disk=False, indent=None)`.
- Move `_push_directory_to_disk` here from `manifest.py`.

Then point every caller at these. Keep each caller's current choice of
`pushed_to_disk` (manifest/identity/publisher: `True`; publishing and
omezarr: `False`; the server's annotations: `True`). Leave the directory swap
in `projections.py` (~line 297) alone; it replaces a folder, which is a
different thing. Update the two tests above to patch
`published.put_json_in_place` (and import it by name in `publishing.py`, so
the patch takes effect). Keep `written_despite_brief_holds` and
`rmtree_despite_brief_holds` importable from `vocabulary.py` as re-exports,
since `omezarr.py` imports from there and other code may too.

**Done when:** every retry and atomic-write helper lives in
`engine/filesystem.py`, the suite is green, and the Windows run (this PC) of
the live-publication tests is green: `tests/test_manifest_driven_refresh.py`,
`tests/test_live_publication_gateway.py`, `tests/test_a_run_arriving.py`,
`tests/test_bake_lock.py`.

### Step 2 — stop importing underscore-prefixed names across modules

**Where:** these imports cross module boundaries today:

- `_read_attrs_at`, `_read_array_description`, `_description_file` from
  `engine/opening/open_folders.py` (imported by `views/projections.py`,
  `views/publishing.py`, `views/slice_top_projection.py`, `serving/coverage.py`).
- `_read_one_tile`, `_refuse_tiles_that_disagree` from
  `engine/picture/arrangement.py` (imported by `views/publishing.py`,
  `views/slice_top_projection.py`).
- `_holding_the_bake_lock` from `engine/picture/built_picture.py` (imported by
  `views/publishing.py`, `views/slice_top_projection.py`).
- `_write_and_replace` from `manifest.py` (goes away in step 1).

**What to do.** Rename each without the leading underscore
(`read_attrs_at`, `read_array_description`, `description_file`,
`read_one_tile`, `refuse_tiles_that_disagree`, `holding_the_bake_lock`),
update every importer, and leave `_old = new` aliases at the definition site
for one release so nothing outside the repository breaks. Grep `tests/` for
each name and update those too. Nothing in `engine/__init__.py` changes.

**Done when:** `grep -rn "import .*\b_[a-z]" engine --include=*.py` finds
no cross-module private imports, and the suite is green.

### Step 3 — split `PublishedTransfer.prepare()`

**Where:** `engine/views/publishing.py`, `prepare()` at ~line 691: 489
lines, about 75 branches, with a 154-line nested `commit()` and an
11-line nested `geometry()`.

**What it does today, in order:**

1. Lines ~702–735: checks the inputs (`versions` is a dict, `composition` has
   exactly the allowed keys, `view` has the right shape, `bake` and
   `composition` agree) and canonicalises `regions`.
2. Lines ~740–770: takes the lock, reads the previous publication state, and
   refuses changes that are not allowed in place (view identity, originals,
   coverage contract, XY origin).
3. The middle: works out what changed since the previous state and whether
   any pixel work is needed at all.
4. `commit()`: writes the pending file, the arrays, the descriptions, and
   the publication state, with `_atomic_json` / `put_json_in_place`.

**What to do.** Lift (1) into a module-level
`validated_composition(versions, canvas, composition, *, bake)` that returns
the cleaned copies or raises the same `ValueError` messages. Lift (2) into a
method `_refuse_changes_that_cannot_be_made_in_place(old, new)`. Move
`commit()` to a method `_commit(...)` taking what it needs as arguments
rather than closing over the enclosing locals. Keep `prepare()` as the
sequence that calls them. The error messages must stay word for word: tests
match on them (`grep -rn "cannot change\|must contain\|Named views require" tests`).

**Done when:** no function in the file is longer than about 120 lines, and
`tests/test_named_views.py`, `tests/test_named_view_formats.py`,
`tests/test_acquired_composition.py`, `tests/test_view_review_regressions.py`
and `tests/test_a_view_survives_moving.py` are green, then the whole suite.

### Step 4 — lift `build_config` and `config_now` out of `make_server`

**Where:** `engine/serving/server.py`, `make_server()` at ~line 1390: 368
lines, with `config_now()` (~line 1451) and `build_config()` (~line 1485,
211 lines) nested inside and sharing its locals (`library`, `published`,
`registry`, `measurements`, `last_built`, `building_config`, `scratch`).

**What to do.** Make a small class, for example `_LayerPanelConfig`, built
once in `make_server` with `library`, `published`, `registry` and whatever
else `build_config` reads; give it `now()` (the cached, locked
`config_now`) and `build(...)` (today's `build_config`). `make_server` keeps
its signature, its defaults, and the handler wiring exactly; it only becomes
shorter. If `build_config` itself is still over 150 lines, split the loop
over `entries` into a helper that describes one row.

The `_Handler` class (~lines 209–1365) could then be grouped by area
(serving files, API reads, API writes, annotations) using comments or
small mixins, but only if it stays one class with the same method names;
tests subclass or patch nothing there, but the routes must not change.

**Done when:** `make_server` is under about 120 lines, the `/api/config`
answers are byte-for-byte what they were for the same inputs (compare a
saved answer before and after on the test data in `tests/demo_data.py`),
and the suite is green, with `tests/test_each_acquisition_gets_a_row.py`,
`tests/test_layer_groups.py`, `tests/test_acquisition_groups.py`,
`tests/test_announcements.py` and `tests/test_open_and_close.py` as the
first ones to run.

### Step 5 — the other long functions, and `vocabulary.py`

Only where the gain is clear. Candidates, with their length:

- `write_projection` (`views/projections.py` ~line 104, 209 lines).
- `LivePublisher.__post_init__` (`live/record/publisher.py` ~line 246, 157
  lines) and `AcquisitionProfile.__post_init__` (`vocabulary.py` ~line 749,
  158 lines): mostly validation; split into named `_check_*` methods called
  in the same order, raising the same messages.
- `read_the_index` (`shard_lookup.py` ~line 635, 164), `_routes_now`
  (`live_serving.py` ~line 238, 165), `plan_the_writing`
  (`storage_plans.py` ~line 674, 155), `link_a_finished_run`
  (`picture_pieces.py` ~line 464, 148), `build_the_scene` (`scene.py` ~line
  656, 145), `_bake_the_coarse_ground` (`built_picture.py` ~line 223, 133),
  `open_window` and `main` in `gui/window.py` (130 and 125).

`engine/live/record/vocabulary.py` (1,726 lines) mixes geometry
(`Interval`, `Box`, `GridCell`), the acquisition profile, scene and layout
types, the errors, channel colours and the file helpers. If you split it,
split into `geometry.py`, `profile.py` and `scene_types.py` **inside
`live/record/`** (existing package, no package-list change) and have
`vocabulary.py` import and re-export every name, so that every existing
`from zmart_viewer.live.record.vocabulary import ...` keeps working. Check
there is no import cycle: the new modules must not import `vocabulary`.

**Done when:** each touched function is under about 120 lines, nothing is
renamed without an alias, and the suite is green.

### Step 6 — the React files, last

**Where:** `gui/source/App.jsx` (2,355 lines; `App()` alone is about 1,400
with 68 `useState`/`useEffect`/`useRef` calls and 130 inline `style={{}}`
objects) and `gui/source/LayerPanel.jsx` (1,747 lines; `ChannelControls`
is about 550 and the `styles` object about 400).

**Why last, and why carefully.** The browser tests are the only net for this
code, and they read the *built* page in `gui/build`. The build stamp
(`gui/source/scripts/stamp-build.mjs`) hashes every file under `gui/source`
(except `node_modules`) and `engine/drawing` into
`gui/build/build-manifest.json`, and `build_support.py` refuses to build a
wheel if any source hash differs — so **every JavaScript change, including
adding a file, needs a rebuild**, and the rebuilt `gui/build` is committed
with the change. On this PC the build runs from the copy under
`home\t.de\build\` (section 4), step by step, since `npm run` is not
available:

```bat
node scripts\patch_neuroglancer.mjs --modules-only
node scripts\precompile-workers.mjs
node scripts\patch_neuroglancer.mjs
node node_modules\vite\bin\vite.js build
node scripts\copy-async-worker.mjs
node scripts\stamp-build.mjs
```

(run inside `<copy>\gui\source`), then copy `<copy>\gui\build` over the
checkout's `gui/build`. `tests/test_build_frontend.py` and
`tests/test_build_artifacts.py` check the result.

**What to do, one piece at a time, rebuilding and running the browser tests
after each:**

1. Move `LoadWindow` (~lines 230–780 of `App.jsx`) to `LoadWindow.jsx`.
   Move `ModeToggle`, `BringItBack`, `ThemeToggle` and the annotation-layer
   helpers (`annotationLayer`, `TARGET_LAYER`) to small files of their own.
2. Pull groups of state in `App()` into custom hooks in a `hooks/` folder:
   `useTargets` (targets, targetsLoaded, targetColor, targetsVisible,
   activeTool, saveState), `useVolumeSettings` (volumeMode, volumeGain,
   volumeAttenuation, chosenDepthSamples, displayScales), `useStoreActions`
   (storeBusy, storeNotice, loadListing), `useBarPlacement` (barOpen, the
   panel side, the theme). Each hook returns what the JSX reads and the
   setters it calls. Keep the comments that explain *why* a piece of state
   is held where it is; they are the valuable part.
3. Consider a `useReducer` for `layerState`, `groupState` and `groupOrder`,
   because the comments say the panel and the viewer "must never disagree
   about what is showing"; a reducer makes that one place.
4. In `LayerPanel.jsx`, move `Histogram`, `ColourChooser`, `VolumeMode` and
   `ChannelControls` to files of their own and the `styles` object to
   `layer-panel-styles.js`.

The engine-side JavaScript (`gui/source/drawing/neuroglancer.js`, 1,674
lines) can be split into `sources.js`, `camera.js` and `layout.js` the same
way, but it is the code that drives neuroglancer and the one most likely to
hide a subtle ordering dependency; do it only if steps 1–4 went smoothly.

**Done when:** the page builds, `gui/build` is rebuilt and committed, and
the whole suite is green with `ZMART_REQUIRE_BROWSER=1`, in particular
`tests/test_layer_panel.py`, `tests/test_interaction.py`,
`tests/test_the_screen_never_goes_black.py`,
`tests/test_no_setting_is_dropped_on_the_way_to_the_engine.py`,
`tests/test_frontend_live_refresh_contract.py` and
`tests/test_manifest_refresh_browser.py`.

## 7. Final verification: the viewer on its own, and inside the interface

Do all of this on the finished branch, and write the results into the report
(section 8).

**7a. The viewer's own suite, with a browser required**, from the viewer
checkout in `zmart-viewer-dev`:

```bat
set ZMART_REQUIRE_BROWSER=1
python -m pytest tests -q -p no:cacheprovider --timeout=600
python -m ruff check .
```

Compare the summary line with the baseline from section 5: same number
passed (or more), no new skips, no failures.

**7b. The viewer window by hand.** Run `zmart-viewer` on a folder of real
data on `D:` (or on `tests/demo_data.py`'s output). Open an image, move the
Z and T sliders, hide and recolour a channel, change contrast, switch to the
3-D view, draw a target and save it, close the image, and open another. It
should feel exactly as before.

**7c. The interface's tests against this viewer.** In the interface
checkout, in its own env (`zmart-interface-dev`, created as its README says
— `conda create -n zmart-interface-dev -c conda-forge python=3.12 pip git "nodejs>=22.12"`),
install this branch of the viewer in editable form **over** the one the
interface pulled from GitHub, so that the tests run against the refactor:

```bat
conda activate zmart-interface-dev
python -m pip install -e "..\ZMART-viewer"
python -c "import zmart_viewer, importlib.metadata as m; print(zmart_viewer.__file__, m.version('zmart-viewer'))"
```

The printed path must be the viewer checkout and the version `0.5.0rc1`.
Then, from the interface checkout:

```bat
python -m pytest
```

(this covers `zmart_interface/framework/test_operator_bridge.py` and
`zmart_interface/parts/storage/test_zarr_positions.py`, which import
`PublishedAcquisition` and `STORE`, and the storage service that starts
`make_server`). Then the page's tests and the page build, from a build copy
of the interface under `home\t.de\build\` as section 4 says, with `PYTHON=`
naming the interface env's `python.exe` so `neuroglancer-workers.mjs` finds
the viewer's `neuroglancer-growth.mjs`:

```bat
set PYTHON=C:\ProgramData\MinicondaZMB\envs\zmart-interface-dev\python.exe
node node_modules\vitest\vitest.mjs run
node neuroglancer-workers.mjs
node node_modules\vite\bin\vite.js build
node node_modules\@playwright\test\cli.js test zmart_interface\parts\canvas\flat-tiles.spec.js zmart_interface\parts\canvas\named-views.spec.js
node node_modules\@playwright\test\cli.js test zmart_interface\workflows\target_acquisition\walk.spec.js
```

`flat-tiles.spec.js` and `named-views.spec.js` start the viewer's
`make_server` through `parts/canvas/fixtures/serve-mixed-acquisition.py`,
call `/api/stores/open`, `/api/announce` and `/api/config`, and import
`/embedding.js`: they are the direct test of the contract in section 3.
`walk.spec.js` is the interface's main acceptance test (it needs the
ZMART-analysis environments; if they are not set up, say so in the report
rather than skipping silently).

**7d. The interface by hand.** Start `zmart-interface` with the mock
microscope, connect, run the overview scan, and watch the picture appear in
the middle of the operator window as fields land; switch a named view (Top,
Slice, Max); recolour a channel. Then close and reopen.

## 8. What to report back

At the end, write (in the pull request description, or in a short
`REFACTOR_REPORT.md` beside this file) for each step: done / partly / left,
why anything was left, the test summary lines (baseline and final, viewer
and interface), anything that behaved differently and how it was resolved,
and any real bugs noticed on the way but deliberately not fixed. Then
delete this file.

## 9. Prompt for the session on the lab PC

Paste this into a new Claude Code session opened in the ZMART-viewer
checkout on the lab PC:

```
Read REFACTOR_HANDOVER.md in this repository from top to bottom before doing
anything else, and follow it. It describes a pure refactor of the ZMART
Viewer: reorganising large files and duplicated helpers with no change in
behaviour, in six ordered steps, each its own commit with the test suite
green first. Section 3 lists the names and HTTP routes the ZMART interface
relies on; none of them may change. Section 4 describes this PC (AppLocker:
run things only from C:\ProgramData\MinicondaZMB, one conda env per project,
call node tools directly rather than npm run, build JavaScript from a copy
under home\t.de\build\, data and scratch on D:). Section 5 says to run the
whole suite with ZMART_REQUIRE_BROWSER=1 before touching anything and keep
the summary. Section 6 is the plan, step by step. Section 7 is the final
verification: the viewer's own suite with a browser, the viewer by hand, and
then the viewer installed editable into the ZMART-interface environment with
the interface's pytest, vitest, page build and the Playwright specs
flat-tiles, named-views and walk, plus the interface by hand on the mock
microscope. Section 8 says what to report. Work on the branch
claude/smart-viewer-refactor-z0857c, commit after each green step, and do
not push a red commit. Every docstring and comment you touch follows
CLAUDE.md: full, calm sentences for biologists. If a step turns out riskier
than described, do the safe part and write down why the rest was left.
```
