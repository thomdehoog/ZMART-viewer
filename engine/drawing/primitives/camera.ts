/**
 * Where the view looks, in micrometres, and what of the
 * picture is on screen.
 *
 * The engine counts its position in its own units per axis and its zoom per
 * canonical voxel; a page thinks in micrometres. These are the two worlds'
 * exchange, written once.
 */

import type { RenderViewport } from "neuroglancer/unstable/display_context.js";
import type { Viewer } from "neuroglancer/unstable/viewer.js";

const METRES_IN_A_MICROMETRE = 1e-6;

/**
 * Where the view is looking: the position by axis name in micrometres, and
 * both zooms as so many micrometres to a screen pixel (the volume's across the
 * height of its panel, as the engine counts it). Kept by name rather than by
 * axis number so it can be put back on an engine built afresh, whose axes may
 * arrive in another order.
 */
export interface Camera {
  positionUm: Record<string, number>;
  zoomUm: number;
  perspectiveZoomUm: number;
}

/** Where the view is looking now, or null while it has no axes to say it by. */
export function captureCamera(viewer: Viewer): Camera | null {
  const position = viewer.navigationState.position, space = position.coordinateSpace.value;
  if (!space.valid) return null;
  const flat = viewer.navigationState.zoomFactor;
  const volume = viewer.perspectiveNavigationState.zoomFactor;
  return {
    positionUm: Object.fromEntries(space.names.map((name, i) =>
      [name, (position.value[i] * space.scales[i]) / METRES_IN_A_MICROMETRE])),
    zoomUm: (flat.value * flat.canonicalVoxelPhysicalSize) / METRES_IN_A_MICROMETRE,
    perspectiveZoomUm: (volume.value * volume.canonicalVoxelPhysicalSize) / METRES_IN_A_MICROMETRE,
  };
}

/** Put the view where `camera` says; an axis it does not name stays put. */
export function restoreCamera(viewer: Viewer, camera: Camera): void {
  const position = viewer.navigationState.position, space = position.coordinateSpace.value;
  position.value = Float32Array.from(space.names, (name, i) =>
    camera.positionUm[name] === undefined
      ? position.value[i]
      : (camera.positionUm[name] * METRES_IN_A_MICROMETRE) / space.scales[i]);
  viewer.navigationState.zoomFactor.setPhysicalScale(camera.zoomUm * METRES_IN_A_MICROMETRE, 1);
  viewer.perspectiveNavigationState.zoomFactor.setPhysicalScale(
    camera.perspectiveZoomUm * METRES_IN_A_MICROMETRE, 1);
}

/**
 * The drawing areas of the flat panel and of the volume panel, where each is on
 * screen. A panel is told apart by what it draws: one slice, or several.
 */
export function panelsOnScreen(viewer: Viewer): { flat: RenderViewport | null; volume: RenderViewport | null } {
  let flat: RenderViewport | null = null;
  let volume: RenderViewport | null = null;
  for (const panel of viewer.display?.panels ?? []) {
    if ("sliceView" in panel) flat = panel.renderViewport;
    else if ("sliceViews" in panel) volume = panel.renderViewport;
  }
  return { flat, volume };
}

/**
 * Which part of the picture is on screen, as fractions of its own bounds:
 * `[[top, left], [bottom, right]]`.
 *
 * Given as fractions rather than as voxels because that is the form a server
 * can use without knowing anything about how the view is arranged: "measure
 * the brightness of this share of the picture" means the same whatever the
 * zoom happens to be expressed in. How many of the picture's own units a
 * screen pixel covers is the zoom divided by the engine's factor for that
 * axis, so half the panel's width in units is half its pixels times that.
 *
 * ``null`` where the picture has not said how big it is yet, or where the view
 * is not a flat one: a volume is turned in three dimensions and "which
 * rectangle is on screen" has no honest answer there.
 */
export function whatIsOnScreen(viewer: Viewer): [[number, number], [number, number]] | null {
  const { position } = viewer.navigationState;
  const space = position.coordinateSpace.value;
  if (!space?.rank) return null;
  const render = viewer.navigationState.pose.displayDimensionRenderInfo.value;
  const drawn = Array.from(render?.displayDimensionIndices ?? []).slice(0, 2);
  if (drawn.length < 2) return null;
  const viewport = panelsOnScreen(viewer).flat;
  if (!viewport?.logicalWidth || !viewport?.logicalHeight) return null;

  const zoom = viewer.navigationState.zoomFactor.value;
  const { lowerBounds, upperBounds } = space.bounds;
  const centre = position.value;
  const names = Array.from(space.names);
  const share: Record<string, [number, number]> = {};
  drawn.forEach((axis, slot) => {
    const factor = render.canonicalVoxelFactors[slot] || 1;
    const pixels = slot === 0 ? viewport.logicalWidth : viewport.logicalHeight;
    const half = (pixels / 2) * (zoom / factor);
    const low = lowerBounds[axis];
    const high = upperBounds[axis];
    if (!Number.isFinite(low) || !Number.isFinite(high) || high <= low) return;
    share[names[axis]] = [
      (centre[axis] - half - low) / (high - low),
      (centre[axis] + half - low) / (high - low),
    ];
  });
  if (!share.y || !share.x) return null;
  return [[share.y[0], share.x[0]], [share.y[1], share.x[1]]];
}
