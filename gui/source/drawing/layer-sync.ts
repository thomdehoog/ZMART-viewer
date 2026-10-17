/**
 * Keeping the engine's picture in step with the panel, without rebuilding it.
 *
 * The panel and the engine hold the same information in two different shapes.
 * The panel thinks in acquisition types and channels; the engine thinks in a flat
 * list of layers. Something has to carry changes from one to the other, and this
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
 * 1. Anything the operator drew is lost. A drawn target lives inside its layer,
 *    and the rebuilt layer is a new, empty one — so the target disappears from the
 *    image while the list beside it still claims the target is there.
 * 2. The pieces of image already fetched are dropped and asked for again. They
 *    are kept in a shared cache that survives for a moment after the last layer
 *    lets go of them, so a fast enough rebuild often catches them still warm and
 *    nothing is refetched. That is luck, not design, and on a run of several
 *    hundred gigabytes — where a rebuild can easily land while pieces are still
 *    in flight — it is luck that runs out.
 * 3. Everything the engine had worked out about the scene has to be worked out
 *    again: which copy of the image to draw, how the layers line up in space, which
 *    shader programs to compile. None of it changed.
 *
 * So this module does the comparing itself. It looks at what the engine currently
 * has, works out the smallest set of changes that would turn it into what the
 * panel is asking for, and makes only those. A layer is built only when it is
 * genuinely new, and thrown away only when it has genuinely gone. Everything else
 * — brightness, colour, opacity, whether a channel is showing, what order the
 * acquisition types are drawn in — is written straight onto the layer that is
 * already there, which is what Neuroglancer expects and is fast enough to do on
 * every drag of a slider.
 */

import { makeLayer, deleteLayer } from "neuroglancer/unstable/layer/index.js";
import type { LayerManager, ManagedUserLayer, UserLayer } from "neuroglancer/unstable/layer/index.js";
import type { LayerDataSource } from "neuroglancer/unstable/layer/layer_data_source.js";
import { WatchableCoordinateSpaceTransform } from "neuroglancer/unstable/coordinate_transform.js";
import type { CoordinateSpaceTransform } from "neuroglancer/unstable/coordinate_transform.js";
import type { WatchableValueInterface } from "neuroglancer/unstable/trackable_value.js";
import type { Viewer } from "neuroglancer/unstable/viewer.js";
import {
  keepDepthLocal as keepEmbeddedDepthLocal,
  refreshGeometry,
} from "../../../engine/drawing/embedding.js";
import type { EngineLayer } from "./layers.ts";
import {
  advancingSourceRevisions,
  memoEntriesForStableSource,
  rememberedSourceRevisions,
  sourceRevisionsFor,
} from "./live-refresh.ts";
import type { RememberedRevisions } from "./live-refresh.ts";

// -- the shapes this file works with ------------------------------------------

/**
 * The targets' layer, as the page adds it to the scene. Only its colour and
 * whether it shows are carried across; what is drawn on it belongs to the engine.
 */
export interface TargetLayer {
  type: "annotation";
  name: string;
  source: string;
  annotations: unknown;
  annotationColor?: string;
  visible?: boolean;
}

/** One layer of the scene the panel asks for. */
export type SceneLayer = EngineLayer | TargetLayer;

/**
 * What a layer's description says about its stores. Every layer of the scene
 * can be read this way; the targets' layer simply says nothing.
 */
type StoreFields = Partial<Pick<EngineLayer,
  "frameCounts" | "sourceIds" | "sourceRevisions" | "sourceGeometryRevisions"
  | "persistentFlat" | "boundaryHeld">> & { source?: string | string[] };

// Neuroglancer's image, segmentation and annotation layers ship no declarations,
// so the settings this file writes on them are named here, and each layer is
// taken as its kind once, where its kind is known.

/** A setting the engine restores from its own description of it. */
interface Restorable {
  restoreState(value: unknown): void;
}

/** The image layer's settings that follow the panel. */
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

