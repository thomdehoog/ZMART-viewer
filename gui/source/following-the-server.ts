/**
 * Following the server: what is open, and the operator's settings on it.
 *
 * The page hears from the server when something on disk changes, asks what is
 * open, and takes the answer on -- keeping, for every channel still open, the
 * colour, contrast and opacity the operator gave it. This hook holds all of
 * that: the open configuration, the settings per channel and per acquisition,
 * the line the panel shows about the server, and the connection that keeps it
 * all current.
 */

import React from "react";
import type { Engine } from "../../engine/drawing/primitives/viewer.ts";
import { layerKey } from "./drawing/layers.ts";
import type { GroupState, LayerState } from "./drawing/layers.ts";
import { anyStoreGainedItsFirstImage, liveStateProblem } from "./drawing/live-refresh.ts";
import { fetchConfig, fetchLiveState } from "./asking-python.ts";
import type { Config } from "./asking-python.ts";
import type { Window } from "./contrast-window.ts";

const LIVE_STATE_CHECK_MS = 10_000;

// How many failed attempts in a row to reach the server's announcements before
// the panel says the server has gone quiet. The browser tries again on its own
// after each, so a few in a row rides out a brief hiccup without leaving the
// operator in the dark for long.
const UNANSWERED_BEFORE_SAYING_SO = 5;

/** What came of taking on an answer about what is open; see catchUp. */
type Outcome = "gained-image" | "changed" | "unchanged" | "rejected";

/** What the panel says when the server's live state cannot be applied safely. */
const staleLiveState = (problem: string) =>
  `Live publication state is stale: ${problem}. The last safe image is still shown.`;

/**
 * Follow the server, and hold what is open and what the operator set on it.
 *
 * `engine` is the viewer the scene was last handed to: an announcement that
 * image was written in place has to reach it there and then, without waiting
 * for the page to draw again.
 */
