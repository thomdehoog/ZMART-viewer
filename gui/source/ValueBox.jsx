import React from "react";
import { styles } from "./layer-panel-styles.js";

/**
 * The number beside a slider, as a box that can be typed into.
 *
 * While untouched it simply shows the slider's value. Start typing and it
 * holds your draft until Enter or leaving the box commits it; a draft that
 * is not a number is quietly dropped and the real value comes back. So box
 * and slider always describe the same setting, whichever one moved last.
 */
function ValueBox({ value, onCommit, label, suffix = "", align = "right" }) {
  const [draft, setDraft] = React.useState(null);
  const commit = () => {
    if (draft !== null) {
      const asked = Number(draft.replace(suffix, ""));
      if (Number.isFinite(asked)) onCommit(asked);
    }
    setDraft(null);
  };
  return (
    <input
      type="text"
      inputMode="numeric"
      value={draft ?? `${value}${suffix}`}
      onChange={(event) => setDraft(event.target.value)}
      onFocus={(event) => event.target.select()}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === "Enter") event.currentTarget.blur();
      }}
      aria-label={label}
      style={{ ...styles.valueBox, textAlign: align }}
    />
  );
}

/** A drawn eye, open or crossed out — the show/hide idea every biologist knows. */

export default ValueBox;
