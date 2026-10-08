// The styles the buttons along the top bar share: the 2-D/3-D toggle, Overview
// and the theme switch each sit in their own file, and the shell uses the
// plain button too, so the look of a button is settled once, here.
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
  // Beside the 2-D/3-D toggle rather than inside it: the toggle is a choice
  // between two states and this is an action, so it gets its own edge and never
  // looks like a third mode. Its place in the row comes from topBar above.
  bringItBack: {
    borderRadius: 6,
    border: "1px solid var(--panel-border)",
    boxShadow: "0 1px 4px var(--shadow-color)",
  },
  // Beside Overview, dressed the same way, placed by the same row.
  themeToggle: {
    borderRadius: 6,
    border: "1px solid var(--panel-border)",
    boxShadow: "0 1px 4px var(--shadow-color)",
  },
};
