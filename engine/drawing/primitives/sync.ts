/**
 * Keeping the engine's picture in step with a page's scene, without rebuilding it.
 *
 * A page and the engine hold the same information in two different shapes.
 * A page thinks in groups and channels; the engine thinks in a flat list of
 * layers. Something has to carry changes from one to the other, and this
 * is that something.
 *
 * The obvious way to do it is to hand Neuroglancer a fresh description of the
 * whole scene every time anything changes, and let it sort out the difference.
 * Neuroglancer offers exactly that call, and we used it. It turns out not to work
 * the way the name suggests: `restoreState` does not compare the new description
 * against the old one. It throws every existing layer away and builds the lot
 * again from scratch. So nudging one contrast slider was quietly demolishing and
 * rebuilding every layer on screen.
 *
 * Three things go wrong when that happens, and they get worse as the data gets
 * bigger:
 *
 * 1. Anything drawn on a layer is lost. A drawn annotation lives inside its
 *    layer, and the rebuilt layer is a new, empty one — so the drawing disappears
 *    from the image while the page's list beside it still claims it is there.
 * 2. The pieces of image already fetched are dropped and asked for again. They
 *    are kept in a shared cache that survives for a moment after the last layer
 *    lets go of them, so a fast enough rebuild often catches them still warm and
 *    nothing is refetched. That is luck, not design, and on several hundred
 *    gigabytes of stores — where a rebuild can easily land while pieces are still
 *    in flight — it is luck that runs out.
 * 3. Everything the engine had worked out about the scene has to be worked out
 *    again: which copy of the image to draw, how the layers line up in space, which
 *    shader programs to compile. None of it changed.
 *
 * So this module does the comparing itself. It looks at what the engine currently
 * has, works out the smallest set of changes that would turn it into what the
 * page is asking for, and makes only those. A layer is built only when it is
 * genuinely new, and thrown away only when it has genuinely gone. Everything else
 * — brightness, colour, opacity, whether a channel is showing, what order the
 * groups are drawn in — is written straight onto the layer that is
 * already there, which is what Neuroglancer expects and is fast enough to do on
 * every drag of a slider.
 */

import { makeLayer, deleteLayer } from "neuroglancer/unstable/layer/index.js";
import type { LayerManager, ManagedUserLayer, UserLayer } from "neuroglancer/unstable/layer/index.js";
import { WatchableCoordinateSpaceTransform } from "neuroglancer/unstable/coordinate_transform.js";
import type { CoordinateSpaceTransform } from "neuroglancer/unstable/coordinate_transform.js";
import type { WatchableValueInterface } from "neuroglancer/unstable/trackable_value.js";
import type { Viewer } from "neuroglancer/unstable/viewer.js";
import { keepDepthLocal, refreshGeometry } from "../embedding.js";
import { isEngineLayer } from "./description.ts";
import type { EngineLayer, PageLayer, SceneLayer } from "./description.ts";
import type { Feeding } from "./feeding.ts";
import {
  advancingSourceRevisions,
  memoEntriesForStableSource,
  rememberedSourceRevisions,
  sourceRevisionsFor,
} from "./revisions.ts";
import type { RememberedRevisions } from "./revisions.ts";

// -- the shapes this file works with ------------------------------------------

/**
 * What a layer's description says about its stores. Every layer of the scene
 * can be read this way; a layer of a page's own kind simply says nothing.
 */
type StoreFields = Partial<Pick<EngineLayer,
  "frameCounts" | "sourceIds" | "sourceRevisions" | "sourceGeometryRevisions"
  | "persistentFlat" | "boundaryHeld">> & { source?: string | string[] };

// Neuroglancer's image and segmentation layers ship no declarations,
// so the settings this file writes on them are named here, and each layer is
// taken as its kind once, where its kind is known.

/** A setting the engine restores from its own description of it. */
interface Restorable {
  restoreState(value: unknown): void;
}

/** The image layer's settings that follow the page. */
interface ImageLayerSettings {
  fragmentMain: WatchableValueInterface<string>;
  blendMode: Restorable;
  shaderControlState: Restorable;
  opacity: WatchableValueInterface<number>;
  volumeRenderingMode: Restorable;
  volumeRenderingDepthSamplesTarget: WatchableValueInterface<number>;
  volumeRenderingGain: WatchableValueInterface<number>;
}

