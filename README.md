# ZMART Viewer

[![tests](https://github.com/thomdehoog/ZMART-viewer/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/thomdehoog/ZMART-viewer/actions/workflows/tests.yml)
[![python](https://img.shields.io/badge/python-3.11%E2%80%933.12-blue)](https://www.python.org/downloads/)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![status](https://img.shields.io/badge/status-release%20candidate-orange)](#status)

<table>
<tr>
<td width="170"><img src="docs/zmart-viewer-icon.png" width="150" alt="ZMART Viewer"></td>
<td valign="middle">

The **ZMART Viewer** shows large, three-dimensional, multi-channel microscopy images, and keeps showing them while the microscope is still writing. Point it at a folder of OME-Zarr images and it draws what is there; new positions and new time points appear on their own.

It is part of [**ZMART**](https://github.com/thomdehoog/ZMART-microscopy) (ZMB's Microscopy-Agnostic Research Toolkit), the tools we use for smart microscopy at the Center for Microscopy and Image Analysis (ZMB), University of Zurich.

</td>
</tr>
</table>

## The Problem

When you want to watch a smart-microscopy run, you are likely to run into
the following four problems.

1. **The images are too large to load.** A run easily holds more data than
   fits in memory, and most viewers want to read a whole image before they
   show it.

2. **The images arrive while the experiment is running.** Most viewers open
   one finished file at a time. During an experiment you want to see the
   specimen grow, position by position and time point by time point.

3. **A run is hundreds or thousands of positions.** They only make sense
   when each is placed where it was taken on the stage, and opening each as
   its own picture quickly becomes slow.

4. **The picture belongs inside the interface that drives the microscope.**
   You want the same view in the operator window as on your desk, without
   that window having to become an image viewer itself.

## The Solution

The ZMART Viewer addresses all four of them. It is two things in one
repository: a viewing engine (`engine/`, installed as the Python package
`zmart_viewer`) and a window (`gui/`) that uses it.

1. **Only what is on screen is read.** The engine serves only the pieces of
   the picture you are looking at, drawn through
   [neuroglancer](https://github.com/google/neuroglancer) with neuroglancer's
   own controls switched off, so even enormous data feels light.

2. **The engine follows the folder.** While a microscope writes into it, new
   positions appear on their own and a timelapse extends its own time
   slider. A workflow can also announce when an acquisition has finished,
   which is better than guessing.

3. **Every position is placed where it was taken.** The positions of one
   acquisition are drawn as one picture, and the engine offers named views
   of it: **Slice** (one plane at a time), **Top** (the surface seen from
   above) and **Min/Max/Sum** projections.

4. **The engine answers over HTTP.** An interface written in any language
   can start it and put the named views on its own page. The ZMART operator
   window in [ZMART Microscopy](https://github.com/thomdehoog/ZMART-microscopy)
   does exactly that. Anyone else uses the window that comes with this
   package: sliders through depth (Z) and time (T), a panel for each
   channel's colour and contrast, a load window and a 3-D view. It never
   talks to a microscope, so it can be used on anybody's data, on any
   machine, with no possibility of disturbing an experiment.

### What you can do

From a terminal, once the package is installed:

```bash
# Open an empty viewer and choose a folder from inside it
zmart-viewer

# Open one image, or a folder holding many positions of one acquisition
zmart-viewer /path/to/run

# Watch a run that is being written right now (the default), or say it is finished
zmart-viewer /path/to/run
zmart-viewer /path/to/run --static

# Print an address instead of opening a window, for a remote desktop or a browser
zmart-viewer /path/to/run --no-window
```

From your own software, to put the picture inside your own interface:

```python
from zmart_viewer import make_server

# 1) Start the engine on a port of the machine's choosing, watching a folder
server = make_server(port=0, data_dir="/path/to/run", live=True)

# 2) Tell it when an acquisition has finished writing (optional, but better than guessing)
#    POST http://127.0.0.1:<port>/api/announce     {"publications": [...]}

# 3) Put the named views on your own canvas with the embedding script it serves
#    <script type="module"> import { viewChoices } from "http://127.0.0.1:<port>/embedding.js" </script>
```

The engine answers over HTTP, so an interface can be written in any language.
The details are in [Inside your own interface](docs/inside-your-own-interface.md).

## Want to give it a try?

1. **[Open your first images](docs/using-the-viewer.md).** Install it, open
   a folder, and what is on screen, the load window and the options.
2. **[Use the engine inside your own interface](docs/inside-your-own-interface.md).**
   Start the engine, tell it what to show, and put the picture on your page.
3. **[The named views](docs/view_modes.md)** and **[the embedding API](docs/embedding.md)**
   that puts them on your canvas.
4. **[How it works](docs/how_it_works/ARCHITECTURE.md).** The design and the
   file layout on disk.

## Install it

There are two setups, one for using the viewer and one for changing it.

**Production**, to use the viewer. You need Python 3.11 or 3.12, and pip
installs everything else:

```bash
pip install "zmart-viewer @ git+https://github.com/thomdehoog/ZMART-viewer"
```

This installs the Python package and the page already built. It needs no
Node.js, no tests and no browsers, so it is what goes onto a microscope
computer.

**Development**, to change the viewer and run its tests. Clone the repository,
install it in editable mode with its test tools, install the page's
JavaScript packages, and install the browser the picture tests drive:

```bash
git clone https://github.com/thomdehoog/ZMART-viewer
cd ZMART-viewer
pip install -e ".[dev]"
cd gui/source && npm ci && cd ../..
python -m playwright install chromium
```

Node.js 22.12 or newer is needed for the page. In a conda environment, take it
from conda-forge (`conda install -c conda-forge nodejs`). Playwright puts its
browsers inside your user profile unless told otherwise; to keep them
somewhere else, set `PLAYWRIGHT_BROWSERS_PATH` to that folder before
installing them and keep it set when running the tests. After changing the
page, rebuild it with `npm run build` in `gui/source` and commit `gui/build/`
with your change.

## Status

This is version 0.5, a release candidate. At the ZMB it is the image engine inside
our smart-microscopy operator window, where it follows runs of thousands of
positions on several microscopes. The standalone window is younger than the
engine: it opens OME-Zarr version 2 and 3, HCS plates, and runs that are still
being written, but it does not yet open OME-TIFF or other file formats, and it
runs best on Windows, where the native window uses the WebView2 engine.

## Testing

From the development setup in *Install it*:

```bash
python -m pytest tests
```

[Testing the viewer](docs/how_it_works/TESTING.md) says what each group of
tests is for, what skips and why, and how to run them on a managed Windows
lab PC.

## Author

Thom de Hoog, Center for Microscopy and Image Analysis (ZMB), University of
Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).

## License

MIT License. See [LICENSE](LICENSE) for details.

## Links

- [ZMART Microscopy](https://github.com/thomdehoog/ZMART-microscopy): the main repository, with the workflows, the operator window and the drivers
- [ZMART Controller](https://github.com/thomdehoog/ZMART-controller): one vocabulary for driving any microscope
- [ZMART Drivers](https://github.com/thomdehoog/ZMART-drivers): the drivers that plug into the controller
- [ZMART Analysis](https://github.com/thomdehoog/ZMART-analysis): the analysis engine that runs between acquisitions
- [ZMART AI agent](https://github.com/thomdehoog/ZMART-ai-agent): drive any microscope by chatting
- [OME-Zarr](https://ngff.openmicroscopy.org/): the image format the viewer reads and writes
- [Center for Microscopy and Image Analysis (ZMB)](https://www.zmb.uzh.ch), University of Zurich
