/**
 * Settling the view: how the picture is fitted to the window -- at first open,
 * on Overview, and when a new acquisition is opened beside the others. Which
 * axes are drawn and where in time it opens are the drawing primitives'
 * (`engine/drawing/primitives/axes.ts`), called from here at the first fit.
 *
 * Three things have to be decided the moment an image says how big it is, and all
 * three were wrong at some point in a way that produced a picture nobody could see:
 * which axes to draw, where in time to start, and how far to zoom. They happen once,
 * before anybody is looking, and afterwards the operator is in charge.
 */

import type { LayerDataSource } from "neuroglancer/unstable/layer/layer_data_source.js";
import type { DisplayDimensionRenderInfo } from "neuroglancer/unstable/navigation_state.js";
import type { Viewer } from "neuroglancer/unstable/viewer.js";
import { geometryRefreshPending } from "../../../engine/drawing/embedding.js";
import {
  pinTheAxesThatMeasureDistance,
  startTimeAtTheFirstMoment,
} from "../../../engine/drawing/primitives/axes.ts";
import { panelsOnScreen } from "../../../engine/drawing/primitives/camera.ts";
import type { Engine } from "../../../engine/drawing/primitives/viewer.ts";

/**
 * What the growth edit (`engine/drawing/neuroglancer-growth.mjs`) adds to a data
 * source: the reading of its description still under way, if one is. Not in
 * neuroglancer's declarations, since the edit is made to the compiled package.
 */
interface GrowingDataSource {
  metadataRefresh?: AbortController;
}

// How much window the overview leaves around the picture. The picture's longest
// side fills the window divided by this, so 1.2 leaves about a sixth of the
// window as visible ground on that axis -- roughly eight percent a side, which
// on a full-screen window of the lab monitor is the two centimetres the
// operator judged right by eye. Proportional rather than a fixed distance, so
// a small window is not mostly margin. Chosen twice by the same operator on
// the 12,800-position survey: first that the exact fit needed an edge at all,
// then that the first margin wanted doubling.
const OVERVIEW_MARGIN = 1.2;

/**
 * Zoom both views so the picture's reach along the drawn axes just fills the
 * window, with the overview's margin.
 *
 * The flat view is fitted on the first two drawn axes, against the panel's width
 * and height; the volume's box on all three, against the window's smaller side,
 * because its zoom counts across the height of the panel. ``extent`` says how far
 * the picture reaches along an axis, or null where it does not say. A view nothing
 * fits keeps its zoom, unless ``resetWhenNothingFits`` puts it back to the engine's
 * default -- which the whole-picture fit does and the fit around one acquisition
 * does not.
 */
function fitExtents(
  viewer: Viewer, render: DisplayDimensionRenderInfo, drawn: readonly number[],
  extent: (axis: number) => number | null, { resetWhenNothingFits }: { resetWhenNothingFits: boolean },
): void {
  const { flat, volume } = panelsOnScreen(viewer);

  let fit = 0;
  drawn.slice(0, 2).forEach((axis, slot) => {
    const across = extent(axis);
    const pixels = slot === 0 ? flat?.logicalWidth : flat?.logicalHeight;
    if (across === null || !pixels) return;
    fit = Math.max(fit, (across * render.canonicalVoxelFactors[slot]) / pixels);
  });
  if (fit > 0) viewer.navigationState.zoomFactor.value = fit * OVERVIEW_MARGIN;
  else if (resetWhenNothingFits) viewer.navigationState.zoomFactor.reset();

  let boxFit = 0;
  const smaller = Math.min(volume?.logicalWidth ?? 0, volume?.logicalHeight ?? 0);
  drawn.slice(0, 3).forEach((axis, slot) => {
    const across = extent(axis);
    if (across === null || !volume || !smaller) return;
    boxFit = Math.max(
      boxFit,
      (across * render.canonicalVoxelFactors[slot] * volume.logicalHeight) / smaller,
    );
  });
  if (boxFit > 0) {
    viewer.perspectiveNavigationState.zoomFactor.value = boxFit * OVERVIEW_MARGIN;
  } else if (resetWhenNothingFits) {
    viewer.perspectiveNavigationState.zoomFactor.reset();
  }
}