/** The mask layer's two strengths. */
interface SegmentationLayerSettings {
  displayState: {
    selectedAlpha: WatchableValueInterface<number>;
    notSelectedAlpha: WatchableValueInterface<number>;
  };
}

/**
 * A piece of the engine's memory: a holder of decoded image, or of what was
 * read about a store, that can be asked to drop what it holds.
 */
interface Holder {
  invalidateCache?: () => void;
}

/**
 * The engine's memory of what it has read and decoded, reached past its
 * declared types: Neuroglancer's chunk manager ships no declaration. `memoize`
 * files every holder under the question that made it; `rpc.objects` is every
 * object shared with the worker that draws.
 */
interface EngineMemory {
  memoize?: { map: Map<string, Holder> };
  rpc?: { objects: Map<number, Holder | null | undefined> };
}

/** The sources and holders already refreshed on one pass, so none is refreshed twice. */
interface Refreshed {
  sources: Set<string>;
  holders: Set<Holder>;
}

/** What one sync pass refreshed, for the tests to read: the cost of a refresh, counted. */
export interface SourceRefreshing {
  passes: number;
  sources: string[];
  metadataEntries: number;
  decodedEntries: number;
}

/** How many times the engine has been told to let go, and how many sources it asked the last time. */
export interface LettingGo {
  times: number;
  asked: number;
}

/**
 * How a page writes the settings of a layer of its own kind -- one the
 * primitives build and order but know nothing else about.
 */
export type OwnSettings = (layer: UserLayer, description: PageLayer) => void;

/** One engine's way of keeping its layers in step with a page's scene. */
export interface LayerSync {
  syncLayers(specs: SceneLayer[], options?: { reread?: boolean }): number;
  letGoOfDecodedPieces(): number;
  sourceRefreshing: SourceRefreshing;
  lettingGo: LettingGo;
}

/** The engine's memory, as this file reads it; see `EngineMemory`. */
function memoryOf(viewer: Viewer): EngineMemory | undefined {
  return viewer.chunkManager as EngineMemory | undefined;
}

function sourceList(spec: StoreFields): string[] {
  if (spec.source == null) return [];
  return Array.isArray(spec.source) ? spec.source : [spec.source];
}

function revisionsFor(spec: StoreFields) {
  return sourceRevisionsFor(sourceList(spec), spec.sourceIds, spec.sourceRevisions);
}

function geometryFor(spec: StoreFields): Map<string, number | undefined> {
  return new Map(sourceList(spec).map((url, i) => [url.split("|")[0], spec.sourceGeometryRevisions?.[i]]));
}

/**
 * Drop cached knowledge and decoded absence for one aggregate source only.
 *
 * A newly committed store region may occupy coordinates this source previously
 * found empty.  Metadata-only forgetting is therefore insufficient: the empty chunk
 * holder would survive and no pixel request would be made.  Neuroglancer files
 * both kinds of memoized object under keys containing the store folder; decoded
 * holders are the entries naming a constructor. Metadata identities are removed
 * so the existing layer's source re-resolution reads them again. Each matching
 * decoded holder is asked to clear its own chunks, while holders belonging to
 * every other stable URL remain untouched.
 */
function forgetOneStableSource(chunkManager: EngineMemory | undefined, url: string, refreshed: Set<Holder> = new Set()) {
  const remembered = chunkManager?.memoize?.map;
  const removed = { metadata: 0, decoded: 0 };
  if (!remembered) return removed;
  const matching = memoEntriesForStableSource(remembered.keys(), url);
  for (const question of matching.metadata) {
    if (remembered.delete(question)) removed.metadata += 1;
  }
  for (const question of matching.decoded) {
    const holder = remembered.get(question);
    if (holder && !refreshed.has(holder) && typeof holder.invalidateCache === "function") {
      refreshed.add(holder);
      holder.invalidateCache();
      removed.decoded += 1;
    }
  }
  return removed;
}