export function useFollowingTheServer(engine: React.RefObject<Engine | null>) {
  const [config, setConfig] = React.useState<Config | null>(null);
  // Per-layer interface state. Held here rather than in the engine because the
  // panel and the viewer must never disagree about what is showing.
  const [layerState, setLayerState] = React.useState<LayerState[]>([]);
  // One entry per acquisition type, plus the order they are drawn in. Held here
  // rather than in the engine for the same reason as the rows: the panel and the
  // view must never disagree about what is showing.
  const [groupState, setGroupState] = React.useState<GroupState>({});
  const [groupOrder, setGroupOrder] = React.useState<string[]>([]);
  const [storeNotice, setStoreNotice] = React.useState<string | null>(null);

  // The set of images last taken on, used to spot an answer that says nothing new.
  const applied = React.useRef<Config | null>(null);
  // Whether the (expensive) question of what is open is already outstanding.
  const asking = React.useRef(false);
  // Set when an announcement arrives, so the next pass through the engine reads each
  // open store's description again rather than assuming it still says what it did.
  // A timelapse gaining a frame changes nothing the panel can see -- same stores,
  // same channels -- so without this the engine would go on believing the old length
  // and the time slider would never reach the new frame.
  const rereadWanted = React.useRef(false);

  // Set when an announcement arrives while the viewer is already part way through
  // asking what is open. The answer on its way was prepared before that
  // announcement, so one more question is asked when it lands.
  const missedWhileAsking = React.useRef(false);

  // Take on a new set of images -- at startup, and again whenever something is
  // opened or closed. Anything still open keeps the colour, contrast and opacity
  // the operator gave it: having those quietly reset because a second run was
  // opened alongside would be its own small betrayal.
  const applyConfig = React.useCallback((loaded: Config | null): Outcome => {
    // Nothing came back: the server could not be reached. Whatever is on screen
    // stays there — an experiment half-watched is better than a blank panel — and
    // the poll below is what says so out loud.
    if (!loaded) return "unchanged";
    // What was taken on last time. Kept here rather than read back out of the
    // displayed state because it is needed *while* deciding the new state, and a
    // piece of state cannot be read and written in the same breath.
    const previous = applied.current;
    // Nothing actually different, so leave everything alone. This matters more
    // than it looks: the viewer asks whether anything has changed several times a
    // second, and without this check an identical answer would still count as new
    // and send the whole picture round again.
    //
    // What changed is also reported back, because the caller needs it in order to
    // decide whether the engine has to be told to look at the disk again.
    const unsafe = liveStateProblem(previous?.liveState, loaded.liveState);
    if (unsafe) {
      setStoreNotice(staleLiveState(unsafe));
      return "rejected";
    }
    setStoreNotice(null);
    if (previous && JSON.stringify(previous) === JSON.stringify(loaded)) return "unchanged";
    const outcome = anyStoreGainedItsFirstImage(previous, loaded) ? "gained-image" : "changed";
    applied.current = loaded;

    // Match each channel now open to the same channel before, so the colour,
    // contrast and opacity the operator chose follow it across the change.
    const before = new Map(
      (previous?.layers || []).map((spec, index) => [layerKey(spec), index]),
    );
    setLayerState((current) =>
      loaded.layers.map((spec) => {
        const was = before.get(layerKey(spec));
        if (was != null && current[was]) return current[was];
        return {
          // Showing, unless the run itself says this channel was switched off.
          // What the microscopist had on at the instrument is what the viewer
          // opens with; on a picture of many channels that is the difference
          // between something to read and a glare. The row is still listed and
          // one press brings it back.
          visible: spec.active !== false,
          color: spec.color,
          // No colour map to begin with: a channel opens in the flat colour the
          // store asked for, and a lookup table is something you choose.
          lut: null,
          opacity: 1,
          // Null means "use the mode-specific measured default". Once the
          // operator moves either contrast handle, their chosen window becomes
          // the source of truth in both 2-D and 3-D.
          window: null,
        };
      }),
    );
    setConfig(loaded);

    const groups = loaded.groups || [...new Set(loaded.layers.map((l) => l.group || ""))];
    // Keep the order already shown, with anything new appended rather than
    // dropped in the middle of it.
    setGroupOrder((current) => [
      ...current.filter((name) => groups.includes(name)),
      ...groups.filter((name) => !current.includes(name)),
    ]);
    setGroupState((current) =>
      Object.fromEntries(
        groups.map((name) => [name, current[name] || { visible: true }]),
      ),
    );
    return outcome;
  }, []);

  // Ask the server what is open, and take it on.
  //
  // This is the only place the question is asked, which matters more than it
  // sounds: on a folder of several thousand positions the answer is genuinely
  // expensive, and two of them racing at startup was measurably slower for no
  // benefit at all.
  //
  // It answers with what came of asking, which the caller needs in order to
  // decide whether the engine has to be told to look at the disk again:
  //
  //   "gained-image" an acquisition already on screen, which had nothing in it,
  //                  now has a picture -- see anyStoreGainedItsFirstImage
  //   "changed"      something else about the scene is different, and is applied
  //   "unchanged"    the answer was identical to the one already in hand
  //   "unreachable"  the server did not answer
  const catchUp = React.useCallback(async (): Promise<"gained-image" | "changed" | "unchanged" | "unreachable" | "busy"> => {
    // Not while one is already outstanding. Several announcements can arrive
    // close together at the start of a run, and a browser allows only six
    // connections to one address -- a queue of expensive questions would leave
    // the engine unable to fetch a single piece of image until they finished.
    //
    // The announcement is remembered rather than dropped. Three arriving during
    // one slow answer used to produce one question and no catching up at all:
    // the note asking for another look was written *after* this line, so it was
    // never reached. Mostly the answer in flight was current anyway, because it
    // reads the disk when it is asked rather than when it was requested -- but
    // not always, and "mostly" is not a thing to rely on during somebody's
    // experiment.
    if (asking.current) {
      missedWhileAsking.current = true;
      return "busy";
    }
    asking.current = true;
    let outcome: "gained-image" | "changed" | "unchanged" = "unchanged";
    try {
      // Asked again if an announcement arrived while the previous answer was on
      // its way. At most one extra question per burst, because the note is
      // cleared at the top of each turn rather than at the end.
      do {
        missedWhileAsking.current = false;
        rereadWanted.current = true;
        const loaded = await fetchConfig();
        if (!loaded) {
          setStoreNotice("Could not reach the server. Is it still running?");
          return "unreachable";
        }
        // The strongest thing seen across the turns below is what is reported, so
        // that an acquisition gaining its picture is not lost behind a second,
        // duller answer that arrived while this one was being applied.
        const said = applyConfig(loaded);
        if (said === "gained-image") outcome = "gained-image";
        else if (said === "changed" && outcome !== "gained-image") outcome = "changed";
      } while (missedWhileAsking.current);
    } finally {
      asking.current = false;
      // Never left set. A note still lying there when the next unrelated change
      // came along was read as though it belonged to that change.
      missedWhileAsking.current = false;
    }
    return outcome;
  }, [applyConfig]);

  // Whether there is anything to listen for. Finished data cannot change, so a
  // viewer on it holds no connection open. Until the first answer arrives we do
  // not know which we have, and "listen" is the right assumption: a live run that
  // heard nothing would sit there missing its own data.
  const shouldListen = config === null || config.live !== false;
  const hasManifestState = Boolean(config?.liveState?.runs?.length);

  // Listen for the server saying that something has changed.
  //
  // The viewer used to find this out by asking, every seven hundred milliseconds
  // for the life of the window, whether anything had moved -- and being told
  // "nothing" almost every time. Now the server says so when it happens: the page
  // holds one connection open and hears a line on it when there is something to
  // hear. Between events nothing is sent and nothing is asked.
  //
  // A message carries no detail, and does not need to. Hearing one means "ask
  // again", and the answer is read from disk, so there is only ever one
  // description of the world and it is the true one.
  React.useEffect(() => {
    // On finished data there is nothing to hear, so no connection is held. This
    // is the whole of static mode on this side.
    //
    // Written so the *first* answer is still fetched whichever mode we are in:
    // until something has been loaded there is a viewer with nothing in it, and
    // we do not yet know which mode we are in.
    if (!shouldListen) return undefined;
    let stop = false;
    const askAgain = async () => (stop ? "busy" : catchUp());

    const listener = new EventSource("/api/events");
    // Any message at all means "ask again", so both the named event and anything
    // else that arrives are treated the same. Being generous here means a future
    // kind of announcement cannot be silently ignored by an older page.
    // An announcement can carry one piece of detail, and only one: that what was
    // written went into a store the viewer may already have open, rather than into a
    // new store of its own. That is what a run does when it fills in one large
    // OME-Zarr tile by tile, and it is the one change the page cannot see for itself —
    // no description moves, so reading the disk again reveals nothing. The engine has
    // to be told to let go of the image it has already decoded, or it will go on
    // showing the emptiness it settled on earlier and never look again.
    //
    // Anything else, including a message with no detail at all, just means "ask again".
    const heard = async (event: MessageEvent) => {
      let said: { imageWrittenInPlace?: boolean } | null = null;
      try {
        said = event.data ? JSON.parse(event.data) : null;
      } catch {
        said = null; // not readable, so treat it as a plain "something changed"
      }
      if (said?.imageWrittenInPlace && engine.current) {
        // Only unversioned stores need this hint. Revisioned sources refresh
        // once through syncSources when catchUp observes a newer publication.
        engine.current.letGoOfDecodedPieces();
        askAgain();
        return;
      }
      const outcome = await askAgain();
      // An acquisition that was already on screen, and had nothing in it, now has
      // a picture. The engine has already decided that store is empty and will
      // never ask the disk again, so it has to be told to forget — otherwise the
      // panel goes on showing black for as long as the page stays open, with the
      // acquisition listed and its eye open and nothing saying why.
      //
      // Narrow on purpose. A position arriving, or a timelapse lengthening, is not
      // this, so nothing already fetched is thrown away in the far commoner case.
      // See anyStoreGainedItsFirstImage for why this particular signal.
      if (outcome === "gained-image" && engine.current) engine.current.letGoOfDecodedPieces();
    };

    listener.addEventListener("changed", heard);
    listener.onmessage = heard;
    // Opening the connection is also what fetches the images for the first time,
    // and this is deliberate rather than convenient. It means "we are connected"
    // and "we are up to date" are established together, so there is no separate
    // path to get wrong -- and a viewer that opened while the server was still
    // starting simply catches up when the browser reconnects, which it does on
    // its own. During a run there is no button to try again with, so recovering
    // without one matters.
    listener.onopen = () => {
      if (stop) return;
      setStoreNotice(null);
      catchUp();
    };
    // The browser reconnects on its own when a connection drops, so this is not a
    // place to retry from -- it is a place to say something. A viewer that has
    // quietly lost its server looks exactly like an experiment that has stopped
    // producing data, and at two in the morning those call for very different
    // reactions.
    let silence = 0;
    listener.onerror = () => {
      if (stop) return;
      silence += 1;
      if (silence === UNANSWERED_BEFORE_SAYING_SO) {
        setStoreNotice("Not hearing from the server — what is shown may be out of date.");
      }
    };

    return () => {
      stop = true;
      listener.close();
    };
    // Depending on one true-or-false rather than on the whole of what is open, and
    // the difference matters more than it looks.
    //
    // The set of open images changes every time an acquisition appears. If that
    // tore this down, it would close and reopen the very connection the news
    // arrived on -- so a run producing data steadily would spend it reconnecting,
    // with a gap each time in which the next announcement would be missed.
    //
    // It is a plain true-or-false rather than the mode itself for a smaller
    // reason: before the first answer we do not yet know the mode, and going from
    // "not known yet" to "live" would count as a change and reconnect once for
    // nothing. Both of those states mean "listen", so as one boolean they are the
    // same value and nothing happens.
  }, [catchUp, shouldListen]);

  // SSE is the immediate path; this slow conditional check is the recovery
  // path.  A proxy, sleeping laptop or brief disconnect can lose a hint.  The
  // authoritative revision cannot be lost, so a later 200 catches up while the
  // ordinary idle answer is a header-only 304 and never reaches Neuroglancer.
  React.useEffect(() => {
    if (!config || !shouldListen || !hasManifestState) return undefined;
    let stopped = false;
    let checking = false;
    let etag: string | null = null;
    const check = async () => {
      if (stopped || checking) return;
      checking = true;
      try {
        const answer = await fetchLiveState(etag);
        if (stopped || !answer) return;
        etag = answer.etag || etag;
        if (answer.unchanged) return;
        const unsafe = liveStateProblem(config.liveState, answer.state);
        if (unsafe) {
          setStoreNotice(staleLiveState(unsafe));
          return;
        }
        setStoreNotice(null);
        if (JSON.stringify(answer.state) !== JSON.stringify(config.liveState)) {
          await catchUp();
        }
      } finally {
        checking = false;
      }
    };
    // The browser tests set this to a few hundred milliseconds, so the recovery
    // can be watched without waiting ten seconds for it.
    const asked = Number((globalThis as { zmartLiveCheckMs?: unknown }).zmartLiveCheckMs);
    const every = Number.isFinite(asked) && asked > 0 ? asked : LIVE_STATE_CHECK_MS;
    const timer = setInterval(check, every);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [catchUp, config?.liveState, hasManifestState, shouldListen]);

  const setGroup = (name: string, change: { visible: boolean }) =>
    setGroupState((current) => ({ ...current, [name]: { ...current[name], ...change } }));

  const setLayer = (index: number, change: Partial<LayerState>) =>
    setLayerState((current) =>
      current.map((entry, i) => (i === index ? { ...entry, ...change } : entry)),
    );

  /**
   * Set a channel's window only if the operator has not set one themselves.
   *
   * The reading a channel opens with is asked over the network and answers a
   * second or so later. By then an operator may already have moved a handle,
   * and a measurement landing on top of that takes their work away for no
   * reason they can see. Decided here rather than by the panel because this
   * is where the current answer lives: the panel would be deciding from a
   * copy of the state as it was when the question was asked.
   */
  const setWindowIfUnset = (index: number, window: Window) =>
    setLayerState((current) =>
      current.map((entry, i) =>
        (i === index && !entry.window ? { ...entry, window } : entry)),
    );

  /** Whether the next pass over the engine reads the stores again; asking clears it. */
  const takeRereadWanted = () => {
    const wanted = rereadWanted.current;
    rereadWanted.current = false;
    return wanted;
  };

  return {
    config, layerState, groupState, groupOrder, notice: storeNotice, setNotice: setStoreNotice,
    applyConfig, setLayer, setWindowIfUnset, setGroup, takeRereadWanted,
  };
}
