/**
 * Settling the view: which axes are drawn, where in time it opens, and how the
 * picture is fitted to the window -- at first open, on Overview, and when a new
 * acquisition is opened beside the others.
 *
 * Three things have to be decided the moment an image says how big it is, and all
 * three were wrong at some point in a way that produced a picture nobody could see:
 * which axes to draw, where in time to start, and how far to zoom. They happen once,
 * before anybody is looking, and afterwards the operator is in charge.
 */

import type { CoordinateSpace } from "neuroglancer/unstable/coordinate_transform.js";
import type { RenderViewport } from "neuroglancer/unstable/display_context.js";
import type { LayerDataSource } from "neuroglancer/unstable/layer/layer_data_source.js";
import type { Viewer } from "neuroglancer/unstable/viewer.js";
import { geometryRefreshPending } from "../../../engine/drawing/embedding.js";
import { sourcesStillWaiting } from "./layer-sync.ts";

/**
 * What the growth edit (`engine/drawing/neuroglancer-growth.mjs`) adds to a data
 * source: the reading of its description still under way, if one is. Not in
 * neuroglancer's declarations, since the edit is made to the compiled package.
 */
interface GrowingDataSource {
  metadataRefresh?: AbortController;
}

/**
 * Draw the axes that measure distance, and leave the rest to the sliders.
 *
 * An image says what its axes are and what each one measures. Some are places in
 * the specimen — depth, height, width — and some are not: which moment this is,
 * or which colour of light was recorded. Only the first kind belongs on screen.
 *
 * The engine does not know that on its own. Left alone it takes the first three
 * axes the image declares, in the order they were written. For an image of a
 * single moment those happen to be depth, height and width and everything looks
 * right — which is exactly what made this hard to see. Add a time axis at the
 * front, as every timelapse has, and the first three become time, depth and
 * height: the engine draws time against depth and pins width, so the specimen is
 * nowhere on screen. Nothing reports a fault. The picture is simply black while
 * the data on disk is perfectly correct.
 *
 * Measured, on the same tiles in the same places: an image declaring one moment
 * filled the view, and an image declaring three drew nothing at all.
 *
 * Which axes measure distance is not guessed at. An image declares a unit for
 * each axis, and "m" — metres — is the only one that measures distance across a
 * specimen. Seconds do not, and an axis with no unit does not. That is the same
 * test the scale bar uses, for the same reason.
 *
 * The *order* they are handed over in matters as much as which ones they are, and
 * it cannot be chosen on its own. The engine decides what goes across the window,
 * what goes down it and what goes into the screen from two things together: the
 * order of this list, and which of its named panel layouts the viewer asks for.
 * The layout is chosen in `App.jsx`, next to `SLICE_LAYOUT`, and the two are a
 * matched pair. Change one without the other and the view comes out either
 * edge-on — looking at the specimen from the side, so a stack of planes collapses
 * to a line — or mirrored left to right, which is the quieter and more dangerous
 * of the two because the picture still looks perfectly healthy.
 *
 * The order used here is the reverse of the order the image declares. An OME-Zarr
 * image lists its distance axes slowest-changing first, which is depth, then
 * height, then width; reversing that gives width, height, depth, and asking for
 * the layout `App.jsx` calls `xy` then puts width across the window running to the
 * right, height down it, and depth into the screen — the plane the operator
 * scrolls through. That is the specimen the right way round.
 *
 * This was not always so. The viewer used to hand the axes over in the declared
 * order and ask for the layout called `yz`, which also put width across and height
 * down and looked entirely right, but ran width towards the *left*: every picture
 * was a mirror image of the specimen, and nothing said so. It is measured, from
 * the picture rather than from anything the engine reports about itself, in
 * `tests/test_the_picture_is_not_mirrored.py`.
 *
 * Note that none of this reads an axis by name. Which axes are drawn comes from
 * their unit, and which way round they go comes from the order the image declares
 * them in — so an image whose axes are called something other than z, y and x is
 * treated exactly the same way.
 *
 * An image declaring fewer than two such axes is left completely alone. We would
 * have nothing better to offer than the engine's own choice, and replacing a
 * possibly-odd view with a certainly-wrong one is not an improvement.
 */
export function keepSpatialAxes(viewer: Viewer): () => void {
  const place = () => pinTheAxesThatMeasureDistance(viewer);
  const stop = viewer.navigationState.position.coordinateSpace.changed.add(place);
  place();
  return stop;
}

