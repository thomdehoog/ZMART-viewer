/**
 * Handing the engine its stores a few at a time, across the whole scene.
 *
 * Each layer has its own queue of stores still to hand over; there is one budget
 * and one loop for all of them, which offers the stores a group at a time and lets
 * each group be read before offering the next. Why, and what this does not do, is
 * set out above `keepHandingOver`. Keeping the layers themselves in step is
 * `sync.ts`, which queues the stores here.
 *
 * One budget per engine (Thom, 2026-10-18): `makeFeeding` holds every queue, the
 * group in flight and the loop, and is made with its engine and dropped with it.
 * Nothing lives in a module variable, so two engines on one page each keep
 * their own.
 */

import type { UserLayer } from "neuroglancer/unstable/layer/index.js";
import type { LayerDataSource } from "neuroglancer/unstable/layer/layer_data_source.js";

// How many stores are offered at a time, **across the whole scene**. Two hundred is the
// size that was measured to work: large enough that a folder of a few thousand stores
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
// while it was being written, say. Without this, one such store would stop every later
// group from ever being offered, which is the same silent loss of the picture this whole
// mechanism exists to prevent. A minute is far longer than a group of two hundred
// stores has ever taken, so in ordinary use it never comes into play.
const PATIENCE_MS = 60_000;

function howManyAtATime(): number {
  // The browser tests set this to a small number, so that the pacing can be watched
  // happening on a folder of a few dozen stores instead of a few thousand. Nothing
  // in the viewer itself sets it.
  const asked = (globalThis as { zmartSourceBatch?: unknown }).zmartSourceBatch;
  return typeof asked === "number" && Number.isFinite(asked) && asked > 0 ? asked : AT_A_TIME;
}

/** What one layer is still waiting to be handed, and whether it has been told to stop. */
interface Feed {
  waiting: string[];
  stopped: boolean;
}

// A layer that has been discarded while its stores were still being read. Offering more
// to it would raise an error in the middle of nothing, so the loop simply drops it.
function goneAway(layer: UserLayer): boolean {
  return Boolean(layer.wasDisposed || layer.managedLayer?.wasDisposed);
}

/** One engine's feeding: its queues, its one budget and its one loop. */
export interface Feeding {
  /** How many stores are offered at a time, across the whole scene. */
  howManyAtATime(): number;
  queueStores(layer: UserLayer, urls: readonly string[]): void;
  alreadyBeingRead(sources: readonly LayerDataSource[]): void;
  stopFeeding(layer: UserLayer): void;
  keepHandingOver(): Promise<void>;
  sourcesStillWaiting(layers: Iterable<UserLayer | null | undefined>): number;
  /** Stop waiting on anything, for an engine going away. */
  dispose(): void;
}

