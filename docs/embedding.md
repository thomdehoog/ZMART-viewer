# The embedding API (version 1)

This page is for someone who puts the viewer's picture inside their own page,
as the ZMART operator window does. If you only want to look at your images,
you do not need it; the [guide](README.md) is enough.

The viewer's server offers a small JavaScript module at `/embedding.js`. Your
page imports it, and it helps with the parts of drawing a growing microscopy
picture that are easy to get wrong: choosing one view per acquisition, holding
the old picture while a new plane loads, and telling neuroglancer that a
picture has grown. Your page still creates and drives neuroglancer itself; the
module does not load neuroglancer and does not need it to be installed.

## What your page needs from the server

| Route | Why your page needs it |
|---|---|
| `GET /embedding.js` | This module. It may be imported from a page on another address. It is checked again on every load rather than kept, so an updated viewer is picked up at once. |
| `GET /api/config` | What is open. Its `layers` list is what `viewChoices` and `selectedViews` take. |
| `GET /api/events` | A stream that says "something changed"; fetch `/api/config` again when it does. |
| `GET /data/…` | The pixels and descriptions neuroglancer reads, at the addresses `/api/config` gives. |
| `POST /api/stores/open` | Tells the engine what to show. See the [guide, section 7](README.md#7-use-the-engine-in-your-own-interface). |
| `POST /api/announce` | Tells the engine that a position was written. |

## Check the version first

```js
import { EMBEDDING_API_VERSION } from "http://127.0.0.1:PORT/embedding.js";
if (EMBEDDING_API_VERSION !== 1) throw new Error("this page knows version 1 only");
```

The number changes only when something below changes in a way that would
break a page written for the old one.

## What the module offers

### Choosing a view

An acquisition can be offered in several views: **Slice**, **Top**, and the
projections **Min**, **Max** and **Sum** (see [the named views](view_modes.md)).
The view of each layer is read from what the engine declares about it, never
guessed from a file name.

| Name | Arguments | Gives back |
|---|---|---|
| `VIEW_LABELS` | (a constant) | The readable label of each view, keyed by `slice`, `top`, `min`, `max` and `sum`. |
| `viewKey(view)` | `view`: the `view` object of one layer in `/api/config` | The view's key: `"slice"`, `"top"`, or the projection's method (`"min"`, `"max"`, `"sum"`). |
| `viewChoices(layers)` | `layers`: the `layers` list of `/api/config` | One entry per acquisition: `{id, acquisition, label, keys}`, where `keys` lists the views it offers, in the order of `VIEW_LABELS`. |
| `selectedViews(layers, requested = {})` | `requested`: the view you would like per acquisition `id` | `{id: key}`. A view an acquisition does not offer falls back to the first one it does. The viewer's own window starts on Slice; your page may ask for Top. |
| `inSelectedView(spec, selected)` | `spec`: one layer; `selected`: what `selectedViews` gave | Whether that layer belongs on screen. Layers without a view are always shown. |

### Keeping Top and the projections at the right depth

`keepDepthLocal(layer, viewer = null, makeTransform = null)`

- `layer`: the neuroglancer layer of a Top or projection view.
- `viewer`: your neuroglancer viewer, for Top. Leave it out for a projection,
  which looks the same at every depth and so does not follow the Z slider.
- `makeTransform`: your page's own `WatchableCoordinateSpaceTransform`
  constructor. The module asks for it instead of importing neuroglancer.

Top keeps the depth range of the whole picture on the slider, but each
position is sampled only inside the planes it actually has, even when another
acquisition uses different units. Slice needs none of this. The layer is
expected to hold one source for the whole acquisition, not one per position.

### Holding the old picture while a new plane loads

`holdCompleteSlice(sliceView, changed = () => {})`

- `sliceView`: the neuroglancer slice view whose picture should be held.
- `changed(pending)`: called with `true` when a hold starts and `false` when it
  ends, so your page can label the old picture as "loading" rather than
  present it as the plane that was asked for.

It returns `{pending, request(), cancel(), dispose()}`. Call `request()` just
before you change Z, `cancel()` before you pan, zoom or change the layers, and
`dispose()` when the view closes. Resizing the view cancels a hold by itself.
The hold costs nothing extra: no requests, no copies of pixels, no timers. A
first picture that is not yet complete is never held.

### Telling neuroglancer that a picture grew

`refreshGeometry(source, chunkManager, refreshed, forgotten, {replaceHeld = true} = {})`

- `source`: the neuroglancer data source whose picture grew or moved.
- `chunkManager`: your viewer's chunk manager. What it remembers about that
  store's descriptions is forgotten, so they are read again.
- `refreshed` and `forgotten`: two `Set`s you create once per update and share
  between all the sources of that update, so that each description is read
  only once.
- `replaceHeld`: whether pieces already on screen are replaced as soon as the
  new ones arrive. Leave it `true` unless you have a reason.

Call it once per source when a source's *geometry* revision changes (its size
or position). When only the pixels change, the ordinary invalidation is
enough. A failed read is tried again a second later; an unchanged picture is
not read again. If the source closes in the meantime, the refresh is given up.
Keep the last revisions you saw when the user switches views: taking a layer
off screen does not throw away what it has already decoded.

`geometryRefreshPending(source)` says whether such a refresh is still under
way for that source.

## For developers who build the page

`refreshGeometry` needs `source.refreshMetadata`, which neuroglancer gains from
the growth patch in `neuroglancer-growth.mjs`. That file exports
`applyGrowthPatches(lib)`, `growthPatches(lib)` and `workerEntry(lib, name)`.

- Build both the page and neuroglancer's worker against the patch. Patching
  only the page leaves the worker with the old size of the picture.
- Apply the patches before compiling, and compile `workerEntry(lib, name)` into
  the worker's original bundle path on every build. That keeps the worker's
  original entry, so a rebuild really reads the changed sources.
- A patch whose target has changed stops the build with an error that asks for
  `npm ci`; it never passes quietly.

The wheel's build check covers `embedding.js` and `neuroglancer-growth.mjs`,
so an installed viewer always serves the `embedding.js` its page was built
with.
