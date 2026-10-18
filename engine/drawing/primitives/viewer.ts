/**
 * The engine itself: made here with its own interface switched off, told here
 * how the view is set, and dropped here with everything it held.
 *
 * Two pages draw with neuroglancer and both turn its interface off: the
 * viewer's own window and the operator page of ZMART-interface. Each owns its
 * controls and tells the engine the values; this is the engine's side of that,
 * once. What it draws is kept in step by `sync.ts`, which the handle carries.
 */

// neuroglancer is built from many optional pieces. The bare `makeMinimalViewer`
// wires up the display but registers none of the ways to *read* data. These
// four imports switch on the pieces we need: the layer types (image, etc.), the
// data-format readers (which include OME-Zarr), and the "key-value stores" that
// know how to fetch bytes over plain HTTP. Without them the viewer loads but
// every data source fails with "unsupported scheme". They must come before the
// viewer is created.
import "neuroglancer/unstable/util/polyfills.js";
import "neuroglancer/unstable/layer/enabled_frontend_modules.js";
import "neuroglancer/unstable/datasource/enabled_frontend_modules.js";
import "neuroglancer/unstable/kvstore/enabled_frontend_modules.js";
import { makeMinimalViewer } from "neuroglancer/unstable/ui/minimal_viewer.js";
import type { RenderedPanel } from "neuroglancer/unstable/display_context.js";
import type { Viewer, ViewerUIConfiguration } from "neuroglancer/unstable/viewer.js";
import type { TrackableBoolean } from "neuroglancer/unstable/trackable_boolean.js";
// Mouse and keyboard navigation (pan, zoom, scroll through z, rotate the 3-D
// view) is NOT part of building a viewer. neuroglancer's panels receive the DOM
// events either way, but without these binding tables no *action* is attached to
// them, so every drag and wheel silently does nothing. Its own entry points
// (default_viewer_setup.js, main_python.js) install them immediately after
// creating the viewer; makeMinimalViewer does not, so we must.
//
// The two tables brought in here are the ones attached to the image panels
// themselves. The engine has a third — its *global* table — which we
// deliberately leave out; see the note beside the call below for why.
import {
  EventActionMap,
  registerActionListener,
} from "neuroglancer/unstable/util/event_action_map.js";
import "neuroglancer/unstable/ui/default_viewer.css";
// Loaded after the engine's own stylesheet so it wins: this hides the handful of
// controls the engine draws inside the image itself. See the file for why.
import "./chrome.css";
import { makeFeeding } from "./feeding.ts";
import { makeLayerSync } from "./sync.ts";
import type { LayerSync } from "./sync.ts";

/**
 * Who answers the operator's hands.
 *
 * `"engine"`: the engine's own panels, with exactly the gestures written out
 * below -- drag pans the flat view and turns the volume, the wheel zooms, and
 * the drawing family neuroglancer's annotation tools ride on. `"none"`: every
 * table emptied, for a page that lays its own surface over the engine and
 * moves the view itself.
 */
export type Gestures = "engine" | "none";

/** How a page makes its engine. */
export interface EngineOptions {
  gestures?: Gestures;
  /**
   * The colour of the ground -- the canvas where no data is drawn -- or a way
   * to read it, for a page whose ground follows its theme.
   */
  ground?: string | (() => string);
}

/**
 * The parts of the view that are not layers: which panels, whether the engine
 * draws its own furniture, and whether the flat view's empty ground is left
 * see-through for whatever the page draws beneath it.
 */
export interface ViewSettings {
  layout: string;
  chrome: boolean;
  transparentBackground?: boolean;
}

/** One engine, as a page holds it. Everything it keeps is dropped by `dispose`. */
export interface Engine extends LayerSync {
  viewer: Viewer;
  /** How many stores the page has still to hand the engine; see `feeding.ts`. */
  sourcesStillWaiting(): number;
  /** Set the parts of the view that are not layers. */
  setView(settings: ViewSettings): void;
  /** Paint the ground again, for a page whose ground has just changed. */
  paintTheGround(): void;
  dispose(): void;
}

/**
 * What the transparency edit (`engine/drawing/neuroglancer-patches.mjs`) adds to
 * the viewer's settings. Not in neuroglancer's declarations, since the edit is
 * made to the compiled package.
 */
interface TransparentUIConfiguration {
  transparentBackground: TrackableBoolean;
}

/**
 * What the flat and volume panels add to a drawn panel: zooming about the
 * cursor. Not every panel the engine draws has it, so each is asked.
 */
interface ZoomingPanel {
  zoomByMouse?: (factor: number) => void;
  onMousemove?: (event: MouseEvent, atOnly?: boolean) => void;
}

