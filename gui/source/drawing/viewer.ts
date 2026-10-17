/**
 * The engine's viewer: built here with exactly the gestures this page documents,
 * and told here how the view is set -- which panels, whether the engine draws its
 * own furniture, the theme's ground, and any stretch along an axis. What it draws
 * is kept in step by `layer-sync.ts`; how it is framed, by `framing.ts`.
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
import "./neuroglancer-chrome.css";

/**
 * Create the neuroglancer viewer inside ``target`` (an empty element), with
 * every one of neuroglancer's own buttons and panels switched off, and with
 * exactly the gestures this engine documents. That is all it does.
 *
 * It owns the viewer's *lifetime* (create it here, dispose it through the
 * returned ``dispose``) but NOT what the viewer *shows*. Which layers, which
 * layout, the brightness, the z-position: all of that is driven by whoever
 * holds the ``viewer`` object, through the functions in ``layer-sync.ts``,
 * ``framing.ts`` and the view settings below.
 * That split is what lets any interface, the viewer's own window or a
 * smart-microscopy page, build its controls without touching this function.
 *
 * Returns ``{ viewer, dispose }``.
 */
export function createViewer(target: HTMLElement): { viewer: Viewer; dispose(): void } {
    // Create the viewer with all of neuroglancer's own buttons and panels
    // turned off — we supply our own controls, so the engine shows nothing but
    // the image. `showLayerDialog`/`resetStateWhenEmpty` are off so the engine
    // does not pop its own "add a layer" dialog or wipe state before the parent
    // loads a volume.
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

    // The engine paints its own ground — the canvas where no data is drawn —
    // and it must match the theme's, or switching to the light dress would
    // leave the picture floating on a black field inside a light page. The
    // colour comes from themeGround() — the same variable the stylesheet
    // uses — and is told again whenever the Light/Dark button changes the
    // page's dress. (syncView below re-asserts the same reading on
    // every view change, so the two can never fight.)
    const paintTheGround = () => {
      const ground = themeGround();
      viewer.crossSectionBackgroundColor.restoreState(ground);
      viewer.perspectiveViewBackgroundColor.restoreState(ground);
    };
    paintTheGround();
    const dressWatcher = new MutationObserver(paintTheGround);
    dressWatcher.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["data-theme"],
    });

    // The panels are given explicit tables holding exactly the gestures our
    // interface documents, instead of inheriting the engine's defaults. What
    // an operator can do to the picture is written out here, once, in full —
    // docs/how_it_works/CONTROLS.md records each decision.
    //
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
    // flat view cannot rotate. The test file
    // `test_the_keyboard_cannot_trap_the_operator.py` holds each of these
    // removals in place.
    //
    // What stays, besides movement, is the drawing family our Point and Box
    // buttons rely on: Ctrl+click places (the engine's "annotate" action
    // triggers whichever placement tool those buttons installed), Enter
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
    // This is ours, not the engine's. Neuroglancer gives the plain wheel to z and
    // puts zoom behind Control, which is right for reading through a volume and
    // wrong for an operator looking at a plate. The wheel is the gesture made
    // most often and without thinking, and a browser has taught everybody what it
    // does; zoom has nowhere else to live, since this interface has no zoom
    // button, slider or key, while the stack keeps the slider down the right-hand
    // side -- better than a gesture anyway, because it shows where in the stack
    // you are. `docs/how_it_works/CONTROLS.md` section 1 records the decision.
    //
    // It is set here rather than left to the defaults because these entries win
    // over the parent table added above, which is why that is added at the lowest
    // possible precedence.
    //
    // The reason this was worth doing: on a run one plane deep -- an overview
    // scan, which is most of them -- the engine's binding leaves the first
    // gesture an operator makes doing nothing whatsoever, with nothing on screen
    // to say why. The operator page beside this one has zoomed on the plain wheel
    // all along, so until now the two disagreed.
    // `preventDefault` is passed explicitly. Handing `set` a bare action name --
    // which is how `docs/how_it_works/CONTROLS.md` writes it -- leaves it unset, because
    // `normalizeEventAction` returns only `{action, originalEventIdentifier}` for
    // a string. The engine's own table sets it on every wheel entry, and without
    // it the browser keeps its native wheel behaviour and acts on the same notch
    // we are acting on.
    // Both panels, and the second one is not an afterthought. Rebinding the flat
    // view alone left the volume view on the engine's defaults, where the plain
    // wheel steps z and zoom hides behind Control -- so an operator taught that
    // the wheel zooms found it doing nothing in three dimensions, and on a run
    // one plane deep it could do nothing at all. That looked exactly like a
    // volume view unable to draw at any resolution but the coarsest, and cost an
    // afternoon and two reviews before the measurement was made with the volume
    // view's own zoom. It refines perfectly well: 20.8 um down to 0.33 um across
    // the projection zooms, the whole pyramid.
    // A gentler notch than the engine's, and the reason is worth stating because
    // the engine's is not wrong -- it is simply pitched for travelling.
    //
    // `getWheelZoomAmount` returns `exp(deltaY / 200)`, and a wheel notch on
    // Windows reports a deltaY of 100, so one notch magnifies by about 1.65. A
    // pyramid level is 2, so a single notch crosses roughly seven-tenths of a
    // level. Pulled back that is pleasant -- you cross the specimen in a few
    // turns. Zoomed in on something it is unusable: there is no stop between too
    // far out and too far in, and because the engine zooms about the *cursor*
    // rather than the centre, every notch slides the view sideways as well. The
    // two compound, and it reads as flying rather than zooming.
    //
    // A third of the exponent gives about 1.18 a notch, so four notches to a
    // level. Zooming about the cursor is kept exactly as it was: this hands the
    // work back to the very method the engine's own action calls, so pointing at
    // something and turning the wheel still travels towards it.
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

    return {
      viewer,
      dispose() {
        dressWatcher.disconnect();
        viewer.dispose();
      },
    };
}

