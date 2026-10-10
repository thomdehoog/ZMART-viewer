# How the viewer is built

This is the map of the code: what the parts are, how they talk to each other,
and the few rules that decide where a new piece of code belongs. Read it
before changing anything that is not a one-line fix.

For how data is laid out on disk, read [DATA_LAYOUT.md](DATA_LAYOUT.md). For
how the viewer is driven by mouse and keyboard, and why, read
[CONTROLS.md](CONTROLS.md). For running the tests, read [TESTING.md](TESTING.md).

## 1. Two parts: the engine and the GUI

The repository holds two things, and the line between them is the most
important thing in this document.

- **The engine** (`engine/`, imported as `zmart_viewer`) is everything a
  smart-microscopy interface needs in order to show images. It reads OME-Zarr,
  places each position where it was taken on the stage, follows a folder while
  a microscope is still writing into it, and serves the pieces of the picture
  over HTTP. The two pieces of JavaScript other interfaces read from it live in
  `engine/drawing/`: `embedding.js`, which the server hands to any page that
  embeds the named views, and `neuroglancer-growth.mjs`, the neuroglancer
  patches an interface applies when it builds its own page. It has no buttons,
  panels or sliders of its own.
- **The GUI** (`gui/`, imported as `zmart_viewer.gui`) is the viewer's own
  window, for people who only want to look at their data: the panels, the
  sliders, the load window, and `window.py`, which opens the window with
  pywebview and is what the `zmart-viewer` command runs. The page's sources,
  including the JavaScript that drives neuroglancer (`gui/source/drawing/`),
  live in `gui/source/` with everything Node needs to build them; the built
  page lives in `gui/build/`.

A smart-microscopy interface lives in its own repository. It uses the engine
exactly as the GUI does and replaces the GUI with its own window. So the test
for where code belongs is simple: **if a separate interface would need it, it
belongs in the engine; if only this window needs it, it belongs in the GUI.**

The engine's public names are listed in `engine/__init__.py`. Everything else
inside it may move between versions.

## 2. Neuroglancer draws; we do the rest

Neuroglancer is the drawing engine, and its own interface is switched off:
`gui/source/drawing/viewer.js` creates it with `makeMinimalViewer` and
`showUIControls: false`, and `neuroglancer-chrome.css` hides the little that
remains. Everything a person sees around the picture is ours.

One trap comes with that, and it costs a day to rediscover: `makeMinimalViewer`
builds the engine but installs **no mouse or keyboard bindings**. Without them
the picture renders perfectly and nothing responds to the mouse. `viewer.js`
installs exactly the gestures this viewer documents; `CONTROLS.md` records
each decision, and `tests/test_interaction.py` holds them in place. Render
tests alone cannot catch this, because drawing and navigating are independent.

**The rule: leave to neuroglancer everything it can do, and do not work around
what it does badly.** Neuroglancer already handles data of this size well, and
it is the one component we chose not to rewrite; a wrapper that starts
compensating for it inherits the maintenance of both. The one exception is not
a loophole: inefficiency of our own making is ours to fix.

| | who does it |
|---|---|
| choosing the zoom level, fetching and decoding pieces | neuroglancer |
| the cache of decoded pieces | neuroglancer |
| slice and 3-D rendering, navigation | neuroglancer |
| which images exist and which are open | the engine |
| placing positions on the stage, and building pieces of the picture | the engine |
| serving the bytes, and deciding what a live run may show | the engine |
| the panel, sliders, colours, marks | the GUI (or your own interface) |

Two places where this is done well, to be copied rather than disturbed:

- **A channel is one layer with many sources.** Neuroglancer composites the
  positions; we never stitch pixels in the browser.
- **Contrast travels as control values, not as shader text.** `shaderFor` in
  `gui/source/drawing/layers.js` declares the contrast control once, and
  `shaderControlsFor` sends the numbers separately, so dragging a contrast
  handle does not recompile a program on the graphics card.

`layers.js` and `neuroglancer.js` are separate on purpose. `layers.js` is pure
translation: give it the window's settings and it hands back descriptions of
layers, with no browser involved, which makes it easy to test.
`neuroglancer.js` does the fiddly work of applying those descriptions to a
running viewer without rebuilding the scene.

### Where the rule is broken today: contrast is measured in Python

`engine/opening/contrast.py` reads pixels from disk to work out a starting
display window and a histogram. Neuroglancer can compute histograms on the
graphics card, and its own auto-range narrows the window until it settles,
which handles the dim 16-bit specimens that matter here. But the histogram is
only computed when neuroglancer's own contrast widget asks for it, and that
widget does not exist while its interface is switched off.

