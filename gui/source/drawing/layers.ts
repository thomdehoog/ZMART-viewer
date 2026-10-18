/**
 * Turning what the panel says into what the engine should draw.
 *
 * This is the translation layer, and it is kept on its own because none of it
 * touches React or the engine: give it the panel's state and it hands back plain
 * descriptions of layers, which the engine's side then applies. All it asks of
 * the browser is the page's own address, which every store's is written
 * against. That makes it the easiest part of the viewer to reason about and to
 * check, and it keeps the shell free of the fiddly business of writing shader
 * programs.
 */

// The shader programs, their colour maps and their controls are the engine's,
// shared with the operator page of ZMART-interface; see engine/drawing/programs.js.
// What a description is, and the rule for how layers meet, are the drawing
// primitives'; this file is the viewer's own panel turned into them.
import { shaderControlsFor, shaderFor } from "../../../engine/drawing/programs.js";
import {
  coverageLayerFor,
  coverPositionsMixChannels,
  needsAGroundPerPlace,
} from "../../../engine/drawing/primitives/description.ts";
import type { ChannelRow, EngineLayer, VolumeMode } from "../../../engine/drawing/primitives/description.ts";
import type { Config, LayerSpec } from "../asking-python.ts";
import type { Rgb } from "../colours.ts";
import type { Window } from "../contrast-window.ts";
import type { Mode } from "../display-axes.ts";

/** What the panel holds for one channel: what the operator has set on it. */
export interface LayerState {
  visible: boolean;
  color: Rgb | null | undefined;
  /** A colour map's name, or null for the flat colour. */
  lut: string | null;
  opacity: number;
  /** Null until the operator moves a contrast handle: until then the resting window. */
  window: Window | null;
}

/** Whether each acquisition type is shown, by its name. */
export type GroupState = Record<string, { visible: boolean }>;

/** A row as `layersFor` hands it on: a channel, cut to one depth where the flat view splits it. */
type Row = LayerSpec & { depth?: "flat" | "stack" };

// How one channel is recognised again after something has been opened or closed.
// The acquisition type and the channel name together, because that pair is what
// stays the same across such a change: a row's position in the list moves as soon
// as anything is added or removed, so a number would quietly come to mean a
// different channel. This is the key the panel carries colour and contrast across
// on, and it never leaves the page.
export function layerKey(spec: LayerSpec): string {
  const view = spec.view;
  return `${spec.group}/${spec.name}` + (view ? `/${view.acquisition}/${view.type}/${view.method || ""}` : "");
}

// What the engine is told to call a layer. The engine keeps one flat list and
// requires the names in it to be unique, while the panel shows them gathered under
// their acquisition type. Two types can easily hold a channel of the same name --
// both an overview and a target scan have a "marker-a" -- so the name handed over
// carries the type with it. The panel still shows the short name on screen; this is
// only what the engine hears.
export function engineName(spec: LayerSpec): string {
  if (spec.view) return layerKey(spec);
  return spec.group ? `${spec.group} · ${spec.name}` : spec.name;
}

/**
 * The brightness window a layer rests at before the operator touches anything.
 *
 * The run's own recorded window comes first: that is what the microscopist
 * asked for, and it stays authoritative. Where the run recorded nothing, the
 * window measured from the pixels (the same one the Auto button applies) is
 * used instead of the camera's whole range -- a real specimen sits in the
 * bottom few per cent of that range, so the whole range showed a picture that
 * was very nearly black until somebody pressed Auto. Watched on every live run
 * before this fallback existed. Both the canvas and the panel's sliders read
 * the window through here, so they can never disagree about where a fresh
 * layer starts.
 */
export function restingWindow(spec: LayerSpec, volumetric: boolean): Window | null {
  const asked = volumetric ? spec.volumeWindow || spec.window : spec.window;
  return asked || spec.histogram?.autoWindow || null;
}

/**
 * Turn the panel's state into the layer list the engine should draw.
 *
 * Two things are decided here. The **order** follows the panel, because the
 * engine composites in list order and so the order is what decides which
 * acquisition type sits on top of which. And **visibility** needs both the
 * group and the channel: hiding a group hides its channels without forgetting
 * which of them were individually switched off. Opacity is the channel's own,
 * from the slider in its display settings -- the per-acquisition opacity that
 * used to multiply in here was removed with its slider (2026-08-18): two
 * opacities acting on one channel read as a control that does nothing.
 */
