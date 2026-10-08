import React from "react";
import { putTheViewBack } from "./drawing/neuroglancer.js";
import { topBarStyles as styles } from "./top-bar-styles.js";

/**
 * The way back to the whole picture, from wherever the operator has wandered.
 *
 * One press centres the picture and zooms so all of it fits the window --
 * panned off the screen, lost deep in detail, turned onto its side, or all
 * three. It sits beside the 2-D/3-D toggle rather than in the panel because
 * it is needed at exactly the moment the panel is no use: the operator
 * cannot see the picture, and the control that would bring it back must be
 * visible without anything being opened first.
 *
 * It puts the whole view back -- straight, centred, sized to the window --
 * and so it means the same thing in both views. There was briefly a second
 * button, Reset, that did the straightening while this one only fitted; one
 * control that behaves the same way everywhere is what an operator can
 * actually rely on, and two that differ only in 3-D is a distinction the
 * button faces cannot carry (2026-08-20).
 *
 * What it does not touch is where the operator is in depth or in time. That
 * is which picture they are looking at, not how they are looking at it.
 */
function BringItBack({ viewer }) {
  if (!viewer) return null;
  return (
    <button
      onClick={() => putTheViewBack(viewer)}
      style={{ ...styles.button, ...styles.bringItBack }}
      title="Put the view back as it opened: straight, centred and sized to the window. The plane and the moment stay where they are"
    >
      Overview
    </button>
  );
}

export default BringItBack;