So moving contrast to neuroglancer means asking for the histogram ourselves
and reading it back at the right moment in a frame. It is worth doing: it
would delete `contrast.py` and its cache, and measure what is actually on
screen rather than what is on disk. It wants its own piece of work, with a
test that photographs a 16-bit specimen before and after.

## 3. One load, one acquisition

**Opening a folder produces exactly one acquisition, however many images it
spans.** An acquisition is one kind of scan, for example an overview or a set
of target scans: its images carry the same channels, because they were taken
the same way. It appears in the panel as one heading with one row per channel.

Which images belong together is read from **inside** them, never from their
names: the size of one voxel (which is the magnification the microscope really
used) and, where an image names its channels, those names. See
`_acquisition_of` and `_same_acquisition` in `engine/opening/open_folders.py`.
A folder can be renamed by anybody; a voxel size cannot.

If a folder holds more than one acquisition, `_one_acquisition_only` refuses to
open it and lists the images in each, so the answer is to open one of them.
That is the one place the viewer declines to show what it was pointed at, and it
is deliberate: such a folder was usually chosen one level too high.

A different acquisition that appears in a watched folder **during** a run, such
as a target scan landing beside the overview it came from, is not refused:
there is nobody left to tell. It is opened under a heading of its own instead.
`_look_again` and `_place` in `open_folders.py` set this out.

`engine/opening/open_a_path.py` is the one door every way in goes through: the
load window, the `zmart-viewer` command and an interface's `/api/stores/open`.
It works out what a path is (one image, a folder of images, an HCS plate, raw
positions from a microscope, or a run that is still being written) and opens it
the right way.

## 4. Three layers: what draws, what answers, what is on disk

```
        DRAWING                    SERVING                     ON DISK
   neuroglancer, driven       engine/serving/            OME-Zarr images, however
   by gui/source/drawing/     server.py                  the microscope wrote them

  +------------------+      +-------------------+      +-------------------+
  |  draws 2-D and   |      | answers questions |      | one image, many   |
  |  3-D, chooses    |      | about a picture   |      | positions, one per|
  |  the zoom level  |      | that need not     |      | well, or a run    |
  |                  |      | exist on disk in  |      | still being       |
  |                  |      | that shape        |      | written           |
  +------------------+      +-------------------+      +-------------------+
           |   "the piece at          |   "the parts of the      |
           |    z=3, y=7, x=2"        |    positions under it"   |
           |------------------------->|------------------------->|
           |<-------------------------|<-------------------------|
           |   one piece of picture   |   the bytes on disk      |
```

One request, end to end: the person drags the view, so neuroglancer works out
which pieces of the picture it is missing and asks for them by position. The
server works out which positions cover each piece, reads them at the zoom level
being drawn, lays them into one piece, and sends it back. Neuroglancer never
learns that positions were involved.

This is why **where a position sits on disk and where it belongs on the stage
are separate questions.** The layout on disk can suit the microscope and the
analysis; the picture on screen is still one image. `engine/picture/` does the
placing: `arrangement.py` works out where each position goes and builds pieces
of the picture from them, and `built_picture.py` writes down a built picture's
description (an OME-Zarr that holds no pixels of its own).
`engine/serving/picture_pieces.py` answers for a piece that is not an ordinary
file: either a byte range pointed at inside a position's own file, or bytes
built when asked for.

**The engine does not write your images.** Opening a folder never changes the
images in it. What the engine may write is its own, and kept apart: a built
picture's description, and, when an interface asks for a baked overview, the
coarse zoomed-out copies under `.zmart-viewer/` beside the data
(`engine/views/publishing.py`). A picture made only for one session, such as
raw positions opened from the load window, is described in a scratch folder
of the session, not beside the data.

### The named views

An acquisition can be looked at in more than one way. `engine/views/` provides
them: **Slice** (one plane at a time, at the specimen's own depth), **Top** (the
surface seen from above) and the **Min/Max/Sum** projections, written beside an
image by `projections.py`. `slice_top_projection.py` defines them;
`embedding.js` in `engine/drawing/` lets a window choose between them. See
[view_modes.md](../view_modes.md).

## 5. Following a run while it is written

Two parts of the engine handle a run that is still going, and they must not be
confused.

- **`engine/live/following.py` is the reader's side.** It pushes one kind of
  message to open windows, *something changed*, and each window re-reads the
  state in the ordinary way, so the disk stays the one source of truth. An
  interface that wrote the data can say so itself, with `/api/announce`, which
  is better than waiting for the folder to be noticed.
- **`engine/live/record/` is the writer's side.** It is how a smart-microscopy
  run writes itself: each position whole, its pyramid, and a manifest of
  signed commits that records what is **finished**. A microscope controller
  drives real hardware through this package.

