import { styles } from "./layer-panel-styles.ts";

/** A drawn eye, open or crossed out — the show/hide idea every biologist knows. */
function Eye({ open }: { open: boolean }) {
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

interface EyeButtonProps {
  open: boolean;
  onClick: () => void;
  label: string;
  /** What it shows or hides, as its title names it. */
  what: "channel" | "acquisition";
}

/** The eye as a button: pressed, it shows or hides what it sits beside. */
function EyeButton({ open, onClick, label, what }: EyeButtonProps) {
  return (
    <button
      onClick={onClick}
      style={{ ...styles.eye, opacity: open ? 1 : 0.4 }}
      title={`${open ? "Hide" : "Show"} this ${what}`}
      aria-label={label}
    >
      <Eye open={open} />
    </button>
  );
}

export default EyeButton;
