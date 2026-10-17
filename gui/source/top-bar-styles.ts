// The styles the buttons along the top bar share: the 2-D/3-D toggle, Overview
// and the theme switch each sit in their own file, and the shell uses the
// plain button too, so the look of a button is settled once, here.

import type { CSSProperties } from "react";

export const topBarStyles = {
  toggle: {
    display: "flex",
    borderRadius: 6,
    overflow: "hidden",
    border: "1px solid var(--panel-border)",
    boxShadow: "0 1px 4px var(--shadow-color)",
  },
  button: {
    padding: "6px 14px",
    border: "none",
    background: "var(--control-bg)",
    color: "var(--text-muted)",
    font: "600 12px/1 system-ui, sans-serif",
    cursor: "pointer",
  },
  buttonActive: { background: "var(--accent)", color: "#fff" },
  // A button that stands on its own beside the 2-D/3-D toggle -- Overview, the
  // theme switch. The toggle is a choice between two states and these are
  // actions, so each gets its own edge and never looks like a third mode.
  action: {
    borderRadius: 6,
    border: "1px solid var(--panel-border)",
    boxShadow: "0 1px 4px var(--shadow-color)",
  },
} satisfies Record<string, CSSProperties>;