/**
 * Put the whole view back to how it opened: straight, centred and fitted.
 *
 * This is what Overview does, in both views. It briefly shared the corner
 * with a second button that only fitted, leaving the turn alone; the two
 * differed only once a volume had been tilted, so the same face meant two
 * things depending on where the operator was, and the pair was folded into
 * one (2026-08-20).
 *
 * What it does NOT touch is where the operator is in depth or in time. That
 * is which picture they are looking at rather than how they are looking at
 * it, and losing your plane because you asked for the view to be put straight
 * would be its own small betrayal.
 */
export function putTheViewBack(viewer: Viewer): boolean {
  // Turned back first, because which axes are on screen follows from it and
  // the fitting below measures what is on screen.
  viewer.navigationState.pose.orientation.reset();
  viewer.perspectiveNavigationState.pose.orientation.reset();
  return showTheWholePicture(viewer);
}

/**
 * Fit the whole picture into the window, centred and straight.
 *
 * The depth slider is drawn on TOP of the drawing area rather than beside
 * it, so the room the picture is fitted into is a little wider than the room
 * an operator can see into, and the picture comes to rest slightly right of
 * where the eye expects it -- its middle at 510 across a window whose
 * visible middle is at 480.
 *
 * Framing around that strip was built on 2026-08-22 and put back the same
 * day, because it cannot be done here alone. This same fit is what a picture
 * OPENS at, and at that moment the slider has not been drawn yet -- so the
 * picture opened reserving nothing and came back from the button reserving
 * 45 pixels, two magnifications 6% apart (0.4267 against 0.4541), which is
 * the one thing the Overview button promises not to do. Waiting for the
 * slider before the opening fit fixed that and moved the VOLUME's opening
 * magnification instead (384 against 408.7), because what is laid out when
 * the fit runs decides that too. Both are pinned by gates in
 * test_interaction.py, and both were red.
 *
 * Doing it properly means settling when a picture is first framed against
 * when the window's furniture is drawn, which is a larger change than the
 * strip is worth on its own.
 *
 * The zoom is computed rather than reset, because the engine's default zoom
 * knows nothing about this picture -- on a survey it left the operator centred
 * but still deep in detail, filling a third of the window with specimen and
 * the rest with ground beyond it. How many pixels an axis of the picture
 * occupies is its voxel extent times the engine's own per-axis factor divided
 * by the zoom (the scale bar reads its size from the same arithmetic), so the
 * fitting zoom is whatever makes the largest axis just fill its side of the
 * window, with a small margin so the edge is visibly an edge.
 *
 * The volume view counts its zoom across the height of the panel rather than
 * per pixel, and its box can be turned, so the box is fitted against the
 * window's smaller side. Where a viewport or a bound is missing, the engine's
 * default zoom is kept -- a wrong guess dressed as a fit would be worse.
 */
function showTheWholePicture(viewer: Viewer): boolean {
  const { position } = viewer.navigationState;
  const space = position.coordinateSpace.value;
  if (!space?.rank) return false;

  // The first two display dimensions only -- the plane on screen. The third is
  // the depth the operator is looking through, and moving it would step them
  // somewhere else in the stack, which is what the test below caught: in a
  // cross-section the engine still counts the perpendicular axis as displayed.
  const render = viewer.navigationState.pose.displayDimensionRenderInfo.value;
  const drawn = Array.from(render?.displayDimensionIndices ?? []);
  const onScreen = new Set(drawn.slice(0, 2));
  const { lowerBounds, upperBounds } = space.bounds;

  const middle = Float32Array.from(position.value);
  for (let axis = 0; axis < space.rank; axis += 1) {
    if (!onScreen.has(axis)) continue;
    const low = lowerBounds[axis];
    const high = upperBounds[axis];
    if (Number.isFinite(low) && Number.isFinite(high)) middle[axis] = (low + high) / 2;
  }
  position.value = middle;

  const extent = (axis: number): number | null => {
    const low = lowerBounds[axis];
    const high = upperBounds[axis];
    return Number.isFinite(low) && Number.isFinite(high) ? high - low : null;
  };

  fitExtents(viewer, render, drawn, extent, { resetWhenNothingFits: true });
  return true;
}

