# Install the viewer and open your first images

This guide is for anyone who has microscopy images in OME-Zarr and wants to
look at them. No microscope is needed, and nothing here can touch one.

## Install

You need Python 3.10, 3.11 or 3.12. Then install the package from a release
wheel, which already contains the viewer page, so no other tools are needed:

```bash
pip install zmart_viewer-0.5.0rc1-py3-none-any.whl
```

The wheel is on the repository's release page. If you would rather install
from the source checkout, the page has to be built once first, which needs
[Node.js](https://nodejs.org):

```bash
npm --prefix app/page install
npm --prefix app/page run build
pip install .
```

Either way you end up with one command, `zmart-viewer`.

On Windows, the viewer opens in its own window through the WebView2 engine that
comes with Microsoft Edge. If that engine is missing, the viewer says so and
prints an address you can open in Edge or Chrome instead. On macOS and Linux the
same window library (`pywebview`) is used where it is available; where it is
not, the address is printed in the same way.

## Open your images

```bash
zmart-viewer /path/to/your/run
```

That path may be a single `.ome.zarr` image or a folder holding many of them.
Both work, so you do not have to know which you have. If nothing is found, the
viewer says so and suggests the folder above or below.

Typing `zmart-viewer` with no folder opens an empty viewer with a **load
data** button, where you choose a folder by hand.

A few things worth knowing:

- **A folder being written to is fine.** Positions that appear while you are
  watching are picked up on their own, usually within a second, and a timelapse
  growing in time extends its own slider as frames arrive. This is the default.
  When a run has finished, add `--static`: the viewer then stops looking for
  new images, which is the largest thing it does on a big folder, and moving
  around old data feels instant.
- **Many positions are shown as one picture, without copying any of them.** The
  positions of a tiled overview are pieces of one specimen, so they are drawn
  as one image, each where the microscope recorded it. Nothing is copied; the
  files stay exactly as the microscope wrote them.
- **One folder, one acquisition.** What you open becomes a single heading in the
  panel, named after the folder, and every image in it feeds that heading. Which
  images belong together is read from the images themselves, not from their
  names: an overview and a close-up target scan were taken at different
  magnifications, and that is recorded inside each image. If the folder you pick
  holds two acquisitions, the viewer says so and lists both, so you can point it
  at the one you wanted.
- **A second kind of scan appearing during a run gets its own heading**, with its
  own controls and its own close button, rather than being merged into the
  overview.
- **HCS plates** open as plates: wells are laid out from the plate's own rows
  and columns, with a small gap between them, and the fields inside a well keep
  the places the microscope recorded for them.

## What is on screen

The image fills the window. Two sliders move you through it, each placed to
match the direction it moves through: **depth (Z) stands upright along the
right-hand edge**, the way a stack of planes is pictured, and **time (T) lies
along the bottom**, the way a recording is. Each appears only if the image
really has that axis with more than one step, so a still picture gets no time
slider and a single plane no depth slider. Each has a play button that steps
through on its own. A scale bar sits in the top-right corner and follows the
zoom.

Everything else is one bar of controls down one edge, which folds away when you
want the whole screen for the specimen:

- **load data** opens the load window, described below.
- **display settings** holds the histogram, the black and white points, the
  opacity and the colour for whichever channel is picked out below. You adjust
  one channel at a time.
- **image data** lists every acquisition open, with its channels under it. Click
  a channel to adjust it, use the eye to hide it, and the × to close an
  acquisition you are done with.
- **selection** lists the places you have marked. It is off unless you start the
  viewer with `--select`.

### Named views

An acquisition can be looked at in more than one way, and the viewer names them:

- **Slice** shows one plane at a time, and the Z slider moves through the planes.
  This is the default.
- **Top** shows the surface of the specimen seen from above, whatever depth each
  position was taken at.
- **Min**, **Max** and **Sum** show a projection through the stack of each
  position.

Which of these exist depends on what was written beside the acquisition; the
viewer offers the ones it finds. See [what the named views are](view_modes.md).

## The load window

The **load data** button opens a window listing the folders where you are.
One click selects a row, a double click steps into a folder, **..** goes up
one folder, and **Choose folder…** opens your operating system's own chooser
where one is available. You can also type or paste a path into the box at the
top.

Each row wears a small tag that says what it is: a built view, one image, a
plate, or raw positions from the microscope. Choose one and press **Open**.

- **A view, an image or a plate** opens directly.
- **Raw positions** are linked into one picture on the spot, so nothing is
  copied and nothing is written beside your data. Tick *build the
  low-resolution mosaic and keep the view* if you will open this data again:
  the zoomed-out picture is then computed once and kept as files (well under
  one percent of the data), and the view opens instantly next time. A
  progress bar follows the build, and **Stop** abandons it without leaving
  anything half-made behind.

## Options worth knowing

```
zmart-viewer /path/to/run --panel-side left    # controls on the left edge
zmart-viewer /path/to/run --select             # show the selection list
zmart-viewer /path/to/run --range 100,4000     # set the display window yourself
zmart-viewer /path/to/run --no-window          # print an address; open it in a browser
zmart-viewer /path/to/run --port 8849          # when 8848 is taken
zmart-viewer --help                            # everything else
```

**If the viewer will not start, it is usually the port.** It answers on 8848
and cannot start if something else on the machine already uses that number,
most often a copy of the viewer you left open. It says so and suggests what to
do. Any free number between 1024 and 65535 will do, and `--port 0` lets the
machine pick one and prints which it chose.

## Marking places

Start the viewer with `--select`, then draw a point or a box around something
interesting and give it a name. The marks are saved to `zmart-annotations.json`
in the same folder as the images, a moment after you make them; there is no save
button to remember. The viewer never moves a microscope. Acting on a mark
belongs to whatever software runs the experiment, which can read that file.
