/**
 * The viewer's own view settings, over the drawing primitives': carrying the
 * field of view across a switch between the flat view and the volume, the
 * theme's ground, and any stretch along an axis.
 *
 * The engine itself -- made with its own interface off, its gestures, its
 * layers kept in step -- is the primitives' (`engine/drawing/primitives/`),
 * shared with the operator page of ZMART-interface. How the view is framed is
 * `framing.ts`.
 */

import type { Viewer } from "neuroglancer/unstable/viewer.js";
import type { Engine, ViewSettings } from "../../../engine/drawing/primitives/viewer.ts";

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
 * Set the parts of the view that are not layers, carrying the field of view
 * across when the operator switches between the flat view and the volume.
 *
 * The ground is painted again on every change, from the theme's own variable:
 * a hard-coded black here once painted the ground dark again on the very next
 * view change, undoing the Light/Dark button (the operator saw it flip while
 * adjusting the histogram, 2026-08-23).
 */
export function syncView(engine: Engine, settings: ViewSettings): void {
  const leaving = engine.viewer.layout.toJSON();
  if (leaving !== settings.layout) carryTheFieldAcross(engine.viewer, leaving, settings.layout);
  engine.setView(settings);
}

/**
 * The colour the theme paints the canvas ground -- where no data is drawn.
 *
 * Read from the same CSS variable the stylesheet uses (see theme.css), so
 * the engine, the page and the veil can never disagree about what the
 * ground looks like.
 *
 * Fluorescence images are mostly dark, and a grey surround sitting right up
 * against a dark specimen makes the specimen look brighter than it is -- the
 * eye judges brightness by comparison. The dark dress therefore grounds the
 * image on black, which is what a microscopist expects; the light dress uses
 * its own light ground (asked for by the operator, 2026-08-23). The engine's
 * grey used to be the only clue that nothing was being drawn at all; a test
 * now looks at the picture and fails if it is a flat colour (tests/pixels.py),
 * and that test is what makes this safe.
 */
export function themeGround(): string {
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
