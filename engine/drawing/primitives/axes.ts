/**
 * The axes: which ones are drawn, where along one the view stands, and how to
 * step it.
 *
 * An image says what each of its axes measures. Some are places in the
 * specimen -- depth, height, width -- and some are not: which moment this is,
 * or which colour of light was recorded. Only the first kind belongs on
 * screen; the others are stepped. Which is which is read from the units an
 * image declares, never guessed from names.
 */

import type { CoordinateSpace } from "neuroglancer/unstable/coordinate_transform.js";
import type { Viewer } from "neuroglancer/unstable/viewer.js";

/**
 * Draw the axes that measure distance, and keep drawing them as the picture's
 * axes change. Returns a function that stops keeping them.
 *
 * Left alone, the engine takes the first three axes the image declares, in the
 * order they were written. For an image of a single moment those happen to be
 * depth, height and width and everything looks right -- which is exactly what
 * made this hard to see. Add a time axis at the front, as every timelapse has,
 * and the engine draws time against depth and pins width, so the specimen is
 * nowhere on screen. Nothing reports a fault; the picture is simply black.
 */
export function keepSpatialAxes(viewer: Viewer): () => void {
  const place = () => pinTheAxesThatMeasureDistance(viewer);
  const stop = viewer.navigationState.position.coordinateSpace.changed.add(place);
  place();
  return stop;
}

/**
 * Hand the engine the axes measured in metres, width first and depth last.
 *
 * "m" is the only unit that measures distance across a specimen; seconds do
 * not, and an axis with no unit does not. The *order* matters as much: the
 * engine decides what goes across the window, what down it and what into the
 * screen from this order and the panel layout together, and the layout a page
 * asks for (`xy`) expects width, height, depth. An OME-Zarr image lists its
 * distance axes slowest-changing first -- depth, height, width -- so they are
 * reversed. The other way round, the picture was a mirror image of the
 * specimen and nothing said so (the viewer's
 * `tests/test_the_picture_is_not_mirrored.py` measures it).
 *
 * An image declaring fewer than two such axes is left alone: replacing a
 * possibly-odd view with a certainly-wrong one is not an improvement.
 */
export function pinTheAxesThatMeasureDistance(viewer: Viewer): void {
  const space = viewer.navigationState.position.coordinateSpace.value;
  if (!space?.names) return;
  const distances = space.names.filter((_, at) => space.units?.[at] === "m");
  if (distances.length < 2) return;
  const facing = distances.every(name => ["x", "y", "z"].includes(name))
    ? ["x", "y", "z"].filter(name => distances.includes(name))
    : distances.slice(0, 3).reverse();
  const current = viewer.navigationState.pose.displayDimensions.value;
  // Left alone when it is already right. Handing the engine the same answer it
  // already had is not free: it counts as a change, and everything that
  // follows the view is asked to catch up with a difference that does not exist.
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

/** One axis of the picture: where it can be stepped, and where the view stands. */
export interface AxisReading {
  index: number;
  /** The first and last step there is room for. */
  min: number;
  max: number;
  value: number;
  /** How many micrometres one step is. */
  unitUm: number;
}

/**
 * Read one named axis: where it can be stepped, and where the view stands on
 * it. Null when the picture has no such axis -- the normal answer for `t` on
 * anything that is not a timelapse.
 *
 * The engine describes an axis by the real-valued extent the data covers, and
 * says separately whether a whole step sits *on* an integer coordinate or half
 * way between two. Both conventions occur, and a step has to land exactly on
 * the planes that exist -- one too few and the last plane of every stack is
 * unreachable, one too many and the view runs off the end of the data. When
 * centres are on integers the extent comes back shifted by half a step (a
 * 48-plane stack reads -0.5 to 47.5), so the steps are the integers inside it;
 * otherwise they are the half-integers inside it.
 */
export function readTheAxis(viewer: Viewer | null, name: string): AxisReading | null {
  const position = viewer?.navigationState.position;
  const space = position?.coordinateSpace.value;
  if (!position || !space?.valid) return null;
  const index = space.names.indexOf(name);
  if (index < 0) return null;
  const { min, max } = stepsAlong(space, index);
  if (!Number.isFinite(min) || !Number.isFinite(max) || max < min) return null;
  return { index, min, max, value: position.value[index], unitUm: space.scales[index] * 1e6 };
}

/** The first and last step along one axis of a space; see `readTheAxis`. */
function stepsAlong(space: CoordinateSpace, at: number): { min: number; max: number } {
  const onWholeNumbers = space.bounds.voxelCenterAtIntegerCoordinates[at];
  const lower = space.bounds.lowerBounds[at];
  const upper = space.bounds.upperBounds[at];
  return onWholeNumbers
    ? { min: Math.ceil(lower), max: Math.floor(upper) }
    : { min: Math.ceil(lower - 0.5) + 0.5, max: Math.floor(upper - 0.5) + 0.5 };
}

/** Move the view along one axis to `value`, leaving every other axis where it is. */
export function moveAlong(viewer: Viewer, index: number, value: number): void {
  const position = viewer.navigationState.position;
  if (position.value[index] === value) return;
  const moved = Float32Array.from(position.value);
  moved[index] = value;
  position.value = moved;
}

/**
 * Open a timelapse at its first moment rather than half way through it.
 *
 * The engine opens every axis in the middle, which is right for depth and
 * wrong for time: a recording is watched from the beginning, and a run still
 * being written declares room for moments not yet imaged, so its middle is a
 * black screen with the data sitting unseen at the start. The time axis is
 * the one measured in seconds.
 */
export function startTimeAtTheFirstMoment(viewer: Viewer): void {
  const space = viewer.navigationState.position.coordinateSpace.value;
  if (!space?.names || !space.units) return;
  const at = Array.from(space.units).indexOf("s");
  if (at < 0) return;
  const { min } = stepsAlong(space, at);
  if (Number.isFinite(min)) moveAlong(viewer, at, min);
}
