// The colours a channel can be shown in, and how they are written: the small
// palette, the lookup-table gradients, and the conversions between the
// numbers the shader wants and the text a style sheet wants.

// A small, deliberately limited palette. Green and magenta lead because that is
// the pairing that reads best on a dark background and stays legible to a
// colour-blind viewer, unlike red/green.
export const PALETTE = [
  { name: "green", rgb: [0.0, 1.0, 0.4] },
  { name: "magenta", rgb: [1.0, 0.2, 1.0] },
  { name: "cyan", rgb: [0.2, 0.8, 1.0] },
  { name: "amber", rgb: [1.0, 0.75, 0.1] },
  { name: "blue", rgb: [0.3, 0.45, 1.0] },
  { name: "red", rgb: [1.0, 0.15, 0.15] },
  { name: "grey", rgb: null },
];

// A colour as the browser's own picker spells it, and back again. The picker
// hands over "#rrggbb"; everything else here keeps colours as three fractions,
// which is what the shader is given.

// A colour as the browser's own picker spells it, and back again. The picker
// hands over "#rrggbb"; everything else here keeps colours as three fractions,
// which is what the shader is given.
export const hexOf = (rgb) =>
  rgb
    ? `#${rgb.map((v) => Math.round(Math.min(1, Math.max(0, v)) * 255)
        .toString(16).padStart(2, "0")).join("")}`
    : "#ffffff";

export const rgbOf = (hex) => {
  const value = parseInt(hex.replace("#", ""), 16);
  return [(value >> 16 & 255) / 255, (value >> 8 & 255) / 255, (value & 255) / 255];
};

// What each colour map roughly looks like, as a little gradient. Drawn beside
// its name in the chooser and on the channel's row, so a map is chosen and
// recognised by eye. It used to be explained in words instead -- "magma"
// means nothing until you have seen one -- and showing the thing itself says
// it better than a sentence could.

// What each colour map roughly looks like, as a little gradient. Drawn beside
// its name in the chooser and on the channel's row, so a map is chosen and
// recognised by eye. It used to be explained in words instead -- "magma"
// means nothing until you have seen one -- and showing the thing itself says
// it better than a sentence could.
export const LUT_GRADIENTS = {
  viridis: "linear-gradient(90deg, #440154, #21918c, #fde725)",
  magma: "linear-gradient(90deg, #000004, #b73779, #fcfdbf)",
  fire: "linear-gradient(90deg, #000000, #e63b1f, #fff3c4)",
  ice: "linear-gradient(90deg, #000000, #3a6fd8, #ffffff)",
};

// The palette entry a stored rgb corresponds to: which flat colour the
// colormap chooser currently holds, or nothing at all when
// a colour was picked by hand and matches no named entry.

// The palette entry a stored rgb corresponds to: which flat colour the
// colormap chooser currently holds, or nothing at all when
// a colour was picked by hand and matches no named entry.
export const paletteNameOf = (rgb) =>
  (PALETTE.find((entry) => css(entry.rgb) === css(rgb)) || { name: null }).name;

export const css = (rgb) =>
  rgb ? `rgb(${rgb.map((v) => Math.round(v * 255)).join(",")})` : "#d8dee6";

// Log used to warp the brightness axis itself, which moved every bar and
// every handle sideways; it lifts the histogram's counts now instead, and the
// arithmetic for a warped axis went with it.

// -- the pieces the panel is drawn from ---------------------------------------
//
// A histogram, an eye, and the arithmetic that decides how far the contrast
// handles may travel. They are here rather than inside the panel below so that
// each can be read on its own, and because each of them answers a question a
// microscopist actually asks rather than a question about the interface.

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
 * operator's to set, in the two boxes beneath the histogram, and ``shown``
 * carries their answer when they have given one.
 *
 * The window in use is always included, so a window wider than the pixels --
 * one the run itself declared, say -- widens the track rather than leaving a
 * handle stranded off the end of it.
 *
 * And where a run declares the range its numbers live in, that is as far as
 * the boxes may be pushed: a twelve-bit camera cannot produce 5000, so an
 * axis drawn to it would be room that can never hold anything.
 */
// Where the window's two edges sit along the histogram, as fractions of its
// width. The stretch of brightness being shown takes the middle of the
// picture and leaves a seventh of it beyond each edge -- enough to see what
// is being clipped at both ends, and enough of a reference to adjust
// against. The operator asked for exactly this (2026-08-22): "the blue bars
// should be at fifteen and eighty five per cent along the histogram, so
// that's important to know for how to adjust the values."