/** Make the feeding for one engine. Everything it holds dies with it. */
export function makeFeeding(): Feeding {
  // What each layer is still waiting to be handed. The queue is per layer, because each
  // row has its own list of stores; the *rate* is not, for the reason given above.
  // A WeakMap is used so that a layer being thrown away takes its entry with it.
  const feeding = new WeakMap<UserLayer, Feed>();

  // The layers with something still to hand over. Held by ordinary reference rather than
  // weakly, which is safe because a layer is taken out the moment its queue empties or it
  // is discarded — nothing lingers here long enough to keep a dead layer alive.
  const hungry = new Set<UserLayer>();

  // The group handed over most recently, across the whole scene, and whether the loop
  // below is already running. Both are this engine's rather than per layer: one budget,
  // one loop.
  let inFlight: LayerDataSource[] = [];
  let handingOver = false;

  // Ways to stop waiting early, so that a layer going away does not leave the loop
  // listening for stores that will never answer. See stopFeeding.
  const giveUpWaiting = new Set<() => void>();

  /**
   * Queue stores for a layer to be handed, in turn, by the loop below.
   *
   * Pushed one at a time rather than spread into the call, because a spread of forty
   * thousand addresses is forty thousand arguments and the browser refuses calls that
   * large.
   */
  function queueStores(layer: UserLayer, urls: readonly string[]): void {
    let feed = feeding.get(layer);
    if (!feed) {
      feed = { waiting: [], stopped: false };
      feeding.set(layer, feed);
    }
    for (const url of urls) feed.waiting.push(url);
    hungry.add(layer);
  }

  /**
   * Count what a layer has just started reading against the group in flight, so the
   * next group waits for it like any other.
   */
  function alreadyBeingRead(sources: readonly LayerDataSource[]): void {
    inFlight = inFlight.concat(sources);
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
      // writing goes on: one store arrives, nothing else is outstanding, and
      // this returns without any waiting at all.
      look();
    });
  }

  /**
   * Hand the engine its stores a few at a time, rather than all at once: offer the
   * waiting stores, a group at a time, until every queue is empty.
   *
   * This exists because of a fault that was easy to miss and serious to have: above
   * roughly six hundred and eighty stores, the rest of the picture never appeared,
   * and nothing on screen said so. A folder of two thousand stores drew six hundred
   * and eighty-six of them and presented that as the whole thing.
   *
   * The cause is not ours and not the disk's. Reading one store means about four small
   * requests for the files that describe it, and a browser will only hold six
   * conversations with one address at a time. Beyond a few thousand requests waiting
   * their turn it stops accepting new ones altogether — it says so in its own words,
   * `net::ERR_INSUFFICIENT_RESOURCES`. The engine takes a refused request as "this
   * store cannot be read" and quietly leaves that store out. Nothing was wrong with
   * the data: every store it gave up on reads back perfectly well afterwards, one at a
   * time. The server was never answering more than seven requests at once and spent two
   * thirds of the wait idle.
   *
   * So the stores are handed over in groups, and each group is allowed to finish being
   * read before the next one is offered. The browser's queue then never fills, nothing
   * is refused, and nothing is lost. Fed this way, a folder of a thousand stores
   * loads a thousand of a thousand and a folder of two thousand loads two thousand of
   * two thousand, with no failures at all.
   *
   * One loop for the whole scene rather than one per row, so that the two hundred is a
   * budget shared between the rows waiting rather than an allowance given to each of them.
   * Anything queued while it is running is picked up by the same loop, so a store
   * arriving during a cold open simply joins the end rather than starting a burst of its
   * own.
   *
   * **There is deliberately no special case for opening a finished folder.** It is
   * tempting to think of this as something needed only for a cold open, but it is better
   * understood as simply *how stores are handed over*: give it however many there are,
   * and it feeds them in groups. While something is writing that number is usually
   * one, which is a single group and costs nothing beyond what happened before. Opening
   * a finished folder of forty thousand stores is many groups. Same path either way,
   * and nothing anywhere asks whether the data is live.
   *
   * **What this does not do is make a large folder quick**, and it should not be expected
   * to. Every one of forty thousand stores is still read, merely in an orderly fashion
   * rather than all at once.
   *
   * Speed is a separate problem, and it is deliberately *not* solved here. Two ways of
   * solving it were considered and both were turned down, for the same reason: each would
   * have added a second copy of something that already exists elsewhere.
   *
   * The first was to hand the engine only the stores in view.
   * Neuroglancer already decides which pieces of image to fetch from where the view is, and
   * it does that better than we would; keeping our own idea of what is in view means holding
   * a second copy of knowledge the engine already has, which is how this project has got
   * into trouble before. The second was to fuse the stores into one image per group,
   * which means a second copy of the data itself, written by us, and a whole writer to
   * maintain.
   *
   * What is left is the honest answer: a folder of forty thousand stores takes as long as
   * it takes, and the way to wait less is to open less — which is the page's choice to
   * make, not ours to make for it. See Decision 5 in `docs/how_it_works/DATA_LAYOUT.md`.
   *
   * There is one real way to make it quicker, and it is not in this file: write into a
   * single image rather than one store per place, so there are forty thousand
   * places within one store instead of forty thousand stores. That is faster by a wide margin
   * and is what we are aiming for, but it is a decision about how data is written, and this
   * function has to go on working well for data that keeps its places in separate stores. See
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
        // Shared out evenly, so that a layer holding forty thousand stores does not keep
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
            // format the page writes and the format the engine wants cannot drift apart.
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

  /**
   * How many stores this page has still to hand over — **not** how many the engine
   * has still to read.
   *
   * The difference matters and has cost real measurements. What is counted here is
   * the page's own queue draining: stores it has been given and not yet passed on.
   * The engine goes on reading each one's description afterwards, and that is the
   * slow part. Traced on a hundred stores, this reached nought with **30**
   * of them actually resolved; on four hundred, with **300**. So it reaches nought
   * at a different fraction of the work depending on how many stores there are,
   * which is the worst possible behaviour for anything comparing one size against
   * another — two folders stop at different amounts of finished work and the
   * difference between them looks like a result.
   *
   * A measurement that wants a settled page should wait on every store having a
   * load state of its own, not on this.
   */
  function sourcesStillWaiting(layers: Iterable<UserLayer | null | undefined>): number {
    let total = 0;
    for (const layer of layers) {
      const feed = layer && feeding.get(layer);
      if (feed) total += feed.waiting.length;
    }
    return total;
  }

  return {
    howManyAtATime,
    queueStores,
    alreadyBeingRead,
    stopFeeding,
    keepHandingOver,
    sourcesStillWaiting,
    dispose() {
      for (const layer of [...hungry]) stopFeeding(layer);
      for (const stopWaiting of [...giveUpWaiting]) stopWaiting();
    },
  };
}
