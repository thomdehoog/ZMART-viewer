# mesoSPIM view

A small neuroglancer view for the mesoSPIM control software: Python decides
which OME-Zarr stores are shown and where, a native neuroglancer page draws
them, and the camera comes back to Python. The package is standard-library
Python; the page is neuroglancer with a hundred lines of glue.

```python
from mesospim_view import Viewer

view = Viewer()                                     # serves on a free local port
view.add("run/Tile0.ome.zarr", layer="overview")    # placed by its own metadata
view.add("run/Tile1.ome.zarr", layer="overview", offset={"x": 1800.0})
view.fit()
widget = view.qt_widget()                           # a QWebEngineView, or:
view.open_in_browser()
```

Everything the operator sees is neuroglancer's own interface -- its layer
panel, its brightness histogram and colour dials, its keyboard and mouse. What
Python adds is only what neuroglancer cannot know: which stores belong
together, where each one sits, and how its channels should first look.

## What it keeps from the ZMART viewer, and what it leaves out

The ZMART viewer (`zmart_viewer/`, `app/page/`) grew to follow a running
acquisition of tens of thousands of positions: a replaced interface, paced
source hand-over, server-side composition of many stores into one, baked
pyramids, publication records and live refresh patches to the engine. None of
that is needed for a view that shows at most a few dozen finished stores, so
this package starts again from native neuroglancer and takes three things
across:

- **A layer is one acquisition, its positions are its sources.** The engine
  places each source by a transform and composites them; nothing is stitched.
- **Channels are controls, not shader text.** Every channel's window, colour
  and switch is a `#uicontrol`, so adjusting one hands a number to a program
  already compiled.
- **The transparent 2D ground**, as four small opt-in edits to the pinned
  engine (`app/mesospim/scripts/patch_neuroglancer.mjs`, the same edits the
  ZMART viewer 0.2.1 carries). Nothing else is patched.