function pinTheAxesThatMeasureDistance(viewer: Viewer): void {
  const space = viewer.navigationState.position.coordinateSpace.value;
  if (!space?.names) return;
  const distances = space.names.filter((_, at) => space.units?.[at] === "m");
  if (distances.length < 2) return;
  // Width first, depth last. The image declares its distance axes the other way
  // round -- depth, height, width -- so reversing them is what puts width across
  // the window and depth into the screen, which is what the `xy` layout asked for
  // in App.jsx expects. The two belong together; see the note above.
  const facing = distances.every(name => ["x", "y", "z"].includes(name))
    ? ["x", "y", "z"].filter(name => distances.includes(name))
    : distances.slice(0, 3).reverse();
  const current = viewer.navigationState.pose.displayDimensions.value;
  // Left alone when it is already right. Handing the engine the same answer it
  // already had is not free: it counts as a change, and everything downstream
  // that follows the view -- the zoom, the scale bar, the panel -- is asked to
  // catch up with a difference that does not exist.
  const showing = Array.from(current?.displayDimensionIndices ?? [])
    .filter((at) => at >= 0)
    .map((at) => space.names[at]);
  if (showing.length === facing.length
      && showing.every((name, at) => name === facing[at])) {
    return;
  }
  try {
    viewer.navigationState.pose.displayDimensions.restoreState(facing);
  } catch {
    // An engine that will not take them leaves the view as it was rather than
    // leaving the page broken. This runs while an operator is waiting for their
    // acquisition to appear, so it must not be the thing that stops it.
  }
}

function firstStepAlong(space: CoordinateSpace, at: number): number {
  // The engine describes an axis by the extent the data covers, and says
  // separately whether a step sits *on* a whole number or half way between two.
  // Both conventions occur, and the first step has to be worked out for the one
  // in use or the view lands between two frames. This is the same reading the
  // time slider does -- see AxisSlider.jsx, where it is set out at length.
  const onWholeNumbers = space.bounds.voxelCenterAtIntegerCoordinates[at];
  const lower = space.bounds.lowerBounds[at];
  return onWholeNumbers ? Math.ceil(lower) : Math.ceil(lower - 0.5) + 0.5;
}

function startAxisAtItsFirstStep(viewer: Viewer, at: number): void {
  const { position } = viewer.navigationState;
  const space = position.coordinateSpace.value;
  const first = firstStepAlong(space, at);
  if (!Number.isFinite(first) || position.value[at] === first) return;
  const moved = Float32Array.from(position.value);
  moved[at] = first;
  position.value = moved;
}

/**
 * Open a timelapse at its first moment rather than half way through it.
 *
 * The engine opens every axis in the middle, which is the right thing to do for
 * the axes that measure the specimen — the middle of a stack is a sensible plane
 * to start on. Time is not like that. A recording is watched from the beginning,
 * and the middle of a run is not a place anybody asked for.
 *
 * There is a second reason, and it is the one that shows on screen. A timelapse
 * declares room for all the moments it could record and fills them in as the
 * experiment goes, so until the run ends most of its declared moments are empty.
 * Opening in the middle of one therefore means opening on a black screen, with
 * the recorded data sitting at the beginning, unseen and with nothing to say so.
 * Measured, on a store declaring three moments with only the first imaged: the
 * view opened blank, and moving to the first moment showed the specimen.
 *
 * This is not a rare shape. It is what every run in progress looks like, and what
 * an instrument that plans its length up front writes as well.
 *
 * The time axis is recognised by its unit — seconds — which is the same test used
 * for the axes that measure distance, and for the same reason: an image says what
 * its axes mean, so there is no need to guess from their names.
 *
 * This runs once, as the image is opened. Afterwards the operator is in charge of
 * where in time they are, and nothing here moves them again.
 */