export function layersFor(
  config: Config, mode: Mode, layerState: LayerState[], groupState: GroupState,
  groupOrder: string[], volumeMode: VolumeMode = "max",
  volume: { attenuation?: number; depthSamples?: number; gain?: number } = {},
): EngineLayer[] {
  const volumetric = mode === "volume";
  const rows = config.layers.flatMap((spec, index): { spec: Row; index: number }[] => {
    const depths = spec.sourceDepths;
    if (volumetric || !depths) return [{ spec, index }];
    return (["flat", "stack"] as const).flatMap((depth) => {
      const indices = depths.flatMap((kind, i) => (kind === depth ? [i] : []));
      if (!indices.length) return [];
      const pick = <T>(values: T[] | undefined) => values && indices.map((i) => values[i]);
      const selected: Row = {
        ...spec, depth,
        sources: pick(spec.sources), coverageSources: pick(spec.coverageSources),
        sourceIds: pick(spec.sourceIds), sourceRevisions: pick(spec.sourceRevisions),
        frameCounts: pick(spec.frameCounts), sourcePlaces: pick(spec.sourcePlaces),
      };
      return [{ spec: selected, index }];
    });
  });
  const ordered = groupOrder.flatMap((group) =>
    rows.filter(({ spec }) => (spec.group || "") === group),
  );
  // Anything whose group is somehow not in the order still gets drawn: a layer
  // silently missing is far worse than one in an unexpected place.
  const seen = new Set(ordered.map(({ index }) => index));
  const all = [...ordered, ...rows.filter(({ index }) => !seen.has(index))];

  const layers = all.map(({ spec, index }) => {
    const { visible, color, opacity, lut, window: windowOverride } = layerState[index];
    const group = groupState[spec.group || ""] || { visible: true };
    const displayWindow = windowOverride || restingWindow(spec, volumetric);
    // A segmentation mask is drawn by a different kind of layer: the engine gives
    // every object its own colour and lets one be picked out, which is what a mask
    // is for. Brightness and contrast mean nothing on an identity number, so none
    // of that is sent.
    const isMask = spec.kind === "segmentation";
    const layer: EngineLayer = {
      type: isMask ? "segmentation" : "image",
      name: engineName(spec) + (spec.depth ? `__${spec.depth}` : ""),
      persistentFlat: spec.depth === "flat" || spec.view?.type === "projection",
      boundaryHeld: spec.view?.type === "top",
      // A row may be drawn from several stores -- several positions of the same
      // acquisition type. The engine takes the list and places each one using the
      // stage position recorded inside it.
      source: (spec.sources || [spec.source!]).map(
        (source) => `${window.location.origin}${source}`,
      ),
      // How many frames the server has counted on disk for each store above, in the
      // same order. It is carried through to the engine for one narrow purpose: it
      // is what says which stores have actually grown since the last look, and
      // re-reading a store is only worth doing when it has. A row with no time axis
      // leaves this undefined and is never re-read at all.
      //
      // Per store rather than for the row as a whole, and that distinction is the
      // whole point. A row's own frame count is the highest across its positions, so
      // one position advancing moves it and says nothing about which one moved --
      // which left the engine going back to every store on the row to ask. See
      // syncSources in layer-sync.ts for what that cost: every store on the row
      // re-read for one position's new frame.
      frameCounts: spec.frameCounts ?? undefined,
      // Manifest-driven sources keep a stable address.  Their separately carried
      // identities and committed revisions tell layer-sync.ts exactly which existing
      // source must be refreshed after a publication, without making the address
      // itself look like a new image.
      sourceIds: spec.sourceIds ?? undefined,
      sourceRevisions: spec.sourceRevisions ?? undefined,
      sourceGeometryRevisions: spec.sourceGeometryRevisions ?? undefined,
    };
    // Where a store holds its channels inside one array, this is what picks the
    // channel: the engine exposes it as a per-layer dimension, and each row pins
    // it to its own index. Nothing splits the data; one store feeds every row.
    if (Array.isArray(spec.localPosition)) layer.localPosition = spec.localPosition;
    else if (spec.channelIndex != null) layer.localPosition = [spec.channelIndex];
    layer.visible = visible && group.visible;
    if (isMask) {
      layer.selectedAlpha = opacity;
      layer.notSelectedAlpha = opacity;
      return layer;
    }
    layer.shader = shaderFor(volumetric, lut, Boolean((config.transparentBackground || spec.view) && spec.opaque));
    // Channels mix: each row is added to its neighbours. Adding is safe only
    // while the row is fed by a single store: the engine draws each store as
    // its own pass, so a row stitched from overlapping tiles would sum them
    // into a bright seam along every join. Such a row covers instead, which
    // is right for an acquisition of one channel; where it has several, the
    // flat view splits them place by place below, so the later position
    // covers the earlier and its channels still add. A volume keeps the
    // covering rule, since no ground can be laid under a ray.
    if ((spec.sources || [spec.source]).length === 1) {
      layer.blend = "additive";
    }
    const controls = shaderControlsFor(displayWindow, volumetric, opacity,
                                       color, lut, volume.attenuation ?? 0);
    if (controls) layer.shaderControls = controls;
    if (volumetric) {
      // Which way the ray is turned into a colour, and it is not a detail.
      // "on" accumulates every voxel it passes, which on sparse specimen
      // gives a milky picture -- measured on 6 August 2026 over a run of a
      // thousand positions, a spread of 8 grey levels against 41 for "max".
      // "max" keeps the brightest voxel along each ray, which is what a
      // microscopist means by a projection and what napari calls mip; it
      // needs no transparency tuned before anything can be seen. "min" is
      // the same for dark objects on a bright field.
      layer.volumeRendering = volumeMode;
      // This, not the zoom, chooses which copy of the image the volume is drawn from.
      // How many steps a ray takes through the volume, and the number that
      // decides how sharp it is allowed to be: the engine will only draw from a
      // level whose voxels are bigger than one step, so a small budget pins a
      // large specimen to its coarsest copy however far you zoom in. Measured on
      // a 75 GB skin volume: 256 stays at 27.2 um until absurdly close, 2048
      // reaches full resolution once zoomed in, and 32768 would refine from the
      // opening view but is too slow to drive. Which of those is right depends
      // on the specimen and the machine, which is why it is a control rather
      // than a constant -- the flag it falls back to is only the starting point.
      layer.volumeRenderingDepthSamples = volume.depthSamples ?? config.depthSamples;
      // Brightness for the accumulated mode, which the engine has had all
      // along and this viewer never set. Accumulation piles alpha up along
      // the ray and washes out; this is the knob built to answer that. The
      // engine raises it to a power, so nought is unchanged.
      layer.volumeRenderingGain = volume.gain ?? 0;
    } else {
      // Which of the two places a weight is read from depends on how this row
      // meets the rows beneath it, and it must be read from exactly one of
      // them or a channel fades as its own setting SQUARED -- measured on this
      // viewer, a half reading a quarter.
      //
      // A row the engine ADDS contributes precisely the colour its program
      // emits, and the weight is already in that colour, so this stays at one.
      // A row that COVERS is the other case: there the engine reads this
      // number to let a faded row reveal what lies beneath it, which the
      // colour alone cannot do, and the old squared fade is the price. No one
      // program can avoid it while a single number has to both dim a row and
      // reveal what it is covering.
      layer.opacity = layer.blend === "additive" ? 1 : opacity;
    }
    return layer;
  });
  if (volumetric) return layers;
  const address = (source: string) => `${window.location.origin}${source}`;
  // Black underpainting supplies acquisition coverage independently of intensity.
  // Coverage goes below its flat/stack channel block, so acquired black can
  // cover an earlier picture without obscuring channels in its own block.
  // One tiled source per image source; the layer count does not grow with positions.
  const coverage = layers.flatMap((layer, index): EngineLayer[] => {
    const sources = all[index].spec.coverageSources;
    if (layer.type !== "image" || !sources?.length) return [];
    const blend = all[index].spec.depth || all[index].spec.view ? "default" : "additive";
    return [coverageLayerFor(layer, sources.map(address), blend)];
  });
  const result: EngineLayer[] = [], handled = new Set<string>();
  const block = (spec: Row) => JSON.stringify([spec.group || "", spec.view?.acquisition]);
  for (let i = 0; i < layers.length; ++i) {
    const spec = all[i].spec;
    const group = block(spec);
    if (handled.has(group)) continue;
    handled.add(group);
    for (const depth of [undefined, "flat", "stack"]) {
      const chosen = layers.flatMap((_, j) => block(all[j].spec) === group
        && all[j].spec.depth === depth ? [j] : []);
      const rows: ChannelRow[] = chosen.map(j => ({
        layer: layers[j],
        places: all[j].spec.sourcePlaces,
        coverage: all[j].spec.coverageSources?.map(address),
      }));
      // Channels drawn from several stores each: every place stands on a
      // ground of its own, which also stands in for the block's coverage.
      if (needsAGroundPerPlace(rows)) {
        result.push(...coverPositionsMixChannels(rows));
        continue;
      }
      const names = new Set(chosen.map(j => `__coverage__${layers[j].name}`));
      result.push(...coverage.filter(layer => names.has(layer.name)), ...chosen.map(j => layers[j]));
    }
  }
  return result;
}