/** The annotation layer's colour. */
interface AnnotationLayerSettings {
  annotationDisplayState: { color: Restorable };
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

/** The engine's memory, as this file reads it; see `EngineMemory`. */
function memoryOf(viewer: Viewer): EngineMemory | undefined {
  return viewer.chunkManager as EngineMemory | undefined;
}

// -- what each layer was last told ---------------------------------------------
//
// Working out the smallest set of changes means remembering what the engine was
// handed last time, because the engine cannot always be asked. Everything
// remembered here is filed against the layer it belongs to, in a kind of map that
// lets go of an entry on its own once the layer is thrown away — so closing an
// acquisition hands the memory back without anything having to tidy up.

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
// separately from the URL because the URL deliberately never changes when a
// position or moment is published.
const revisionsSeen = new WeakMap<UserLayer, RememberedRevisions>();

// The geometry revision last applied for each of a layer's stores, by the store's
// folder: a store whose geometry revision moves has changed shape, not only pixels.
const geometrySeen = new WeakMap<UserLayer, Map<string, number | undefined>>();

// Request-cost evidence exposed to the browser tests.  ``sources`` contains
// only stable source identities that advanced on the most recent sync pass;
// unchanged and unrelated sources must never appear here.
export const sourceRefreshing: {
  passes: number;
  sources: string[];
  metadataEntries: number;
  decodedEntries: number;
} = {
  passes: 0,
  sources: [],
  metadataEntries: 0,
  decodedEntries: 0,
};

function sourceList(spec: StoreFields): string[] {
  if (spec.source == null) return [];
  return Array.isArray(spec.source) ? spec.source : [spec.source];
}

// -- handing the stores over a few at a time ----------------------------------

// How many stores are offered at a time, **across the whole scene**. Two hundred is the
// size that was measured to work: large enough that a folder of a few thousand positions
// is not made needlessly slow by waiting, and far enough below the browser's limit that a
// group of them can be in flight together without any risk of one being refused.
//
// That it is shared between rows rather than allowed to each of them separately is the
// point, and it is easy to get wrong. A store holding four channels in separate files
// feeds four rows, and if each row were free to offer two hundred stores the browser
// would see eight hundred at once and be back at the limit this exists to stay under.
// The browser's queue of outstanding requests is one shared thing, so the budget for it
// has to be one shared thing too. When several rows are waiting they share the two
// hundred between them.
const AT_A_TIME = 200;

// How long to wait for one group before going on regardless. A store that cannot be
// read reports that quickly and is not the case this guards against; what it guards
// against is a store that never answers at all — a share that has gone away
// mid-acquisition, say. Without this, one such store would stop every later group from
// ever being offered, which is the same silent loss of the specimen this whole
// mechanism exists to prevent. A minute is far longer than a group of two hundred
// stores has ever taken, so in ordinary use it never comes into play.
const PATIENCE_MS = 60_000;

function howManyAtATime(): number {
  // The browser tests set this to a small number, so that the pacing can be watched
  // happening on a folder of a few dozen positions instead of a few thousand. Nothing
  // in the viewer itself sets it.
  const asked = (globalThis as { zmartSourceBatch?: unknown }).zmartSourceBatch;
  return typeof asked === "number" && Number.isFinite(asked) && asked > 0 ? asked : AT_A_TIME;
}

/** What one layer is still waiting to be handed, and whether it has been told to stop. */
interface Feed {
  waiting: string[];
  stopped: boolean;
}

// What each layer is still waiting to be handed. The queue is per layer, because each
// row has its own list of stores; the *rate* is not, for the reason given above.
// A WeakMap is used so that a layer being thrown away takes its entry with it, the same
// as the two maps above.
const feeding = new WeakMap<UserLayer, Feed>();

// The layers with something still to hand over. Held by ordinary reference rather than
// weakly, which is safe because a layer is taken out the moment its queue empties or it
// is discarded — nothing lingers here long enough to keep a dead layer alive.
const hungry = new Set<UserLayer>();

// The group handed over most recently, across the whole scene, and whether the loop
// below is already running. Both are shared rather than per layer: one budget, one loop.
let inFlight: LayerDataSource[] = [];
let handingOver = false;

// Ways to stop waiting early, so that a layer going away does not leave the loop
// listening for stores that will never answer. See stopFeeding.
const giveUpWaiting = new Set<() => void>();

function feedFor(layer: UserLayer): Feed {
  let feed = feeding.get(layer);
  if (!feed) {
    feed = { waiting: [], stopped: false };
    feeding.set(layer, feed);
  }
  return feed;
}

// A layer that has been discarded while its stores were still being read. Offering more
// to it would raise an error in the middle of nothing, so the loop simply drops it.
function goneAway(layer: UserLayer): boolean {
  return Boolean(layer.wasDisposed || layer.managedLayer?.wasDisposed);
}

/**
 * Stop handing stores to a layer that is going away.
 *
 * Without this, closing a folder while it was still loading would leave the loop
 * waiting on stores belonging to a layer that no longer exists — waiting that would
 * never end, because a discarded layer's data sources have nothing left to announce.
 */
function stopFeeding(layer: UserLayer): void {
  hungry.delete(layer);
  const feed = feeding.get(layer);
  if (!feed) return;
  feed.stopped = true;
  feed.waiting.length = 0;
  // Wake the loop so it notices, rather than leaving it waiting on this layer's stores.
  for (const stopWaiting of [...giveUpWaiting]) stopWaiting();
}

/**
 * Wait until the engine has finished reading this group of stores.
 *
 * "Finished" here means it has an answer, whichever answer it is: a store that turns
 * out to be unreadable has been dealt with just as much as one that read perfectly, and
 * waiting longer would not improve it. The engine says so by giving the data source a
 * load state, which is empty while the reading is still under way.
 */
function whenTheseHaveBeenRead(sources: LayerDataSource[]): Promise<void> {
  return new Promise((done) => {
    const stopListening: (() => void)[] = [];
    let patience: ReturnType<typeof setTimeout> | undefined;
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      for (const stop of stopListening) stop();
      giveUpWaiting.delete(finish);
      clearTimeout(patience);
      done();
    };
    const look = () => {
      if (sources.some((source) => source.loadState === undefined)) return;
      finish();
    };
    giveUpWaiting.add(finish);
    patience = setTimeout(finish, PATIENCE_MS);
    for (const source of sources) stopListening.push(source.changed.add(look));
    // In case they are all read already, which is the ordinary case while the
    // microscope is running: one position arrives, nothing else is outstanding, and
    // this returns without any waiting at all.
    look();
  });
}

