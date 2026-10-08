import React from "react";
import FilledRange from "./FilledRange.jsx";
import { styles } from "./layer-panel-styles.js";

/**
 * How a ray through the volume becomes a colour.
 *
 * Only shown in the volume view, because it means nothing in a flat one. The
 * default is a projection rather than accumulation: on sparse specimen -- which
 * is what a fluorescence run is -- accumulating every voxel a ray passes gives a
 * milky picture with almost no contrast, and a projection needs no transparency
 * tuned before anything can be seen.
 */
function VolumeMode({ volumeMode, onVolumeMode, gain, onGain,
                     attenuation, onAttenuation,
                     depthSamples, onDepthSamples,
                     displayScales, onDisplayScales }) {
  const accumulating = volumeMode === "on";
  return (
    <>
    <label style={styles.control}>
      <span
        style={styles.controlLabel}
        title="How the voxels along each line of sight are combined into one pixel"
      >
        projection
      </span>
      <select
        value={volumeMode}
        onChange={(event) => onVolumeMode?.(event.target.value)}
        aria-label="volume projection"
        title="Brightest keeps the brightest voxel along each ray -- a maximum-intensity projection (MIP), which is how most fluorescence is looked at. Accumulated adds every voxel up instead."
        // The row has no value readout, so the dropdown may take the value
        // column too -- its option names are long.
        style={{ ...styles.select, gridColumn: "2 / -1" }}
      >
        <option value="max">brightest along the ray</option>
        <option value="on">accumulated along the ray</option>
        <option value="min">darkest along the ray</option>
      </select>
    </label>
    {/* Gain belongs to accumulation and to nothing else. The engine swaps the
        whole colour-emitting function out for a projection and the replacement
        never mentions its gain, so the slider would sit here looking alive and
        do nothing -- which is the fault this viewer has now produced three
        times in one day. Shown disabled rather than hidden, so that somebody
        looking for it finds it and is told why. */}
    <label style={{ ...styles.control, opacity: accumulating ? 1 : 0.45 }}>
      <span
        style={styles.controlLabel}
        title={accumulating
          ? "Brighten a picture that piles up along the ray and washes out"
          : "Only for the accumulated projection; there is nothing to accumulate in a brightest or darkest one"}
      >
        gain
      </span>
      <FilledRange
        min="-3" max="3" step="0.1" value={gain}
        disabled={!accumulating}
        onChange={(event) => onGain?.(Number(event.target.value))}
        aria-label="volume gain"
        title={accumulating
          ? "Accumulating along a ray washes a picture out; this brightens it back"
          : "Only for the accumulated projection"}
        style={styles.range}
      />
      <output style={styles.value}>{accumulating ? gain.toFixed(1) : "n/a"}</output>
    </label>
    {/* Doubling steps along the ray, because the engine compares a level's
        voxel against the cube of one step -- so the useful settings are spread
        over orders of magnitude, not evenly. The readout is the real number,
        since that is what the launch flag takes. */}
    <label style={styles.control}>
      <span style={styles.controlLabel} title="How many steps a ray takes through the volume. More is sharper and slower">
        detail
      </span>
      <FilledRange
        min="6" max="16" step="1"
        value={Math.round(Math.log2(depthSamples))}
        onChange={(event) => onDepthSamples?.(2 ** Number(event.target.value))}
        aria-label="volume detail"
        title="Too few steps and the volume stays on its coarsest copy however far you zoom in; too many and it will not keep up"
        style={styles.range}
      />
      <output style={styles.value}>{depthSamples}</output>
    </label>
    <label style={styles.control}>
      <span style={styles.controlLabel} title="Fade the far side of the specimen, so front reads in front of back">
        depth fade
      </span>
      <FilledRange
        min="0" max="8" step="0.1" value={attenuation}
        onChange={(event) => onAttenuation?.(Number(event.target.value))}
        aria-label="volume depth fade"
        title="Weighs each voxel by how far along the line of sight it is. Nought is no fading"
        style={styles.range}
      />
      <output style={styles.value}>{attenuation.toFixed(1)}</output>
    </label>
    {/* Stretching the picture along an axis. It lives here because squashing
        or exaggerating depth on anisotropic data is what it is for, and depth
        is seen in this view. The factors change how the specimen is DRAWN and
        nothing about what it claims to be, and they keep acting in the flat
        view too -- the warning beside the display settings says so whenever
        the axes on screen disagree. */}
    <div style={styles.control}>
      <span style={styles.controlLabel} title="Draw the specimen stretched along an axis. Does not change the data">
        stretch
      </span>
      {/* The three inputs take the slider column AND the value column, so
          their right edge lines up with the numbers above them. */}
      <div style={{ display: "flex", gap: 6, gridColumn: "2 / -1" }}>
        {["x", "y", "z"].map((axis) => (
          <label key={axis} style={{ display: "flex", alignItems: "center", gap: 3, flex: 1 }}>
            <span style={{ ...styles.controlLabel, minWidth: 0 }}>{axis}</span>
            <input
              type="number" min="0.05" max="20" step="0.05"
              value={displayScales[axis]}
              onChange={(event) => {
                const asked = Number(event.target.value);
                if (asked > 0) onDisplayScales?.({ ...displayScales, [axis]: asked });
              }}
              aria-label={`stretch ${axis}`}
              title={`How many times to stretch the picture along ${axis}. 1 is as the run declared it`}
              style={{ ...styles.select, width: "100%", minWidth: 34 }}
            />
          </label>
        ))}
      </div>
    </div>
    </>
  );
}


/**
 * The axes the operator can actually see, which is what decides whether one
 * scale bar can be true.
 *
 * A bar states one distance per screen pixel, so it stays honest only while every
 * axis on screen is drawn at the same stretch. Which axes those are is a property
 * of the *view* rather than of the data, and that is the whole reason this cannot
 * be decided from the stretch factors alone.
 *
 * A single plane shows x and y and leaves depth off screen entirely, so
 * stretching z there — the ordinary thing to do with anisotropic data — cannot
 * make the bar wrong, and warning about it would cry wolf on the common case.
 * Turning the volume on puts z on screen beside them and the same stretch now
 * does make it wrong: measured on the demo volume with z quadrupled, ten
 * micrometres covers about 201 screen pixels along z against 50 along x, so a bar
 * reading either one is off by four for the other. The operator changed nothing
 * but what they were looking at.
 */

export default VolumeMode;