function startTimeAtTheFirstMoment(viewer: Viewer): void {
  const space = viewer.navigationState.position.coordinateSpace.value;
  if (!space?.names || !space.units) return;
  const at = Array.from(space.units).indexOf("s");
  if (at < 0) return;
  startAxisAtItsFirstStep(viewer, at);
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
 * Which part of the picture is on screen, as fractions of its own bounds.
 *
 * Given as fractions rather than as voxels because that is the form the
 * server can use without knowing anything about how the view is arranged:
 * the panel asks "measure the brightness of this share of the picture", and
 * the share is the same number whatever the zoom happens to be expressed in.
 *
 * Worked out from what the engine already holds. How many of the picture's
 * own units a screen pixel covers is the zoom divided by the engine's factor
 * for that axis -- the same arithmetic the scale bar and the whole-picture
 * fit both use -- so half the panel's width in units is half its pixels times
 * that. The two axes drawn on screen are the ones this answers for.
 *
 * ``null`` where the picture has not said how big it is yet, or where the
 * view is not a flat one: a volume is turned in three dimensions and the
 * question "which rectangle is on screen" has no honest answer there.
 */
export function whatIsOnScreen(viewer: Viewer): [[number, number], [number, number]] | null {
  const { position } = viewer.navigationState;
  const space = position.coordinateSpace.value;
  if (!space?.rank) return null;
  const render = viewer.navigationState.pose.displayDimensionRenderInfo.value;
  const drawn = Array.from(render?.displayDimensionIndices ?? []).slice(0, 2);
  if (drawn.length < 2) return null;
  let panel: RenderViewport | null = null;
  for (const one of viewer.display?.panels ?? []) {
    if ("sliceView" in one) panel = one.renderViewport;
  }
  if (!panel?.logicalWidth || !panel?.logicalHeight) return null;
  const viewport = panel;

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

  let flat: RenderViewport | null = null;
  let volume: RenderViewport | null = null;
  for (const panel of viewer.display?.panels ?? []) {
    if ("sliceView" in panel) flat = panel.renderViewport;
    else if ("sliceViews" in panel) volume = panel.renderViewport;
  }

  let fit = 0;
  drawn.slice(0, 2).forEach((axis, slot) => {
    const across = extent(axis);
    const pixels = slot === 0 ? flat?.logicalWidth : flat?.logicalHeight;
    if (across === null || !pixels) return;
    fit = Math.max(fit, (across * render.canonicalVoxelFactors[slot]) / pixels);
  });
  if (fit > 0) viewer.navigationState.zoomFactor.value = fit * OVERVIEW_MARGIN;
  else viewer.navigationState.zoomFactor.reset();

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
  } else {
    viewer.perspectiveNavigationState.zoomFactor.reset();
  }
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
export function lookAtWhatOpened(viewer: Viewer, named: readonly string[] | null | undefined): boolean {
  const wanted = new Set(named ?? []);
  if (!wanted.size) return false;
  const { position } = viewer.navigationState;
  const space = position.coordinateSpace.value;
  if (!space?.rank) return false;
  // Anything still queued means more of this acquisition is on its way.
  if (sourcesStillWaiting(viewer) > 0) return false;

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

  let flat: RenderViewport | null = null;
  let volume: RenderViewport | null = null;
  for (const panel of viewer.display?.panels ?? []) {
    if ("sliceView" in panel) flat = panel.renderViewport;
    else if ("sliceViews" in panel) volume = panel.renderViewport;
  }

  let fit = 0;
  drawn.slice(0, 2).forEach((axis, slot) => {
    const span = spanOf(axis);
    const pixels = slot === 0 ? flat?.logicalWidth : flat?.logicalHeight;
    if (!span || !pixels) return;
    fit = Math.max(fit, ((span[1] - span[0]) * render.canonicalVoxelFactors[slot]) / pixels);
  });
  if (fit > 0) viewer.navigationState.zoomFactor.value = fit * OVERVIEW_MARGIN;

  let boxFit = 0;
  const smaller = Math.min(volume?.logicalWidth ?? 0, volume?.logicalHeight ?? 0);
  drawn.slice(0, 3).forEach((axis, slot) => {
    const span = spanOf(axis);
    if (!span || !volume || !smaller) return;
    boxFit = Math.max(
      boxFit,
      ((span[1] - span[0]) * render.canonicalVoxelFactors[slot] * volume.logicalHeight)
        / smaller,
    );
  });
  if (boxFit > 0) {
    viewer.perspectiveNavigationState.zoomFactor.value = boxFit * OVERVIEW_MARGIN;
  }
  return true;
}

/**
 * Wait until every store the page asked for has been handed over and answered,
 * and the axes are known, then ``act`` -- once, in the very dispatch that brings
 * the last answer. ``ready`` is the caller's own condition, checked as well.
 *
 * Returns a function that stops the waiting.
 */
export function whenTheSourcesHaveSettled(viewer: Viewer, ready: () => boolean, act: () => void): () => void {
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
    sourcesStillWaiting(viewer) === 0 &&
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
export function chooseScaleWhenTheImagesAreMeasured(viewer: Viewer, framed: (() => void) | null = null): () => void {
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
  return whenTheSourcesHaveSettled(viewer, panelLaidOut, () => {
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
