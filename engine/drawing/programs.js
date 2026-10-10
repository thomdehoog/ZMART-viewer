/**
 * The little programs that turn a stored value into a colour on screen.
 *
 * Neuroglancer draws every image layer through a short shader program, and the
 * look of a ZMART picture lives in these: a window that maps the stored numbers
 * onto brightness, a colour or a colour map, how solid a channel is, and the
 * rule that only a pixel nobody ever wrote is left see-through. Operators have
 * learned to read their specimens by this look, so there is one copy of it.
 *
 * Two pages draw with it: the viewer's own window, and the operator page of
 * ZMART-interface, whose build reads this file from the installed viewer. It
 * imports nothing and touches no browser, which is what lets both builds take
 * it as it is. Change it here and both pages change together; that is the
 * point, because a copy kept in line by hand drifted.
 */

// A lookup table paints a single channel in a run of colours rather than one flat
// colour: dim values one shade, bright values another, with a smooth path between.
// On a single channel that reads far more detail than a plain brightness ramp,
// which is why it is a staple of microscopy display.
//
// These are written as the small shader program the engine already compiles for
// every layer, so nothing new runs anywhere -- the colour is decided on the
// graphics card exactly as a flat colour is. The stops are the well-known
// perceptually-even maps: evenly spaced in apparent brightness, so equal steps in
// the data look like equal steps on screen, and none of them is red-green.
export const LOOKUP_TABLES = {
  viridis: [[0.267,0.005,0.329],[0.190,0.407,0.556],[0.208,0.718,0.473],[0.993,0.906,0.144]],
  magma:   [[0.001,0.000,0.014],[0.443,0.122,0.507],[0.925,0.412,0.372],[0.987,0.991,0.750]],
  fire:    [[0.000,0.000,0.000],[0.600,0.100,0.000],[1.000,0.650,0.000],[1.000,1.000,0.900]],
  ice:     [[0.000,0.000,0.100],[0.000,0.400,0.700],[0.400,0.800,1.000],[1.000,1.000,1.000]],
};

// The colour maps on offer, worked out once. Building this list inside the render
// would hand the panel a brand-new array every time anything at all changed, and
// the panel would have to assume the choices had changed with it.
export const LOOKUP_TABLE_NAMES = Object.keys(LOOKUP_TABLES);

// Turn a table of colour stops into the few lines of shader that walk between
// them. Straight-line blending between neighbouring stops is enough: the stops are
// chosen so that is faithful, and it keeps the generated program small.
export function lutShader(stops, name = "zmartLut") {
  const literal = (c) => `vec3(${c.map((v) => v.toFixed(4)).join(",")})`;
  let body = `  vec3 c = ${literal(stops[0])};\n`;
  const step = 1.0 / (stops.length - 1);
  for (let i = 1; i < stops.length; i++) {
    const lo = ((i - 1) * step).toFixed(4);
    body += `  c = mix(c, ${literal(stops[i])}, clamp((v - ${lo}) / ${step.toFixed(4)}, 0.0, 1.0));\n`;
  }
  return `vec3 ${name}(float v) {\n${body}  return c;\n}\n`;
}

// A colour as the engine's own control expects it. The engine parses the same
// specifications a stylesheet does and keeps the value as a uniform, so this is
// simply how a colour is spelled on the way over.
export function hexColour(rgb) {
  if (!rgb) return "#ffffff";
  const part = (value) =>
    Math.round(Math.min(1, Math.max(0, value)) * 255)
      .toString(16)
      .padStart(2, "0");
  return `#${part(rgb[0])}${part(rgb[1])}${part(rgb[2])}`;
}


