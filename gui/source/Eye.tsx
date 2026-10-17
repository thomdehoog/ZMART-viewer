import { styles } from "./layer-panel-styles.ts";

interface EyeProps {
  open: boolean;
}

/** A drawn eye, open or crossed out — the show/hide idea every biologist knows. */
function Eye({ open }: EyeProps) {
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

export default Eye;