/**
 * Move the view to the acquisition that has just been opened.
 *
 * Opening a second acquisition used to leave the operator looking wherever
 * they already were, which on a run whose positions sit somewhere else on the
 * stage is a black panel and no clue that anything arrived at all. So a fresh
 * acquisition is framed: centred, and zoomed to hold the whole of it.
 *
 * ``named`` is what the engine calls the layers of that one acquisition --
 * every channel of it -- and only those are measured. That is the difference
 * from :func:`showTheWholePicture`, which frames everything on screen: after
 * a second acquisition is opened there are two, and framing both would put
 * the operator somewhere between them looking at neither.
 *
 * **The answer is ``false`` until there is something true to say.** The
 * caller polls, because a store's extent is the store's own and it has none
 * to give until its description has been read. Two things have to hold: every
 * named layer exists with all of its sources answered for, and the page has
 * no stores left to hand over. Without the second, an acquisition of many
 * positions would be framed around whichever one happened to be read first --
 * reliably a single tile of many on a fast machine, which is the same trap
 * ``whenTheSourcesHaveSettled`` documents.
 */
export function lookAtWhatOpened(engine: Engine, named: readonly string[] | null | undefined): boolean {
  const { viewer } = engine;
  const wanted = new Set(named ?? []);
  if (!wanted.size) return false;
  const { position } = viewer.navigationState;
  const space = position.coordinateSpace.value;
  if (!space?.rank) return false;
  // Anything still queued means more of this acquisition is on its way.
  if (engine.sourcesStillWaiting() > 0) return false;

  const found = viewer.layerManager.managedLayers.filter(
    (managed) => wanted.has(managed.name));
  if (found.length < wanted.size) return false;

  // How far this acquisition reaches along each axis, gathered from the
  // sources themselves. A source states its own extent in the shared
  // coordinate space -- its place on the stage included -- so the axes are
  // matched up by name rather than by number: a layer's own space carries the
  // channel dimension the shared one does not.
  const reach = new Map<string, [number, number]>();
  for (const managed of found) {
    const sources = managed.layer?.dataSources ?? [];
    if (!sources.length) return false;
    for (const source of sources) {
      const loaded = source.loadState;
      if (loaded === undefined) return false;
      // A source that could not be read has no transform, and so no extent to give.
      const own = "transform" in loaded ? loaded.transform?.outputSpace?.value : undefined;
      if (!own?.rank) return false;
      const names = Array.from(own.names);
      const { lowerBounds, upperBounds } = own.bounds;
      names.forEach((name, axis) => {
        const low = lowerBounds[axis];
        const high = upperBounds[axis];
        if (!Number.isFinite(low) || !Number.isFinite(high) || high <= low) return;
        const already = reach.get(name);
        reach.set(name, already
          ? [Math.min(already[0], low), Math.max(already[1], high)]
          : [low, high]);
      });
    }
  }
  if (!reach.size) return false;

  const render = viewer.navigationState.pose.displayDimensionRenderInfo.value;
  const drawn = Array.from(render?.displayDimensionIndices ?? []);
  if (drawn.length < 2) return false;
  const names = Array.from(space.names);
  const spanOf = (axis: number) => reach.get(names[axis]) ?? null;

  const middle = Float32Array.from(position.value);
  drawn.slice(0, 2).forEach((axis) => {
    const span = spanOf(axis);
    if (span) middle[axis] = (span[0] + span[1]) / 2;
  });
  // Depth is moved only when the plane the operator is on lies outside what
  // just opened. Their plane is which picture they are looking at rather than
  // how they are looking at it, and taking it from them is the small betrayal
  // putTheViewBack refuses to commit -- but a jump that lands on a plane this
  // acquisition does not occupy shows a black panel, which fails at the one
  // thing this is for. So it is left alone wherever it already works.
  const deep = drawn[2];
  if (deep !== undefined) {
    const span = spanOf(deep);
    const at = middle[deep];
    if (span && (at < span[0] || at > span[1])) middle[deep] = (span[0] + span[1]) / 2;
  }
  position.value = middle;

  const extent = (axis: number): number | null => {
    const span = spanOf(axis);
    return span ? span[1] - span[0] : null;
  };
  fitExtents(viewer, render, drawn, extent, { resetWhenNothingFits: false });
  return true;
}