/**
 * Create the neuroglancer viewer inside ``target`` (an empty element), with
 * every one of neuroglancer's own buttons and panels switched off.
 *
 * It owns the viewer's *lifetime* (create it here, drop it through the returned
 * ``dispose``) but NOT what the viewer *shows*. Which layers, which layout, the
 * brightness, the z-position: all of that comes from the page, through the
 * handle's `syncLayers` and `setView` and through the camera and axes
 * functions beside this file. That split is what lets any page build its
 * controls without touching this function.
 */
export function createViewer(target: HTMLElement, { gestures = "engine", ground = "#000000" }: EngineOptions = {}): Engine {
  // Create the viewer with all of neuroglancer's own buttons and panels
  // turned off — the page supplies its own controls, so the engine shows
  // nothing but the image. `showLayerDialog`/`resetStateWhenEmpty` are off so
  // the engine does not pop its own "add a layer" dialog or wipe state before
  // the page loads a volume.
  const viewer = makeMinimalViewer({
    target,
    showUIControls: false,
    showTopBar: false,
    showLayerPanel: false,
    showLocation: false,
    showPanelBorders: false,
    showLayerDialog: false,
    resetStateWhenEmpty: false,
  });

  const readTheGround = typeof ground === "function" ? ground : () => ground;
  // The engine paints its own ground behind both views, and it must match the
  // page's, or switching to a light dress would leave the picture floating on
  // a black field inside a light page. Written through `restoreState`, which
  // parses the colour and only writes when it differs.
  const paintTheGround = () => {
    const colour = readTheGround();
    viewer.crossSectionBackgroundColor.restoreState(colour);
    viewer.perspectiveViewBackgroundColor.restoreState(colour);
  };
  paintTheGround();

  if (gestures === "engine") giveTheEngineItsGestures(viewer, target);
  else emptyTheEnginesOwnBindings(viewer);

  const feeding = makeFeeding();
  const sync = makeLayerSync(viewer, feeding);

  return {
    viewer,
    ...sync,
    sourcesStillWaiting: () =>
      feeding.sourcesStillWaiting(viewer.layerManager.managedLayers.map((managed) => managed.layer)),
    setView(settings) {
      setTheView(viewer, settings);
      paintTheGround();
    },
    paintTheGround,
    dispose() {
      feeding.dispose();
      viewer.dispose();
    },
  };
}

/**
 * Give the panels explicit tables holding exactly the gestures documented
 * here, instead of inheriting the engine's defaults. What an operator can do
 * to the picture is written out here, once, in full -- the viewer's
 * docs/how_it_works/CONTROLS.md records each decision.
 */