The rule the record enforces is short: **data becomes visible only after the
complete position, or the complete moment, has been committed.** Files that
exist on disk mean nothing until then; a picture assembled from a half-written
position is not a slow picture, it is a wrong one that looks right.
`record/live_serving.py` applies that rule to every request for a live run's
pixels. `record/manifest.py` explains the record in full.

## 6. What stays exactly as it is

- **The server is Python's own `http.server`**, with no web framework, so it
  installs anywhere pip does.
- **The traversal guard.** Every request is resolved and refused unless it
  lands inside an open folder.
- **No line to the microscope.** There is no endpoint that moves a stage.
  Places a person marks are saved to a file beside the data, and whatever runs
  the experiment reads them from there. A test asserts that no stage-moving
  endpoint exists, so this cannot drift back in.

## 7. Where each file sits

For somebody who has just cloned the repository and wants to know which file
to open.

```
   WHAT YOU RUN       zmart-viewer   →   gui/window.py   (a window, or an address)

 ═══════════════════════════════════════════════════════════════════════════════
   THE GUI — the viewer's own window                        gui/
 ═══════════════════════════════════════════════════════════════════════════════

     window.py ───────────── opens the window with pywebview; the zmart-viewer command
     __main__.py ─────────── lets `python -m zmart_viewer.gui` do the same
     source/ ─────────────── the page's sources, and everything Node needs to build them
       App.jsx ───────────── the whole window's state
         ├── LoadWindow.jsx ─────── LOAD DATA: walking folders and opening one
         ├── NeuroglancerView.jsx ── gives the engine an element to draw into
         ├── LayerPanel.jsx ─────── acquisitions, channels, colour, contrast
         ├── AxisSlider.jsx ─────── depth up the side, time along the bottom
         ├── ScaleBar.jsx ───────── how large the specimen really is
         ├── TargetsPanel.jsx ───── places you mark, saved to a file
         └── the small pieces they are made of (ChannelControls.jsx, Histogram.jsx, …)
       drawing/ viewer.js ─── creates neuroglancer with its own interface off
                layers.js ─── settings → plain layer descriptions
                neuroglancer.js  applies them to the viewer without rebuilding
                live-refresh.js  what changed since the window last looked
     build/ ──────────────── the page, already built; what an installed viewer serves

     A smart-microscopy interface replaces this folder with its own window.

 ════════════════════════════════════════════▲══════════════════════════════════
                                  HTTP       │  pieces, descriptions, events
 ════════════════════════════════════════════▼══════════════════════════════════
   THE ENGINE — what answers and draws                      engine/
 ═══════════════════════════════════════════════════════════════════════════════

     filesystem.py ──────────────── writing files in one step, patient with a brief hold
     drawing/   embedding.js ─────── the named views, for windows that draw themselves
                neuroglancer-growth.mjs  lets an image grow while it is shown
     serving/   server.py ────────── answers every request; guards the opened folder
                picture_pieces.py ── pieces that are not plain files
                coverage.py ──────── which ground was acquired, for transparency
     opening/   open_a_path.py ───── the one door: what a path is, and how to open it
                open_folders.py ──── what is open, and how images are read
                contrast.py ──────── a sensible brightness to start with
     picture/   arrangement.py ───── where each position goes; building pieces
                built_picture.py ─── a built picture written down
                acquired_regions.py  the regions that were really imaged
     views/     slice_top_projection.py  the named views
                publishing.py ────── publishing an acquisition, with a baked overview
                projections.py ───── writing a projection beside an image
     live/      following.py ─────── telling open windows that something changed
                record/ ──────────── how a live run writes and publishes itself
                  publisher.py ───── writes positions and pyramids, one commit each
                  manifest.py ────── the record of what is finished
                  live_serving.py ── which bytes may answer for a published piece
                  live_state.py ──── the small state an open window follows
                  storage_plans.py ─ how one kind of acquisition is written
                  omezarr.py ─────── makes a written position readable by other software
                  shard_lookup.py ── finds one chunk inside a bundled file
                  view_routes.py ─── serves a piece of the linked view from a bundle
                  scene.py ───────── a run's images described as one scene
                  ownership.py ───── which tile's measurements count in an overlap
                  identity.py ────── names a run's descriptions can be trusted by
                  vocabulary.py ──── the words the rest of record/ speaks in

 ═══════════════════════════════════════════════════════════════════════════════
   THE BUILD — only for changing the GUI's JavaScript
 ═══════════════════════════════════════════════════════════════════════════════

     gui/source/package.json,
     gui/source/vite.config.js ────── `npm run build` in gui/source turns the page's
     gui/source/scripts/              sources and engine/drawing/ into gui/build/,
                                      and stamps what went in
     build_support.py ─────────────── refuses to package a gui/build/ that no longer
                                      matches its sources

     tests/browsercheck.py ── the safety net: serves the page, opens it in a real
                              browser, and reads the pixels that came out
```
