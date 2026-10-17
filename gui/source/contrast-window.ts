// Where a channel's contrast window sits over its histogram, and how it is
// framed on screen.

/** A stretch of brightness: the stored values from `low` to `high`. */
export interface Window {
  low: number;
  high: number;
}

// Where the window's two edges sit along the histogram, as fractions of its
// width. The stretch of brightness being shown takes the middle of the
// picture and leaves a seventh of it beyond each edge -- enough to see what
// is being clipped at both ends, and enough of a reference to adjust
// against. The operator asked for exactly this (2026-08-22): "the blue bars
// should be at fifteen and eighty five per cent along the histogram, so
// that's important to know for how to adjust the values."
const WINDOW_SITS_FROM = 0.15;

/**
 * The stretch of brightness to draw so that a window sits across the middle.
 *
 * Given the window a channel was handed -- the one its file declared when it
 * opened, or the one Auto just measured -- this says how far the picture
 * beneath it should reach on either side. The answer is only ever taken when
 * a window is GIVEN, never while a handle is being dragged: an axis that
 * moved with the handle would mean the operator could never see what their
 * drag had done.
 *
 * The bars AIM at fifteen and eighty five per cent, and the axis never
 * shows a value the data cannot hold. Those two wishes met in this order:
 * first the axis was clamped at zero, which pinned the min bar to the left
 * edge for exactly the dim channels Auto is most used on; then the framing
 * was made exact and unclamped (2026-08-23), and a wide window promptly
 * put "-7915" and "77099" under a sixteen-bit histogram — numbers that do
 * not exist. The operator settled it (2026-08-24): fifteen and eighty five
 * are the aim, the possible range is the wall, and where the two collide
 * the wall wins — a bar sitting off its mark is a smaller lie than an
 * axis reaching brightness no camera produced.
 */
export function frameTheWindow(window_: Window | null | undefined, bounds: Window | null = null): Window | null {
  if (!window_ || !(window_.high > window_.low)) return null;
  const across = (window_.high - window_.low) / (1 - 2 * WINDOW_SITS_FROM);
  const beyond = across * WINDOW_SITS_FROM;
  return {
    low: bounds ? Math.max(bounds.low, window_.low - beyond)
      : window_.low - beyond,
    high: bounds ? Math.min(bounds.high, window_.high + beyond)
      : window_.high + beyond,
  };
}

/**
 * The window with one edge moved to `value`.
 *
 * The edge stays inside the image's own range: a black or white point beyond
 * the pixels would describe brightness that does not exist. And the two edges
 * stay at least one count apart: a window of no width makes every value in the
 * image land on the same shade, so the picture goes flat and it is not obvious
 * why. The histogram's bars and the MIN and MAX controls move the window
 * through here alike.
 */
export function withEdgeAt(window_: Window, edge: "low" | "high", value: number, range: Window): Window {
  const within = Math.min(Math.max(value, range.low), range.high);
  return edge === "low"
    ? { low: Math.min(within, window_.high - 1), high: window_.high }
    : { low: window_.low, high: Math.max(within, window_.low + 1) };
}

/**
 * How far the black and white handles are allowed to travel.
 *
 * From the dimmest pixel in the channel to the brightest, and no further. An
 * axis drawn to what the camera COULD have written instead puts a real
 * specimen in the first few per cent of the track -- a few hundred counts of
 * background inside sixty-five thousand -- and leaves the rest as headroom
 * nothing occupies, so a whole slider becomes two pixels of useful travel.
 * That was tried both ways within a day: measured-span, then the camera's
 * range with a logarithmic axis to make it usable, and now the data again
 * with the axis SAID rather than guessed. Which part of it is drawn is the
 * operator's to set, in the two boxes beneath the histogram, and `shown`
 * carries their answer when they have given one.
 *
 * The window in use is always included, so a window wider than the pixels --
 * one the run itself declared, say -- widens the track rather than leaving a
 * handle stranded off the end of it.
 */
export function contrastRange(
  layer: { histogram?: Window | null }, window_: Window, shown: Window | null = null,
): { min: number; max: number } {
  const measured = layer.histogram;
  let min = 0;
  let max = 65535;
  if (measured && Number.isFinite(measured.low) && measured.high > measured.low) {
    min = Math.floor(measured.low);
    max = Math.ceil(measured.high);
  }
  if (shown && Number.isFinite(shown.low) && shown.high > shown.low) {
    // Taken as given, even where it runs past what the run declared or below
    // zero: the framing promises the window's bars a fixed pair of places on
    // the picture, and an axis trimmed back to the declared range would move
    // them (see frameTheWindow).
    //
    // Rounded outward to whole numbers, though, and that is not cosmetic. The
    // MIN and MAX sliders below can only rest on whole steps counted from this
    // axis's left end, so a left end at a fraction -- which frameTheWindow
    // freely produces -- put every position the handle could take at a
    // fraction too: an operator asking for 100 was quietly given 100.5
    // (watched 2026-08-23). Widening by under one grey level moves nothing an
    // eye can see, and it lets the sliders land exactly where they are put.
    return { min: Math.floor(shown.low), max: Math.ceil(shown.high) };
  }
  return {
    min: Math.min(min, Math.floor(window_.low)),
    max: Math.max(max, Math.ceil(window_.high), min + 1),
  };
}
