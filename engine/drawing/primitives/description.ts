/**
 * What a page tells the engine to draw: one description per layer, and the one
 * rule for how layers meet.
 *
 * A page owns its controls -- which channels show, their colours, their
 * windows -- and turns them into these descriptions; the primitives turn the
 * descriptions into the engine's layers (see `sync.ts`). Nothing here touches
 * the engine or the browser, so a plain Node process can check it.
 *
 * The rule (Thom, 2026-10-18): the channels of one place always mix -- their
 * coloured brightnesses are added -- and where two places of one group cover
 * the same ground, the one that came in last covers the other. In a picture
 * already composed into one store, its channels live together and its places
 * were laid down in that order (or averaged, when a page asks for it), so
 * every layer simply adds. Only a channel drawn straight from several stores
 * needs more: `coverPlacesMixChannels` gives each place its own ground to
 * stand on.
 */

import { COVERAGE_SHADER, GROUND_SHADER } from "../programs.js";

/** How the volume is drawn: a projection of the brightest or the dimmest voxel, or accumulated. */
export type VolumeMode = "max" | "on" | "min";

/**
 * One image layer as the engine is told to draw it: neuroglancer's own layer
 * description, plus the few fields the primitives read to know which stores
 * have changed and how a layer is held (neuroglancer ignores those).
 */
export interface EngineLayer {
  type: "image";
  name: string;
  /** Whole addresses, scheme included: the primitives never work one out. */
  source: string[];
  visible?: boolean;
  persistentFlat?: boolean;
  boundaryHeld?: boolean;
  frameCounts?: number[];
  sourceIds?: string[];
  sourceRevisions?: number[];
  sourceGeometryRevisions?: number[];
  localPosition?: number[];
  shader?: string;
  shaderControls?: Record<string, unknown>;
  blend?: "additive" | "default";
  opacity?: number;
  volumeRendering?: VolumeMode;
  volumeRenderingDepthSamples?: number;
  volumeRenderingGain?: number;
}

/**
 * A layer of a kind only one page draws -- an annotation layer, a mask. The
 * primitives build it from its description, hand it its stores as they
 * arrive, put it in its place in the order and show or hide it; every other
 * setting is the page's own, written by the page's hook (see `createViewer`'s
 * `ownSettings`).
 */
export interface PageLayer {
  type: string;
  name: string;
  source: string | string[];
  visible?: boolean;
}

/** One layer of the scene a page asks for. */
export type SceneLayer = EngineLayer | PageLayer;

/** Whether a description is an image layer, the one kind the primitives write settings for. */
export function isEngineLayer(layer: SceneLayer): layer is EngineLayer {
  return layer.type === "image";
}

/** The per-store fields of a description, which travel with each store when a row is split. */
const PER_STORE = ["frameCounts", "sourceIds", "sourceRevisions", "sourceGeometryRevisions"] as const;

/** One channel of one group, drawn from one store or several. */
export interface ChannelRow {
  layer: EngineLayer;
  /**
   * Which place each store shows, in the order of `layer.source`. Stores of
   * different channels that show the same place carry the same name. Left
   * out, each store is its own place, and stores shared between rows (one
   * store holding several channels) are the same place.
   */
  places?: readonly string[];
  /** Where each store's coverage is read, when the page knows which of its ground holds data. */
  coverage?: readonly string[];
}

/**
 * The ground a layer stands on: black wherever its coverage says there is
 * data, covering what lies beneath. One coverage store per image store, so the
 * layer count does not grow with places.
 */
export function coverageLayerFor(layer: EngineLayer, coverage: readonly string[], blend: "additive" | "default"): EngineLayer {
  return {
    type: "image", name: `__coverage__${layer.name}`,
    source: [...coverage],
    sourceIds: layer.sourceIds?.map(id => `${id}/coverage`),
    sourceRevisions: layer.sourceRevisions,
    sourceGeometryRevisions: layer.sourceGeometryRevisions,
    frameCounts: layer.frameCounts, localPosition: layer.localPosition,
    persistentFlat: layer.persistentFlat,
    boundaryHeld: layer.boundaryHeld,
    visible: layer.visible, opacity: 1,
    blend,
    shader: COVERAGE_SHADER,
  };
}

/**
 * Whether some of these rows are drawn straight from several stores while the
 * group has more than one channel -- the one case where adding layers would
 * also add overlapping places into a bright seam, and covering would hide every
 * channel but the top one.
 */
export function needsAGroundPerPlace(rows: readonly ChannelRow[]): boolean {
  return rows.length > 1 && rows.some(row => row.layer.source.length > 1);
}

/**
 * Draw a group's channels place by place: each place stands on its own
 * black ground, which covers whatever was drawn before it, and its channels
 * are added on top of that ground.
 *
 * Neuroglancer blends a layer's stores one pass at a time with the layer's own
 * blend, so a layer cannot add its channels and cover its places at once.
 * Split by place, it can: the ground covers, the channels add. The places are
 * drawn in the order they first appear in the rows -- the order the stores
 * came in -- so the last one covers. A place's ground is drawn from its
 * coverage store where the page has one, and from its first store otherwise.
 *
 * The cost is layers: one per place for the ground and one per channel and
 * place. A folder the viewer can compose into one picture never comes here.
 *
 * The first place's channels keep their rows' own names, so a row that was
 * drawn from one store and gains a second -- stores arriving one at a time
 * while something is still writing -- keeps the layer it had, with everything fetched for it, and only the new
 * place's layers are built.
 */
export function coverPlacesMixChannels(rows: readonly ChannelRow[]): EngineLayer[] {
  const order: string[] = [];
  const at = new Map<string, { row: ChannelRow; index: number }[]>();
  const shared = new Map<string, string>();
  for (const row of rows) {
    row.layer.source.forEach((url, index) => {
      const place = row.places?.[index] ?? shared.get(url) ?? url;
      shared.set(url, place);
      if (!at.has(place)) {
        at.set(place, []);
        order.push(place);
      }
      at.get(place)!.push({ row, index });
    });
  }
  const first = rows[0].layer.name;
  return order.flatMap((place, nth) => {
    const here = at.get(place)!;
    const standing = here[0];
    const one = storeOf(standing.row.layer, standing.index, `${first}@${place}`);
    const ground: EngineLayer = {
      ...one,
      name: `__ground__${first}@${place}`,
      visible: here.some(({ row }) => row.layer.visible !== false),
      shader: GROUND_SHADER,
      blend: "default",
      opacity: 1,
    };
    delete ground.shaderControls;
    const covered = standing.row.coverage?.[standing.index];
    if (covered) {
      ground.source = [covered];
      ground.shader = COVERAGE_SHADER;
      ground.sourceIds = ground.sourceIds?.map(id => `${id}/coverage`);
    }
    const channels = here.map(({ row, index }) => {
      const drawn = storeOf(row.layer, index, nth === 0 ? row.layer.name : `${row.layer.name}@${place}`);
      drawn.blend = "additive";
      drawn.opacity = 1;
      return drawn;
    });
    return [ground, ...channels];
  });
}

/** One store of a description, as a description of its own. */
function storeOf(layer: EngineLayer, index: number, name: string): EngineLayer {
  const one: EngineLayer = { ...layer, name, source: [layer.source[index]] };
  for (const field of PER_STORE) {
    const values = layer[field] as unknown[] | undefined;
    if (values) (one as unknown as Record<string, unknown[]>)[field] = [values[index]];
  }
  return one;
}