function giveTheEngineItsGestures(viewer: Viewer, target: HTMLElement): void {
  // The engine's *global* keyboard table is not installed either, and never
  // was. It binds single unmodified letters and digits across the whole
  // page, to actions belonging to an interface this viewer hides — the
  // space bar split the image into four panels with no way back, the digits
  // hid channels while the panel's eye still showed them open, and several
  // letters restored scale bars and axis lines this viewer draws for
  // itself. The engine's element holds the keyboard focus from the moment
  // the page loads, so a stray keystroke was enough.
  //
  // The engine's *panel* tables used to be installed whole, and they were
  // the second hole (closed 2026-08-18): they bound their own keyboard —
  // arrows panned, comma and full stop stepped z, the brackets stepped time
  // straight past the T slider's clamp to the written moments, `r`, `e` and
  // Shift with the arrows rotated the flat view so the picture silently
  // stopped lining up with the stage coordinates drawn over it, and a right
  // click jumped the view to the point clicked. None of it is shown
  // anywhere on screen. Moving through the stack and through time belongs
  // to the sliders, where it is visible, labelled and clamped; moving
  // around belongs to the two gestures. So: no keyboard at all, and the
  // flat view cannot rotate. The viewer's test file
  // `test_the_keyboard_cannot_trap_the_operator.py` holds each of these
  // removals in place.
  //
  // What stays, besides movement, is the drawing family the viewer's Point
  // and Box buttons rely on: Ctrl+click places (the engine's "annotate"
  // action triggers whichever placement tool those buttons installed), Enter
  // finishes a shape and Backspace undoes a step of one. Without an active
  // tool all three do nothing.
  const bindings = viewer.inputEventBindings;
  bindings.sliceView.addParent(
    EventActionMap.fromObject({
      "at:mousedown0": { action: "translate-via-mouse-drag", stopPropagation: true },
      "at:touchtranslate2": "translate-in-plane-via-touchtranslate",
      "at:touchpinch": "zoom-via-touchpinch",
      "at:control+mousedown0": "annotate",
      enter: "finish-annotation",
      backspace: "undo-annotation-step",
    }),
    Number.NEGATIVE_INFINITY,
  );
  // The volume view keeps rotation — turning the specimen over is the point
  // of a three-dimensional view, and nothing is drawn over it in stage
  // coordinates — and it keeps the slab-thickness wheel, which has no
  // control of its own yet.
  bindings.perspectiveView.addParent(
    EventActionMap.fromObject({
      "at:mousedown0": { action: "rotate-via-mouse-drag", stopPropagation: true },
      "at:shift+mousedown0": { action: "translate-via-mouse-drag", stopPropagation: true },
      "at:touchtranslate1": "rotate-out-of-plane-via-touchtranslate",
      "at:touchtranslate2": "translate-in-plane-via-touchtranslate",
      "at:touchrotate": "rotate-in-plane-via-touchrotate",
      "at:touchpinch": "zoom-via-touchpinch",
      "at:alt+wheel": { action: "adjust-depth-range-via-wheel", preventDefault: true },
      "at:control+mousedown0": "annotate",
      enter: "finish-annotation",
      backspace: "undo-annotation-step",
    }),
    Number.NEGATIVE_INFINITY,
  );

  // The plain wheel zooms, and stepping through z moves to shift+wheel.
  //
  // Neuroglancer gives the plain wheel to z and puts zoom behind Control,
  // which is right for reading through a volume and wrong for an operator
  // looking at a plate. The wheel is the gesture made most often and without
  // thinking, and a browser has taught everybody what it does; zoom has
  // nowhere else to live, while the stack keeps its slider -- better than a
  // gesture anyway, because it shows where in the stack you are.
  //
  // These entries win over the parent tables added above, which is why those
  // are added at the lowest possible precedence. `preventDefault` is passed
  // explicitly: handing `set` a bare action name leaves it unset, and without
  // it the browser keeps its native wheel behaviour and acts on the same
  // notch. Both panels, and the second one is not an afterthought: rebinding
  // the flat view alone left the volume view on the engine's defaults, where
  // an operator taught that the wheel zooms found it doing nothing.
  //
  // A gentler notch than the engine's. `getWheelZoomAmount` returns
  // `exp(deltaY / 200)`, and a wheel notch on Windows reports a deltaY of 100,
  // so one notch magnifies by about 1.65 -- roughly seven-tenths of a pyramid
  // level, with no stop between too far out and too far in. A third of the
  // exponent gives about 1.18 a notch, four notches to a level. Zooming about
  // the *cursor* is kept: this hands the work back to the very method the
  // engine's own action calls.
  const GENTLER = 3;
  const howFarToZoom = (event: WheelEvent) => {
    const perUnit: Record<number, number> = { 0: 1 / 200, 1: 1 / 10, 2: 2 };
    const perPixel = perUnit[event.deltaMode] ?? 1 / 200;
    return Math.exp((event.deltaY * perPixel) / GENTLER);
  };
  registerActionListener<WheelEvent>(target, "zmart-zoom-via-wheel", (fired) => {
    const wheel = fired.detail;
    for (const drawn of viewer.display.panels) {
      const panel = drawn as RenderedPanel & ZoomingPanel;
      if (!panel.element?.contains(wheel.target as Node | null) || !panel.zoomByMouse) continue;
      panel.onMousemove?.(wheel, false);
      panel.zoomByMouse(howFarToZoom(wheel));
      return;
    }
  });

  for (const panel of [bindings.sliceView, bindings.perspectiveView]) {
    panel.set("at:wheel", { action: "zmart-zoom-via-wheel", preventDefault: true });
    panel.set("at:shift+wheel", {
      action: "z+1-via-wheel",
      preventDefault: true,
    });
  }
}

/**
 * Empty every table of the engine's own bindings, and the parents beneath
 * them, where the engine's defaults actually live: a page that lays its own
 * surface over the engine catches every gesture itself, and an engine that
 * also answered would move the view twice.
 */
function emptyTheEnginesOwnBindings(viewer: Viewer): void {
  for (const table of Object.values(viewer.inputEventBindings)) {
    table.clear();
    for (const parent of table.parents) parent.clear();
  }
}

/**
 * Set the parts of the view that are not layers: which panels are on screen,
 * whether the engine draws its own furniture, and whether the flat view's
 * empty ground is left see-through.
 *
 * Rebuilding the panel layout means new drawing surfaces, so the layout is
 * written only when it actually differs -- never as a side effect of some
 * unrelated change.
 */
function setTheView(viewer: Viewer, { layout, chrome, transparentBackground = false }: ViewSettings): void {
  const ui = viewer.uiConfiguration as ViewerUIConfiguration & TransparentUIConfiguration;
  ui.transparentBackground.value = transparentBackground && layout === "xy";
  if (viewer.layout.toJSON() !== layout) viewer.layout.restoreState(layout);
  viewer.showDefaultAnnotations.value = chrome;
  viewer.showAxisLines.value = chrome;
  // Neuroglancer's own scale bars are off. It draws one per axis along the
  // bottom left, including one for time -- which looks like a distance and is
  // not one. A page that wants a scale bar draws its own.
  viewer.showScaleBar.value = false;
}