/**
 * What the flat and volume panels add to a drawn panel: zooming about the
 * cursor. Not every panel the engine draws has it, so each is asked.
 */
interface ZoomingPanel {
  zoomByMouse?: (factor: number) => void;
  onMousemove?: (event: MouseEvent, atOnly?: boolean) => void;
}

// -- the parts of the view that are not layers --------------------------------

/**
 * What the transparency edit (`engine/drawing/neuroglancer-patches.mjs`) adds to
 * the viewer's settings. Not in neuroglancer's declarations, since the edit is
 * made to the compiled package.
 */
interface TransparentUIConfiguration {
  transparentBackground: TrackableBoolean;
}

/** The view settings the page sets: which panels, and whether the engine draws its own furniture. */
export interface ViewSettings {
  layout: string;
  chrome: boolean;
  transparentBackground?: boolean;
}

/**
 * Carry the visible field across a switch between the flat and volume views.
 *
 * The two views keep separate zooms and count them differently: the flat zoom
 * is specimen per screen pixel, the volume zoom is specimen across the height
 * of the panel. Left unconverted, a switch jumped the magnification to
 * wherever the other view happened to last be, so the operator lost their
 * place in scale even though the position held. The panel's height is the
 * exchange rate, and both views draw in the same box, so the height of the
 * panel being left is the height of the one arriving.
 */
function carryTheFieldAcross(viewer: Viewer, from: string, to: string): void {
  const panels = [...(viewer.display?.panels ?? [])];
  const height = panels[0]?.renderViewport?.logicalHeight;
  if (!height) return;
  const flat = viewer.navigationState.zoomFactor;
  const volume = viewer.perspectiveNavigationState.zoomFactor;
  if (from === "xy" && to === "3d") volume.value = flat.value * height;
  else if (from === "3d" && to === "xy") flat.value = volume.value / height;
}

/**
 * Set the parts of the view that are not layers: which panels are on screen, and
 * whether the engine draws its own furniture.
 *
 * Rebuilding the panel layout means new drawing surfaces, so it is done only when
 * the operator has actually switched between the flat view and the volume — never
 * as a side effect of some unrelated change.
 */
