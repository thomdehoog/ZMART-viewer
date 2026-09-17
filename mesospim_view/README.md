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

And for a folder the microscope is writing into, the **Data viewer window**:

```
python -m mesospim_view.window /path/to/data        # or, from mesoSPIM-control: View > Open Data Viewer
python -m mesospim_view.demo --live                 # a pretend run, followed as it lands
```

A dropdown of the acquisitions in the folder, newest first, and the viewer
below. The newest acquisition is followed on its own: a tile or a time point
that lands is on screen within a second, and a new acquisition starting is
switched to, unless an older one was picked from the dropdown. That logic is
`Follower` in `watch.py` and is tested without Qt; `window.py` binds it to a
combo box and a timer.

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
- **A channel is an engine layer, added like light.** Each channel of an
  acquisition is its own image layer over the same sources, blended
  additively, with the window and colour as `#uicontrol` values rather than
  shader text -- the arrangement neuroglancer's own multichannel setup uses
  and the one the viewer's channel-mixing work settled on.
- **The transparent 2D ground**, as four small opt-in edits to the pinned
  engine (`app/mesospim/scripts/patch_neuroglancer.mjs`, the same edits the
  ZMART viewer 0.2.1 carries). Nothing else is patched.

Left out, on purpose: live refresh and growth patches, contrast measured in
Python (the engine's own histogram does it), the composed `.zmartview.zarr`
picture, baking, publication and revision bookkeeping, the React interface,
and every server route but three.

## The data contract

A store is accepted when it is OME-NGFF **0.4 on zarr v2** or **0.5 on zarr
v3**, with exactly the axes **`t, c, z, y, x`** in that order and `c` of type
`channel`. A store holding one channel is fine; so is one holding several.

How the arrays are chunked and sharded is the writer's choice, and the viewer
does not look. **One chunk (and one shard) per time point and channel** is the
layout it reads best: the engine fetches whole chunks for the plane it shows,
and a chunk spanning other time points or channels is bytes downloaded for
nothing. Shards are read through byte-range requests, which the server
answers; a shard must be written in one go by the writer for reasons of its
own, and that changes nothing here.

A store may grow along `t` while it is shown: a time-lapse appends time points
to the stores already on disk. Show the store again (`add()` with the same
path) or call `refresh()`, and the page reads its new extent without touching
the other stores or the operator's adjustments.

Placement reads the store's own `scale` and `translation` (per-dataset and
multiscale-level transformations composed the way the format says). The
`omero` block, when present, names and colours the channels and sets their
starting window; `add()` can override all three.

`read_store()` raises `NotAStore` with a plain reason for anything else.

## Why one engine layer per channel

The engine reads every channel of a voxel from a single chunk, so a *channel
dimension* -- the arrangement that lets one shader read all channels -- works
only when every chunk spans the whole `c` axis (measured: split it across
chunks and the source draws nothing, with "Channel dimension ... has extent N
but corresponding chunk dimension has extent 1"). Chunks per channel are the
right layout for writing and reading, so `c` stays a per-layer dimension and
each channel is a layer that pins it (`localPosition`). The engine adds the
layers together on the graphics card. The panel lists them as
`overview · 488`, `overview · 561`; the Python API still speaks of one layer.

## Known limits

- **Overlapping positions add along their overlap.** Additive blending is a
  property of a layer, so two tiles of one acquisition that overlap sum where
  they meet, and the strip reads brighter. Butting tiles (`origin=` places a
  tile exactly) or a stitched store avoid it; cropping a source is not
  something the engine offers.
- **The engine needs WebGL 2** and a canvas with a size: the page fills its
  window, so give the widget one.

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
- `watch.py` follows a folder: `Acquisitions` lists the `*.ome.zarr` groups
  in it newest first, `Watcher` polls one of them and adds a new tile or
  re-reads a grown one, `Follower` keeps a viewer on the newest.
- `state.py` turns placed stores into neuroglancer state: one engine layer per
  channel, over the same sources. A shifted source carries a `transform` whose
  translation column holds the shift, in voxels. Each layer's shader is the
  engine's own multichannel program with the store's window and colour as the
  controls' starting values; in 3D the brightness drives the opacity.
- `server.py` is a `ThreadingHTTPServer`: the built page, store bytes with
  byte ranges and ETags (sharded zarr v3 needs ranges), and the scene.
- `viewer.py` is the API. Every change publishes a new version; the page waits
  on `/api/state` for it, so a change is on screen within a frame, with no
  polling while nothing happens.
- `app/mesospim/src/main.js` is the whole page. It builds a stock viewer
  (`makeDefaultViewer` plus the default bindings), applies states, reports
  back. A layer whose revision moved has its stores forgotten from the
  engine's memo before it is rebuilt, so a grown store is read afresh.

Positions and picks are spoken in **micrometres** (seconds for `t`) by axis
name, whatever unit a store was written in.

## Embedding in mesoSPIM-control

mesoSPIM-control is PyQt5, its main window owns the core thread and opens its
child windows (`mesoSPIM_TileViewWindow` among them), and its `OmeZarrWriter`
plugin writes one OME-Zarr store per tile and channel. That fits the API
directly:

```python
# the Data viewer window, as mesoSPIM_MainWindow.open_data_viewer_window opens it
from mesospim_view.window import make_window_class
self.data_viewer_window = make_window_class()(acq_list[0]["folder"])
self.data_viewer_window.show()

# or the plain widget inside a window of your own
self.view = Viewer(transparent=False)
layout.addWidget(self.view.qt_widget(self))
self.view.add(store_path, layer=acq["filename"], window=(100, 4000))
self.view.on_pick(lambda point: self.core.sig_move_absolute.emit(point))
```

The stores it expects are the ones `MP_OME_Zarr_TCZYX_Writer` writes: one
`<Sample>.ome.zarr` group per acquisition holding one `(t, c, z, y, x)` store
per tile, channels along `c`, time points appended along `t`.

A separate store per channel shows as one engine layer each; a store with
several channels in one array shows as one engine layer per channel under one
name. Either way every position of an acquisition feeds the same layers, and
appended time points reach the screen through `add()` or `refresh()`.

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
cd app/mesospim && npm ci && npm run build     # once; the page lands in mesospim_view/page/
python -m mesospim_view.demo                    # four tiles in a browser
python -m pytest tests/test_mesospim_view.py tests/test_mesospim_watch.py
```

The page is built into the package, so `pip install .` (or `pip install
git+https://github.com/thomdehoog/ZMART-viewer`) ships it, and mesoSPIM-control
only needs the package installed to open the Data viewer.

The picture tests drive a headless Chromium and assert what is drawn: four
tiles as two channel layers over the same sources, the axes on screen, a camera
move from Python and a pick from the page, an operator's adjustments surviving a
new tile, a store that gains time points while shown, and a transparent ground
that is clear outside the tile and opaque inside it. They
skip, saying so, when the page is not built or no browser is found
(`ZMART_CHROMIUM` names one). The Qt window itself is only driven with
`MESOSPIM_VIEW_QT_TESTS=1` on a machine with OpenGL: QtWebEngine aborts the
process, rather than raising, where it cannot create a context.

## What comes next

`PLAN_simplified_interface.md` beside this file: the engine's chrome off and
our own control panel on the right with a 2D/3D button, built once the tczyx
writer and the Data viewer are in use.

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
what a multichannel acquisition needs. This package keeps positions as sources
of one layer with a native transform, and channels as native per-channel
layers added together.
