import React from "react";
import ChannelControls from "./ChannelControls.jsx";
import ColourChooser from "./ColourChooser.jsx";
import Eye from "./Eye.jsx";
import VolumeMode from "./VolumeMode.jsx";
import { styles } from "./layer-panel-styles.js";

/**
 * The layer list, in napari's shape: one row per layer, an eye to hide it, a
 * swatch to recolour it.
 *
 * Deliberately the only chrome on screen. Everything the engine would otherwise
 * put up -- its own layer panel, top bar and dialogs -- is off, so this is the
 * single place layers are controlled and there is no second owner to fight.
 */
export default function LayerPanel({
  layers,
  included = null,
  state,
  mode,
  groupOrder = [],
  groupState = {},
  onToggle,
  onColor,
  onOpacity,
  onWindow,
  onMeasureHere,
  onLut,
  volumeMode = "max",
  onVolumeMode,
  volumeGain = 0,
  onVolumeGain,
  volumeAttenuation = 0,
  onVolumeAttenuation,
  depthSamples = 256,
  onDepthSamples,
  displayScales = { x: 1, y: 1, z: 1 },
  onDisplayScales,
  selected = 0,
  onSelect,
  canOpen = true,
  lookupTables = [],
  onGroupToggle,
  onOpenStore,
  onCloseGroup,
  busy = false,
  notice = null,
}) {
  const [collapsed, setCollapsed] = React.useState({});

  // Every row, paired with the position it holds in the panel's own state, so a
  // row can still be controlled after being gathered under its group.
  const rows = layers.map((layer, index) => ({ layer, index }))
    .filter(({ index }) => !included || included.has(index));
  const groups = groupOrder.length
    ? groupOrder
    : [...new Set(layers.map((layer) => layer.group || ""))];

  // One line per channel: whether it is showing, what colour it is, what it is
  // called. Everything adjustable lives in the single block of controls below the
  // list, and applies to whichever line is picked out — the way napari does it.
  // The reason is simply that there is not room otherwise: with every slider on
  // every row, three channels filled a tall screen and six could not be seen at
  // all. Adjusting one channel at a time is also how the work actually goes.
  const renderRow = ({ layer, index }) => {
    const { visible } = state[index];
    const chosen = index === selected;
    return (
      <div
        key={layer.name}
        style={{ ...styles.layer, ...(chosen ? styles.layerChosen : null) }}
        onClick={() => onSelect?.(index)}
        aria-current={chosen ? "true" : undefined}
      >
        <div style={styles.row}>
          <button
            onClick={() => onToggle(index)}
            style={{ ...styles.eye, opacity: visible ? 1 : 0.4 }}
            title={visible ? "Hide this channel" : "Show this channel"}
            aria-label={`toggle ${layer.name}`}
          >
            <Eye open={visible} />
          </button>
          {/* The colour, and the way to change it: press the square and the
              list of everything this channel could be painted in opens. */}
          <ColourChooser
            layer={layer}
            entry={state[index]}
            names={lookupTables}
            onPick={(choice) => {
              if (choice.lut) {
                onLut?.(index, choice.lut);
              } else {
                onLut?.(index, null);
                onColor?.(index, choice.rgb);
              }
            }}
          />
          <span style={styles.name} title={layer.name}>
            {layer.name}
          </span>
        </div>
      </div>
    );
  };

  return (
    <section style={styles.panel} aria-label="layer panel">
      {/* Choosing folders by hand is for using the viewer on its own. During a
          run the workflow decides what is shown, so this whole box is absent —
          see `allow_open` in the server. */}
      {canOpen && onOpenStore && (
        <div style={styles.card}>
          <div style={styles.headingRow}>
            <span style={styles.heading}>load data</span>
            <button
              type="button"
              onClick={onOpenStore}
              disabled={busy}
              style={styles.openButton}
              aria-label="open images"
              title="Choose a folder of images to show"
            >
              {busy ? "…" : "choose folder"}
            </button>
          </div>
        </div>
      )}
      {/* Each section sits on the same lighter card, so the darker panel
          ground showing between them is what separates one from the next. */}
      <div style={styles.card}>
      <div style={styles.headingRow}>
        <span style={styles.heading}>data</span>
      </div>
      {notice && (
        <div style={styles.notice} role="alert">
          {notice}
        </div>
      )}
      {!layers.length && (
        <div style={styles.empty}>Open the folder your run is writing into.</div>
      )}
      <div style={styles.list}>
      {groups.map((group, position) => {
        const members = rows.filter(({ layer }) => (layer.group || "") === group);
        if (!members.length) return null;
        const settings = groupState[group] || { visible: true };
        const isCollapsed = collapsed[group];
        // A group with no name is a store that carried no acquisition type in its
        // filename. It still needs to appear, so its rows are shown plainly with
        // no header rather than hidden under an empty heading.
        if (!group) return <div key="ungrouped">{members.map(renderRow)}</div>;
        return (
          <div key={group} style={styles.group}>
            <div style={styles.groupHead}>
              <button
                onClick={() => setCollapsed((c) => ({ ...c, [group]: !c[group] }))}
                style={styles.disclose}
                aria-label={`${isCollapsed ? "expand" : "collapse"} ${group}`}
                aria-expanded={!isCollapsed}
              >
                {isCollapsed ? "▸" : "▾"}
              </button>
              <button
                onClick={() => onGroupToggle?.(group)}
                style={{ ...styles.eye, opacity: settings.visible ? 1 : 0.4 }}
                aria-label={`toggle group ${group}`}
                title={settings.visible ? "Hide this acquisition" : "Show this acquisition"}
              >
                <Eye open={settings.visible} />
              </button>
              <span style={styles.groupName} title={group}>
                {group}
              </span>
              {/* Closing an acquisition by hand belongs to the browse-your-own
                  workflow; during a run the workflow decides what is shown, so
                  the button is absent then -- same rule as the folder chooser. */}
              {canOpen && onCloseGroup && (
                <button
                  type="button"
                  onClick={() => onCloseGroup(group)}
                  disabled={busy}
                  style={styles.close}
                  aria-label={`close ${group}`}
                  title="Stop showing this acquisition (the files are not touched)"
                >
                  ×
                </button>
              )}
            </div>
            {!isCollapsed && <div style={styles.members}>{members.map(renderRow)}</div>}
          </div>
        );
      })}
      </div>
      </div>
      {/* The settings sit directly under the list they act on: pick a channel
          above, adjust it here. The block names the channel it is adjusting,
          so the pairing can be read rather than remembered. */}
      {layers[selected] && state[selected] && (
        <ChannelControls
          layer={layers[selected]}
          index={selected}
          entry={state[selected]}
          mode={mode}
          onToggle={onToggle}
          lookupTables={lookupTables}
          onWindow={onWindow}
          onMeasureHere={onMeasureHere}
          onOpacity={onOpacity}
          onColor={onColor}
          onLut={onLut}
          displayScales={displayScales}
        />
      )}
      {/* How the volume is drawn. These act on the whole view rather than on
          one channel, so they live in their own section, shown only while the
          3-D view is. */}
      {mode === "volume" && (
        // marginTop matches the gap above the settings block: the display
        // settings carry marginBottom 12 of their own, so 4 more makes the
        // same clear 16 pixels of separation.
        <div style={{ ...styles.card, marginTop: 4 }}>
          <div style={styles.headingRow}>
            <span style={styles.heading}>3d viewer</span>
          </div>
          <VolumeMode
            volumeMode={volumeMode}
            onVolumeMode={onVolumeMode}
            gain={volumeGain}
            onGain={onVolumeGain}
            attenuation={volumeAttenuation}
            onAttenuation={onVolumeAttenuation}
            depthSamples={depthSamples}
            onDepthSamples={onDepthSamples}
            displayScales={displayScales}
            onDisplayScales={onDisplayScales}
          />
        </div>
      )}
    </section>
  );
}

// -- how it all looks ---------------------------------------------------------

// What separates one block of the bar from the next: a rule, and enough room on
// either side of it that the eye reads two sections rather than one long list.
