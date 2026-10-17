// The colours a channel can be shown in, and how they are written: the small
// palette, the lookup-table gradients, and the conversions between the
// numbers the shader wants and the text a style sheet wants.

/** A colour as the shader is given it: red, green and blue, each 0 to 1. */
export type Rgb = readonly [number, number, number];

// A small, deliberately limited palette. Green and magenta lead because that is
// the pairing that reads best on a dark background and stays legible to a
// colour-blind viewer, unlike red/green.
export const PALETTE: readonly { name: string; rgb: Rgb | null }[] = [
  { name: "green", rgb: [0.0, 1.0, 0.4] },
  { name: "magenta", rgb: [1.0, 0.2, 1.0] },
  { name: "cyan", rgb: [0.2, 0.8, 1.0] },
  { name: "amber", rgb: [1.0, 0.75, 0.1] },
  { name: "blue", rgb: [0.3, 0.45, 1.0] },
  { name: "red", rgb: [1.0, 0.15, 0.15] },
  { name: "grey", rgb: null },
];

// A colour as the browser's own picker hands it over, "#rrggbb", back into the
// three fractions everything else keeps. (The other way is `hexColour`, in the
// engine's drawing programs, which the shader's controls spell colours with.)
export const rgbOf = (hex: string): Rgb => {
  const value = parseInt(hex.replace("#", ""), 16);
  return [(value >> 16 & 255) / 255, (value >> 8 & 255) / 255, (value & 255) / 255];
};

// What each colour map roughly looks like, as a little gradient. Drawn beside
// its name in the chooser and on the channel's row, so a map is chosen and
// recognised by eye. It used to be explained in words instead -- "magma"
// means nothing until you have seen one -- and showing the thing itself says
// it better than a sentence could.
export const LUT_GRADIENTS: Readonly<Record<string, string>> = {
  viridis: "linear-gradient(90deg, #440154, #21918c, #fde725)",
  magma: "linear-gradient(90deg, #000004, #b73779, #fcfdbf)",
  fire: "linear-gradient(90deg, #000000, #e63b1f, #fff3c4)",
  ice: "linear-gradient(90deg, #000000, #3a6fd8, #ffffff)",
};

/** A colour as a style sheet writes it; a channel with none is drawn in the page's pale grey. */
export const css = (rgb: Rgb | null | undefined): string =>
  rgb ? `rgb(${rgb.map((v) => Math.round(v * 255)).join(",")})` : "#d8dee6";