// Real acquisitions occupy a narrow band of the 16-bit range, so without an
// explicit window they render black. In 3-D the intensity drives opacity as
// well, or every background voxel along the ray adds haze and the specimen is
// lost in fog.
//
// **What is not written into the text below**: the window, the colour and the
// weight. Every one of them is declared as a control and sent as a number to a
// program the engine has already compiled, so choosing a colour or dragging a
// handle costs one number handed to the graphics card -- no rebuild, and
// nothing re-read from disk, since what has been downloaded is keyed by the
// store and the piece and knows nothing of how it is being shown. The colour
// used to be written into the text, which compiled a fresh program per colour
// and gave every channel a program of its own; the engine's own multichannel
// display has always used a control (`#uicontrol vec3 color`) and so does this.
//
// **Channels are separate layers, and that is the engine's arrangement rather
// than ours.** A brightness control can be bound to a named channel of the data
// -- but only where the data declares CHANNEL dimensions, and an OME-Zarr `c`
// axis arrives as a LOCAL dimension instead (measured 2026-08-20: the layer
// reports `c\'` local and a channel rank of nought, so `channel=[1]` will not
// even parse). A local dimension is pinned to one position per layer, so no
// single program can read two channels of it. This is exactly why neuroglancer
// splits a multichannel volume into one layer per channel too, and why the
// channels are added by the engine between layers rather than by a program
// within one.
//
// **Gamma is offered only when asked for.** It bends the curve between the
// window's two ends: below one the dim end takes more of the screen, which is
// what a picture with a few bright objects and faint structure between them
// needs. The operator page offers it; the viewer's own window does not yet, and
// leaves it out rather than sending a gamma of one, because `pow(v, 1.0)` is
// not always exactly `v` on a graphics card and the picture would shift by a
// level here and there for nothing.
export function shaderFor(volumetric, lut = null, opaque = false, { gamma = false } = {}) {
  const stops = lut ? LOOKUP_TABLES[lut] : null;
  const declared = [
    "#uicontrol invlerp normalized",
    "#uicontrol float weight slider(min=0, max=1, default=1)",
  ];
  if (gamma) declared.push("#uicontrol float gamma slider(min=0.1, max=4, default=1)");
  declared.push(
    // The raw value, untouched by the window: mapped over a fixed nought-to-
    // one range without clamping, so ``imaged()`` simply answers the pixel's
    // own number. It exists for one question only — was anything written
    // here at all — which the window must have no say in.
    "#uicontrol invlerp imaged(range=[0, 1], clamp=false)",
  );
  if (!stops) declared.push('#uicontrol vec3 color color(default="white")');
  if (volumetric) {
    declared.push("#uicontrol float attenuation slider(min=0, max=8, default=0)");
  }
  const lines = [...declared];
  if (stops) lines.push(lutShader(stops));
  lines.push("void main() {", gamma ? "  float v = pow(normalized(), gamma);" : "  float v = normalized();");
  // A colour map already carries the brightness in its colour, which is what a
  // colour map is; a flat colour has to be multiplied by it.
  lines.push(stops
    ? "  vec3 shown = zmartLut(v) * weight;"
    : "  vec3 shown = color * (v * weight);");
  if (volumetric) {
    // Fading the far side of the specimen away, which the engine does not offer
    // and napari calls an attenuated projection. `emitIntensity` is what decides
    // the contest in a projection -- the engine keeps whichever voxel along the
    // ray reports the largest value -- so the weight belongs in it as well, or a
    // channel turned down would still win the ray and then be drawn dim.
    lines.push(
      "  float faded = exp(-attenuation * depthAtRayPosition);",
      "  emitIntensity(v * weight * faded);",
      "  emitRGBA(vec4(shown * faded, v * weight * faded));",
      "}");
  } else if (opaque) {
    // Dense position stores are acquired throughout their declared extent,
    // including exact-zero pixels. No coverage texture is needed.
    lines.push("  emitRGBA(vec4(shown, 1.0));", "}");
  } else {
    // Brightness rides in the colour and coverage rides in the transparency,
    // and they answer two different questions: how bright is this spot, and
    // was it imaged at all. A row that is ADDED to its neighbours needs the
    // second one only so that ground nobody imaged contributes nothing; a
    // row that COVERS needs it so an empty corner does not black out the
    // picture beneath.
    //
    // Coverage is read from the RAW value now, never from the windowed one.
    // For a day it was read from the window, and raising MIN above real but
    // dim ground punched a see-through hole in the picture where the
    // dimmest pixels lived — the operator watched it happen (2026-08-23,
    // closing the limit recorded 2026-08-11). The display does exactly what
    // a window means: below MIN clamps to black, above MAX to full, and
    // only a pixel holding nothing at all — a true zero, the fill of ground
    // never written — is not drawn.
    lines.push("  emitRGBA(vec4(shown, imaged() > 0.0 ? 1.0 : 0.0));", "}");
  }
  return `${lines.join("\n")}\n`;
}

// The values for the controls declared above. These reach the graphics card
// without the program being touched, which is why contrast is smooth to drag.
//
// `gamma` is sent only to a program that declares it (see `shaderFor`): a
// number, or `null` for a program without one.
export function shaderControlsFor(window_, volumetric, weight, colour,
                                  lut = null, attenuation = 0, gamma = null) {
  if (!window_) return undefined;
  const controls = { normalized: { range: [window_.low, window_.high] } };
  // The weight is what an eye and an opacity slider both come to: nought hides
  // the channel, one shows it whole, and everything between fades it. Sent as a
  // number rather than as a switch on purpose -- the engine treats a checkbox as
  // part of a program's identity and would recompile on every glance, while a
  // number reaches the program already running.
  controls.weight = weight;
  // A channel painted through a colour map has no flat colour to send.
  if (!lut) controls.color = hexColour(colour);
  if (volumetric) controls.attenuation = attenuation;
  if (gamma !== null) controls.gamma = gamma;
  return controls;
}
