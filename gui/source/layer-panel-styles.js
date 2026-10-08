// How the layer panel looks: every style the panel and its pieces use, and
// the few sizes they share, so that a row restyled in one place stays in
// step everywhere else.

// One block for every small control in the display settings: the boxes an
// operator types into and the buttons beside them. Named rather than repeated
// so the row cannot drift apart the next time one of them is restyled.
export const ROW_ITEM = 54;

export const ROW_HEIGHT = 24;

// What separates one block of the bar from the next: a rule, and enough room on
// either side of it that the eye reads two sections rather than one long list.
export const BLOCK = {
  // Ruled top and bottom, so a section reads as a card whichever side the
  // eye arrives from.
  borderTop: "1px solid var(--panel-border)",
  borderBottom: "1px solid var(--panel-border)",
  paddingTop: 8,
  paddingBottom: 8,
  marginBottom: 12,
};

// The ground under the channel being adjusted, in both the places it is
// named: its row in the list, and its row above the histogram. One value, so
// the two cannot drift apart.

// The ground under the channel being adjusted, in both the places it is
// named: its row in the list, and its row above the histogram. One value, so
// the two cannot drift apart.
export const CHOSEN_GROUND = "var(--selected-row-bg)";

// How tall the colour list may grow before it scrolls. Named, because the
// list also asks "is there room for me below the square?", and the two have
// to be the same number.

// How tall the colour list may grow before it scrolls. Named, because the
// list also asks "is there room for me below the square?", and the two have
// to be the same number.
export const LIST_TALL_AT_MOST = 220;