/**
 * Wait until every store the page asked for has been handed over and answered,
 * and the axes are known, then ``act`` -- once, in the very dispatch that brings
 * the last answer. ``ready`` is the caller's own condition, checked as well.
 *
 * Returns a function that stops the waiting.
 */
export function whenTheSourcesHaveSettled(engine: Engine, ready: () => boolean, act: () => void): () => void {
  const { viewer } = engine;
  const { position } = viewer.navigationState;
  // Axes, not images: a space with no axes is the placeholder described at
  // chooseScaleWhenTheImagesAreMeasured. The moment it has any, the engine knows
  // how big a voxel is.
  const measured = () => (position.coordinateSpace.value?.rank ?? 0) > 0;
  // But the first axes are not the whole picture. A folder of several stores
  // reaches the engine one description at a time, and the space has axes the
  // moment the FIRST of them answers -- fitting then zooms to whichever store's
  // reading happened to finish first, which on a fast machine is reliably one
  // tile of many. Every store the page asked for has to have been handed over
  // and answered -- "answered" in the sense whenTheseHaveBeenRead uses, an
  // unreadable store having been dealt with as much as a perfect one -- before
  // the bounds are worth fitting to. ``ready`` guards the other direction:
  // a caller waiting for layers that have not even been HANDED OVER yet (a
  // run whose config just arrived) says so, or a look between the answer
  // and the hand-over would find everything old settled and act too early.
  const settled = () =>
    ready() &&
    measured() &&
    engine.sourcesStillWaiting() === 0 &&
    viewer.layerManager.managedLayers.every((managed) =>
      (managed.layer?.dataSources ?? []).every(
        (source) => source.loadState !== undefined && !geometryRefreshPending(source)
          && !(source as LayerDataSource & GrowingDataSource).metadataRefresh,
      ),
    );
  let stop = () => {};
  // Answers arrive on each source's own changed signal -- the same place
  // whenTheseHaveBeenRead hears them -- so the fit runs in the very dispatch
  // that delivers the last answer, before the first full drawing, rather than
  // some time later. A fit that runs late is worse than one that runs early:
  // it moves the picture under an operator (or a measurement) already using it.
  // Sources appear over time as the page hands stores over, so each look also
  // adopts any source it has not heard of yet.
  const listened = new Set<LayerDataSource>();
  const heard: (() => void)[] = [];
  const check = () => {
    for (const managed of viewer.layerManager.managedLayers) {
      for (const source of managed.layer?.dataSources ?? []) {
        if (listened.has(source)) continue;
        listened.add(source);
        heard.push(source.changed.add(check));
      }
    }
    if (!settled()) return;
    act();
    stop();
    stop = () => {};
  };
  const stopWatching = position.coordinateSpace.changed.add(check);
  // A store handed over between two answers has nobody to announce it, so a
  // slow look on the side adopts strays; it costs a handful of property reads
  // a few times a second and ends with the fit.
  const glance = setInterval(check, 250);
  stop = () => {
    stopWatching();
    for (const stopHearing of heard) stopHearing();
    heard.length = 0;
    clearInterval(glance);
  };
  // In case everything is already read by the time we are asked -- reopening a
  // folder, say, where the descriptions are still in hand.
  check();
  return () => stop();
}

