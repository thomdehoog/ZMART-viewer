import React from "react";
import NeuroglancerView from "./NeuroglancerView.jsx";
import LayerPanel from "./LayerPanel.jsx";
import TargetsPanel from "./TargetsPanel.jsx";
import { PlacePointTool, PlaceBoundingBoxTool } from "neuroglancer/unstable/ui/annotations.js";
import {
  lookAtWhatOpened,
  whatIsOnScreen,
  chooseScaleWhenTheImagesAreMeasured,
  keepSpatialAxes,
  whenTheSourcesHaveSettled,
  letGoOfDecodedPieces,
  lettingGo,
  sourceRefreshing,
  sourcesStillWaiting,
  syncLayers,
  syncView,
  stretchTheDisplay,
} from "./drawing/neuroglancer.js";
import ScaleBar from "./ScaleBar.jsx";
import AxisSlider from "./AxisSlider.jsx";
import { LOOKUP_TABLE_NAMES, engineName, layerKey, layersFor } from "./drawing/layers.js";
import { VIEW_LABELS, inSelectedView, selectedViews, viewChoices } from "../../engine/drawing/embedding.js";
import { liveStateProblem } from "./drawing/live-refresh.js";
import LoadWindow from "./LoadWindow.jsx";
import ModeToggle from "./ModeToggle.jsx";
import BringItBack from "./BringItBack.jsx";
import ThemeToggle from "./ThemeToggle.jsx";
import { TARGET_LAYER, annotationLayer } from "./annotation-layer.js";
import { closeGroup, fetchConfig, fetchLiveState, listFolders, loadTargets, measureHere } from "./asking-python.js";
import { topBarStyles } from "./top-bar-styles.js";

const LIVE_STATE_CHECK_MS = 10_000;

// Which of the engine's named panel layouts the flat view asks for.
//
// The engine names its panels after *display* axes -- the first, second and third
// of the axes it has been handed, not the axes the image calls x, y and z. So
// this name only means anything alongside the order those axes are handed over
// in, which is settled in neuroglancer.js by `pinTheAxesThatMeasureDistance`. It hands
// them over width first, then height, then depth. With that order, "xy" puts width
// across the window running to the right, height down it, and depth into the
// screen -- the plane the operator scrolls through, drawn the same way round as
// the specimen.
//
// **The two must be changed together.** Either one on its own gives a view that
// is edge-on or mirrored, and a mirrored view is the dangerous one because it
// still looks like a good picture. The viewer shipped a mirrored one for months.
// neuroglancer.js sets this out at length, and
// `tests/test_the_picture_is_not_mirrored.py` measures it off the screen.
const SLICE_LAYOUT = "xy";
const VOLUME_LAYOUT = "3d";

// How many unanswered questions in a row before the panel says the server has
// gone quiet. At one question every 700 ms this is a little over three seconds,
// which is long enough to ride out a brief hiccup and short enough to be useful.
const UNANSWERED_BEFORE_SAYING_SO = 5;

/**
 * Did an acquisition that was already on screen, but had nothing in it, just gain
 * its first image?
 *
 * This is the signal behind a fault that cost an operator a whole session. The
 * viewer notices an acquisition the instant its *description* lands, which is the
 * earliest possible moment and is deliberate — waiting would mean a run appearing
 * in the panel some seconds after it started. But at that moment there is no
 * picture behind it yet. The engine looks, finds nothing, and remembers that with
 * no time limit; so when the image arrived a moment later the panel went on
 * showing black, for as long as the page stayed open. The acquisition was listed,
 * its eye was open, and nothing said why it was empty. Reloading the page showed
 * the data perfectly, which is the tell: only the open viewer was stuck.
 *
 * The engine has a way to be told to forget what it has decoded, and the question
 * is when to use it. Doing so on every announcement is far too blunt — it throws
 * away everything fetched, and during a run announcements arrive constantly, so a
 * position arriving would cost a refetch of the whole view. What is wanted is the
 * narrow case: a store the panel already knew about, which had no measurable
 * picture in it, and now has one.
 *
 * That transition is visible in the answer the server gives. A store with nothing
 * written yet has no histogram — there were no pixels to measure — and no count of
 * moments. Once an image lands, both appear. A store arriving for the first time
 * is not this: it was not there before, so the engine has decoded nothing for it
 * and has nothing to forget.
 */
function anyStoreGainedItsFirstImage(previous, loaded) {
  if (!previous) return false;
  const before = new Map((previous.layers || []).map((spec) => [layerKey(spec), spec]));
  return (loaded.layers || []).some((spec) => {
    const was = before.get(layerKey(spec));
    if (!was) return false; // newly arrived, so nothing has been decoded for it
    const hadNothing = was.histogram == null && was.frames == null;
    const hasSomethingNow = spec.histogram != null || spec.frames != null;
    return hadNothing && hasSomethingNow;
  });
}

// -- the shell ----------------------------------------------------------------

/**
 * The application shell, and the single owner of what the viewer shows.
 *
 * <NeuroglancerView> mounts the engine and hands back the `viewer`; everything
 * about *what* is displayed is pushed in from here. Switching between the plane
 * and the volume re-applies state to the same viewer rather than rebuilding it,
 * so the data already fetched stays in memory and the toggle is instant.
 */
