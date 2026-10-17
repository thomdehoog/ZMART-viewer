import React from "react";
import FilledRange from "./FilledRange.tsx";
import { restingWindow } from "./drawing/layers.ts";
import type { LayerState } from "./drawing/layers.ts";
import type { Histogram as Measurement, LayerSpec, Measured } from "./asking-python.ts";
import ColourChooser from "./ColourChooser.tsx";
import EyeButton from "./Eye.tsx";
import Histogram from "./Histogram.tsx";
import ValueBox from "./ValueBox.tsx";
import { contrastRange, frameTheWindow, withEdgeAt } from "./contrast-window.ts";
import type { Window } from "./contrast-window.ts";
import { stretchedUnevenly } from "./display-axes.ts";
import type { DisplayScales, Mode } from "./display-axes.ts";
import { styles } from "./layer-panel-styles.ts";

export interface ChannelControlsProps {
  layer: LayerSpec;
  index: number;
  entry: LayerState;
  mode: Mode;
  /** A change to what the operator has set on one channel. */
  onChange: (index: number, change: Partial<LayerState>) => void;
  /** A new window for the channel; Auto with nothing to go on hands on what it has. */
  onWindow: (index: number, window: Window | null | undefined) => void;
  /** Measure the pixels of this channel on screen; null where there is nothing to read. */
  onMeasureHere: (index: number) => Promise<Measured | null>;
  displayScales: DisplayScales;
}

/**
 * The one block of controls, acting on whichever channel is picked out in the list.
 *
 * There is a single copy of this rather than one per channel. That is partly for
 * room — with sliders on every row, three channels filled a tall screen — and
 * partly because it matches how the work goes: you look at one channel, set it up,
 * then move to the next. It is the arrangement napari uses, and anyone who has
 * used napari will already know where to look.
 */