/**
 * Hand the engine its stores a few at a time, rather than all at once: offer the
 * waiting stores, a group at a time, until every queue is empty.
 *
 * This exists because of a fault that was easy to miss and serious to have: above
 * roughly six hundred and eighty positions, the rest of the specimen never appeared,
 * and nothing on screen said so. A folder of two thousand positions drew six hundred
 * and eighty-six of them and presented that as the whole thing.
 *
 * The cause is not ours and not the disk's. Reading one store means about four small
 * requests for the files that describe it, and a browser will only hold six
 * conversations with one address at a time. Beyond a few thousand requests waiting
 * their turn it stops accepting new ones altogether — it says so in its own words,
 * `net::ERR_INSUFFICIENT_RESOURCES`. The engine takes a refused request as "this
 * store cannot be read" and quietly leaves that position out. Nothing was wrong with
 * the data: every store it gave up on reads back perfectly well afterwards, one at a
 * time. The server was never answering more than seven requests at once and spent two
 * thirds of the wait idle.
 *
 * So the stores are handed over in groups, and each group is allowed to finish being
 * read before the next one is offered. The browser's queue then never fills, nothing
 * is refused, and nothing is lost. Fed this way, a folder of a thousand positions
 * loads a thousand of a thousand and a folder of two thousand loads two thousand of
 * two thousand, with no failures at all.
 *
 * One loop for the whole scene rather than one per row, so that the two hundred is a
 * budget shared between the rows waiting rather than an allowance given to each of them.
 * Anything queued while it is running is picked up by the same loop, so a position
 * arriving during a cold open simply joins the end rather than starting a burst of its
 * own.
 *
 * **There is deliberately no special case for opening a finished folder.** It is
 * tempting to think of this as something needed only for a cold open, but it is better
 * understood as simply *how stores are handed over*: give it however many there are,
 * and it feeds them in groups. While the microscope is running that number is one,
 * which is a single group and costs nothing beyond what happened before. Opening a
 * finished folder of forty thousand positions is many groups. Same path either way,
 * and nothing anywhere asks whether the data is live.
 *
 * **What this does not do is make a large folder quick**, and it should not be expected
 * to. Every one of forty thousand positions is still read, merely in an orderly fashion
 * rather than all at once.
 *
 * Speed is a separate problem, and it is deliberately *not* solved here. Two ways of
 * solving it were considered and both were turned down, for the same reason: each would
 * have added a second copy of something that already exists elsewhere.
 *
 * The first was to hand the engine only the positions the operator is looking at.
 * Neuroglancer already decides which pieces of image to fetch from where the view is, and
 * it does that better than we would; keeping our own idea of what is in view means holding
 * a second copy of knowledge the engine already has, which is how this project has got
 * into trouble before. The second was to fuse the positions into one image per acquisition
 * type, which means a second copy of the data itself, written by us, and a whole writer to
 * maintain.
 *
 * What is left is the honest answer: a folder of forty thousand positions takes as long as
 * it takes, and the way to wait less is to open less — which is the operator's choice to
 * make, not ours to make for them. See Decision 5 in `docs/how_it_works/DATA_LAYOUT.md`.
 *
 * There is one real way to make it quicker, and it is not in this file: have the run write
 * into a single image rather than one store per position, so there are forty thousand
 * places within one store instead of forty thousand stores. That is faster by a wide margin
 * and is what we are aiming for, but it is a decision about how data is written, and this
 * function has to go on working well for the runs that keep their positions separate. See
 * Decision 1b.
 */
