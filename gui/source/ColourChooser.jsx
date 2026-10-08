import React from "react";
import { LUT_GRADIENTS, PALETTE, css, hexOf, rgbOf } from "./colours.js";
import { LIST_TALL_AT_MOST, styles } from "./layer-panel-styles.js";

/**
 * The square a channel is painted in, which is also how the colour is chosen.
 *
 * Press it and a list opens showing every choice as it actually looks: a
 * block for a flat colour, and the whole run of colours for a map. That is
 * the point of drawing them. A plain dropdown can only offer words, and the
 * word "magma" tells a microscopist meeting it nothing at all -- the version
 * before this one had to spell out "(black -> purple -> cream)" beside every
 * map and still left an operator picking by trial.
 *
 * "Custom" heads the list and opens the browser's own colour picker, because
 * an operator who wants a particular colour is not going to find it among
 * the named ones.
 *
 * It is a list of buttons rather than a select because a select cannot draw
 * anything but text. That costs the keyboard nothing: each entry is a real
 * button, so tabbing walks them and Enter picks, and Escape closes the list.
 */
function ColourChooser({ layer, entry, names, onPick, named = null }) {
  // Where the list is drawn, in window coordinates rather than inside the
  // square. The channel list it may be opened from scrolls, and anything
  // drawn inside a box that scrolls is cut off at that box's edge -- opened
  // from a row, the list showed two of its twelve entries (seen 2026-08-22).
  // Placed against the window instead, it escapes that box entirely.
  const [openAt, setOpenAt] = React.useState(null);
  const open = openAt !== null;
  const setOpen = (want) => setOpenAt(want ? openAt : null);
  const openBeside = (square) => {
    const box = square.getBoundingClientRect();
    // Above the square where there is no room beneath it, so the list is
    // never half off the bottom of the screen.
    const room = window.innerHeight - box.bottom;
    setOpenAt(room < LIST_TALL_AT_MOST + 8
      ? { left: box.left, bottom: window.innerHeight - box.top + 2 }
      : { left: box.left, top: box.bottom + 2 });
  };
  const choices = [
    ...PALETTE.map((one) => ({
      key: `flat:${one.name}`,
      name: one.name,
      rgb: one.rgb,
      lut: null,
      look: css(one.rgb),
    })),
    ...names.map((name) => ({
      key: name,
      name,
      lut: name,
      look: LUT_GRADIENTS[name] || "#d8dee6",
    })),
  ];
  const chosen = entry.lut
    ? choices.find((one) => one.lut === entry.lut)
    : choices.find((one) => !one.lut && css(one.rgb) === css(entry.color));
  const painted = (entry.lut && LUT_GRADIENTS[entry.lut]) || css(entry.color);
  // A colour picked by hand belongs to no entry in the list, and saying the
  // nearest name would tell the operator they chose something they did not.
  const naming = chosen ? chosen.name : "picked";
  // What to call the channel in the labels a screen reader reads out.
  // The row says its own name; the settings block says "the chosen
  // channel", because the name is written beside it already -- and
  // because a label that CONTAINS another label makes the two
  // impossible to tell apart (found by a browser gate, 2026-08-22).
  const about = named || layer.name;
  // Closed as soon as the focus leaves the whole chooser. Asking about the
  // chooser rather than about the button pressed means pressing an entry does
  // not close the list out from under the press.
  const closeOnLeaving = (event) => {
    if (!event.currentTarget.closest("[data-chooser]")
      ?.contains(event.relatedTarget)) {
      setOpen(false);
    }
  };
  return (
    <span style={styles.chooser} data-chooser="">
      {/* The colour itself is the control: press the square a channel is
          painted in and the list of everything it could be painted in opens
          under it. There was a row of its own for this, labelled COLORMAP,
          with the square beside a named dropdown; one thing to press, where
          the operator is already looking, says the same in less room. */}
      <button
        type="button"
        onClick={(event) => (open ? setOpenAt(null) : openBeside(event.currentTarget))}
        onBlur={closeOnLeaving}
        aria-label={`colour ${about}`}
        aria-expanded={open}
        title={`Painted ${naming}. Press to choose another colour`}
        style={{ ...styles.swatch, ...styles.swatchButton, background: painted }}
      />
      {open && (
        <span role="listbox" style={{ ...styles.chooserList, ...openAt }}>
          {/* Any colour at all, first in the list, because an operator who
              wants one is not looking for it among the named ones. The
              browser's own picker opens on it -- the one they already know. */}
          <label
            style={{ ...styles.chooserEntry, ...styles.chooserCustom }}
            title="Choose any colour"
          >
            {/* Left empty on purpose: this entry stands for a colour not
                chosen yet, so showing the current one would say the channel
                is already painted it. An empty well is the invitation. */}
            <span style={{ ...styles.chooserSwatch, background: "transparent" }} />
            custom
            <input
              type="color"
              value={hexOf(entry.color)}
              onBlur={closeOnLeaving}
              onChange={(event) => {
                onPick({ rgb: rgbOf(event.target.value), lut: null });
                setOpen(false);
              }}
              aria-label={`choose a colour for ${about}`}
              style={styles.hiddenPicker}
            />
          </label>
          {choices.map((choice) => (
            <button
              key={choice.key}
              type="button"
              role="option"
              aria-selected={chosen?.key === choice.key}
              aria-label={`${choice.name} for ${about}`}
              onBlur={closeOnLeaving}
              onClick={() => {
                onPick(choice);
                setOpen(false);
              }}
              style={{
                ...styles.chooserEntry,
                ...(chosen?.key === choice.key ? styles.chooserEntryOn : null),
              }}
            >
              <span style={{ ...styles.chooserSwatch, background: choice.look }} />
              {choice.name}
            </button>
          ))}
        </span>
      )}
    </span>
  );
}

export default ColourChooser;
