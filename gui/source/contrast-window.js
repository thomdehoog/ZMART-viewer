// Where a channel's contrast window sits over its histogram, and how it is
// framed on screen.

// Where the window's two edges sit along the histogram, as fractions of its
// width. The stretch of brightness being shown takes the middle of the
// picture and leaves a seventh of it beyond each edge -- enough to see what
// is being clipped at both ends, and enough of a reference to adjust
// against. The operator asked for exactly this (2026-08-22): "the blue bars
// should be at fifteen and eighty five per cent along the histogram, so
// that's important to know for how to adjust the values."
export const WINDOW_SITS_FROM = 0.15;

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
export function frameTheWindow(window_, bounds = null) {
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

export function contrastRange(layer, window_, shown = null) {
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

/**
 * The spread of brightness in a channel, with the chosen window marked on it.
 *
 * This is the one picture in the panel that answers a question a microscopist
 * actually asks: is this channel saturating, or is it sitting on background?
 * The bars between the window's two marks are drawn at full light — that is
 * the brightness the display ramp is spent on — and the bars outside them are
 * dimmed: everything to the left saturates to black, everything to the right
 * to white.
 *
 * The two bars ARE the window, and only they move it: take hold of one — the
 * pointer has to be over it, a few pixels of grace either side — and pull,
 * and that edge of the window follows, the same window the MIN and MAX
 * sliders move, so the two controls can never disagree. A bar can never be
 * pulled past the image's own range: there are no pixels out there for a
 * black or white point to say anything about.
 *
 * Everywhere that is not a bar, the pointer works the AXIS instead — the
 * stretch of brightness being drawn, never the picture itself. Dragging pans
 * it; the wheel zooms it toward the pointer, out as far as the whole range
 * framed the usual way and no further. Auto puts the default framing back.
 * (All of this is the operator's design, 2026-08-23.)
 */
