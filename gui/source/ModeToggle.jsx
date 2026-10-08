import React from "react";
import { topBarStyles as styles } from "./top-bar-styles.js";

// The two ways of looking at a volume, and the only thing the operator has to
// choose between. 2-D is the working view -- one plane, scroll through the
// stack. 3-D is for reading shape: the same data ray-cast, rotatable.
const MODES = { flat: "2D", volume: "3D" };

function ModeToggle({ mode, onChange }) {
  return (
    <div style={styles.toggle}>
      {Object.entries(MODES).map(([key, label]) => (
        <button
          key={key}
          onClick={() => onChange(key)}
          style={{ ...styles.button, ...(mode === key ? styles.buttonActive : null) }}
          title={key === "flat" ? "One plane; scroll to move through z" : "Ray-cast volume; drag to rotate"}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

export default ModeToggle;