export default function App() {
  const [viewer, setViewer] = React.useState(null);
  // Whether the first fit-to-window has run on this engine; the canvas stays
  // veiled (plain black) until it has. See the effect further down.
  const [framed, setFramed] = React.useState(false);
  // Bumped when an acquisition is closed, which builds the engine again. See
  // the note in NeuroglancerView.
  const [engineGeneration, setEngineGeneration] = React.useState(0);
  // The same engine, reachable without waiting for a re-render. applyConfig below
  // runs while answering the server and needs it there and then; a piece of state
  // would only reach it on the next pass, which is too late to be any use.
  const engine = React.useRef(null);
  const [config, setConfig] = React.useState(null);
  const [requestedMode, setMode] = React.useState("flat");
  const [requestedViews, setRequestedViews] = React.useState({});
  const rememberedPosition = React.useRef({});
  const cameraOnReopen = React.useRef(null);
  const views = React.useMemo(() => viewChoices(config?.layers || []), [config]);
  const hasLegacy = (config?.layers || []).some(spec => !spec.view);
  const mode = views.length && !hasLegacy ? "flat" : requestedMode;
  React.useEffect(() => {
    if (views.length && !hasLegacy) setMode("flat");
  }, [views, hasLegacy]);
  const chosenViews = React.useMemo(() => selectedViews(config?.layers || [], requestedViews), [config, requestedViews]);
  const included = React.useMemo(() => new Set((config?.layers || [])
    .flatMap((spec, i) => inSelectedView(spec, chosenViews) && (mode === "flat" || !spec.view) ? [i] : [])), [config, chosenViews, mode]);
  const transparentBackground = config?.transparentBackground === true && mode === "flat";
  React.useEffect(() => {
    document.documentElement.toggleAttribute("data-transparent-background", transparentBackground);
    return () => document.documentElement.removeAttribute("data-transparent-background");
  }, [transparentBackground]);
  // A projection by default rather than accumulation; see `VolumeMode`.
  const [volumeMode, setVolumeMode] = React.useState("max");
  const [volumeGain, setVolumeGain] = React.useState(0);
  const [volumeAttenuation, setVolumeAttenuation] = React.useState(0);
  // Null until the operator moves it, so the launch flag is respected
  // until somebody overrides it deliberately.
  const [chosenDepthSamples, setChosenDepthSamples] = React.useState(null);
  const [displayScales, setDisplayScales] = React.useState({ x: 1, y: 1, z: 1 });
  const depthSamples = chosenDepthSamples ?? config?.depthSamples ?? 256;

  // Applied straight to the engine rather than through the scene, because it
  // is a property of how the picture is viewed and not of any layer in it.
  React.useEffect(() => {
    if (viewer) stretchTheDisplay(viewer, displayScales);
  }, [viewer, displayScales]);
  // Per-layer interface state. Held here rather than in the engine because the
  // panel and the viewer must never disagree about what is showing.
  const [layerState, setLayerState] = React.useState([]);
  // One entry per acquisition type, plus the order they are drawn in. Held here
  // rather than in the engine for the same reason as the rows: the panel and the
  // view must never disagree about what is showing.
  const [groupState, setGroupState] = React.useState({});
  const [groupOrder, setGroupOrder] = React.useState([]);
  const [targets, setTargets] = React.useState([]);
  const [targetsLoaded, setTargetsLoaded] = React.useState(false);
  const [targetColor, setTargetColor] = React.useState("#ffd34d");
  const [targetsVisible, setTargetsVisible] = React.useState(true);
  const [activeTool, setActiveTool] = React.useState(null);
  // What happened the last time the targets were saved, shown in the panel so a
  // failed save cannot pass unnoticed.
  const [saveState, setSaveState] = React.useState({ status: "idle" });
  // Opening or closing images talks to Python, so the button is held while that
  // happens and anything that went wrong is shown rather than swallowed.
  const [storeBusy, setStoreBusy] = React.useState(false);
  const [storeNotice, setStoreNotice] = React.useState(null);
  // The in-page load window's folder listing; null while it is closed.
  const [loadListing, setLoadListing] = React.useState(null);
  // Which channel the block of controls is acting on. Held by name rather than by
  // position in the list, because the list is rebuilt whenever something is opened
  // or closed: a position would still be a valid number afterwards and would
  // quietly refer to a different channel, so the sliders would go on working while
  // adjusting something the operator was not looking at.
  const [selectedKey, setSelectedKey] = React.useState(null);
  const [barOpen, setBarOpen] = React.useState(true);
  // Which edge the bar of controls sits on, decided when the viewer is started.
  const onLeft = config?.panelSide === "left";
  const annotationSource = React.useRef(null);
  // How to stop listening to the annotation source we are currently following.
  const stopListening = React.useRef(null);
  // The targets read back from the previous session. These are handed to the
  // engine once, when the layer that holds them is built. Kept aside from the
  // living list of targets on purpose: that list changes with every scribble, and
  // if the description of the scene depended on it, drawing would rebuild the very
  // layer being drawn into.
  const targetsFromDisk = React.useRef([]);
  // The target list as last read from or written to disk, as text, so a save is
  // only sent for a real change. Null until the engine's list is first seen.
  const lastSavedText = React.useRef(null);
  // True when the saved file could not be read: nothing may be saved over it.
  const targetsUnreadable = React.useRef(false);
  // The set of images last taken on, used to spot an answer that says nothing new.
  const applied = React.useRef(null);
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
  const applyConfig = React.useCallback((loaded) => {
    // What was taken on last time. Kept here rather than read back out of the
    // displayed state because it is needed *while* deciding the new state, and a
    // piece of state cannot be read and written in the same breath.
    // Nothing came back: the server could not be reached. Whatever is on screen
    // stays there — an experiment half-watched is better than a blank panel — and
    // the poll below is what says so out loud.
    if (!loaded) return "unchanged";
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
      setStoreNotice(`Live publication state is stale: ${unsafe}. The last safe image is still shown.`);
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
    // Keep the order the operator dragged things into, with anything new appended
    // rather than dropped in the middle of what they arranged.
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
  const catchUp = React.useCallback(async () => {
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
    let outcome = "unchanged";
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

  // The targets drawn in a previous session, read once. The images are not read
  // here: they arrive when the connection below opens, which is the moment we
  // know the server is actually answering.
  React.useEffect(() => {
    let cancelled = false;
    loadTargets().then(({ targets: savedTargets, error }) => {
      if (cancelled) return;
      if (error) {
        // Drawing still works, but nothing is saved for the rest of this
        // session, and the panel says why.
        targetsUnreadable.current = true;
        setSaveState({ status: "error", message: error });
      }
      targetsFromDisk.current = savedTargets || [];
      setTargets(savedTargets || []);
      setTargetsLoaded(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

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
    const heard = async (event) => {
      let said = null;
      try {
        said = event.data ? JSON.parse(event.data) : null;
      } catch {
        said = null; // not readable, so treat it as a plain "something changed"
      }
      if (said?.imageWrittenInPlace && engine.current) {
        // Only unversioned stores need this hint. Revisioned sources refresh
        // once through syncSources when catchUp observes a newer publication.
        letGoOfDecodedPieces(engine.current);
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
      // See anyStoreGainedItsFirstImage above for why this particular signal.
      if (outcome === "gained-image" && engine.current) letGoOfDecodedPieces(engine.current);
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
    if (!shouldListen || !hasManifestState) return undefined;
    let stopped = false;
    let checking = false;
    let etag = null;
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
          setStoreNotice(
            `Live publication state is stale: ${unsafe}. The last safe image is still shown.`,
          );
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
    const asked = Number(globalThis.zmartLiveCheckMs);
    const every = Number.isFinite(asked) && asked > 0 ? asked : LIVE_STATE_CHECK_MS;
    const timer = setInterval(check, every);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [catchUp, config?.liveState, hasManifestState, shouldListen]);

  // Everything the engine should be showing, in the order it should be drawn.
  //
  // This is worked out here, on its own, so that it is recomputed only when
  // something about the picture has genuinely changed. Note what it does *not*
  // depend on: the list of drawn targets. Those live in the engine once their
  // layer exists, and the list beside the image is a reflection of them — so
  // drawing one must not send the whole scene back through here.
  // An acquisition being shown position by position: which group, how many of
  // its stores are on screen, and how fast the rest should follow. Nothing is
  // written for this -- every position is already on disk and already placed;
  // the row is simply drawn from a growing number of them.
  const [revealing, setRevealing] = React.useState(null);
  // The acquisition the view should be pointed at, once its pieces are on
  // screen. Opening used to leave the camera where it was, which on a machine
  // holding one picture at the stage's coordinates and another at zero means
  // opening the second and seeing black (2026-08-21).
  const [lookAt, setLookAt] = React.useState(null);
  const groupsBefore = React.useRef(null);
  React.useEffect(() => {
    if (!config) return;
    const groups = config.groups || [];
    const before = groupsBefore.current;
    groupsBefore.current = groups;
    if (before === null) return;           // the first look is fitted already
    const fresh = groups.filter((one) => !before.includes(one));
    if (fresh.length) setLookAt(fresh[fresh.length - 1]);
  }, [config]);
  React.useEffect(() => {
    if (!viewer || !lookAt || !config) return undefined;
    const named = config.layers
      .filter((spec, i) => (spec.group || "") === lookAt && included.has(i))
      .map((spec) => engineName(spec));
    if (!named.length) return undefined;
    // Given a moment for the engine to take the new sources on: the bounds
    // this reads are the sources' own, and they are not there until they are.
    // Tried again while the answer is "not yet": the bounds this reads are
    // the sources' own, and a source that has not arrived has none to give.
    const soon = window.setInterval(() => {
      if (lookAtWhatOpened(viewer, named)) setLookAt(null);
    }, 900);
    return () => window.clearInterval(soon);
    // Deliberately not depending on the scene: this effect is declared above
    // it, and naming it here reaches a constant before it exists -- which
    // took the whole page down with "Cannot access 'scene' before
    // initialization" (2026-08-21).
  }, [viewer, lookAt, config, included]);
  React.useEffect(() => {
    if (!revealing || !config) return undefined;
    // How many pieces this acquisition has to arrive: the stores of its one
    // row, or its rows, whichever way the dataset opened.
    const rows = config.layers.filter(
      (spec) => (spec.group || "") === revealing.group);
    const all = rows.length > 1
      ? rows.length
      : (rows[0]?.sources || []).length;
    if (!all || revealing.count >= all) return undefined;
    const wait = revealing.perSecond > 0 ? 1000 / revealing.perSecond : 0;
    const soon = window.setTimeout(
      () => setRevealing((now) => (now && now.group === revealing.group
        ? { ...now, count: now.count + 1 } : now)),
      wait);
    return () => window.clearTimeout(soon);
  }, [revealing, config]);

  const scene = React.useMemo(() => {
    if (!config || layerState.length !== config.layers.length) return null;
    const indices = [...included];
    const layers = layersFor({ ...config, layers: indices.map(i => config.layers[i]) }, mode,
                              indices.map(i => layerState[i]), groupState, groupOrder, volumeMode,
                              { gain: volumeGain, attenuation: volumeAttenuation,
                                depthSamples },
                              revealing);
    // The layer holding drawn targets is added once the saved ones have been read
    // back, so whatever was saved is present from the moment the layer exists.
    if (targetsLoaded) {
      layers.push(annotationLayer(targetsFromDisk.current, targetColor, targetsVisible));
    }
    return layers;
  }, [
    config,
    mode,
    layerState,
    included,
    revealing,
    groupState,
    groupOrder,
    volumeMode,
    volumeGain,
    volumeAttenuation,
    depthSamples,
    targetsLoaded,
    targetColor,
    targetsVisible,
  ]);

  // Let the engine settle on a starting magnification once the images have said
  // how big they are. Declared ahead of the effect that adds the layers so the
  // waiting is in place before there is anything to wait for; see neuroglancer.js for
  // what goes wrong without it.
  //
  // Until that first fit has run, the canvas is kept veiled: the engine
  // paints the freshly-attached image at its own default magnification for a
  // frame or two before the fit lands, and that wrongly-sized glimpse read
  // as the picture jumping in size on every first open (2026-08-23). Behind
  // the veil is plain black, so what shows instead is what an empty canvas
  // shows anyway.
  React.useEffect(() => {
    if (!viewer) return undefined;
    return keepSpatialAxes(viewer);
  }, [viewer]);

  React.useEffect(() => {
    if (!viewer) return undefined;
    setFramed(false);
    return chooseScaleWhenTheImagesAreMeasured(viewer, () => {
      const camera = cameraOnReopen.current;
      if (camera) {
        const position = viewer.navigationState.position, space = position.coordinateSpace.value;
        position.value = Float32Array.from(space.names, (name, i) =>
          camera.position[name] === undefined ? position.value[i] : camera.position[name] / space.scales[i]);
        viewer.navigationState.zoomFactor.setPhysicalScale(camera.zoomMeters, 1);
        viewer.perspectiveNavigationState.zoomFactor.setPhysicalScale(camera.perspectiveZoomMeters, 1);
        cameraOnReopen.current = null;
      }
      setFramed(true);
    });
  }, [viewer]);

  React.useEffect(() => {
    if (!viewer || !scene) return undefined;
    engine.current = viewer;
    window.zmartViewer = viewer; // handy for inspection and the browser tests
    window.zmartConfig = config;
    window.zmartMode = mode;
    window.zmartLayerState = layerState;
    // Where the operator is looking, remembered by axis name rather than by
    // position in the list. Adding an acquisition can introduce an axis the view
    // did not have before, which shifts every axis after it along one place; going
    // by name means the view still comes back to the same plane rather than to
    // whatever now happens to sit in that slot.
    const space = viewer.navigationState.position.coordinateSpace.value;
    const looking = space?.valid
      ? { ...rememberedPosition.current, ...Object.fromEntries(
          space.names.map((name, index) => [
            name,
            viewer.navigationState.position.value[index],
          ]),
        ) }
      : null;
    if (looking) rememberedPosition.current = looking;
    const zoom = viewer.navigationState.zoomFactor.value;
    const perspectiveZoom = viewer.perspectiveNavigationState.zoomFactor.value;

    syncView(viewer, {
      transparentBackground: config.transparentBackground === true,
      layout: mode === "volume" ? VOLUME_LAYOUT : SLICE_LAYOUT,
      // The engine's own furniture -- the yellow data-bounds box and the axis
      // lines -- is off unless asked for. We are supplying the interface.
      chrome: config.chrome ?? false,
    });
    // An announcement means something on disk has changed, and it may be something
    // no description of the scene would show -- a frame added to a store already
    // open. So the descriptions are read again on the pass that follows one.
    const reread = rereadWanted.current;
    rereadWanted.current = false;
    // The descriptions the panel just handed the engine, exactly as they were
    // handed over. This exists for one test and is worth the line: `neuroglancer.js`
    // carries every field of a description onto the live layer by hand, one line
    // each, so a field added to `layers.js` and forgotten there is dropped in
    // silence -- three controls were found dead that way in a single afternoon.
    // A test can only guard against the *next* one if it can ask what fields are
    // in a description rather than being told a list somebody has to remember to
    // update. See tests/test_no_setting_is_dropped_on_the_way_to_the_engine.py.
    window.zmartScene = scene;
    const reshaped = syncLayers(viewer, scene, { reread });
    const refreshed = sourceRefreshing.sources.length;
    window.zmartLayersReshaped = reshaped; // what the browser tests count
    // How many stores are still queued to be handed to the engine. Asked as a question
    // rather than left as a number, because the answer changes while a large folder is
    // loading and nothing re-runs this to keep a number up to date. Zero means every
    // position the panel knows about has reached the engine.
    window.zmartSourcesWaiting = () => sourcesStillWaiting(viewer);
    // How many times the viewer has asked the engine to let go of decoded image, and
    // how many sources it asked the last time. The browser tests read this to tell an
    // announcement that did nothing from one that did something that did not help.
    window.zmartLetGo = lettingGo;
    window.zmartSourceRefreshing = sourceRefreshing;

    // Only a change in the shape of the scene can move the view: adding or
    // removing an image makes the engine work out the coordinate space afresh,
    // and it lands somewhere sensible rather than where the operator was. Turning
    // a knob cannot, so in that far more common case nothing here runs at all.
    if ((!reshaped && !refreshed) || !looking) return undefined;
    const lookAgain = () => {
      const position = viewer.navigationState.position;
      const now = position.coordinateSpace.value;
      if (!now?.valid) return;
      const moved = Float32Array.from(position.value);
      let changed = false;
      now.names.forEach((name, index) => {
        if (Number.isFinite(looking[name]) && moved[index] !== looking[name]) {
          moved[index] = looking[name];
          changed = true;
        }
      });
      if (changed) position.value = moved;
      if (Number.isFinite(zoom) && viewer.navigationState.zoomFactor.value !== zoom) {
        viewer.navigationState.zoomFactor.value = zoom;
      }
      if (
        Number.isFinite(perspectiveZoom)
        && viewer.perspectiveNavigationState.zoomFactor.value !== perspectiveZoom
      ) {
        viewer.perspectiveNavigationState.zoomFactor.value = perspectiveZoom;
      }
    };
    lookAgain();
    // A replacement can recreate identically named axes with new identities.
    // Restore after those sources load; revision-only refreshes leave navigation alone.
    const axesAtArming = space?.ids?.join(",");
    return whenTheSourcesHaveSettled(viewer, () => true, () => {
      const now = viewer.navigationState.position.coordinateSpace.value;
      if (now?.ids?.join(",") !== axesAtArming) lookAgain();
    });
  }, [viewer, scene, config, mode, layerState]);

  // Start listening to the layer that holds drawn targets, so the list beside the
  // image follows what is actually in the engine. The layer's store of annotations
  // is created a moment after the layer itself, which is why this waits for it
  // rather than assuming it is there.
  React.useEffect(() => {
    if (!viewer || !targetsLoaded) return undefined;
    const connect = () => {
      const layer = viewer.layerManager.getLayerByName(TARGET_LAYER)?.layer;
      const source = layer?.localAnnotations;
      if (!source) return false; // not built yet; look again shortly
      if (source === annotationSource.current) return true; // already listening
      // Let go of the previous one first. This only happens if the layer really
      // was rebuilt, which is rare now, but leaving the old listener attached
      // would mean two of them writing to the same list.
      if (stopListening.current) stopListening.current();
      annotationSource.current = source;
      stopListening.current = source.changed.add(() => setTargets(source.toJSON()));
      window.zmartAnnotationSource = source;
      const present = source.toJSON();
      // What is on disk, as the engine holds it: only a list that differs from
      // this is worth saving, so opening the viewer writes nothing.
      if (lastSavedText.current === null) lastSavedText.current = JSON.stringify(present);
      setTargets(present);
      return true;
    };
    if (connect()) return undefined;
    const waiting = setInterval(() => {
      if (connect()) clearInterval(waiting);
    }, 50);
    return () => clearInterval(waiting);
  }, [viewer, targetsLoaded, scene]);

  // Saving happens on its own, shortly after any change, so the operator never
  // has to remember to. That makes it all the more important to *show* whether it
  // worked: a target list that silently failed to save looks exactly like one
  // that saved, right up until the acquisition is reopened and the targets are
  // gone.
  React.useEffect(() => {
    if (!targetsLoaded || targetsUnreadable.current) return undefined;
    const text = JSON.stringify(targets);
    if (lastSavedText.current === null || text === lastSavedText.current) return undefined;
    let cancelled = false;
    const timer = setTimeout(async () => {
      setSaveState({ status: "saving" });
      try {
        const response = await fetch("/api/annotations", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ version: 1, annotations: targets }),
        });
        if (cancelled) return;
        if (response.ok) {
          lastSavedText.current = text;
          setSaveState({ status: "saved" });
          return;
        }
        const detail = await response.json().catch(() => null);
        setSaveState({ status: "error", message: detail?.error || `save failed (${response.status})` });
      } catch (error) {
        // Almost always the server having gone away -- worth saying plainly
        // rather than leaving the panel looking as though all is well.
        if (!cancelled) setSaveState({ status: "error", message: "could not reach the server" });
      }
    }, 150);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [targets, targetsLoaded]);

  React.useEffect(() => {
    if (!viewer) return;
    const layer = viewer.layerManager.getLayerByName(TARGET_LAYER)?.layer;
    if (!layer) return;
    layer.tool.value =
      activeTool === "point"
        ? new PlacePointTool(layer, {})
        : activeTool === "box"
          ? new PlaceBoundingBoxTool(layer, {})
          : undefined;
  }, [viewer, activeTool, targetsLoaded]);

  // The furthest any open acquisition has got in time. Several may be running at
  // once and one can be a frame ahead of another, so the slider follows the
  // longest -- a frame that exists somewhere is worth being able to reach.
  const framesAvailable = React.useMemo(() => {
    const counts = (config?.layers || [])
      .map((spec) => spec.frames)
      .filter((n) => typeof n === "number" && n > 0);
    return counts.length ? Math.max(...counts) : null;
  }, [config]);

  // Manifest runs may publish timepoints with gaps, so a single high-water
  // number is not an honest description. Read the explicit half-open ranges
  // directly from authoritative live state, not from declared array shape or a
  // convenience field on a layer row. Ordinary folders alongside it retain
  // their existing counted limit.
  const committedTimeRanges = React.useMemo(() => {
    const rows = config?.layers || [];
    const liveRuns = config?.liveState?.runs;
    if (!Array.isArray(liveRuns) || !liveRuns.length) return null;
    const ranges = [
      ...liveRuns.flatMap((run) => (
        run.sources || []
      ).flatMap((source) => source.committed_time_ranges || [])),
      ...rows
        .filter((spec) => spec.liveRunId == null)
        .flatMap((spec) => (
          typeof spec.frames === "number" && spec.frames > 0
            ? [{ start: 0, stop: spec.frames }]
            : []
        )),
    ]
      .map((range) => ({ start: range.start, stop: range.stop }))
      .sort((one, other) => one.start - other.start || one.stop - other.stop);
    const merged = [];
    for (const range of ranges) {
      const last = merged[merged.length - 1];
      if (last && range.start <= last.stop) last.stop = Math.max(last.stop, range.stop);
      else merged.push(range);
    }
    return merged;
  }, [config]);

  // Where the chosen channel now sits in the list. Falling back to the first row
  // means closing the channel that was being adjusted leaves the controls on
  // something real rather than on nothing.
  const selected = React.useMemo(() => {
    const rows = config?.layers || [];
    const at = rows.findIndex((spec) => layerKey(spec) === selectedKey);
    return at >= 0 && included.has(at) ? at : ([...included][0] ?? 0);
  }, [config, selectedKey, included]);

  const setGroup = (name, change) =>
    setGroupState((current) => ({ ...current, [name]: { ...current[name], ...change } }));

  const setLayer = (index, change) =>
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
  const setWindowIfUnset = (index, window) =>
    setLayerState((current) =>
      current.map((entry, i) =>
        (i === index && !entry.window ? { ...entry, window } : entry)),
    );

  // A channel that arrives knowing nothing about its brightness — no
  // declared window, no measured histogram — is asked about as soon as its
  // pixels exist. A live run's channels are exactly this: the live view
  // carries no display window, so they drew on the camera's whole range and
  // the arriving positions looked near-black. Each landing refreshes the
  // config, so a channel
  // whose pixels had not arrived yet is simply asked again on the next one.
  // An answer never lands over an operator's own setting: the moment a
  // window is set by hand the channel is marked touched, synchronously,
  // and is never measured again.
  const aboutTheChannel = (spec) =>
    `${(spec.sources || [spec.source])[0]}#${spec.channelIndex ?? ""}`;
  const touchedWindows = React.useRef(new Set());
  const askedForAWindow = React.useRef(new Set());
  React.useEffect(() => {
    (config?.layers || []).forEach((spec, i) => {
      if (spec.kind === "segmentation" || spec.window || spec.histogram) return;
      if (layerState[i]?.window) return;
      const about = aboutTheChannel(spec);
      if (touchedWindows.current.has(about)) return;
      if (askedForAWindow.current.has(about)) return;
      askedForAWindow.current.add(about);
      (async () => {
        const answer = await measureHere({
          source: (spec.sources || [spec.source])[0],
          sources: spec.sourceDepths ? spec.sources : undefined,
          channel: spec.channelIndex ?? null,
          box: [[0, 0], [1, 1]],
        });
        if (answer && !answer.empty && answer.window
            && !touchedWindows.current.has(about)) {
          setWindowIfUnset(i, answer.window);
        } else {
          // Nothing to read yet: let the next landing ask again.
          askedForAWindow.current.delete(about);
        }
      })();
    });
  }, [config, layerState]);

  const source = () => annotationSource.current;
  const deleteTarget = (id) => {
    const reference = source()?.getReference(id);
    if (!reference) return;
    source().delete(reference);
    reference.dispose();
  };
  /**
   * Show a target: pick it out, and move the view to where it is.
   *
   * Both halves are needed. Picking it out on its own leaves the view exactly
   * where it was, and a target is usually on some other plane of the stack — so
   * clicking it in the list looked, from the operator's side, like the button
   * simply did nothing. Moving the view is what makes it a way of getting back to
   * a place you marked earlier, which is the whole point of marking it.
   */
  const selectTarget = (id) => {
    const layer = viewer?.layerManager.getLayerByName(TARGET_LAYER)?.layer;
    const state = layer?.annotationStates?.states?.find((entry) => !entry.source.readonly);
    if (!state) return;
    layer.selectAnnotation(state, id, true);
    window.zmartSelectedTarget = id;

    const target = targets.find((entry) => entry.id === id);
    // A point sits at one place; a box is a corner and its opposite, so the
    // middle of the two is the place to go.
    const where =
      target?.point ||
      (target?.pointA && target?.pointB
        ? target.pointA.map((value, axis) => (value + target.pointB[axis]) / 2)
        : null);
    if (!where) return;
    const position = viewer.navigationState.position;
    const moved = Float32Array.from(position.value);
    // A target is recorded against the same axes the view uses, in the same
    // order, so this is a straight copy of as much of it as there is room for.
    where.slice(0, moved.length).forEach((value, axis) => {
      if (Number.isFinite(value)) moved[axis] = value;
    });
    position.value = moved;
  };
  // Give a target a name. Neuroglancer owns the annotation itself, so the new
  // description is written back through the annotation source rather than kept
  // beside it -- that way the engine, the list, and the saved file cannot drift
  // apart, and the change reaches the sidecar by the same route as everything
  // else the operator draws.
  const describeTarget = (id, description) => {
    const reference = source()?.getReference(id);
    if (!reference?.value) return;
    source().update(reference, { ...reference.value, description });
    reference.dispose();
  };

  return (
    <div
      style={{
        ...styles.shell,
        // Putting the bar on the left is done by reversing the row rather than by
        // moving anything: the image and the bar keep the same order in the page,
        // so the fold strip stays between them and still folds towards the edge the
        // bar is on, whichever edge that is.
        flexDirection: onLeft ? "row-reverse" : "row",
      }}
    >
      <main style={styles.stage}>
        <NeuroglancerView onViewer={setViewer} generation={engineGeneration} veiled={!framed} />
        <div style={styles.topBar}>
          {views.map(({ id, label, keys }) => (
            <label key={id} style={{ display: "flex", alignItems: "center", gap: 6 }}>
              {views.length > 1 ? label : "View"}
              <select aria-label={`${label} view`} value={chosenViews[id]} disabled={mode !== "flat"}
                style={topBarStyles.button}
                onChange={event => setRequestedViews(previous => ({ ...previous, [id]: event.target.value }))}>
                {keys.map(key => <option key={key} value={key}>{VIEW_LABELS[key]}</option>)}
              </select>
            </label>
          ))}
          {(!views.length || hasLegacy) && <ModeToggle mode={mode} onChange={setMode} />}
          <BringItBack viewer={viewer} />
          <ThemeToggle />
        </div>
        <ScaleBar viewer={viewer} />
        {/* The two sliders are placed to match the directions they move in, which
            makes them quicker to reach for without reading the labels. Depth runs
            up and down the right-hand edge, the way a stack is pictured; time runs
            left to right along the bottom, the way a recording is pictured.

            Z steps through the planes of the stack, so it belongs to the 2-D
            working view; in 3-D the whole depth is already on screen. Time is
            meaningful in both. Neither appears unless the image actually has that
            axis with more than one step along it -- a still image gets no time
            slider, and a single plane gets no Z slider. */}
        {/* The depth slider stands OVER the right-hand edge of the drawing
            area rather than beside it, so a picture centred on the whole
            area sits a little behind it. Framing around that strip was
            tried on 2026-08-22 and put back; see the note in
            showTheWholePicture for what it cost. */}
        <div style={styles.depthControl}>
          {mode === "flat" && (
            <AxisSlider viewer={viewer} axis="z" orientation="vertical"
              label={!hasLegacy && Object.values(chosenViews).every(key => key === "top") ? "Plane" : "Z"}
              physicalZ={views.length > 0 && !hasLegacy && Object.values(chosenViews).every(key => key === "slice")} />
          )}
        </div>
        <div style={styles.timeControl}>
          <AxisSlider
            viewer={viewer}
            axis="t"
            label="T"
            // A timelapse is given its full length in time when it is created,
            // long before the run has produced that many frames. The slider stops
            // at what has actually been written, because the engine remembers
            // "nothing here" for a frame looked at too early and will not look
            // again -- so that frame would stay blank for the rest of the session.
            limit={framesAvailable}
            ranges={committedTimeRanges}
          />
        </div>
      </main>
      {/* One bar holding everything: the images and the targets.
          It folds away to the edge because at the microscope the screen is often
          a laptop's, and a third of it permanently given to controls is a third
          of the specimen not being looked at. */}
      <button
        type="button"
        onClick={() => setBarOpen((open) => !open)}
        style={styles.fold}
        aria-label={barOpen ? "hide the controls" : "show the controls"}
        aria-expanded={barOpen}
        title={barOpen ? "Fold the controls away" : "Show the controls"}
      >
        {barOpen === onLeft ? "›" : "‹"}
      </button>
      {barOpen && (
        <aside style={styles.bar} aria-label="controls">
          {config && (
            <LayerPanel
              layers={config.layers}
              included={included}
              state={layerState}
              mode={mode}
              groupOrder={groupOrder}
              groupState={groupState}
              selected={selected}
              onSelect={(index) => setSelectedKey(layerKey(config.layers[index]))}
              canOpen={config.canOpen !== false}
              onGroupToggle={(name) => setGroup(name, { visible: !groupState[name]?.visible })}
              busy={storeBusy}
              notice={storeNotice}
              onOpenStore={async () => {
                setStoreBusy(true);
                setStoreNotice(null);
                // The window always appears -- the choice of what kind of
                // thing is being loaded comes first. The native chooser is a
                // button inside it.
                const listing = await listFolders(null);
                if (listing.error) setStoreNotice(listing.error);
                else setLoadListing(listing);
                setStoreBusy(false);
              }}
              onCloseGroup={async (group) => {
                setStoreBusy(true);
                setStoreNotice(null);
                const result = await closeGroup(group);
                if (result.error) {
                  setStoreNotice(result.error);
                  setStoreBusy(false);
                  return;
                }
                if (result.config) applyConfig(result.config);
                cameraOnReopen.current = null;
                if (viewer?.navigationState.position.coordinateSpace.value.valid && result.config?.layers.length) {
                  const position = viewer.navigationState.position, space = position.coordinateSpace.value;
                  cameraOnReopen.current = {
                    position: Object.fromEntries(space.names.map((name, i) =>
                      [name, position.value[i] * space.scales[i]])),
                    zoomMeters: viewer.navigationState.zoomFactor.value
                      * viewer.navigationState.zoomFactor.canonicalVoxelPhysicalSize,
                    perspectiveZoomMeters: viewer.perspectiveNavigationState.zoomFactor.value
                      * viewer.perspectiveNavigationState.zoomFactor.canonicalVoxelPhysicalSize,
                  };
                }
                // The engine is built again, and what was closed goes with
                // it. Removing the layer alone did not: whatever was opened
                // NEXT came up with most of its tiles unpainted, while the
                // server sent the same description, the browser fetched the
                // same pieces, and the engine reported the same sources, the
                // same bounds and the same camera. The only thing that ever
                // put it right was a fresh drawing context (measured
                // 2026-08-21). Closing is a rare, deliberate act and this
                // costs a redraw of what remains, which is the right trade
                // against a picture that is quietly incomplete.
                //
                // Let go of it BEFORE it is thrown away: every effect
                // watching the engine unsubscribes on the way, so nothing is
                // left reading one that has been disposed -- which took the
                // page down with "Cannot read properties of undefined".
                setViewer(null);
                setEngineGeneration((count) => count + 1);
                setStoreBusy(false);
              }}
              onToggle={(i) => setLayer(i, { visible: !layerState[i].visible })}
              onColor={(i, color) => setLayer(i, { color })}
              onOpacity={(i, opacity) => setLayer(i, { opacity })}
              onWindow={(i, window) => {
                // Marked touched before anything else: the automatic
                // windowing below must never answer over an operator's own
                // setting, however the timing falls.
                const spec = config?.layers?.[i];
                if (spec) touchedWindows.current.add(aboutTheChannel(spec));
                setLayer(i, { window });
              }}
              onMeasureHere={async (i, asked = {}) => {
                // Reading the pixels themselves, for the two things in the
                // panel that need them. The panel has the picture drawn but
                // not the numbers behind it -- and what is on the canvas has
                // already been windowed and coloured -- so the reading is
                // asked of the server, which has the store.
                //
                // Auto asks about the part on screen and takes the window
                // from it. The two boxes under the histogram ask about the
                // same pixels over a different stretch of brightness, and
                // take only the bars: widening the axis has to show what is
                // out there, not blank space beside a squeezed picture.
                const spec = config?.layers?.[i];
                if (!spec) return null;
                const box = asked.whole
                  ? [[0, 0], [1, 1]]
                  : viewer && whatIsOnScreen(viewer);
                if (!box) return null;
                const answer = await measureHere({
                  source: (spec.sources || [spec.source])[0],
                  sources: spec.sourceDepths ? spec.sources : undefined,
                  channel: spec.channelIndex ?? null,
                  box,
                  span: asked.span || null,
                });
                if (!answer || answer.empty || !answer.window) return null;
                // The window belongs to the picture, so it goes to the layer;
                // the histogram belongs to the panel that drew it, so it goes
                // back as the answer. An empty view moves nothing: a press
                // that measured no imaged pixels has nothing to say. And a
                // reading taken only to redraw the bars leaves the window
                // exactly where the operator put it.
                if (asked.ifUnset) setWindowIfUnset(i, answer.window);
                else if (!asked.span) setLayer(i, { window: answer.window });
                return answer;
              }}
              onLut={(i, lut) => setLayer(i, { lut })}
              volumeMode={volumeMode}
              onVolumeMode={setVolumeMode}
              volumeGain={volumeGain}
              onVolumeGain={setVolumeGain}
              volumeAttenuation={volumeAttenuation}
              onVolumeAttenuation={setVolumeAttenuation}
              depthSamples={depthSamples}
              onDepthSamples={setChosenDepthSamples}
              displayScales={displayScales}
              onDisplayScales={setDisplayScales}
              lookupTables={LOOKUP_TABLE_NAMES}
            />
          )}
          {config?.canSelect && (
          <TargetsPanel
            targets={targets}
            activeTool={activeTool}
            color={targetColor}
            visible={targetsVisible}
            saveState={saveState}
            onTool={setActiveTool}
            onColor={setTargetColor}
            onVisible={() => setTargetsVisible((value) => !value)}
            onSelect={selectTarget}
            onDelete={deleteTarget}
            onDescribe={describeTarget}
          />
          )}
        </aside>
      )}
      {loadListing && (
        <LoadWindow
          listing={loadListing}
          onNavigate={async (path) => {
            const listing = await listFolders(path);
            setLoadListing((current) =>
              listing.error ? { ...current, error: listing.error } : listing);
          }}
          onOpened={(config) => {
            applyConfig(config);
            setLoadListing(null);
          }}
          onArriving={(loaded, perSecond) => {
            // The acquisition just opened, shown position by position. Its
            // group is the one the server has just added, and the first
            // position is on screen at once -- an empty screen for the first
            // beat says nothing about anything.
            const group = (loaded.groups || []).at(-1) || "";
            setRevealing({ group, count: 1, perSecond });
          }}
          onConstructed={async () => {
            const config = await fetchConfig();
            if (config) applyConfig(config);
            setLoadListing(null);
          }}
          onCancel={() => setLoadListing(null)}
        />
      )}
    </div>
  );
}

// -- how it all looks ---------------------------------------------------------

const styles = {
  shell: { position: "absolute", inset: 0, display: "flex", background: "var(--page-bg)" },
  bar: {
    width: 264,
    flexShrink: 0,
    display: "flex",
    flexDirection: "column",
    minHeight: 0,
    border: "1px solid var(--panel-border)",
    borderTop: "none",
    borderBottom: "none",
    background: "var(--card-bg)",
  },
  fold: {
    alignSelf: "stretch",
    width: 14,
    border: "none",
    borderLeft: "1px solid var(--panel-border)",
    background: "var(--card-bg)",
    color: "var(--text-muted)",
    font: "12px/1 system-ui, sans-serif",
    cursor: "pointer",
    padding: 0,
  },
  stage: { flex: 1, position: "relative" },
  // The three top-bar controls sit in one row that lays itself out, so a
  // control appearing or disappearing (Overview only exists once an image
  // is open) never leaves a hole, and the gap between neighbours is said
  // once instead of being baked into each control's distance from the edge.
  topBar: {
    position: "absolute",
    top: 12,
    left: 12,
    zIndex: 10,
    display: "flex",
    gap: 12,
    alignItems: "center",
  },
  // The sliders stack in one column at the bottom of the stage, so a timelapse
  // showing both Z and T never has them overlapping.
  // Down the right-hand edge, between the scale bar in the top corner and the time
  // slider along the bottom. Both gaps are deliberate: the depth slider takes
  // whatever height is left, so it is as long as the window allows without ever
  // running into either of them.
  depthControl: {
    position: "absolute",
    top: 72,
    right: 14,
    bottom: 62,
    zIndex: 10,
    display: "grid",
    justifyItems: "end",
  },
  timeControl: {
    position: "absolute",
    left: 14,
    right: 14,
    bottom: 14,
    zIndex: 10,
    display: "grid",
    justifyItems: "stretch",
  },
};
