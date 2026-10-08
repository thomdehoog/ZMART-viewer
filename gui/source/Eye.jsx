import React from "react";
import { styles } from "./layer-panel-styles.js";

/** A drawn eye, open or crossed out — the show/hide idea every biologist knows. */
function Eye({ open }) {
  return (
    <svg viewBox="0 0 16 16" style={styles.eyeGlyph} aria-hidden="true">
      <path
        d="M1 8s2.6-4.2 7-4.2S15 8 15 8s-2.6 4.2-7 4.2S1 8 1 8z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
      />
      <circle cx="8" cy="8" r="1.9" fill="currentColor" />
      {!open && <path d="M2.5 13.5L13.5 2.5" stroke="currentColor" strokeWidth="1.3" />}
    </svg>
  );
}


// -- the controls, and the list they act on -----------------------------------

/**
 * The one block of controls, acting on whichever channel is picked out in the list.
 *
 * There is a single copy of this rather than one per channel. That is partly for
 * room — with sliders on every row, three channels filled a tall screen — and
 * partly because it matches how the work goes: you look at one channel, set it up,
 * then move to the next. It is the arrangement napari uses, and anyone who has
 * used napari will already know where to look.
 */
/**
 * How a ray through the volume becomes a colour.
 *
 * Only shown in the volume view, because it means nothing in a flat one. The
 * default is a projection rather than accumulation: on sparse specimen -- which
 * is what a fluorescence run is -- accumulating every voxel a ray passes gives a
 * milky picture with almost no contrast, and a projection needs no transparency
 * tuned before anything can be seen.
 */

export default Eye;