/**
 * Wait for the images to say how big they are, then let the engine choose the
 * starting magnification again.
 *
 * Without this the viewer opens on an empty grey rectangle, with the data
 * present and correct but drawn far too small to see. It is worth explaining
 * why, because the cause is nowhere near the symptom.
 *
 * The engine picks a starting magnification the first moment it believes it
 * knows what space the picture lives in. It expresses that magnification in
 * physical units — so many micrometres to a screen pixel — and it is careful
 * afterwards: if the size of a voxel changes, it adjusts the number so that what
 * is on screen stays the same real size. That is the right thing to do, and it
 * is exactly what hurts us here.
 *
 * The trouble is timing. We hand the engine its layers immediately, while the
 * images themselves are still being read over the network. For a moment there
 * are layers but no axes yet, and in that moment the engine considers the space
 * settled — an empty space, in which it has no voxel size to work from and falls
 * back to treating one voxel as one metre. It picks its ordinary default of one
 * voxel to a pixel, which now means *one metre* to a pixel. A little later the
 * real axes arrive saying a voxel is a third of a micrometre, and the engine
 * dutifully preserves the physical scale it was given. A specimen a tenth of a
 * millimetre across is then drawn about a ten-thousandth of a pixel wide, which
 * is to say invisibly, and the panel shows nothing but its own background.
 *
 * So we wait for the axes to actually arrive, and for the panel to be laid
 * out, and then fit the whole picture to the window with
 * :func:`showTheWholePicture` -- the same fit the Overview button makes, this
 * time knowing how big a voxel really is. This happens once, before anything
 * is on screen, so it cannot disturb an operator who has started looking
 * around. Afterwards the engine's careful adjustment is left alone, because
 * from then on it is working from real sizes and is right.
 *
 * Returns a function that stops the waiting, for the caller to use when the
 * viewer goes away.
 */
export function chooseScaleWhenTheImagesAreMeasured(engine: Engine, framed: (() => void) | null = null): () => void {
  const { viewer } = engine;
  // The whole-picture fit divides the picture's extent by the panel's size
  // in screen pixels, so it cannot run before the panel has been laid out.
  // A first load never hit this -- the network is slower than the layout --
  // but a reload finds every answer already in the browser's cache, the
  // sources settle before the panel has a size, and the fit quietly became
  // the engine's default zoom: the same picture opened at one magnification
  // cold and at half that on F5, stable both ways (measured 2026-08-19 on
  // the workstation, the scale bar reading 50 um warm against 100 um
  // fresh). The settling watch glances every quarter second, so waiting for
  // the layout here delays the fit by at most one glance.
  const panelLaidOut = () =>
    [...(viewer.display?.panels ?? [])].some(
      (panel) => "sliceView" in panel
        && (panel.renderViewport?.logicalWidth ?? 0) > 0
        && (panel.renderViewport?.logicalHeight ?? 0) > 0,
    );
  return whenTheSourcesHaveSettled(engine, panelLaidOut, () => {
    // Which axes are drawn is settled first, because how far to zoom is worked
    // out from what is being drawn. Choosing the zoom and then changing the axes
    // leaves the view scaled for a picture nobody is looking at.
    pinTheAxesThatMeasureDistance(viewer);
    // And then where in time to start, which is the beginning rather than the
    // middle the engine would otherwise choose.
    startTimeAtTheFirstMoment(viewer);
    // Open on the overview: the whole picture, centred and sized to the window
    // with the overview's own margin, exactly as if the button had been
    // pressed. This used to clear the zoom to the engine's default instead, on
    // the argument that an operator who knew neuroglancer would expect it; the
    // operator at the actual microscope met a 12,800-position survey at an
    // arbitrary magnification and asked for this. One function serves the
    // button and the opening, so the two can never disagree.
    showTheWholePicture(viewer);
    // Whoever is waiting to unveil the picture is told only now, after the
    // fit: between the layer attaching and this moment the engine paints at
    // its own default magnification, and that wrongly-sized frame is exactly
    // what the caller is keeping off the screen (seen as a brief size
    // flicker on every first open, 2026-08-23).
    framed?.();
  });
}
