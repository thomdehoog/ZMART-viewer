# Plan: the simplified interface

> Written after the tczyx writer (mesoSPIM-control #2) and the Data viewer
> window (#3) were opened. Status: steps 1 and 2 BUILT (`ui="simple"`,
> `app/mesospim/src/panel.js`, `tests/test_mesospim_panel.py`); 3 and 4 open.
> Two things came out differently from the plan below: the panel is one of
> the engine's own side panels, because the engine draws a histogram only
> inside its canvas; and channels are composited by brightness rather than
> added, which removed the overlap seam.

The aim: switch the engine's own interface off and put ours on, the way the
smart viewer does it -- a control panel down the right-hand edge, the picture
filling the rest, one button between 2D and 3D. The engine stays stock; only
its chrome goes.

## What is already there

- `ui="bare"` on `Viewer` (and `?ui=bare` on the page) builds the engine
  without its top bar, layer panel and in-picture buttons, keeps the mouse and
  keyboard, and is tested (`test_a_transparent_ground_is_clear_outside...`).
- Everything the panel will show is already native layer state: one engine
  layer per channel with `invlerp` (window), `color` and `visible`, opacity
  and blend as layer fields, `layout` for 2D/3D. Nothing new has to be
  invented for the engine; the panel writes what the engine reads.
- The React panel of the ZMART viewer (`app/page/src/LayerPanel.jsx`,
  `AxisSlider.jsx`, `ScaleBar.jsx`, `theme.css`) is the look to keep. It is
  bound to that viewer's server config, not to engine layer state, so the
  components are the material, not the wiring.

## The shape

```
+---------------------------------------------+-----------------------+
|                                             | acquisition  [v]      |
|                                             |  2D | 3D              |
|                  picture                    |-----------------------|
|                                             | channels              |
|                                         [Z] |  o 488   ---o---  [c] |
|                                             |  o 561   ---o---  [c] |
|              [T ----------o----]            |-----------------------|
|  [50 um]                                    | tiles: 4  t: 2/2      |
+---------------------------------------------+-----------------------+
```

- **Panel on the right**, folding away. Rows are the acquisition's channels:
  an eye, the label, a colour dot, a window slider with the engine's own
  histogram behind it.
- **2D / 3D** is `layout: "xy"` / `"3d"`; the volume view keeps the engine's
  rotation and depth-range wheel.
- **Z and T sliders** as in the smart viewer: Z upright at the right edge of
  the picture, T along the bottom, each only when the axis has more than one
  step, reading and writing `viewer.navigationState.position`.
- **Scale bar** ours, bottom left; the engine's bars off.

## Where each piece lives

The page owns the panel; Python stays what it is. The panel edits engine
layer state in the page (`managed.layer.shaderControlState`, `opacity`,
`visible`, `viewer.layout`), which is exactly what the native panel edits
today, so the operator's adjustments survive Python's updates by the same
rule as now (`carryAdjustments` in `main.js`). Python is not in the loop
for a slider drag.

- `app/mesospim/src/panel/` -- the panel, as plain DOM or a small React
  tree (React is a build dependency, not a runtime cost worth avoiding;
  decide by whether the smart viewer's components are lifted or rewritten).
- `app/mesospim/src/main.js` -- mounts the panel when `ui === "bare"` and
  hands it the viewer; unchanged otherwise.
- **The histogram** is the engine's: `layer.histogramSpecifications` computes
  it on the GPU once something registers visibility (the native invlerp
  widget does; ours registers the same way), and `invlerp_range_finder.js`
  gives an Auto button for free. This is the one piece worth measuring
  before building the row, on a 16-bit stack.
- `Viewer` gains nothing but `ui="bare"`, which exists. `look_at`, `fit`,
  `on_pick` keep working.

## Order

1. Bare page + 2D/3D button + Z/T sliders + scale bar (no channel rows yet).
   Gate: a Chromium test that the sliders move the position and the button
   swaps the layout, and that `ui="full"` is untouched.
2. Channel rows: eye, colour, window from the engine's histogram, Auto.
   Gate: dragging a window changes a control value and never the shader
   text; a row hidden in the panel is hidden in the state; adjustments
   survive a tile landing (the existing growth test, run in bare mode).
3. The acquisition dropdown moves from the Qt window into the panel, so the
   browser and the Qt window show the same thing; the Qt window becomes a
   web view and nothing else.
4. Theme: dark by default, fluorescence on black; the light dress optional.

## Not planned

Annotations and target lists, stage moves from the panel, contrast measured
in Python, and any patch to the engine beyond the four transparency edits.
