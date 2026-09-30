# Review: the ZMART Viewer release candidate

This file is a review brief for an AI code reviewer (Codex). It is not part of
the release; delete it once the review is done.

## Where to look

- Repository: https://github.com/thomdehoog/ZMART-viewer
- Branch: `release-candidate-zmart-viewer`
- Starting point: commit `6791d28` (version `0.5.0.dev0`), which is exactly
  what the ZMART operator window installs today.
- Review everything on the branch since then: `git diff 6791d28...HEAD`.

**This is a review, not a rewrite.** Read, run and judge, then report. Do not
push to the branch. Where a fix is small and certain, include it in the report
as a suggested patch.

## What the release is for

The repository holds two things, and the split between them is the point of
this release candidate:

1. **The viewing engine**, in `engine/`, installed as the Python package
   `zmart_viewer`. It reads OME-Zarr images, places each position where it
   was taken on the stage, follows a folder while a microscope is still
   writing into it, and serves the pieces of the picture over HTTP. It draws
   through neuroglancer with neuroglancer's own controls switched off
   (`engine/drawing/`). A separate smart-microscopy interface, which lives in
   its own repository, uses the engine and nothing else.
2. **The GUI**, in `gui/`: the viewer's own window, for people who only want
   to look at their data. It is JavaScript (React) plus `gui/window.py`,
   which opens the window with pywebview and is what the `zmart-viewer`
   command runs. The smart interface does not use it.

The engine's public names, which other software may rely on, are
`make_server`, `PublishedAcquisition`, `STORE` and `write_projection`,
importable from `zmart_viewer` itself (see `engine/__init__.py`), plus the
`/embedding.js` module the server serves.

**Installing needs only pip.** The built page is committed in `gui/built/`,
so `pip install git+https://github.com/thomdehoog/ZMART-viewer@<branch>`
gives a working viewer with no Node, npm or Vite. Node and npm are needed
only by a developer who changes the GUI's JavaScript: `npm install && npm run
build` rebuilds `gui/built/`, and the build check in `build_support.py` refuses
to package a page that no longer matches its sources.

## Who reads this code

ZMART is used mostly by microscopists and biologists who are learning, not by
professional software engineers. Judge README, docs, docstrings and
user-facing messages for that reader: complete, easy sentences; calm and
neutral; say why something matters, not only what the code does; no
unexplained jargon. The README should follow the shape of the ZMART
Controller's README on `release-candidate-zmart-controller` in
https://github.com/thomdehoog/ZMART-microscopy. Microscope makers are not to
be named in the docs.

## What changed on this branch

- **One command to start it:** `zmart-viewer`, or `zmart-viewer <folder>`
  (`gui/window.py`). The old demo scripts are gone.
- **Removed:** Replay (playing a finished dataset back as if live), the Viv,
  deck.gl and luma.gl dependencies (neuroglancer is the only drawing engine),
  the demo, benchmark and measurement scripts, install and test helper
  scripts, planning, handover and review notes, and dead code
  (`record/coarse.py`, test helpers nothing ran).
- **Restructured:** the old flat `zmart_viewer/` package became `engine/`
  with `serving/`, `opening/`, `picture/`, `views/`, `live/` and `drawing/`,
  and files were renamed to say what they hold (for example `named.py` →
  `slice_top_projection.py`, `coordinator.py` → `publisher.py`).
  `app/page/` became `gui/`; the build files moved to the repository root.
- **New docs:** `README.md`, `docs/using-the-viewer.md`,
  `docs/inside-your-own-interface.md`.
- **Version:** `0.5.0rc1` in `pyproject.toml`.

## What to review

1. **Correctness of the restructure.** Every import, path and build step must
   still point at the right place. Look for anything missed: stale module
   names in strings, docs or JavaScript imports; files that no longer ship in
   the wheel; tests that now skip because a path moved.
2. **The engine/GUI split.** Does anything in `engine/` exist only for the
   standalone window? Does anything in `gui/` belong in the engine, because
   a separate smart interface would need it? Note in particular that
   `engine/drawing/viewer.js`, `neuroglancer.js` and `layers.js` import
   neuroglancer by package name, so a separate interface can only use them
   through its own build. Propose how the engine should offer them
   ready-built, if you agree that it should.
3. **Install and packaging.** From a clean machine with no Node or npm:
   `pip install git+…` on this branch, then `zmart-viewer <folder>`,
   `zmart-viewer --no-window`, and `python -m zmart_viewer.gui --help`.
   Check that the wheel holds the engine, the built page and the GUI's Python
   only, with no JavaScript sources or `__pycache__`.
4. **Tests.** Run `python -m pytest tests` (install `.[dev]` first; browser
   tests need Playwright's Chromium). Report failures, skips and anything that
   tests only a removed feature. One failure is known and predates this
   branch: `test_view_release_contract.py::test_partial_publication_notifies_and_retries_without_blocking_config`
   expects a 503 while a publication runs, but commits `221074d` and `6791d28`
   deliberately made the server keep serving the previous generation. Say
   whether the test or the code should change.
5. **File and folder names.** Someone new should be able to find the right
   file from its name alone. Flag names that don't say what they hold, and
   folders that are split too finely or not enough. Keep `tests/` flat.
6. **Leftovers.** Anything else that should not ship in a release: old
   references to removed files, development-diary text in docs, dead code,
   experimental switches.
7. **Docs and messages.** The README against the controller's README;
   `docs/how_it_works/ARCHITECTURE.md`'s file map against the real tree;
   error messages a biologist would see.

## What to deliver

One Markdown report, in this order:

1. **Verdict:** ready to release after fixes, or not, and the three biggest
   issues.
2. **Must fix before release**, then **should fix**, then **nice to have**.
   For each item: the file (and line where it helps), what is wrong, and
   what to do, in a sentence or two, with a patch where the fix is small.
3. **The test run:** the command, the totals, and every failure with its
   cause.
4. **The clean install:** each step and what it printed.