async function keepHandingOver(): Promise<void> {
  if (handingOver) return;
  handingOver = true;
  try {
    for (;;) {
      for (const layer of [...hungry]) {
        const feed = feeding.get(layer);
        if (!feed || feed.stopped || !feed.waiting.length || goneAway(layer)) {
          hungry.delete(layer);
        }
      }
      if (!hungry.size) break;
      // Let the group offered last time finish before offering another. On the first
      // turn that is whatever the new layers were built with; after that it is the
      // previous group. Either way it is at most one group's worth, so this never costs
      // anything that grows with the size of the folder.
      await whenTheseHaveBeenRead(inFlight);
      const offered: LayerDataSource[] = [];
      let room = howManyAtATime();
      // Shared out evenly, so that a row holding forty thousand positions does not keep
      // another row's handful waiting behind it.
      const share = Math.max(1, Math.floor(room / hungry.size));
      for (const layer of [...hungry]) {
        if (room <= 0) break;
        const feed = feeding.get(layer);
        // Checked again here as well as at the top: the waiting above is where the time
        // passes, and so is where a layer is most likely to disappear.
        if (!feed || feed.stopped || goneAway(layer)) {
          hungry.delete(layer);
          continue;
        }
        const group = feed.waiting.splice(0, Math.min(share, room));
        room -= group.length;
        for (const url of group) {
          // Neuroglancer's own reader turns the address into whatever it needs, so the
          // format the panel writes and the format the engine wants cannot drift apart.
          // It is handed a list of one rather than a bare address on purpose: that is
          // the same path a layer takes when it is first built, so a store added later
          // is set up in exactly the same way as one that was there from the start.
          for (const source of layer.getDataSourceSpecifications({ source: [url] })) {
            offered.push(layer.addDataSource(source));
          }
        }
        if (!feed.waiting.length) hungry.delete(layer);
      }
      inFlight = offered;
    }
  } finally {
    handingOver = false;
  }
}