export const styles = {
  empty: {
    margin: "0 12px 12px",
    padding: "10px 11px",
    border: "1px dashed var(--panel-border)",
    borderRadius: 5,
    color: "var(--text-muted)",
    font: "11px/1.5 system-ui, sans-serif",
  },
  emptyLine: { margin: "0 0 6px" },
  orderNote: {
    margin: "0 12px 6px",
    color: "var(--text-muted)",
    font: "10px/1.4 system-ui, sans-serif",
  },
  // The two numbers under the histogram are the window in use, so the picture
  // above can be read as "this part of the spread is what you are seeing".
  histogramCaption: {
    display: "grid",
    gridTemplateColumns: "1fr auto 1fr",
    alignItems: "baseline",
    gap: 6,
    padding: "0 12px 3px 60px",
    color: "var(--text-muted)",
    font: "10px/1.3 system-ui, sans-serif",
    fontVariantNumeric: "tabular-nums",
  },
  eyeGlyph: { width: 14, height: 14, display: "block" },
  // The card every section sits on. Same blue as the display settings, so the
  // darker panel ground showing between the cards reads as the separation.
  // One spacing rhythm for every section, so the bar reads as one design:
  // a heading sits 10 of room above its content (5 from the heading row, 5
  // from the content's own top), and every card ends with 12 of room after
  // its last row (the operator asked for both, 2026-08-23).
  card: { ...BLOCK, paddingBottom: 12, background: "var(--card-bg)", flexShrink: 0 },
  headingRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8,
    padding: "0 12px 5px",
    minHeight: 22,
  },
  openButton: {
    border: "1px solid var(--control-border)",
    borderRadius: 4,
    background: "var(--control-bg)",
    color: "var(--accent-text)",
    font: "600 10px/1 system-ui, sans-serif",
    padding: "4px 7px",
    cursor: "pointer",
  },
  notice: {
    margin: "0 12px 8px",
    padding: "6px 8px",
    border: "1px solid var(--danger-border)",
    borderRadius: 4,
    background: "var(--danger-bg)",
    color: "var(--danger-text)",
    font: "11px/1.4 system-ui, sans-serif",
  },
  // Deliberately quiet: closing is easy to reach but should not invite a stray
  // click, since it clears the settings the operator gave those channels.
  close: {
    border: "none",
    background: "none",
    color: "var(--text-faint)",
    fontSize: 15,
    lineHeight: 1,
    cursor: "pointer",
    padding: "0 2px",
  },
  group: { borderBottom: "1px solid var(--subtle-border)" },
  groupHead: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    // The 5 of top padding is half the heading rhythm; see the card note.
    padding: "5px 12px 3px",
  },
  disclose: {
    background: "none",
    border: "none",
    color: "var(--text-muted)",
    cursor: "pointer",
    fontSize: 10,
    padding: 0,
    width: 10,
  },
  groupName: {
    flex: 1,
    font: "600 12px/1 system-ui, sans-serif",
    letterSpacing: ".02em",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  maskNote: { padding: "2px 12px 4px 60px", color: "var(--text-faint)", fontSize: 10 },
  select: {
    width: "100%",
    background: "var(--control-bg)",
    color: "var(--text-secondary)",
    border: "1px solid var(--control-border)",
    borderRadius: 3,
    font: "10px system-ui, sans-serif",
    padding: "3px 4px",
  },
  // Channels are indented so it reads as "these belong to that acquisition".
  // marginLeft puts the 2px line exactly under the centre of the disclosure
  // triangle above it (12px head padding + half the 10px button).
  members: { paddingLeft: 8, borderLeft: "2px solid var(--subtle-border)", marginLeft: 16 },
  // The list keeps its natural height so the settings sit directly beneath
  // it, but a run with many acquisitions is capped and scrolls inside the
  // cap rather than pushing the settings off the bottom of the bar.
  list: { flex: "0 1 auto", minHeight: 90, maxHeight: "42vh", overflowY: "auto" },
  panel: {
    display: "flex",
    flexDirection: "column",
    minHeight: 0,
    // Inside the single right-hand bar now, so it takes the bar's width and
    // shares the height with the targets list below it. If everything
    // together still outgrows the bar, the whole panel scrolls.
    flex: 1,
    overflowY: "auto",
    // Noticeably darker than the section cards (#141922), so the ground
    // showing between them separates the sections at a glance.
    background: "var(--panel-bg)",
    padding: "12px 0 0",
    font: "13px/1.4 system-ui, -apple-system, 'Segoe UI', sans-serif",
    color: "var(--text-primary)",
  },
  heading: {
    font: "600 11px/1 system-ui, sans-serif",
    letterSpacing: ".08em",
    textTransform: "uppercase",
    color: "var(--text-faint)",
  },
  layer: {
    position: "relative",
    padding: "1px 0",
    cursor: "pointer",
    // The highlight below stops where the histogram does, so the row picked
    // out and the picture it belongs to share a right-hand edge.
    marginRight: 12,
    borderRadius: 3,
  },
  // The channel the controls below are acting on. It has to be unmistakable:
  // otherwise a slider appears to do nothing because it is adjusting a
  // different channel from the one being looked at. A tinted ground says that
  // on its own; there was a blue bar down the left of it as well, which said
  // the same thing twice and drew the eye to the panel's edge rather than to
  // the row.
  layerChosen: { background: CHOSEN_GROUND },
  // Directly below the list of channels, so the two things that belong
  // together -- the highlighted row naming the channel and the controls
  // adjusting it -- end up next to each other rather than at opposite ends
  // of the bar.
  // The clear space above the block is what separates it from the list at a
  // glance; the section rule alone was too easy to read past.
  // Every section is separated from the next by the darker panel ground
  // showing between the cards, and by the same amount: what set this one
  // apart with extra room above was making the settings look like a
  // different kind of thing from the list they belong to.
  controls: { ...BLOCK, paddingBottom: 12, background: "var(--card-bg)", flexShrink: 0 },
  controlsHead: {
    display: "flex",
    flexDirection: "column",
    gap: 3,
    // The 5 of top padding is half the heading rhythm; see the card note.
    padding: "5px 12px 6px",
  },
  // The channel's own row, built like the rows in the list above so the two
  // read as the same thing said twice: eye, colour, name -- on the same
  // tinted ground the chosen row carries up there, which is what ties the
  // two together at a glance.
  controlsChannel: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    minWidth: 0,
    background: CHOSEN_GROUND,
    borderRadius: 3,
    padding: "4px 6px",
  },
  controlsName: {
    color: "var(--text-bright)",
    font: "600 12px/1 system-ui, sans-serif",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  // The acquisition, written exactly as the list above writes it, so the two
  // read as one name in two places rather than two labels.
  controlsGroup: {
    font: "600 12px/1 system-ui, sans-serif",
    letterSpacing: ".02em",
    color: "var(--text-primary)",
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  row: { position: "relative", display: "flex", alignItems: "center", gap: 8, padding: "5px 12px" },
  eye: { background: "none", border: "none", color: "var(--text-primary)", cursor: "pointer", fontSize: 13, padding: 0 },
  swatch: { width: 13, height: 13, borderRadius: 3, border: "1px solid var(--control-border)", display: "inline-block", flexShrink: 0 },
  // The square is a button now, so it needs a button's manners taken off it:
  // no browser chrome, no text metrics, just the colour.
  swatchButton: { padding: 0, cursor: "pointer", appearance: "none" },
  // Under the histogram and exactly as wide: the same grid as the row above,
  // so the ends of the axis sit under the ends of the picture they describe.
  // Written as the same template rather than as a padding that happens to
  // come out near it, because the two would drift the moment either changed.
  // The same side padding the histogram carries, so the row's two ends sit
  // exactly under the two ends of the picture they describe. The generous
  // space beneath is deliberate: it closes the group -- picture, its two
  // ends, and what one can ask of it -- and separates it from the window
  // controls below, which are a different subject. Four pixels left the two
  // groups reading as one long list.
  axisRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 6,
    padding: "0 12px 14px",
  },
  // Between the two ends, side by side and close: Auto and Log are a pair,
  // and the four pixels between them against the nineteen on either side of
  // the pair is what says so. Equal gaps would read as four unrelated
  // controls in a line.
  axisButtons: {
    display: "flex",
    alignItems: "center",
    gap: 4,
  },
  // One width for all four things in the row under the histogram -- these two
  // boxes and the two buttons between them -- and the same width again for
  // the value boxes in the slider rows, so the right-hand box stands in
  // their column. An edge that does not quite line up reads as a mistake
  // even when nobody can say which pixel is wrong (2026-08-20).
  axisBox: { width: ROW_ITEM, flexShrink: 0 },
  // Only as wide as the square it holds, since the square is now the whole
  // control; the list beneath it is free to be wider.
  chooser: { position: "relative", flexShrink: 0, lineHeight: 0 },
  // Above everything else in the panel, and scrolling once the list is longer
  // than the room beneath it.
  chooserList: {
    position: "fixed",
    minWidth: 116,
    zIndex: 40,
    display: "flex",
    flexDirection: "column",
    background: "var(--card-bg)",
    border: "1px solid var(--control-border)",
    borderRadius: 3,
    boxShadow: "0 6px 18px var(--shadow-color)",
    maxHeight: LIST_TALL_AT_MOST,
    overflowY: "auto",
  },
  chooserEntry: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    background: "transparent",
    color: "var(--text-secondary)",
    border: 0,
    font: "10px system-ui, sans-serif",
    padding: "4px 6px",
    cursor: "pointer",
    textAlign: "left",
  },
  chooserEntryOn: { background: "var(--selected-row-bg)", color: "var(--text-bright)" },
  // The custom entry holds the browser's own picker, invisible and filling
  // it, so pressing the entry opens the picker rather than a control of ours.
  chooserCustom: { position: "relative", cursor: "pointer" },
  chooserSwatch: {
    width: 22,
    height: 11,
    borderRadius: 2,
    border: "1px solid var(--control-border)",
    flexShrink: 0,
  },
  // The picker itself is invisible and fills the entry that holds it, so
  // that entry IS the button: the browser's own colour dialog opens on it,
  // which is the one an operator already knows and needs no widget of ours
  // to maintain.
  hiddenPicker: {
    position: "absolute",
    inset: 0,
    width: "100%",
    height: "100%",
    opacity: 0,
    padding: 0,
    border: 0,
    cursor: "pointer",
  },
  name: { overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
  // The histogram spans the panel: left edge with the control labels, right
  // edge with the end of every slider's track below it.
  histogramRow: {
    padding: "1px 12px 4px",
  },
  histogram: {
    display: "block",
    width: "100%",
    // Tall enough to read a distribution in, now that nothing stands beside
    // it setting the height. It used to be sized to two stacked buttons.
    height: 60,
    // The loudest ink of the theme: the full-light bars between the window's
    // marks must read as "this is what you are seeing", against the dimmed
    // rest -- near-white on the dark theme, near-black on the light one.
    color: "var(--text-bright)",
    background: "var(--input-bg)",
    border: "1px solid var(--subtle-border)",
    borderRadius: 3,
  },
  autoButton: {
    width: ROW_ITEM,
    flexShrink: 0,
    // Built to the same block as the two value boxes it sits between: same
    // width, same height, same corner, same border thickness, so the four
    // read as one row of equals rather than four sizes in a line.
    height: ROW_HEIGHT,
    boxSizing: "border-box",
    padding: 0,
    textAlign: "center",
    border: "1px solid var(--control-border)",
    borderRadius: 3,
    background: "var(--control-bg)",
    color: "var(--text-secondary)",
    font: "600 11px/22px system-ui, sans-serif",
    cursor: "pointer",
  },
  control: {
    display: "grid",
    // The label column is sized to the longest label now in it (COLORMAP,
    // measured at 56px); anything narrower lets the text run underneath the
    // control beside it. It was 68 to hold BRIGHTNESS, a slider that no
    // longer exists, and those twelve pixels were costing every slider,
    // the colour well and the colormap list their left-hand reach.
    // 58 either side. The right-hand column is four pixels wider than the box
    // that sits in it, on purpose: that is what makes the slider track start
    // and stop exactly where Auto and Log do in the row above, so the middle
    // of the panel reads as one column rather than two that nearly agree.
    gridTemplateColumns: "58px 1fr 58px",
    alignItems: "center",
    gap: 6,
    padding: "2px 12px",
    color: "var(--text-muted)",
    fontSize: 10,
  },
  controlLabel: { textTransform: "uppercase", letterSpacing: ".04em" },
  // A toggle that is on: the same blue the sliders carry, so "lit" reads as
  // "active" without a legend.
  autoButtonOn: {
    background: "var(--accent-selection)",
    borderColor: "var(--accent)",
    color: "var(--accent-selection-text)",
  },
  // margin: 0 because the browser gives a range input two pixels of its own,
  // which pushed every track two to the right of the column it lives in --
  // and so two off the buttons above it that share that column.
  range: {
    width: "100%",
    margin: 0,
    cursor: "pointer",
  },
  value: { color: "var(--text-secondary)", textAlign: "right", fontVariantNumeric: "tabular-nums" },
  // The typed twin of the value read-out: same column, same right-aligned
  // numerals, with just enough of a border to say "you may type here".
  valueBox: {
    // The same block as everything else in this panel, held to the right of
    // its slightly wider column so its edge stays in the column the boxes
    // above it make.
    width: ROW_ITEM,
    justifySelf: "end",
    height: ROW_HEIGHT,
    // Padding and border inside the width, or the box overflows its column
    // by their sum and stops lining up with the buttons above it.
    boxSizing: "border-box",
    background: "var(--input-bg)",
    border: "1px solid var(--subtle-border)",
    borderRadius: 3,
    color: "var(--text-secondary)",
    font: "inherit",
    fontVariantNumeric: "tabular-nums",
    textAlign: "right",
    padding: "1px 3px",
  },
};