/**
 * Write the adjustable settings of one layer.
 *
 * These are the things a person changes constantly — contrast, colour,
 * opacity, whether a channel is showing — so this runs very often and does as
 * little as possible. Each setting is a value the engine watches, and writing the
 * value it already holds is ignored, so setting all of them and letting the engine
 * notice which actually moved is both correct and cheap.
 */
function applySettings(managed: ManagedUserLayer, spec: SceneLayer, ownSettings: OwnSettings | undefined): void {
  const visible = spec.visible !== false;
  if (managed.visible !== visible) managed.setVisible(visible);
  const layer = managed.layer;
  if (!layer) return;
  if (!isEngineLayer(spec)) {
    ownSettings?.(layer, spec);
    return;
  }
  if (spec.type === "image") {
    const image = layer as UserLayer & ImageLayerSettings;
    // The shader is the small program that turns stored numbers into what you
    // see: it carries the channel colour and any colour map. Setting it to the
    // text it already holds is ignored by the engine, so this is free whenever
    // the colour has not changed -- which is nearly always.
    if (spec.shader != null) image.fragmentMain.value = spec.shader;
    // How this layer blends with what is beneath travels with the description
    // rather than being set once: a layer that starts with one store may gain
    // more while something is still writing, and the covering rule must come
    // back with them -- left additive, their overlaps would sum into bright
    // seams.
    image.blendMode.restoreState(spec.blend ?? "default");
    // The contrast window and the 3-D opacity travel separately, as values for
    // controls the program declares. Sent this way they reach a program that is
    // already compiled, so dragging a contrast handle costs the graphics card
    // almost nothing however many layers are open.
    if (spec.shaderControls) image.shaderControlState.restoreState(spec.shaderControls);
    if (spec.opacity != null) image.opacity.value = spec.opacity;
    image.volumeRenderingMode.restoreState(spec.volumeRendering ?? "off");
    if (spec.volumeRenderingDepthSamples != null) {
      image.volumeRenderingDepthSamplesTarget.value = spec.volumeRenderingDepthSamples;
    }
    // Gain has to be named here or it goes nowhere. Nothing in this function
    // restores a whole specification; every field is carried across by a line of
    // its own, so a field added to the specification and not to this list is
    // dropped in silence -- which is exactly what happened when this control was
    // first added, and why it appeared to have no effect while the depth fade
    // beside it worked. That one travels as a shader control, and those are
    // restored wholesale two lines above.
    if (spec.volumeRenderingGain != null) {
      image.volumeRenderingGain.value = spec.volumeRenderingGain;
    }
  } else {
    const mask = layer as UserLayer & SegmentationLayerSettings;
    // A mask has no brightness to adjust; how strongly it is painted over the
    // image is the only thing there is to set.
    mask.displayState.selectedAlpha.value = spec.selectedAlpha ?? 1;
    mask.displayState.notSelectedAlpha.value = spec.notSelectedAlpha ?? 0;
  }
}

/**
 * Put the layers in the order the page lists them.
 *
 * This is a real setting rather than tidying: the engine paints the list from
 * bottom to top, so the order decides which layer covers which.
 */
function applyOrder(manager: LayerManager, names: string[]): void {
  // Where each layer currently sits, looked up once. Searching the list for every
  // name instead costs the square of the number of layers, which at a few thousand
  // is tens of milliseconds -- again on every step of a slider drag.
  const at = new Map(manager.managedLayers.map((managed, index) => [managed.name, index]));
  names.forEach((name, wanted) => {
    const here = at.get(name);
    if (here === undefined || here === wanted) return;
    manager.reorderManagedLayer(here, wanted);
    // Moving one layer shifts the others, so the map is rebuilt rather than
    // trusted. This happens only when the order has genuinely changed.
    manager.managedLayers.forEach((managed, index) => at.set(managed.name, index));
  });
}

/**
 * Make the layer keeping for one engine: everything it remembers about the
 * layers, and its counters, live here and die with the engine.
 */