export function syncView(viewer: Viewer, { layout, chrome, transparentBackground = false }: ViewSettings): void {
  const ui = viewer.uiConfiguration as ViewerUIConfiguration & TransparentUIConfiguration;
  ui.transparentBackground.value = transparentBackground && layout === "xy";
  const leaving = viewer.layout.toJSON();
  if (leaving !== layout) {
    carryTheFieldAcross(viewer, leaving, layout);
    viewer.layout.restoreState(layout);
  }
  viewer.showDefaultAnnotations.value = chrome;
  viewer.showAxisLines.value = chrome;
  // Neuroglancer's own scale bars are off. It draws one per axis along the bottom
  // left, including one for time -- which looks like a distance and is not one. A
  // single bar for distance is drawn in the corner of the image instead, and it is
  // ours so it can be put somewhere out of the way. See ScaleBar.jsx.
  viewer.showScaleBar.value = false;
  // The theme's ground behind the slice, rather than the engine's mid-grey.
  //
  // Fluorescence images are mostly dark, and a grey surround sitting right up
  // against a dark specimen makes the specimen look brighter than it is -- the
  // eye judges brightness by comparison, so the same image reads differently
  // depending on what is next to it. The dark dress therefore grounds the
  // image on black, which is what a microscopist expects; the light dress
  // uses its own light ground (asked for by the operator, 2026-08-23).
  //
  // Worth knowing if you are debugging: the engine's grey used to be the only
  // clue that nothing was being drawn at all, since an empty panel showed the
  // engine's background and a drawn one did not. That clue is no longer
  // needed, because a test now looks at the picture and fails if it is a flat
  // colour -- see tests/pixels.py. It is the test that makes this line safe.
  //
  // Written through `restoreState` rather than assigned: it parses the colour
  // and only writes when it differs from what is already there, and this
  // function runs on every change to the view. Reading the variable afresh
  // each time is also what keeps this from quietly undoing the Light/Dark
  // button: a hard-coded black here painted the ground dark again on the
  // very next view change (the operator saw it flip while adjusting the
  // histogram, 2026-08-23).
  viewer.crossSectionBackgroundColor.restoreState(themeGround());
}

/**
 * The colour the theme paints the canvas ground -- where no data is drawn.
 *
 * Read from the same CSS variable the stylesheet uses (see theme.css), so
 * the engine, the page and the veil can never disagree about what the
 * ground looks like.
 */
function themeGround(): string {
  return (getComputedStyle(document.documentElement)
    .getPropertyValue("--canvas-bg").trim()) || "#000000";
}

/**
 * Stretch the picture along an axis without touching what is on disk.
 *
 * The engine keeps one factor per dimension of the coordinate space and applies
 * them to the display alone, so this changes how the specimen is drawn and
 * nothing about what it claims to be. That distinction is the whole reason this
 * is safe to offer: correcting a run whose declared voxel size is wrong is a
 * different act, with different consequences, and belongs somewhere harder to
 * reach than a panel.
 *
 * Which is also why anything above the panel has to say when a factor is not
 * one. A stretched picture with an unmarked scale bar is a way to take a wrong
 * measurement and never know.
 *
 * Factors are held by axis name rather than by position, because the space is
 * `t, z, y, x` here and `t, c, z, y, x` elsewhere, and an index would silently
 * scale the wrong thing on a store with a channel axis.
 */
export function stretchTheDisplay(viewer: Viewer, byAxis: Record<string, number | undefined>): void {
  const scales = viewer?.navigationState?.relativeDisplayScales;
  if (!scales) return;
  const names = viewer.navigationState.coordinateSpace.value?.names;
  if (!names) return;
  const factors = new Float64Array(scales.value.factors);
  let moved = false;
  names.forEach((name, index) => {
    const wanted = byAxis[name];
    if (typeof wanted === "number" && wanted > 0 && factors[index] !== wanted) {
      factors[index] = wanted;
      moved = true;
    }
  });
  if (moved) scales.setFactors(factors);
}