// -- making the engine look at the disk again ---------------------------------
//
// The engine remembers everything it has ever read, and there is no time limit on
// that memory. It is the right thing almost always, and it is exactly wrong while a
// run is still writing: what was true when the engine looked is no longer true.
//
// There are two ways of telling it to look again, and they are different sizes.
// `letGoOfDecodedPieces` drops the picture the engine has already decoded, for the
// whole scene. `forgetWhatWasReadAbout` drops only what was read about one store,
// which is what a timelapse gaining a frame needs. Between them sits
// `sourcesStillWaiting`, which is not about looking again at all — it belongs to the
// feeding above and lives here only so that the two counts a test reads are together.

// How many times the viewer has let go, and how many sources it asked the last time.
// Kept so that a test can tell "it was asked and nothing came of it" from "it was never
// asked at all" -- two failures that look identical on screen and have quite different
// causes. Read through `window.zmartLetGo`; see App.jsx.
export const lettingGo = { times: 0, asked: 0 };

/**
 * Let go of the pieces of image the engine has already decoded, so that looking again
 * really looks.
 *
 * This is what makes it possible to watch a run that writes into **one** store rather
 * than one store per position — a single OME-Zarr, created empty at the start, with each
 * tile written straight into its place. That layout is worth a great deal: what costs
 * the viewer is the number of separate stores rather than the amount of data behind
 * them, and one store opens in a second and a half where three hundred take longer on
 * thirty times the requests.
 *
 * The difficulty is that the engine remembers every piece of image it has decoded, and
 * that includes the pieces it found to be empty. There is no time limit on that memory.
 * So a tile written into a place the viewer has already looked at is simply not noticed:
 * the panel goes on showing the emptiness it decided on earlier and never goes back to
 * the disk. Measured, that is not "slow to appear" — it is **no request at all**, ever.
 *
 * So the sources are asked to let go, which the engine offers a way to do: the pieces
 * live in a separate worker, and this sends word to that worker to drop them. What comes
 * back is fetched afresh.
 *
 * **What it costs, and why it is affordable.** Only the pieces actually on screen are
 * fetched again — nine of them in the measurement — because the engine asks for what it
 * needs to draw and nothing else. That number follows the size of the window rather than
 * the size of the specimen, so it is the same on a folder of forty terabytes as on one of
 * forty megabytes. Anything the operator had scrolled past is dropped and will be fetched
 * again if they scroll back, which is the price, and it is a fair one.
 *
 * **When this is called matters as much as what it does**, and the rule is narrow on
 * purpose. There are exactly two occasions, both in App.jsx:
 *
 * 1. A run announced that it wrote image into a store already open, by sending
 *    `wrote_image_in_place`. It is the only one who knows, so it is believed.
 * 2. An acquisition that the panel already knew about, and that had no picture in it at
 *    all, has just gained one — seen as a store going from having no histogram and no
 *    count of moments to having them.
 *
 * An earlier attempt used a wider rule: an announcement arrived and the answer came back
 * completely unchanged. That reads well and is wrong in practice. Redundant announcements
 * are ordinary — the microscope and the folder watcher both see the same write — so it
 * fired on every one of them, and measured, that cost a refetch of the whole view on
 * every position arrival. When a position arrives or a timelapse lengthens, this is not
 * called, and nothing already fetched is thrown away.
 *
 * Returns how many sources were asked, so a test can tell "it was asked and nothing
 * happened" from "it was never asked".
 */
