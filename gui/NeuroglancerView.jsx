import React from "react";
import { createViewer } from "../engine/drawing/viewer.js";

/**
 * Mounts the engine's neuroglancer viewer in this window and hands the live
 * `viewer` back through `onViewer`. The engine creates and disposes the
 * viewer (see engine/drawing/viewer.js); this component only gives it an
 * element to draw into and ties its lifetime to React's.
 *
 * The effect is written to survive React StrictMode's deliberate mount →
 * dispose → mount in development, so do not be surprised to see the viewer
 * built twice under `vite dev`.
 *
 * ``generation`` is how the interface asks for a *new* viewer rather than a
 * changed one. Closing an acquisition is the only thing that asks: with the
 * layer gone, its sources rebuilt and every piece fetched again, the next
 * acquisition still came up with most of its tiles unpainted, and the only
 * thing that ever put it right was a fresh drawing context (measured
 * 2026-08-21). So a close builds one.
 */
export default function NeuroglancerView({ onViewer, generation = 0, veiled = false }) {
  const containerRef = React.useRef(null);

  React.useEffect(() => {
    const target = containerRef.current;
    if (!target) return undefined;
    const { viewer, dispose } = createViewer(target);
    onViewer?.(viewer);
    return dispose;
  }, [onViewer, generation]);

  // Size the mount with width/height rather than absolute insets: neuroglancer
  // sets `position: relative` on this element itself, which would cancel any
  // inset-based sizing and collapse it to zero height. Filling the (already
  // sized) parent sidesteps that entirely.
  //
  // The black stays black in the light theme too, on purpose. Fluorescence
  // is read against black -- at the microscope and in every viewer -- and a
  // light ground here would change what a dim signal looks like. Only the
  // chrome around the image follows the theme.
  //
  // While ``veiled`` the drawing is held at opacity zero over the bare
  // ground: the engine's first frames come out at its own default
  // magnification before the fit-to-window lands, and showing them read as
  // the picture jumping in size on every first open. The ground underneath
  // means the veil looks like an empty canvas, and the short fade makes the
  // arrival read as the picture appearing rather than snapping.
  return (
    <div style={{ width: "100%", height: "100%", background: "var(--image-surface-bg, var(--canvas-bg, #000))" }}>
      <div
        ref={containerRef}
        style={{
          width: "100%",
          height: "100%",
          background: "var(--image-surface-bg, var(--canvas-bg, #000))",
          opacity: veiled ? 0 : 1,
          transition: "var(--image-arrival-transition, opacity 120ms linear)",
        }}
      />
    </div>
  );
}
