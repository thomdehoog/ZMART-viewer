# ZMART Viewer guide

The ZMART Viewer shows large, multi-channel, three-dimensional microscopy images stored as OME-Zarr. It draws only what is on screen, so huge data feels light. It follows a folder while a microscope is still writing into it.

This guide has two parts. **Use the viewer** is for anyone with images. **Use the engine** is for anyone building their own interface around it. Each part has a hands-on tutorial as a Jupyter notebook.

| Part | Read | Try |
|---|---|---|
| Use the viewer | [sections 1 to 6](#1-install) | [Tutorial 1: open your first images](tutorials/01_open_your_first_images.ipynb) |
| Use the engine | [section 7](#7-use-the-engine-in-your-own-interface) | [Tutorial 2: the engine in your own interface](tutorials/02_the_engine_in_your_own_interface.ipynb) |

To run the tutorials:

```bash
pip install jupyterlab
jupyter lab docs/tutorials
```

---

## 1. Install

You need Python 3.11 or 3.12. Then:

```bash
pip install "zmart-viewer @ git+https://github.com/thomdehoog/ZMART-viewer"
```

This installs one command, `zmart-viewer`, and the Python package `zmart_viewer`. The page is already built. You need no Node.js, no browser install, no tests.

To change the viewer itself, use the development setup in the [main README](../README.md#install-it).

## 2. Open your images

```bash
zmart-viewer /path/to/your/data
```

The path can be:

| You give | You get |
|---|---|
| One `.ome.zarr` image | That image |
| A folder of positions from one acquisition | One picture, each position where the stage took it |
| An HCS plate | The plate, wells laid out in rows and columns |
| Nothing | An empty viewer with a **LOAD DATA** button |

The viewer finds out which you gave it. If it finds nothing, it says so and suggests the folder above or below.

**Live by default.** The viewer keeps looking at the folder. New positions appear within a second. A timelapse that grows extends its own time slider. When a run is finished, add `--static`. The viewer then stops looking, and moving through old data feels instant.

**Nothing is copied.** Positions are drawn as one picture, but your files stay exactly as the microscope wrote them.

**One folder, one acquisition.** The folder you open becomes one heading in the panel. If the folder holds two acquisitions, the viewer says so and lists both. A second kind of scan that appears during a run gets its own heading.

## 3. What is on screen

```
┌──────────────────────────────────────────────┬──────────────────┐
│ 2D 3D   Overview   light-mode     scale bar  │ LOAD DATA        │
│                                          ▶   │                  │
│                                          Z   │ DATA             │
│                                          │   │   acquisitions   │
│                 the image                │   │   and channels   │
│                                          │   │                  │
│                                          │   │ CHANNEL SETTINGS │
│                                        9/16  │                  │
├──────────────────────────────────────────────┤ SELECTION        │
│ ▶  T ───────────────────────────────── 1/3   │                  │
└──────────────────────────────────────────────┴──────────────────┘
```

| Element | What it does |
|---|---|
| **Z slider** | Upright on the right of the image. Moves through depth. Shown only if the image has more than one plane. |
| **T slider** | Along the bottom. Moves through time. Shown only if the image has more than one frame. |
| **▶** | Each slider has a play button that steps on its own. |
| **Scale bar** | Top right. Follows the zoom. |
| **2D / 3D** | Planes one at a time, or the stack as a volume. |
| **Overview** | Puts the view back as it opened: straight, centred, sized to the window. |
| **light-mode** | Light panels. The image stays on black. |
| **LOAD DATA** | Opens the load window (section 5). **choose folder** opens your system's own chooser directly. |
| **DATA** | Every open acquisition with its channels. Click a channel to select it. The eye hides it. The × closes an acquisition. |
| **CHANNEL SETTINGS** | The selected channel's histogram, **MIN** and **MAX** (black and white points), **OPACITY** and colour. **Auto** measures a window from what is on screen. |
| **SELECTION** | The **Point** and **Box** tools and the marks you made (section 6). Shown only with `--select`. |

| Gesture | Does |
|---|---|
| Drag | Pan |
| Wheel | Zoom, about the pointer |
| Shift + wheel | One plane up or down |
| Ctrl + click | Place a mark, when Point or Box is chosen |

The **‹** handle folds the bar of controls away. Put the bar on the left with `--panel-side left`.

## 4. Named views

An acquisition can be looked at in more than one way. The viewer offers the views it finds beside the data.

| View | Shows |
|---|---|
| **Slice** | One plane at a time. The Z slider moves through planes. The default. |
| **Top** | The surface seen from above, whatever depth each position was taken at. |
| **Min**, **Max**, **Sum** | A projection through each position's stack. |

Details are in [the named views](view_modes.md).

## 5. The load window

**LOAD DATA** opens a window that lists the folders where you are.

1. Walk the folders. One click selects a row. A double click steps into a folder. `..` goes up. **Choose folder…** opens your operating system's own chooser. You can also type or paste a path at the top.
2. Read the tag on the row. `zarr` is data: one image, a plate, or raw positions. `zmartview` is a saved view. ⚡ means the view keeps its precomputed low-resolution mosaic and opens fast.
3. Select a row and press **Open**.

An image, a plate or a view opens directly. Raw positions are linked into one picture for this session. Nothing is copied and nothing is written beside your data.

If you will open the same positions again, tick **build the low-resolution mosaic and keep the view**. The zoomed-out picture is computed once and kept as files, well under one percent of the data. Next time it opens instantly. A progress bar follows the build. **Stop** abandons it cleanly.

## 6. Options

```
zmart-viewer /path/to/data --static            # the run is finished: stop watching
zmart-viewer /path/to/data --select            # show the selection list
zmart-viewer /path/to/data --panel-side left   # controls on the left edge
zmart-viewer /path/to/data --range 100,4000    # fix the display window yourself
zmart-viewer /path/to/data --no-window         # print an address; open it in a browser
zmart-viewer /path/to/data --port 8849         # when 8848 is taken; 0 picks a free one
zmart-viewer --help                            # everything else
```

**Marking places.** Start with `--select`. Under **SELECTION** choose **Point** or **Box**, then Ctrl + click in the image. Give the mark a name. Marks are saved to `zmart-annotations.json` next to your images, a moment after you make them. There is no save button. The viewer never moves a microscope. Acting on a mark is up to the software that runs the experiment.

**Where the viewer opens.** On Windows it opens in its own window through WebView2, which comes with Edge. On macOS and Linux it does the same where a window library is available. Everywhere else, and with `--no-window`, it prints an address. Open that address in a browser.

### If something goes wrong

| You see | Why | Do |
|---|---|---|
| The viewer will not start | Port 8848 is in use, often by a viewer you left open | Close it, or add `--port 8849` |
| An address instead of a window | No window library on this machine | Open the address in Edge, Chrome or Firefox |
| The viewer lists two acquisitions | The folder mixes two kinds of scan | Point the viewer at the one you want |
| No image is found | The path is one level too high or too low | Follow the folder the viewer suggests |

---

## 7. Use the engine in your own interface

This part is for an operator window, a lab dashboard or a script that wants the picture of a run inside its own page. You use the engine, the Python package `zmart_viewer`, and bring your own page. The ZMART operator window in [ZMART Microscopy](https://github.com/thomdehoog/ZMART-microscopy) is built this way.

### What the engine is

A small HTTP server on the local machine. It reads OME-Zarr images, places each position where it belongs, follows the folder while a microscope writes, and serves the pieces of the picture a page asks for. The engine reads and serves. Your page draws. Because they talk over HTTP, the page can be written in any language.

### The stable surface

| What | Where | Used for |
|---|---|---|
| `make_server(...)` | `zmart_viewer` | Starting the engine on a port |
| `POST /api/stores/open` | HTTP | Telling the engine what to show |
| `POST /api/announce` | HTTP | Telling it a position was written |
| `GET /embedding.js` | HTTP | Putting the named views on your own canvas |
| `PublishedAcquisition`, `STORE` | `zmart_viewer` | Reading what the engine has published |
| `write_projection(...)` | `zmart_viewer` | Writing a Top, Min, Max or Sum view beside an image |

These names stay stable between versions. Everything deeper inside the package may move.

### The three steps

**1. Start the engine.**

```python
import threading
from zmart_viewer import make_server

server = make_server(port=0, data_dir="/path/to/run", live=True, allow_open=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
port = server.server_address[1]
```

`port=0` lets the machine pick a free port. `make_server` builds the server and leaves the thread to you.

**2. Tell it what to show, and when something changed.**

```json
POST /api/stores/open
{"path": "/path/to/run/positions",
 "bake": true,
 "canvas": {"x_um": [0, 10000], "y_um": [0, 5000]},
 "source_revisions": {"P000.ome.zarr": 1}}
```

`canvas` is the stage area the run will cover, in micrometres. The picture is laid out on it from the start, so the view never jumps. `bake` keeps a coarse overview as files beside the run, under `.zmart-viewer/`, so thousands of positions open fast next time.

After every completed write, send the full mapping of position to revision:

```json
POST /api/announce
{"publications": [{"path": "/path/to/run/positions",
                   "source_revisions": {"P000.ome.zarr": 2, "P001.ome.zarr": 1}}]}
```

A revision is any number that goes up when a position's pixels change. An unchanged mapping does no work. The engine also watches the folder on its own, but announcing is better: the watcher can only guess that a write has finished, and your software knows. Send announcements from a background thread, never from a capture callback.

**3. Put the picture on your page.**

```html
<script type="module">
  import { EMBEDDING_API_VERSION, viewChoices, selectedViews }
    from "http://127.0.0.1:PORT/embedding.js";
  if (EMBEDDING_API_VERSION !== 1) throw new Error("newer viewer than this page knows");
</script>
```

The picture is drawn by [neuroglancer](https://github.com/google/neuroglancer). The embedding script tells you which named views exist for each acquisition and picks one. The full description is in [the embedding API](embedding.md).

### What stays with you

The engine knows nothing about stages, targets, drivers or workflow steps. Your interface draws its own marks over the picture, turns a click into a stage coordinate, and decides which folder is the current run. That line is what lets one engine serve every microscope and still be a plain viewer for everyone else.

---

## Further reading

- [How it works](how_it_works/ARCHITECTURE.md): the design and the file layout on disk.
- [The named views](view_modes.md) and [the embedding API](embedding.md).
- [Testing](how_it_works/TESTING.md): what each group of tests is for.
