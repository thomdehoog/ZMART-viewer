# Use the engine inside your own interface

This guide is for someone building an interface around the viewer: a
smart-microscopy operator window, a lab dashboard, or a script that wants a
picture of a run while it is being written. You use the **engine**, the Python
package `zmart_viewer`, and bring your own page. The viewer's own window is
not involved. The ZMART operator window in
[ZMART Microscopy](https://github.com/thomdehoog/ZMART-microscopy) is built
this way, so everything below is in daily use.

## What the engine is

The engine is a small HTTP server on the local machine. It reads OME-Zarr
images from a folder, places each position where it belongs, keeps following
the folder while a microscope writes into it, and answers requests for the
pieces of the picture that a page wants to draw. Your page draws; the engine
reads and serves. The two talk over HTTP, so the page can be written in any
language and run in any browser or embedded web view.

The public surface, which the release keeps stable, is:

| What | Where | Used for |
|---|---|---|
| `make_server(...)` | `zmart_viewer.server` | starting the engine on a port |
| `POST /api/stores/open`, `POST /api/announce` | HTTP | telling the engine what to show and when it changed |
| `GET /embedding.js` | HTTP | placing the named views on your own canvas |
| `PublishedAcquisition`, `STORE` | `zmart_viewer.published` | reading what the engine has published for an acquisition |
| `write_projection(...)` | `zmart_viewer.projections` | writing a Top, Min, Max or Sum view beside an image |
| `open_window(...)`, `main(...)` | `zmart_viewer.launcher` | opening the viewer's own window from Python, when you want it |

Everything else in the package is internal and may change between versions.

## 1. Start the engine

```python
import threading
from zmart_viewer.server import make_server

server = make_server(
    port=0,                      # 0: let the machine pick a free port
    data_dir="/path/to/run",     # the folder to watch
    live=True,                   # keep following it while images arrive
    allow_open=True,             # let the page open folders through the engine
    transparent_background=True, # draw nothing where nothing was acquired yet
)
threading.Thread(target=server.serve_forever, daemon=True).start()
port = server.server_address[1]
```

`make_server` builds the server but does not run it, so you decide which thread
it runs on. With `data_dir` and nothing else it serves an empty picture and
waits to be told what to show. To open something at once, pass `store` (one
image name, or a list of them, inside `data_dir`).

For an acquisition whose positions cover a known area of the stage, pass
`canvas={"x_um": [0, 10000], "y_um": [0, 5000]}` (micrometres) and the engine
lays the picture out on that area from the start, so the view does not jump as
positions arrive. With `bake=True` it also keeps a coarse overview as files
beside the run, under `.zmart-viewer/`, so a survey of thousands of positions
opens quickly the next time.

## 2. Tell it what to show, and when something changed

Open a folder of positions:

```json
POST /api/stores/open
{"path": "/path/to/run/positions",
 "canvas": {"x_um": [0, 10000], "y_um": [0, 5000]},
 "source_revisions": {"P000.ome.zarr": 1}}
```

After every completed write, send the full current mapping of position to
revision:

```json
POST /api/announce
{"publications": [{"path": "/path/to/run/positions",
                   "source_revisions": {"P000.ome.zarr": 2, "P001.ome.zarr": 1}}]}
```

A revision is any number that goes up when a position's pixels change. An
unchanged mapping does no work. A change refreshes the affected pieces of the
picture and every open page re-reads them. You do not have to say *what*
changed inside a position; the engine reads that from the files.

Announcing is not compulsory: the engine also watches the folder and notices
changes on its own, which is what makes it work with a microscope that has
never heard of ZMART. But announcing is better, because the watching can only
ever guess that a write has finished, and your software knows.

Send the announcement from a background thread of the software that wrote the
data, not from inside a capture or status callback, so a slow page never holds
up the microscope.

## 3. Put the picture on your page

Your interface brings its own controls: sliders, channel settings, and
whatever else your experiment needs. The viewer's own window (`app/page`) is
not part of this; it is the interface for people who use the viewer on its
own. The picture itself is drawn by [neuroglancer](https://github.com/google/neuroglancer),
the one drawing engine the viewer supports.

To draw the picture on your own canvas, use the embedding script the engine
serves at `/embedding.js`. It is a plain ES module
with no dependencies of its own:

```html
<script type="module">
  import { EMBEDDING_API_VERSION, viewChoices, selectedViews, keepDepthLocal }
    from "http://127.0.0.1:PORT/embedding.js";
  if (EMBEDDING_API_VERSION !== 1) throw new Error("newer viewer than this page knows");
</script>
```

`viewChoices` and `selectedViews` tell you which named views (Slice, Top,
Min/Max/Sum) exist for each acquisition and pick one per acquisition from the
declared metadata, not from file names. `keepDepthLocal` keeps a Top view
clamped to its own depth while another acquisition changes the shared
coordinates. The full description, including how Z-slider transitions are
held so the old plane is never presented as the new one, is in
[the embedding API](embedding.md).

## 4. Read what was published, from Python

When your own code needs to know what the engine has made of an acquisition,
for instance to save a thumbnail or to check that a run is complete:

```python
from zmart_viewer.published import STORE, PublishedAcquisition

view = PublishedAcquisition(run_folder, piece=64)
view.sources     # which published pictures exist for this run
view.revision    # goes up whenever any of them changes
```

And to write a projection of one position beside it, for example a maximum
projection an analysis step will look at:

```python
from zmart_viewer.projections import write_projection

write_projection(source_store, target_folder / "P000-max.ome.zarr", "max", revision=2)
```

Unchanged inputs are no-ops, and the output records which source and method
made it, so it is never overwritten by something else.

## What stays with you

The engine is deliberately general. It knows nothing about stages, targets,
drivers or workflow steps. Those belong to your interface:

- drawing your own marks above the picture (targets, the current stage position,
  the field of view);
- turning a click on the picture into a stage coordinate and a target;
- deciding which folder is the current run, and when a run starts or ends.

Keeping that line clean is what lets the same engine serve a Leica, a Nikon, a
ZEISS and a mesoSPIM from one operator window, and still be a plain viewer for
anyone else.