Left out, on purpose: live refresh and growth patches, contrast measured in
Python (the engine's own histogram does it), the composed `.zmartview.zarr`
picture, baking, publication and revision bookkeeping, the React interface,
and every server route but three.

## The data contract

A store is accepted when it is OME-NGFF **0.4 on zarr v2** or **0.5 on zarr
v3**, with exactly the axes **`t, c, z, y, x`** in that order, `c` of type
`channel`, and **chunks that span the whole `c` axis** at every level.

The last rule is the engine's: a channel dimension must lie inside one chunk,
or the source draws nothing at all (`sliceview/frontend.js`, "Channel dimension
... has extent N but corresponding chunk dimension has extent 1"). A writer that
chunks per channel produces stores this viewer refuses with that reason.

Placement reads the store's own `scale` and `translation` (per-dataset and
multiscale-level transformations composed the way the format says). The
`omero` block, when present, names and colours the channels and sets their
starting window; `add()` can override all three.

`read_store()` raises `NotAStore` with a plain reason for anything else.

## How it works

```
Python                                    the page (app/mesospim)
------                                    -----------------------
Viewer.add / remove / set_layout  --->    GET /api/state?since=N   (long poll)
  builds neuroglancer layer JSON            brings layers into line, keeping the
  (state.py)                                operator's own adjustments on layers
                                            Python did not change
Viewer.look_at / fit              --->    camera, applied once the sources settled
Viewer.position, on_view          <---    POST /api/view   (camera, debounced)
Viewer.on_pick                    <---    POST /api/pick   (a double-click)
                                          GET  /data/<key>/...   (store bytes)
```

- `omezarr.py` reads the metadata above.
- `state.py` turns placed stores into neuroglancer state. A source's
  `transform` renames `c` to the channel dimension `c^` and carries any shift
  as the translation column of the matrix, in voxels. A layer's shader adds its
  channels together like light and, in 3D, lets the brightest one drive the
  opacity -- the same arithmetic as neuroglancer's own multichannel setup.
- `server.py` is a `ThreadingHTTPServer`: the built page, store bytes with
  byte ranges and ETags (sharded zarr v3 needs ranges), and the scene.
- `viewer.py` is the API. Every change publishes a new version; the page waits
  on `/api/state` for it, so a change is on screen within a frame, with no
  polling while nothing happens.
- `app/mesospim/src/main.js` is the whole page. It builds a stock viewer
  (`makeDefaultViewer` plus the default bindings), applies states, reports back.

Positions and picks are spoken in **micrometres** (seconds for `t`) by axis
name, whatever unit a store was written in.

## Embedding in mesoSPIM-control

mesoSPIM-control is PyQt5, its main window owns the core thread and opens its
child windows (`mesoSPIM_TileViewWindow` among them), and its `OmeZarrWriter`
plugin writes one OME-Zarr store per tile and channel. That fits the API
directly:

```python
# in the window that should show the acquisition
self.view = Viewer(transparent=False)
layout.addWidget(self.view.qt_widget(self))

# when the writer finalises a tile
self.view.add(store_path, layer=acq["filename"], window=(100, 4000))

# an operator double-clicks a point of interest
self.view.on_pick(lambda point: self.core.sig_move_absolute.emit(point))
```

A separate store per channel shows as a separate layer per store; stores
written with several channels in one array share one layer and one set of
controls, which is what this package is built for.

`transparent=True` clears the ground outside the acquired pixels, so a host
widget under the view shows through (`QWebEngineView` is given a clear page
background). The engine's in-picture axis lines are switched off in that mode:
they blend against destination alpha and would wipe the transparency.
`ui="bare"` drops neuroglancer's panels and in-picture buttons, for a host that
draws its own controls; the mouse and keyboard still work.

The engine needs WebGL 2. Qt WebEngine has it; on a machine that blocks the
GPU, set `QTWEBENGINE_CHROMIUM_FLAGS="--ignore-gpu-blocklist"` before Qt starts.

## Building and testing

```
cd app/mesospim && npm ci && npm run build     # once; the page lands in dist/
python -m mesospim_view.demo                    # four tiles in a browser
python -m pytest tests/test_mesospim_view.py    # reader, state, server, and the picture
```

The picture tests drive a headless Chromium and assert what is drawn: four
tiles as one two-channel layer, the axes on screen, a camera move from Python
and a pick from the page, an operator's adjustments surviving a new tile, and a
transparent ground that is clear outside the tile and opaque inside it. They
skip, saying so, when the page is not built or no browser is found
(`ZMART_CHROMIUM` names one).

## The three most recent branches, read for this design

`codex/view-modes-data`, `codex/operator-embedding` and
`codex/operator-publication-responsiveness` are one stack (about 13,000 lines,
8,000 of them tests) on top of `main`. What they add:

- **Named views** (`views.py`, `projections.py`): Slice, Top and min/max/sum
  aggregates written beside an acquisition, identified in store metadata.
- **Publication** (`published.py`, a larger `compose.py`): the server composes
  per-position stores into one virtual `.zmartview.zarr` with baked coarse
  levels, driven by revisions and coverage.
- **Operator embedding** (`embedding.js`): a JavaScript module for a host that
  already owns a neuroglancer viewer -- no process, URL or Qt contract.
- **Publication responsiveness** (`readable.py`): immutable on-disk generations
  with hard links, copy workers and reader leases.

Worth taking: view identity in store attributes rather than filenames; the
frontend packaged into the wheel; `embedding.js` as a tiny versioned module.
Not taken, and why: the publication and readable machinery exists for live
growth and for thousands of positions, which this view does not have; the
embedding module patches `sliceView.updateRendering` and mirrors global Z into
a hidden local transform, which fights the engine's coordinate model; the
aggregate path collapses a store to a single logical channel, the opposite of
what a multichannel layer needs. This package keeps positions as sources of one
layer with a native transform, and channels as a native channel dimension.
