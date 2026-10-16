import React from "react";
import { styles } from "./layer-panel-styles.js";

/**
 * The spread of brightness in a channel, with the chosen window marked on it.
 *
 * This is the one picture in the panel that answers a question a microscopist
 * actually asks: is this channel saturating, or is it sitting on background?
 * The bars between the window's two marks are drawn at full light — that is
 * the brightness the display ramp is spent on — and the bars outside them are
 * dimmed: everything to the left saturates to black, everything to the right
 * to white.
 *
 * The two bars ARE the window, and only they move it: take hold of one — the
 * pointer has to be over it, a few pixels of grace either side — and pull,
 * and that edge of the window follows, the same window the MIN and MAX
 * sliders move, so the two controls can never disagree. A bar can never be
 * pulled past the image's own range: there are no pixels out there for a
 * black or white point to say anything about.
 *
 * Everywhere that is not a bar, the pointer works the AXIS instead — the
 * stretch of brightness being drawn, never the picture itself. Dragging pans
 * it; the wheel zooms it toward the pointer, out as far as the whole range
 * framed the usual way and no further. Auto puts the default framing back.
 * (All of this is the operator's design, 2026-08-23.)
 */
function Histogram({ layer, window_, color, onWindow, onAxis = null,
                     range = null, scale = "linear", axis = null,
                     steady = null }) {
  const dragging = React.useRef(null);
  const [overBar, setOverBar] = React.useState(false);
  const box = React.useRef(null);
  // The freshest zoom arithmetic, held where the listener below can reach
  // it. The listener itself is attached once per mount; the maths it calls
  // is replaced on every render, so it always reads today's axis.
  const zooming = React.useRef(null);
  const counts = layer.histogram?.counts;
  // The wheel zooms the axis, so the page must not scroll with it. React's
  // own wheel listener is registered as "passive" — one that has promised
  // never to stop the scroll — so the listener is attached by hand instead,
  // with that promise withheld.
  React.useEffect(() => {
    const target = box.current;
    if (!target) return undefined;
    const wheel = (event) => zooming.current?.(event);
    target.addEventListener("wheel", wheel, { passive: false });
    return () => target.removeEventListener("wheel", wheel);
  }, [Boolean(counts?.length)]);
  if (!counts?.length) return null;
  const measured = layer.histogram;
  // The box spans the AXIS -- the camera's whole range when the store's
  // numbers have one -- and the bars sit at the brightness they were
  // measured at inside it, so the empty stretch up to saturation is honest
  // headroom on show. Marks, bars and the pointer all share one mapping, or
  // they would sit under the wrong values; on the log axis it warps them
  // together.
  const low = axis ? axis.min : measured.low;
  const span = (axis ? axis.max - axis.min : measured.high - measured.low) || 1;
  // Brightness runs along the box evenly, always. It once ran logarithmically
  // when Log was on, which moved every bar sideways and dragged the handles
  // with them, so a window an operator had set stopped sitting where they put
  // it. Log now lifts the bars instead (see their height below), which is the
  // thing that actually needs it: fluorescence piles almost every pixel into
  // the dim bins and leaves the interesting tail one pixel high.
  const at = (value) =>
    Math.min(Math.max((value - low) / span, 0), 1) * counts.length;
  const left = at(window_.low);
  const right = at(window_.high);

  // Which bin each count belongs to, in the brightness the server counted.
  const bins = measured.high - measured.low || 1;
  const brightnessOf = (index) =>
    measured.low + ((index + 0.5) * bins) / counts.length;
  // The tallest bar is measured over the bins inside the RESTING frame --
  // the one Auto or a fresh window lays out -- not over every bin the
  // server counted, and not over whatever stretch a pan or zoom happens to
  // be showing. The first would let a background peak outside the frame
  // squash the specimen to a line one pixel high; the second made the bars
  // breathe up and down while the operator panned, a scale that changed
  // under the eye measuring against it (2026-08-23). Anchored to the
  // frame, the heights hold still until the framing itself is asked to
  // change.
  const anchor = steady || { min: low, max: low + span };
  const drawn = counts.filter(
    (_count, index) => brightnessOf(index) >= anchor.min
      && brightnessOf(index) <= anchor.max);
  const peak = Math.max(...(drawn.length ? drawn : counts), 1);

  // The image's own range of values, which neither the window nor the axis
  // may leave: a black or white point beyond the pixels would say something
  // about brightness that does not exist, and an axis reaching past the
  // range showed a sixteen-bit histogram running to numbers no camera
  // produced (the operator's refinement, 2026-08-24). Panning and zooming
  // therefore travel at most to the range itself.
  const bounds = range && Number.isFinite(range.high)
    ? { low: range.low ?? 0, high: range.high }
    : { low: Math.min(0, measured.low), high: Math.max(65535, measured.high) };
  const widest = bounds;
  const withinImage = (value) =>
    Math.min(Math.max(value, bounds.low), bounds.high);
  const holdTheAxis = (next) => {
    const width = Math.min(
      Math.max(next.high - next.low, (bounds.high - bounds.low) / 256),
      widest.high - widest.low,
    );
    const from = Math.min(Math.max(next.low, widest.low), widest.high - width);
    return { low: from, high: from + width };
  };

  // Where a pointer event sits on the brightness axis.
  const valueUnder = (event) => {
    const face = event.currentTarget.getBoundingClientRect();
    const fraction = Math.min(1, Math.max(0, (event.clientX - face.left) / face.width));
    return low + fraction * span;
  };
  // Which bar the pointer is over, if either: within a few screen pixels,
  // so the target is the drawn line and not "the nearer half of the box".
  const barUnder = (event) => {
    const face = event.currentTarget.getBoundingClientRect();
    const grace = (6 / face.width) * span;
    if (Math.abs(valueUnder(event) - window_.low) <= grace) return "low";
    if (Math.abs(valueUnder(event) - window_.high) <= grace) return "high";
    return null;
  };
  const takeHold = (event) => {
    const bar = onWindow ? barUnder(event) : null;
    if (bar) {
      dragging.current = { bar };
    } else if (onAxis) {
      // Not a bar: the drag pans the axis. Everything is measured from
      // where the drag began, so the ground cannot creep under a held
      // pointer as the axis it is measured against moves.
      dragging.current = { panFrom: event.clientX, axisWas: { low, span } };
    } else {
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    follow(event);
  };
  const follow = (event) => {
    const held = dragging.current;
    if (!held) {
      // A resting pointer only updates what the cursor face says it would
      // grab, so the bars read as handles before they are ever touched.
      const bar = onWindow ? barUnder(event) : null;
      if (Boolean(bar) !== overBar) setOverBar(Boolean(bar));
      return;
    }
    if (held.bar) {
      const value = withinImage(valueUnder(event));
      // The floor may not cross the ceiling: a window at least one count
      // wide always remains, so the picture can never invert.
      if (held.bar === "low") {
        onWindow({ low: Math.min(value, window_.high - 1), high: window_.high });
      } else {
        onWindow({ low: window_.low, high: Math.max(value, window_.low + 1) });
      }
      return;
    }
    const face = event.currentTarget.getBoundingClientRect();
    const moved = ((held.panFrom - event.clientX) / face.width) * held.axisWas.span;
    onAxis(holdTheAxis({
      low: held.axisWas.low + moved,
      high: held.axisWas.low + held.axisWas.span + moved,
    }));
  };
  const letGo = () => {
    dragging.current = null;
  };
  // The wheel zooms the axis toward the pointer: the brightness under the
  // cursor stays under the cursor, everything else breathes in or out
  // around it. The picture itself is untouched — this moves only what
  // stretch of brightness is DRAWN.
  zooming.current = onAxis
    ? (event) => {
      event.preventDefault();
      const face = box.current.getBoundingClientRect();
      const fraction = Math.min(1, Math.max(0, (event.clientX - face.left) / face.width));
      const anchor = low + fraction * span;
      const factor = Math.exp(event.deltaY * 0.002);
      onAxis(holdTheAxis({
        low: anchor - (anchor - low) * factor,
        high: anchor + (low + span - anchor) * factor,
      }));
    }
    : null;

  return (
    <svg
      ref={box}
      viewBox={`0 0 ${counts.length} 24`}
      preserveAspectRatio="none"
      style={{ ...styles.histogram,
               cursor: overBar ? "ew-resize" : onAxis ? "grab" : "default" }}
      role="img"
      aria-label={`histogram ${layer.name}`}
      onPointerDown={takeHold}
      onPointerMove={follow}
      onPointerUp={letGo}
      onPointerCancel={letGo}
      onPointerLeave={() => overBar && setOverBar(false)}
      // A double click puts the default framing back after a pan or a zoom
      // — the same resting layout the panel opened with. It does NOT press
      // Auto: nothing is measured and the window does not move (the
      // operator's ask, 2026-08-23).
      onDoubleClick={() => onAxis?.(null)}
    >
      {/* Bars inside the window at full light -- near-white, so the stretch
          the display ramp is spent on is unmistakable -- and bars outside it
          dimmed to a quarter: that brightness saturates to black or white.
          One glance says which pixels are being looked at. */}
      {counts.map((count, index) => {
        // How many pixels this bin holds, against the fullest bin. On the
        // plain scale that is the honest proportion; on the log scale the
        // quiet bins are lifted until they can be seen at all, which is the
        // whole reason a microscopist asks for it.
        const share = scale === "log"
          ? Math.log1p(count) / Math.log1p(peak)
          : count / peak;
        const height = share * 22;
        // The bins live in MEASURED brightness -- that is what the server
        // counted -- and only their places are mapped through the axis, which
        // may run past them, or stop short of them.
        const shown = brightnessOf(index) >= window_.low
          && brightnessOf(index) <= window_.high;
        const starts = at(measured.low + (index * bins) / counts.length);
        const ends = at(measured.low + ((index + 1) * bins) / counts.length);
        return (
          <rect
            key={index}
            x={starts}
            y={24 - height}
            width={ends - starts}
            height={height}
            fill="currentColor"
            opacity={shown ? 1 : 0.25}
          />
        );
      })}
      {[left, right].map((x, edge) =>
        x > 0 && x < counts.length ? (
          <rect key={edge} x={x} y="0" width="0.8" height="24" fill="var(--accent)" />
        ) : null,
      )}
    </svg>
  );
}

/**
 * The number beside a slider, as a box that can be typed into.
 *
 * While untouched it simply shows the slider's value. Start typing and it
 * holds your draft until Enter or leaving the box commits it; a draft that
 * is not a number is quietly dropped and the real value comes back. So box
 * and slider always describe the same setting, whichever one moved last.
 */

export default Histogram;