function ChannelControls({ layer, index, entry, mode, onChange, onWindow, onMeasureHere,
                          displayScales }: ChannelControlsProps) {
  // The brightness of the part of the picture on screen, once Auto has asked
  // for it. Kept here rather than pushed into the layer because it describes
  // where the operator was looking at one moment, not the channel: pan away
  // and the old reading is no longer about anything.
  const [here, setHere] = React.useState<Measurement | null>(null);
  // The same resting window the canvas draws with (see drawing/layers.ts): the run's
  // recorded window, or the measured one when the run said nothing.
  const window_ = entry.window || restingWindow(layer, mode === "volume")
    || { low: 0, high: 65535 };
  // Which part of the brightness axis is drawn. Nothing said means the data's
  // own span; the two boxes under the histogram are where an operator says
  // otherwise, and their answer is kept per channel for as long as the panel
  // is showing it.
  const [shown, setShown] = React.useState<Window | null>(null);
  // The image's own range of values, which nothing in this panel may claim
  // to exceed: not the handles (a black or white point beyond the pixels
  // would describe brightness that does not exist, 2026-08-23), and not the
  // axis either — its framing aims the bars at 15% and 85% but stops at
  // this wall (2026-08-24). One gate, every mover passes it.
  const imageRange = layer.range && Number.isFinite(layer.range.high)
    ? { low: layer.range.low ?? 0, high: layer.range.high }
    : { low: 0, high: 65535 };
  const withinImage = (value: number) =>
    Math.min(Math.max(value, imageRange.low), imageRange.high);
  // The stretch of brightness the picture draws when the operator has not
  // said otherwise: settled from the window this channel was handed, and
  // settled again whenever Auto hands it another. Held in between, so that
  // dragging a handle moves the handle rather than the ground under it.
  const [framed, setFramed] = React.useState(
    () => frameTheWindow(window_, imageRange));
  const frameAround = (given: Window | null | undefined) => {
    const around = frameTheWindow(given, imageRange);
    if (!around) return;
    setShown(null);
    setFramed(around);
  };
  // The channel as measured: the same layer, but wearing the reading Auto
  // last took where the operator was looking. Substituted whole so that the
  // axis, the bars and the Auto light all describe one measurement instead
  // of disagreeing about which picture they are talking about.
  const seen = here ? { ...layer, histogram: here } : layer;
  const { min, max } = contrastRange(seen, window_, shown || framed);
  const setLow = (low: number) => onWindow(index, withEdgeAt(window_, "low", low, imageRange));
  const setHigh = (high: number) => onWindow(index, withEdgeAt(window_, "high", high, imageRange));

  // There used to be BRIGHTNESS and CONTRAST sliders below MIN and MAX --
  // the same window re-described, the way Fiji presents it. They were removed
  // (2026-08-18) once the window became directly grabbable in the histogram:
  // MIN and MAX are the two saturation points, everything below the first is
  // black and everything above the second is white, and a second pair of
  // handles moving the very same window read as controls that do something
  // else when they do not.
  const isMask = layer.kind === "segmentation";

  // Whether the histogram's counts are drawn plainly or lifted. It starts
  // plain: with the brightness axis now the data's own span, the picture is
  // readable as it stands, and Log is there for the channel whose dim bins
  // dwarf everything else. It was briefly turned on by itself for such
  // channels, back when the axis ran to the camera's whole range and needed
  // the help.
  const [scale, setScale] = React.useState<"linear" | "log">("linear");

  // The measurement Auto falls back on: the whole picture's, taken once when
  // the run was opened. It is what the button can still offer in the moment
  // before the view can be asked -- an unlaid-out panel, a volume turned in
  // three dimensions -- and nothing more than that.
  const autoWindow = seen.histogram?.autoWindow;

  return (
    <div style={styles.card} aria-label="channel controls">
      <div style={styles.headingRow}>
        <span style={styles.heading}>channel settings</span>
      </div>
      {/* Which channel these settings are about, said the same way the list
          above says it: the acquisition it belongs to, and under that the
          channel's own row -- eye, colour, name. Reading the same shape in
          both places is what makes the pairing obvious, and without it the
          sliders adjust something the operator has to remember rather than
          read. */}
      <div style={styles.controlsHead}>
        <span style={styles.controlsGroup} title={layer.group}>{layer.group}</span>
        <div style={styles.controlsChannel}>
          <EyeButton
            open={entry.visible}
            onClick={() => onChange(index, { visible: !entry.visible })}
            label="toggle the chosen channel"
            what="channel"
          />
          <ColourChooser
            layer={layer}
            entry={entry}
            named="the chosen channel"
            onPick={(change) => onChange(index, change)}
          />
          <span style={styles.controlsName} title={layer.name}>
            {layer.name}
          </span>
        </div>
      </div>
      {isMask ? (
        <div style={styles.maskNote}>objects, each in its own colour</div>
      ) : (
        <>
          {/* The brightness of this channel, drawn across the panel. It
              takes the whole width now: the two buttons that used to sit
              beside it stole a sixth of the picture to say four letters
              each, and they read just as well under it. */}
          <div style={styles.histogramRow}>
            <Histogram
              layer={seen}
              window_={window_}
              onWindow={(next) => onWindow(index, next)}
              onAxis={(next) => setShown(next)}
              range={imageRange}
              scale={scale}
              axis={{ min, max }}
              // The bars' height scale is anchored to the resting frame, so
              // panning or zooming the axis never moves it — only Auto or a
              // fresh window does, by laying a new frame.
              steady={contrastRange(seen, window_, framed)}
            />
          </div>
          {/* Under the picture, in one row: what stretch of brightness it
              draws -- the two boxes, at its two ends, where they label the
              ends they set -- and between them the two things one can ask
              of it.

              Auto is a plain press, not a state: it reads the brightness of
              what is on screen and sets the window to it, and that is all.
              It was briefly a light that stayed on while the window matched
              its reading, with a second press that undid it -- but nobody
              presses Auto to undo it, and a button that means one thing on
              the way in and another on the way out is a button an operator
              has to remember rather than read (2026-08-20). Every handle,
              box and slider below stays free afterwards.

              Log is the one thing in this row that IS a state: it lifts the
              quiet bins and stays lifted until pressed again. */}
          <div style={styles.axisRow}>
            <span style={styles.axisBox}>
              <ValueBox
                value={Math.round(min)}
                onCommit={(asked) => setShown({
                  low: withinImage(asked),
                  high: Math.max(withinImage(asked) + 1, shown ? shown.high : max),
                })}
                label={`axis from ${layer.name}`}
                align="left"
              />
            </span>
            <span style={styles.axisButtons}>
              <button
                type="button"
                onClick={async () => {
                  // What Auto is for: the brightness of what is in front of
                  // the operator, read afresh on every press -- on a plate,
                  // the whole picture's reading and one well's are worlds
                  // apart. The whole picture's is the fallback for the moment
                  // the view cannot be asked.
                  const answer = await onMeasureHere(index);
                  if (answer?.histogram) {
                    setHere(answer.histogram);
                    // A fresh reading is a fresh picture, so the axis is laid
                    // out again around the window it produced -- the bars land
                    // at fifteen and eighty five per cent of the width, exactly
                    // as they do when a channel first opens.
                    frameAround(answer.window);
                    return;
                  }
                  const fallingBackOn = autoWindow || layer.window;
                  onWindow(index, fallingBackOn);
                  frameAround(fallingBackOn);
                }}
                aria-label={`auto contrast ${layer.name}`}
                title="Set the window from the brightness of what is on screen, leaving out the ground nobody imaged"
                style={styles.autoButton}
              >
                Auto
              </button>
              <button
                type="button"
                onClick={() => setScale(scale === "log" ? "linear" : "log")}
                aria-label={scale === "log" ? "plain counts" : "logarithmic counts"}
                aria-pressed={scale === "log"}
                title="Lift the quiet bins of the histogram into view. Fluorescence piles almost every pixel into the dim bins, which leaves the interesting tail one pixel high"
                style={{ ...styles.autoButton, ...(scale === "log" ? styles.autoButtonOn : null) }}
              >
                Log
              </button>
            </span>
            <span style={styles.axisBox}>
              <ValueBox
                value={Math.round(max)}
                onCommit={(asked) => setShown({
                  low: Math.min(withinImage(asked) - 1, shown ? shown.low : min),
                  high: withinImage(asked),
                })}
                label={`axis to ${layer.name}`}
              />
            </span>
          </div>
          <label style={styles.control}>
            <span style={styles.controlLabel} title="Anything dimmer than this is shown as black">
              min
            </span>
            {/* The handle travels over brightness itself, evenly, and over
                exactly the stretch the histogram above it draws -- so a mark
                on the picture and a handle beneath it always mean the same
                number. It used to count steps along a warped scale whenever
                Log was on, which moved the two apart. */}
            <FilledRange
              min={min}
              max={max}
              step="1"
              value={window_.low}
              onChange={(event) => setLow(Number(event.target.value))}
              aria-label={`min ${layer.name}`}
              title="Anything dimmer than this is shown as black"
              style={styles.range}
            />
            <ValueBox
              value={Math.round(window_.low)}
              onCommit={setLow}
              label={`min value ${layer.name}`}
            />
          </label>
          <label style={styles.control}>
            <span style={styles.controlLabel} title="Anything brighter than this is shown as white">
              max
            </span>
            <FilledRange
              min={min}
              max={max}
              step="1"
              value={window_.high}
              onChange={(event) => setHigh(Number(event.target.value))}
              aria-label={`max ${layer.name}`}
              title="Anything brighter than this is shown as white"
              style={styles.range}
            />
            <ValueBox
              value={Math.round(window_.high)}
              onCommit={setHigh}
              label={`max value ${layer.name}`}
            />
          </label>
        </>
      )}
      {/* The stretch inputs themselves live in the 3D viewer section below --
          squashing or exaggerating depth is what stretching is for, and that
          is seen in the volume. The warning stays HERE, in both views: a
          stretch set in the volume still distorts the flat picture after
          switching back, and a stretched picture with a quiet scale bar is a
          way to measure wrongly and never find out. */}
      {stretchedUnevenly(displayScales, mode) && (
        <div style={{ ...styles.controlLabel, color: "var(--warning-text)", padding: "0 12px 6px" }}>
          {mode === "volume"
            ? "the axes are stretched differently, so no single scale bar is true in every direction"
            : "x and y are stretched differently, so no single scale bar is true in both directions"}
        </div>
      )}
      <label style={styles.control}>
        <span style={styles.controlLabel} title="How strongly this channel is drawn">
          opacity
        </span>
        <FilledRange
          min="0"
          max="1"
          step="0.01"
          value={entry.opacity}
          onChange={(event) => onChange(index, { opacity: Number(event.target.value) })}
          aria-label={`opacity ${layer.name}`}
          style={styles.range}
        />
        <ValueBox
          value={Math.round(entry.opacity * 100)}
          suffix="%"
          onCommit={(asked) => onChange(index, { opacity: Math.min(1, Math.max(0, asked / 100)) })}
          label={`opacity value ${layer.name}`}
        />
      </label>
    </div>
  );
}

export default ChannelControls;