export function letGoOfDecodedPieces(viewer: Viewer): number {
  // Legacy write hints cover unversioned stores only. Revisioned sources are
  // refreshed by syncSources, even when the same write also emits a legacy hint.
  const shared = memoryOf(viewer)?.rpc?.objects;
  if (!shared) return 0;
  const remembered = memoryOf(viewer)?.memoize?.map;
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

/**
 * How many stores this page has still to hand over — **not** how many the engine
 * has still to read.
 *
 * The difference matters and has cost real measurements. What is counted here is
 * the page's own queue draining: stores it has been given and not yet passed on.
 * The engine goes on reading each one's description afterwards, and that is the
 * slow part. Traced on a run of a hundred stores, this reached nought with **30**
 * of them actually resolved; on four hundred, with **300**. So it reaches nought
 * at a different fraction of the work depending on how many stores there are,
 * which is the worst possible behaviour for anything comparing one size against
 * another — two runs stop at different amounts of finished work and the
 * difference between them looks like a result.
 *
 * A measurement that wants a settled page should wait on every store having a
 * load state of its own, not on this.
 */
export function sourcesStillWaiting(viewer: Viewer): number {
  let total = 0;
  for (const managed of viewer.layerManager.managedLayers) {
    const feed = managed.layer && feeding.get(managed.layer);
    if (feed) total += feed.waiting.length;
  }
  return total;
}

/**
 * Drop cached knowledge and decoded absence for one aggregate source only.
 *
 * A newly committed tile may occupy coordinates this source previously found
 * empty.  Metadata-only forgetting is therefore insufficient: the empty chunk
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

function revisionsFor(spec: StoreFields) {
  return sourceRevisionsFor(sourceList(spec), spec.sourceIds, spec.sourceRevisions);
}

function geometryFor(spec: StoreFields): Map<string, number | undefined> {
  return new Map(sourceList(spec).map((url, i) => [url.split("|")[0], spec.sourceGeometryRevisions?.[i]]));
}

// -- bringing the engine into line with the panel -----------------------------
//
// This is the heart of the module: given the scene the panel wants, change as
// little as possible to reach it. One function per kind of change — the images a
// layer reads from, the settings the operator turns, the order the layers are drawn
// in — and `syncLayers` below puts them together.

/**
 * Give one layer any images it does not yet have.
 *
 * A row in the panel can be drawn from several stores at once — the same channel
 * of the same acquisition type, recorded at a dozen stage positions — and during a
 * run those positions appear one at a time. Adding the new one to the layer that
 * is already there costs nothing; rebuilding the layer would throw away the
 * eleven positions that were fine.
 *
 * Only additions are made. A position is never removed on its own: closing things
 * is done a whole acquisition type at a time, which removes the layer outright, so
 * there is no case where a layer should quietly lose one of its images.
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
  // Held as a set rather than a list. A row can be drawn from as many stores as
  // the run has positions, and asking a list "do you already contain this?" for
  // each of them means walking the whole list every time -- which for a few
  // thousand positions is most of a second, spent on every single step of a
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

  // A valid higher revision means the run committed something new into a
  // stable aggregate source this layer is already drawing. Only the pixels
  // have changed: the picture behind that address is described once, when the
  // run is declared, and its description never moves afterwards — the frame
  // was never derived from what has arrived (see declare_a_governed_picture).
  //
  // So the refresh is pixels-only, done in place. forgetOneStableSource asks
  // each decoded holder to refresh itself, and that request rides the
  // keep-drawing-until-replaced delivery this repository patches into its
  // engine: the stale pieces keep drawing while the fresh bytes download, and
  // each is swapped for its replacement the moment it arrives. The operator
  // never sees a gap.
  //
  // What must NOT happen here is re-resolving the source's address
  // (`source.spec = { ...source.spec }`, which this block once did). That
  // looks free — same address in, same description out — but the engine
  // answers it by throwing the loaded source away first and reading the
  // description again from the server afterwards, and between those two
  // moments the layer has nothing to draw. On a live run that road
  // ran at every landing: the whole picture went black for 100–300 ms,
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
      if (!refreshed || !refreshed.sources.has(identity)) {
        const removed = forgetOneStableSource(chunkManager, store, refreshed?.holders);
        sourceRefreshing.sources.push(identity);
        sourceRefreshing.metadataEntries += removed.metadata;
        sourceRefreshing.decodedEntries += removed.decoded;
        if (refreshed) refreshed.sources.add(identity);
      }
    }
  }

  // Which of this row's stores have gained a frame since the last look.
  //
  // An announcement says only that *something* on disk has changed, never what, so
  // the counts the server reports are the only way to tell. They are per store, and
  // that matters: a row can hold a position for every place the microscope visited,
  // and asking all of them whether they have grown -- when the answer for all but
  // one of them is no -- was measured at a thousand positions as six thousand small
  // requests and eighteen seconds for a single frame landing. Comparing per store
  // makes that one store, however many there are.
  //
  // A row with no time axis reports no counts and so is never re-read at all, which
  // is right: nothing about it can grow.
  if (grown.length) {
    // Re-read changed metadata while keeping acquired pixels drawable. The NG
    // bounds-refresh patch extends compatible Zarr sources in place; replacing
    // source.spec would drop both image and coverage before their chunks return.
    // Forget cached metadata first, or the new extent never reaches the engine.
    //
    // Note that this happens *as well as* adding anything new below, not instead of
    // it. One announcement can mean both -- a position finished and another gained a
    // frame -- and an earlier version that treated them as alternatives quietly
    // stopped new positions appearing at all.
    // Matched on the folder each address points at rather than on the address
    // itself. Neuroglancer tidies up an address it has been handed, so what comes
    // back out is not always character-for-character what went in -- and a
    // comparison that got that wrong here would quietly re-read nothing at all.
    const growing = new Set(grown.map((url) => url.split("|")[0]));
    for (const source of layer.dataSources) {
      const store = source.spec.url.split("|")[0];
      // The stores that did not grow are left completely alone. This is the line
      // that turns a thousand positions from six thousand requests into six.
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
    // are offered to the engine in groups. While the microscope is running this is a
    // single position, which becomes a group of one and reaches the engine
    // immediately; see the notes above keepHandingOver for why there is
    // deliberately no separate path for that case.
    //
    // Pushed one at a time rather than spread into the call, because a spread of forty
    // thousand addresses is forty thousand arguments and the browser refuses calls that
    // large.
    const feed = feedFor(layer);
    for (const url of fresh) feed.waiting.push(url);
    hungry.add(layer);
    // Recorded as applied as soon as they are queued. From here on they belong to the
    // feeding above, and a later pass over the same scene must not offer them a second
    // time while the first offer is still working its way through the queue.
    sourcesApplied.set(layer, new Set(wanted));
  }
  return fresh.length;
}

/**
 * Write the adjustable settings of one layer.
 *
 * These are the things the operator changes constantly — contrast, colour,
 * opacity, whether a channel is showing — so this runs very often and does as
 * little as possible. Each setting is a value the engine watches, and writing the
 * value it already holds is ignored, so setting all of them and letting the engine
 * notice which actually moved is both correct and cheap.
 */
function applySettings(managed: ManagedUserLayer, spec: SceneLayer): void {
  const visible = spec.visible !== false;
  if (managed.visible !== visible) managed.setVisible(visible);
  const layer = managed.layer;
  if (!layer) return;
  if (spec.type === "image") {
    const image = layer as UserLayer & ImageLayerSettings;
    // The shader is the small program that turns stored numbers into what you
    // see: it carries the channel colour and any colour map. Setting it to the
    // text it already holds is ignored by the engine, so this is free whenever
    // the colour has not changed -- which is nearly always.
    if (spec.shader != null) image.fragmentMain.value = spec.shader;
    // How this row blends with what is beneath travels with the spec rather
    // than being set once: a live row that starts as one source may gain
    // tiles as the run grows, and the covering rule must come back with
    // them -- left additive, their overlaps would sum into bright seams.
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
  } else if (spec.type === "segmentation") {
    const mask = layer as UserLayer & SegmentationLayerSettings;
    // A mask has no brightness to adjust; how strongly it is painted over the
    // image is the only thing there is to set.
    mask.displayState.selectedAlpha.value = spec.selectedAlpha ?? 1;
    mask.displayState.notSelectedAlpha.value = spec.notSelectedAlpha ?? 0;
  } else if (spec.type === "annotation" && spec.annotationColor) {
    const targets = layer as UserLayer & AnnotationLayerSettings;
    targets.annotationDisplayState.color.restoreState(spec.annotationColor);
  }
  // Note what is deliberately absent: for an annotation layer, the annotations
  // themselves. Once the layer exists, the drawings inside it belong to the
  // engine, and the panel's list is a reflection of them rather than the other way
  // round. Writing them back here is what used to erase whatever had just been
  // drawn.
}

/**
 * Put the layers in the order the panel shows them.
 *
 * This is a real setting rather than tidying: the engine paints the list from
 * bottom to top, so the order decides which acquisition type covers which.
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
 * Hold a layer's depth as its own rather than the view's (see `keepDepthLocal` in
 * `engine/drawing/embedding.js`), with this engine's own kind of coordinate transform.
 */
function keepDepthLocal(layer: UserLayer, viewer: Viewer | null = null) {
  return keepEmbeddedDepthLocal(layer, viewer, (value: CoordinateSpaceTransform) => new WatchableCoordinateSpaceTransform(value));
}

/**
 * Bring the engine's layers into line with what the panel is asking for.
 *
 * ``specs`` is the scene the panel wants, in the order it should be drawn, each
 * entry written the way Neuroglancer describes a layer.
 *
 * Returns how many layers were built, removed or given a new image — in other
 * words, how much of the scene actually changed shape. That number matters
 * because it is the only case where the engine has to work out where everything
 * sits in space again, so the interface uses it to decide whether the view needs
 * putting back where the operator had it. It is also what the browser tests
 * watch: for an ordinary change — a slider moved, a channel hidden — it must be
 * zero.
 *
 * Pass ``reread`` when something on disk has changed — that is, on the pass that
 * follows an announcement. It means "check whether anything already open has grown",
 * not "re-read everything": only a row whose frame count has actually moved is read
 * again, and its layers are left exactly as they are while that happens. This is the
 * one case where nothing is added to the scene and yet something must still happen.
 */
export function syncLayers(viewer: Viewer, specs: SceneLayer[], { reread = false }: { reread?: boolean } = {}): number {
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

  // Anything the panel no longer lists has genuinely been closed, so let it go.
  // Doing this first also frees the name, in case something new is taking it.
  // A twin still being adopted is not closed — its name is in no spec on
  // purpose, because the name it will take still belongs to its elder.
  for (const managed of [...manager.managedLayers]) {
    if (wanted.has(managed.name)) continue;
    if (managed.layer) stopFeeding(managed.layer);
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
  const firstShare = Math.max(1, Math.floor(howManyAtATime() / Math.max(1, building)));

  specs.forEach((spec, index) => {
    const stored: StoreFields = spec;
    let managed = manager.getLayerByName(spec.name);
    // A layer that has changed kind — an image where there was a mask — cannot be
    // adjusted into the other; that one really does have to be built again.
    if (managed && managed.layer?.type !== spec.type) {
      if (managed.layer) stopFeeding(managed.layer);
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
      applySettings(managed, spec);
      specApplied.set(managed, spec);
      return;
    }
    // A layer is built with at most one group of stores, and the rest are fed to it
    // afterwards. This is where the loss described above actually happened, and it is
    // worth saying why, because it is not where it appears to be. When a folder is
    // opened cold the layer does not exist yet, so it is built from a description that
    // already names *every* position — and the engine reads the lot inside the
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
    if (stored.persistentFlat || stored.boundaryHeld) keepDepthLocal(built, stored.boundaryHeld ? viewer : null);
    // Building from the description already applied everything in it, including
    // the images; record them so the next pass does not add them a second time.
    sourcesApplied.set(built, new Set(stores));
    geometrySeen.set(built, geometryFor(spec));
    specApplied.set(managed, spec);
    if (rest.length) {
      const feed = feedFor(built);
      for (const url of rest) feed.waiting.push(url);
      hungry.add(built);
      // What the constructor has just started reading counts against the shared budget
      // like any other group, so the next group waits for it. Without this the second
      // group would be offered immediately and the burst would simply be twice as large.
      inFlight = inFlight.concat(built.dataSources);
    }
    // Likewise how far along each of its stores was when it was built, so that the
    // first announcement after it appears does not mistake "I have never asked" for
    // "this has grown" and re-read every store on the row for nothing.
    const frameCounts = stored.frameCounts;
    if (Array.isArray(frameCounts)) {
      framesSeen.set(
        built,
        new Map(sourceList(spec).map((url, at) => [url, frameCounts[at]])),
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
  keepHandingOver();

  return reshaped;
}
