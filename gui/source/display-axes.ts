// Which image axes are on screen in each view, and whether the display is
// stretched unevenly along them.

/** How the picture is looked at: one plane, or the volume. */
export type Mode = "flat" | "volume";

/** How much each axis is stretched on screen; 1 draws it as measured. */
export type DisplayScales = Record<"x" | "y" | "z", number>;

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
function axesOnScreen(mode: Mode): ("x" | "y" | "z")[] {
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
export function stretchedUnevenly(displayScales: DisplayScales, mode: Mode): boolean {
  const onScreen = axesOnScreen(mode).map((axis) => displayScales[axis]);
  return onScreen.some((factor) => factor !== onScreen[0]);
}
