// Which image axes are on screen in each view, and whether the display is
// stretched unevenly along them.

/**
 * The axes the operator can actually see, which is what decides whether one
 * scale bar can be true.
 *
 * A bar states one distance per screen pixel, so it stays honest only while every
 * axis on screen is drawn at the same stretch. Which axes those are is a property
 * of the *view* rather than of the data, and that is the whole reason this cannot
 * be decided from the stretch factors alone.
 *
 * A single plane shows x and y and leaves depth off screen entirely, so
 * stretching z there — the ordinary thing to do with anisotropic data — cannot
 * make the bar wrong, and warning about it would cry wolf on the common case.
 * Turning the volume on puts z on screen beside them and the same stretch now
 * does make it wrong: measured on the demo volume with z quadrupled, ten
 * micrometres covers about 201 screen pixels along z against 50 along x, so a bar
 * reading either one is off by four for the other. The operator changed nothing
 * but what they were looking at.
 */
export function axesOnScreen(mode) {
  return mode === "volume" ? ["x", "y", "z"] : ["x", "y"];
}

/**
 * Whether the axes on screen are drawn at stretches that disagree.
 *
 * Compared against each other rather than against 1, because stretching every
 * axis alike is a zoom: it changes how large the specimen is drawn and not its
 * proportions, so one bar still describes it perfectly. Only a difference between
 * two axes that are both on screen shears the picture, and then it is 20 µm wide
 * and 30 µm tall per the same bar. Fiji and napari avoid this by not offering the
 * control at all. We offer it and say so.
 *
 * The bar itself follows a stretch rather than ignoring one — it divides by the
 * engine's `canonicalVoxelFactors`, which are computed from these very factors —
 * so this is a warning that no single number can be right, not that the bar has
 * been left stale.
 */

/**
 * Whether the axes on screen are drawn at stretches that disagree.
 *
 * Compared against each other rather than against 1, because stretching every
 * axis alike is a zoom: it changes how large the specimen is drawn and not its
 * proportions, so one bar still describes it perfectly. Only a difference between
 * two axes that are both on screen shears the picture, and then it is 20 µm wide
 * and 30 µm tall per the same bar. Fiji and napari avoid this by not offering the
 * control at all. We offer it and say so.
 *
 * The bar itself follows a stretch rather than ignoring one — it divides by the
 * engine's `canonicalVoxelFactors`, which are computed from these very factors —
 * so this is a warning that no single number can be right, not that the bar has
 * been left stale.
 */
export function stretchedUnevenly(displayScales, mode) {
  const onScreen = axesOnScreen(mode).map((axis) => displayScales[axis]);
  return onScreen.some((factor) => factor !== onScreen[0]);
}

/**
 * The square a channel is painted in, which is also how the colour is chosen.
 *
 * Press it and a list opens showing every choice as it actually looks: a
 * block for a flat colour, and the whole run of colours for a map. That is
 * the point of drawing them. A plain dropdown can only offer words, and the
 * word "magma" tells a microscopist meeting it nothing at all -- the version
 * before this one had to spell out "(black -> purple -> cream)" beside every
 * map and still left an operator picking by trial.
 *
 * "Custom" heads the list and opens the browser's own colour picker, because
 * an operator who wants a particular colour is not going to find it among
 * the named ones.
 *
 * It is a list of buttons rather than a select because a select cannot draw
 * anything but text. That costs the keyboard nothing: each entry is a real
 * button, so tabbing walks them and Enter picks, and Escape closes the list.
 */