export function makeLayerSync(viewer: Viewer, feeding: Feeding, ownSettings?: OwnSettings): LayerSync {
  // -- what each layer was last told ---------------------------------------------
  //
  // Working out the smallest set of changes means remembering what the engine was
  // handed last time, because the engine cannot always be asked. Everything
  // remembered here is filed against the layer it belongs to, in a kind of map that
  // lets go of an entry on its own once the layer is thrown away — so closing a
  // group hands the memory back without anything having to tidy up.

  // The addresses each layer was last given. Kept here rather than read back out of
  // the engine because Neuroglancer tidies up the addresses it is handed, so what
  // comes back out is not always character-for-character what went in — and a
  // comparison that got that wrong would add the same image over and over. A
  // WeakMap is used so that a layer being thrown away takes its entry with it.
  const sourcesApplied = new WeakMap<UserLayer, Set<string>>();

  // The last description each layer was given, kept so a refresh can rebuild the
  // same layer from outside a sync pass. Keyed by the managed layer, and weakly,
  // for the same reason as everything else here: a layer thrown away takes its
  // memory with it.
  const specApplied = new WeakMap<ManagedUserLayer, SceneLayer>();

  // For each layer, how many frames each of its stores was last known to hold: a map
  // from the store's address to its count. This is what decides which stores are worth
  // reading again, and it has to be per store rather than per layer -- see syncSources
  // for what asking the wrong question cost. Kept beside the addresses above and for the
  // same reason: a layer being thrown away takes its entry with it.
  const framesSeen = new WeakMap<UserLayer, Map<string, number | undefined>>();

  // The committed revision last applied for each stable aggregate source.  Kept
  // separately from the URL because the URL deliberately never changes when new
  // pixels are committed into it.
  const revisionsSeen = new WeakMap<UserLayer, RememberedRevisions>();

  // The geometry revision last applied for each of a layer's stores, by the store's
  // folder: a store whose geometry revision moves has changed shape, not only pixels.
  const geometrySeen = new WeakMap<UserLayer, Map<string, number | undefined>>();

  // Request-cost evidence exposed to the browser tests.  ``sources`` contains
  // only stable source identities that advanced on the most recent sync pass;
  // unchanged and unrelated sources must never appear here.
  const sourceRefreshing: SourceRefreshing = {
    passes: 0,
    sources: [],
    metadataEntries: 0,
    decodedEntries: 0,
  };

  // -- making the engine look at the disk again ---------------------------------
  //
  // The engine remembers everything it has ever read, and there is no time limit on
  // that memory. It is the right thing almost always, and it is exactly wrong while
  // something is still writing: what was true when the engine looked is no longer true.
  //
  // There are two ways of telling it to look again, and they are different sizes.
  // `letGoOfDecodedPieces` drops the picture the engine has already decoded, for the
  // whole scene. Refreshing one store's geometry drops only what was read about that
  // store, which is what a store gaining a step along time needs.

  // How many times the engine has let go, and how many sources it asked the last time.
  // Kept so that a test can tell "it was asked and nothing came of it" from "it was never
  // asked at all" -- two failures that look identical on screen and have quite different
  // causes. The handle carries it for a page to show its tests.
  const lettingGo: LettingGo = { times: 0, asked: 0 };

  /**
   * Let go of the pieces of image the engine has already decoded, so that looking again
   * really looks.
   *
   * This is what makes it possible to watch something that writes into **one** store
   * rather than into many — a single OME-Zarr, created empty at the start, with each
   * region written straight into its place. That layout is worth a great deal: what costs
   * the engine is the number of separate stores rather than the amount of data behind
   * them, and one store opens in a second and a half where three hundred take longer on
   * thirty times the requests.
   *
   * The difficulty is that the engine remembers every piece of image it has decoded, and
   * that includes the pieces it found to be empty. There is no time limit on that memory.
   * So a region written into a place the engine has already looked at is simply not
   * noticed: the picture goes on showing the emptiness decided on earlier and never goes
   * back to the disk. Measured, that is not "slow to appear" — it is **no request at all**, ever.
   *
   * So the sources are asked to let go, which the engine offers a way to do: the pieces
   * live in a separate worker, and this sends word to that worker to drop them. What comes
   * back is fetched afresh.
   *
   * **What it costs, and why it is affordable.** Only the pieces actually on screen are
   * fetched again — nine of them in the measurement — because the engine asks for what it
   * needs to draw and nothing else. That number follows the size of the window rather than
   * the size of the picture, so it is the same on a folder of forty terabytes as on one of
   * forty megabytes. Anything scrolled past is dropped and will be fetched again on the way
   * back, which is the price, and it is a fair one.
   *
   * **When this is called matters as much as what it does**, and the rule is narrow on
   * purpose. The viewer's own page calls it on exactly two occasions:
   *
   * 1. Whatever writes announced that it wrote into a store already open. It is the only
   *    one who knows, so it is believed.
   * 2. A store the page already knew about, which had nothing in it at all, has just
   *    gained something.
   *
   * An earlier attempt used a wider rule: an announcement arrived and the answer came back
   * completely unchanged. That reads well and is wrong in practice. Redundant announcements
   * are ordinary — the writer and a folder watcher both see the same write — so it fired
   * on every one of them, and measured, that cost a refetch of the whole view on every
   * store arriving. When a store arrives or grows along time, this is not called, and
   * nothing already fetched is thrown away.
   *
   * Returns how many sources were asked, so a test can tell "it was asked and nothing
   * happened" from "it was never asked".
   */
  function letGoOfDecodedPieces(): number {
    // Legacy write hints cover unversioned stores only. Revisioned sources are
    // refreshed by syncSources, even when the same write also emits a legacy hint.
    const memory = memoryOf(viewer);
    const shared = memory?.rpc?.objects;
    if (!shared) return 0;
    const remembered = memory?.memoize?.map;
    const excluded = new Set<Holder | undefined>();
    for (const managed of viewer.layerManager.managedLayers) {
      const spec = specApplied.get(managed);
      if (!spec) continue;
      for (const { url, revision } of revisionsFor(spec).values()) {
        if (!Number.isFinite(revision) || !remembered) continue;
        for (const key of memoEntriesForStableSource(remembered.keys(), url).decoded) {
          excluded.add(remembered.get(key));
        }
      }
    }
    let asked = 0;
    for (const [, held] of shared) {
      if (!held || excluded.has(held) || typeof held.invalidateCache !== "function") continue;
      excluded.add(held);
      held.invalidateCache();
      asked += 1;
    }
    lettingGo.times += 1;
    lettingGo.asked = asked;
    return asked;
  }


  // -- bringing the engine into line with the page ------------------------------
  //
  // This is the heart of the module: given the scene the page wants, change as
  // little as possible to reach it. One function per kind of change — the stores a
  // layer reads from, the settings a person turns, the order the layers are drawn
  // in — and `syncLayers` below puts them together.

  /**
   * Give one layer any images it does not yet have.
   *
   * A layer can be drawn from several stores at once — the same channel at a dozen
   * places — and while something is still writing, those stores arrive one at a time.
   * Adding the new one to the layer that is already there costs nothing; rebuilding
   * the layer would throw away the eleven stores that were fine.
   *
   * Only additions are made. A store is never removed on its own: closing things is
   * done a whole group at a time, which removes the layer outright, so there is no
   * case where a layer should quietly lose one of its stores.
   *
   * Returns how many images were added, so the caller knows whether the shape of
   * the scene changed.
   */
  function syncSources(
    layer: UserLayer,
    spec: StoreFields,
    reread: boolean,
    chunkManager: EngineMemory | undefined,
    forgotten: Set<string>,
    refreshed: Refreshed,
  ): number {
    const wanted = sourceList(spec);
    // Held as a set rather than a list. A layer can be drawn from thousands of
    // stores, and asking a list "do you already contain this?" for each of them
    // means walking the whole list every time -- which for a few thousand stores
    // is most of a second, spent on every single step of a
    // contrast drag, on the same thread the engine draws with.
    const already = sourcesApplied.get(layer) || new Set();
    const fresh = wanted.filter((url) => !already.has(url));
    const counts = spec.frameCounts;
    const lastCounts = framesSeen.get(layer);
    const grown =
      reread && Array.isArray(counts) && lastCounts
        ? wanted.filter((url, at) => lastCounts.get(url) !== undefined
                                     && lastCounts.get(url) !== counts[at])
        : [];
    const grownStores = new Set(grown.map((url) => url.split("|")[0]));
    if (Array.isArray(counts)) {
      framesSeen.set(layer, new Map(wanted.map((url, at) => [url, counts[at]])));
    }

    // A valid higher revision means something new was committed into a stable
    // aggregate source this layer is already drawing. Only the pixels have
    // changed: the picture behind that address is described once, when it is
    // declared, and its description never moves afterwards.
    //
    // So the refresh is pixels-only, done in place. forgetOneStableSource asks
    // each decoded holder to refresh itself, and that request rides the
    // keep-drawing-until-replaced delivery this repository patches into its
    // engine: the stale pieces keep drawing while the fresh bytes download, and
    // each is swapped for its replacement the moment it arrives. Nobody looking
    // ever sees a gap.
    //
    // What must NOT happen here is re-resolving the source's address
    // (`source.spec = { ...source.spec }`, which this block once did). That
    // looks free — same address in, same description out — but the engine
    // answers it by throwing the loaded source away first and reading the
    // description again from the server afterwards, and between those two
    // moments the layer has nothing to draw. While something was writing, that
    // road ran at every commit: the whole picture went black for 100–300 ms,
    // sixteen times in a row — the flicker of 2026-08-23, photographed frame
    // by frame in Chromium before it was understood. Since the description
    // cannot have changed, re-reading it bought nothing and cost the picture.
    const revisions = revisionsFor(spec);
    const lastRevisions = revisionsSeen.get(layer);
    const advanced = advancingSourceRevisions(lastRevisions, revisions);
    const previousGeometry = geometrySeen.get(layer);
    const geometry = geometryFor(spec);
    geometrySeen.set(layer, geometry);
    const geometryChanged = new Set<string>();
    if (revisions.size) {
      revisionsSeen.set(layer, rememberedSourceRevisions(revisions));
    }
    if (advanced.length) {
      const growing = new Map(
        advanced.map(([identity, value]) => [value.url.split("|")[0], identity]),
      );
      for (const source of layer.dataSources) {
        const store = source.spec.url.split("|")[0];
        const identity = growing.get(store);
        if (identity == null) continue;
        if (grownStores.has(store) || (previousGeometry?.has(store) && previousGeometry.get(store) !== geometry.get(store))) {
          geometryChanged.add(store);
          // Metadata extension/replacement owns this geometry change. Do not also
          // invalidate its decoded chunks through the pixels-only path below.
          refreshGeometry(source, chunkManager, refreshed.holders, forgotten);
          sourceRefreshing.sources.push(identity);
          continue;
        }
        if (!refreshed.sources.has(identity)) {
          const removed = forgetOneStableSource(chunkManager, store, refreshed.holders);
          sourceRefreshing.sources.push(identity);
          sourceRefreshing.metadataEntries += removed.metadata;
          sourceRefreshing.decodedEntries += removed.decoded;
          refreshed.sources.add(identity);
        }
      }
    }

    // Which of this row's stores have gained a frame since the last look.
    //
    // An announcement says only that *something* on disk has changed, never what, so
    // the counts the page passes on are the only way to tell. They are per store, and
    // that matters: a layer can hold a store for every place there is, and asking all
    // of them whether they have grown -- when the answer for all but one of them is
    // no -- was measured at a thousand stores as six thousand small requests and
    // eighteen seconds for a single step along time. Comparing per store
    // makes that one store, however many there are.
    //
    // A row with no time axis reports no counts and so is never re-read at all, which
    // is right: nothing about it can grow.
    if (grown.length) {
      // Re-read changed metadata while keeping the pixels held drawable. The NG
      // bounds-refresh patch extends compatible Zarr sources in place; replacing
      // source.spec would drop both image and coverage before their chunks return.
      // Forget cached metadata first, or the new extent never reaches the engine.
      //
      // Note that this happens *as well as* adding anything new below, not instead of
      // it. One announcement can mean both -- a store arrived and another grew along
      // time -- and an earlier version that treated them as alternatives quietly
      // stopped new stores appearing at all.
      // Matched on the folder each address points at rather than on the address
      // itself. Neuroglancer tidies up an address it has been handed, so what comes
      // back out is not always character-for-character what went in -- and a
      // comparison that got that wrong here would quietly re-read nothing at all.
      const growing = new Set(grown.map((url) => url.split("|")[0]));
      for (const source of layer.dataSources) {
        const store = source.spec.url.split("|")[0];
        // The stores that did not grow are left completely alone. This is the line
        // that turns a thousand stores from six thousand requests into six.
        if (!growing.has(store)) continue;
        if (geometryChanged.has(store)) continue;
        // And a store that did grow is forgotten once, not once per row that reads
        // from it. A store holding two channels feeds two rows, and forgetting per
        // row meant the second row throwing away the very files the first had just
        // fetched and asking for them again. Re-resolving still happens for every row
        // that reads the store, because each row's own sense of how long the image is
        // has to be brought up to date -- it is only the forgetting that is shared.
        // The second row then finds the first row's request already in flight and
        // waits for it rather than making one of its own.
        //
        // Frames arriving add pieces and change none already held, so those are
        // kept: dropping them re-read the whole picture on screen at every frame.
        refreshGeometry(source, chunkManager, refreshed.holders, forgotten, { replaceHeld: false });
        sourceRefreshing.sources.push(store);
      }
    }

    if (fresh.length) {
      // Queued rather than handed straight over, so that a great many arriving at once
      // are offered to the engine in groups. While something is writing this is usually
      // a single store, which becomes a group of one and reaches the engine
      // immediately; see the notes above keepHandingOver in feeding.ts for why there
      // is deliberately no separate path for that case.
      feeding.queueStores(layer, fresh);
      // Recorded as applied as soon as they are queued. From here on they belong to the
      // feeding, and a later pass over the same scene must not offer them a second
      // time while the first offer is still working its way through the queue.
      sourcesApplied.set(layer, new Set(wanted));
    }
    return fresh.length;
  }

  /**
   * Bring the engine's layers into line with what the page is asking for.
   *
   * ``specs`` is the scene the page wants, in the order it should be drawn, each
   * entry written the way Neuroglancer describes a layer.
   *
   * Returns how many layers were built, removed or given a new image — in other
   * words, how much of the scene actually changed shape. That number matters
   * because it is the only case where the engine has to work out where everything
   * sits in space again, so a page uses it to decide whether the view needs
   * putting back where it was. It is also what the browser tests
   * watch: for an ordinary change — a slider moved, a channel hidden — it must be
   * zero.
   *
   * Pass ``reread`` when something on disk has changed — that is, on the pass that
   * follows an announcement. It means "check whether anything already open has grown",
   * not "re-read everything": only a row whose frame count has actually moved is read
   * again, and its layers are left exactly as they are while that happens. This is the
   * one case where nothing is added to the scene and yet something must still happen.
   */
  function syncLayers(specs: SceneLayer[], { reread = false }: { reread?: boolean } = {}): number {
    const manager = viewer.layerManager;
    const wanted = new Set(specs.map((spec) => spec.name));
    let reshaped = 0;
    // The stores already forgotten on this pass, so that a store feeding several
    // rows is forgotten once rather than once per row. See syncSources.
    const forgotten = new Set<string>();
    // Coverage and channel rows can share decoded holders despite different IDs.
    const refreshed: Refreshed = { sources: new Set(), holders: new Set() };
    sourceRefreshing.passes += 1;
    sourceRefreshing.sources = [];
    sourceRefreshing.metadataEntries = 0;
    sourceRefreshing.decodedEntries = 0;

    // Anything the page no longer lists has genuinely been closed, so let it go.
    // Doing this first also frees the name, in case something new is taking it.
    // A twin still being adopted is not closed — its name is in no spec on
    // purpose, because the name it will take still belongs to its elder.
    for (const managed of [...manager.managedLayers]) {
      if (wanted.has(managed.name)) continue;
      if (managed.layer) feeding.stopFeeding(managed.layer);
      deleteLayer(managed);
      reshaped += 1;
    }

    // How many layers this pass will build. The stores they are built with have to add up
    // to one group between them rather than one group each, for the same reason the
    // feeding budget is shared: four rows each starting with two hundred stores is eight
    // hundred at once, which is the burst all of this exists to avoid.
    const building = specs.filter((spec) => {
      const already = manager.getLayerByName(spec.name);
      return !already || already.layer?.type !== spec.type;
    }).length;
    const firstShare = Math.max(1, Math.floor(feeding.howManyAtATime() / Math.max(1, building)));

    specs.forEach((spec, index) => {
      const stored: StoreFields = spec;
      let managed = manager.getLayerByName(spec.name);
      // A layer that has changed kind — an image where there was a mask — cannot be
      // adjusted into the other; that one really does have to be built again.
      if (managed && managed.layer?.type !== spec.type) {
        if (managed.layer) feeding.stopFeeding(managed.layer);
        deleteLayer(managed);
        managed = undefined;
        reshaped += 1;
      }
      if (managed?.layer) {
        reshaped += syncSources(
          managed.layer,
          spec,
          reread,
          memoryOf(viewer),
          forgotten,
          refreshed,
        );
        applySettings(managed, spec, ownSettings);
        specApplied.set(managed, spec);
        return;
      }
      // A layer is built with at most one group of stores, and the rest are fed to it
      // afterwards. This is where the loss described above actually happened, and it is
      // worth saying why, because it is not where it appears to be. When a folder is
      // opened cold the layer does not exist yet, so it is built from a description that
      // already names *every* store — and the engine reads the lot inside the
      // constructor, in one burst, before this module has any say in it. Pacing only the
      // stores added later would have left a cold open exactly as broken as it was.
      const stores = sourceList(spec);
      const rest = stores.slice(firstShare);
      managed = makeLayer(
        viewer.layerSpecification,
        spec.name,
        rest.length ? { ...spec, source: stores.slice(0, firstShare) } : spec,
      );
      // makeLayer builds the layer or throws, so it is there; neuroglancer's type
      // allows a managed layer without one.
      const built = managed.layer as UserLayer;
      // Held at its own depth, with this engine's own kind of coordinate transform.
      if (stored.persistentFlat || stored.boundaryHeld) {
        keepDepthLocal(built, stored.boundaryHeld ? viewer : null,
                       (value: CoordinateSpaceTransform) => new WatchableCoordinateSpaceTransform(value));
      }
      // Building from the description already applied everything in it, including
      // the images; record them so the next pass does not add them a second time.
      sourcesApplied.set(built, new Set(stores));
      geometrySeen.set(built, geometryFor(spec));
      specApplied.set(managed, spec);
      if (rest.length) {
        feeding.queueStores(built, rest);
        // What the constructor has just started reading counts against the shared budget
        // like any other group, so the next group waits for it. Without this the second
        // group would be offered immediately and the burst would simply be twice as large.
        feeding.alreadyBeingRead(built.dataSources);
      }
      // Likewise how far along each of its stores was when it was built, so that the
      // first announcement after it appears does not mistake "I have never asked" for
      // "this has grown" and re-read every store on the row for nothing.
      const frameCounts = stored.frameCounts;
      if (Array.isArray(frameCounts)) {
        framesSeen.set(
          built,
          new Map(stores.map((url, at) => [url, frameCounts[at]])),
        );
      }
      const initialRevisions = revisionsFor(spec);
      if (initialRevisions.size) {
        revisionsSeen.set(built, rememberedSourceRevisions(initialRevisions));
      }
      viewer.layerSpecification.add(managed, index);
      reshaped += 1;
    });

    applyOrder(manager, specs.map((spec) => spec.name));
    // Start handing over only once the whole pass is done. Started earlier — from inside
    // the loop above — the first row would begin feeding before the other rows had even
    // been built, so it would take the whole budget for itself and the rows built a moment
    // later would have their first stores read outside it. Doing it here means every row
    // that wants stores this pass is known before any of them are offered. Costs nothing
    // when there is nothing waiting, which is the ordinary case.
    feeding.keepHandingOver();

    return reshaped;
  }

    return { syncLayers, letGoOfDecodedPieces, sourceRefreshing, lettingGo };
}
