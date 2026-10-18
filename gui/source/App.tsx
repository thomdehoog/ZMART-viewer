import React from "react";
import type { CSSProperties } from "react";
import NeuroglancerView from "./NeuroglancerView.tsx";
import LayerPanel from "./LayerPanel.tsx";
import VolumeMode from "./VolumeMode.tsx";
import TargetsPanel from "./TargetsPanel.tsx";
import {
  lookAtWhatOpened,
  chooseScaleWhenTheImagesAreMeasured,
  whenTheSourcesHaveSettled,
} from "./drawing/framing.ts";
import { captureCamera, restoreCamera, whatIsOnScreen } from "../../engine/drawing/primitives/camera.ts";
import type { Camera } from "../../engine/drawing/primitives/camera.ts";
import { keepSpatialAxes } from "../../engine/drawing/primitives/axes.ts";
import type { SceneLayer, VolumeMode as Projection } from "../../engine/drawing/primitives/description.ts";
import type { Engine } from "../../engine/drawing/primitives/viewer.ts";
import { syncView, stretchTheDisplay } from "./drawing/viewer.ts";
import ScaleBar from "./ScaleBar.tsx";
import AxisSlider from "./AxisSlider.tsx";
import { engineName, layerKey, layersFor } from "./drawing/layers.ts";
import { VIEW_LABELS, inSelectedView, selectedViews, viewChoices } from "../../engine/drawing/embedding.js";
import { committedTimeRanges } from "./drawing/live-refresh.ts";
import LoadWindow from "./LoadWindow.tsx";
import ModeToggle from "./ModeToggle.tsx";
import BringItBack from "./BringItBack.tsx";
import ThemeToggle from "./ThemeToggle.tsx";
import { useTargets } from "./targets.tsx";
import { useFollowingTheServer } from "./following-the-server.ts";
import { closeGroup, listFolders, measureChannel } from "./asking-python.ts";
import type { LayerSpec, Listing } from "./asking-python.ts";
import type { DisplayScales, Mode } from "./display-axes.ts";
import { topBarStyles } from "./top-bar-styles.ts";


// What each named view is called in the picker, read by whichever key a choice offers.
const VIEW_NAMES: Readonly<Record<string, string>> = VIEW_LABELS;

// Which of the engine's named panel layouts the flat view asks for.
//
// The engine names its panels after *display* axes -- the first, second and third
// of the axes it has been handed, not the axes the image calls x, y and z. So
// this name only means anything alongside the order those axes are handed over
// in, which is settled in drawing/framing.ts by `pinTheAxesThatMeasureDistance`. It hands
// them over width first, then height, then depth. With that order, "xy" puts width
// across the window running to the right, height down it, and depth into the
// screen -- the plane the operator scrolls through, drawn the same way round as
// the specimen.
//
// **The two must be changed together.** Either one on its own gives a view that
// is edge-on or mirrored, and a mirrored view is the dangerous one because it
// still looks like a good picture. The viewer shipped a mirrored one for months.
// drawing/framing.ts sets this out at length, and
// `tests/test_the_picture_is_not_mirrored.py` measures it off the screen.
const SLICE_LAYOUT = "xy";
const VOLUME_LAYOUT = "3d";

// -- the shell ----------------------------------------------------------------

/**
 * The application shell, and the single owner of what the viewer shows.
 *
 * <NeuroglancerView> mounts the engine and hands back its handle; everything
 * about *what* is displayed is pushed in from here. Switching between the plane
 * and the volume re-applies state to the same viewer rather than rebuilding it,
 * so the data already fetched stays in memory and the toggle is instant.
 */
export default function App() {
  const [engine, setEngine] = React.useState<Engine | null>(null);
  const viewer = engine?.viewer ?? null;
  // Whether the first fit-to-window has run on this engine; the canvas stays
  // veiled (plain black) until it has. See the effect further down.
  const [framed, setFramed] = React.useState(false);
  // Bumped when an acquisition is closed, which builds the engine again. See
  // the note in NeuroglancerView.
  const [engineGeneration, setEngineGeneration] = React.useState(0);
  // The same engine, reachable without waiting for a re-render. The connection
  // to the server below hears announcements while answering them and needs it
  // there and then; a piece of state would only reach it on the next pass, which
  // is too late to be any use. It is always the engine on screen, and nothing
  // once that is closed: kept after a close, it pointed an announcement at an
  // engine already disposed until the new one arrived.
  const engineOnScreen = React.useRef<Engine | null>(null);
  React.useEffect(() => { engineOnScreen.current = engine; }, [engine]);
  const {
    config, layerState, groupState, groupOrder, notice: storeNotice, setNotice: setStoreNotice,
    applyConfig, setLayer, setWindowIfUnset, setGroup, takeRereadWanted,
  } = useFollowingTheServer(engineOnScreen);
  const targets = useTargets(viewer, config ? config.canSelect === true : null);
  const [requestedMode, setMode] = React.useState<Mode>("flat");
  const [requestedViews, setRequestedViews] = React.useState<Record<string, string>>({});
  const rememberedPosition = React.useRef<Record<string, number>>({});
  const cameraOnReopen = React.useRef<Camera | null>(null);
  const views = React.useMemo(() => viewChoices(config?.layers || []), [config]);
  const hasLegacy = (config?.layers || []).some(spec => !spec.view);
  const mode: Mode = views.length && !hasLegacy ? "flat" : requestedMode;
  React.useEffect(() => {
    if (views.length && !hasLegacy) setMode("flat");
  }, [views, hasLegacy]);
  const chosenViews: Record<string, string> = React.useMemo(
    () => selectedViews(config?.layers || [], requestedViews), [config, requestedViews]);
  const included = React.useMemo(() => new Set((config?.layers || [])
    .flatMap((spec, i) => inSelectedView(spec, chosenViews) && (mode === "flat" || !spec.view) ? [i] : [])), [config, chosenViews, mode]);
  const transparentBackground = config?.transparentBackground === true && mode === "flat";
  React.useEffect(() => {
    document.documentElement.toggleAttribute("data-transparent-background", transparentBackground);
    return () => document.documentElement.removeAttribute("data-transparent-background");
  }, [transparentBackground]);
  // A projection by default rather than accumulation; see `VolumeMode`.
  const [volumeMode, setVolumeMode] = React.useState<Projection>("max");
  const [volumeGain, setVolumeGain] = React.useState(0);
  const [volumeAttenuation, setVolumeAttenuation] = React.useState(0);
  // Null until the operator moves it, so the launch flag is respected
  // until somebody overrides it deliberately.
  const [chosenDepthSamples, setChosenDepthSamples] = React.useState<number | null>(null);
  const [displayScales, setDisplayScales] = React.useState<DisplayScales>({ x: 1, y: 1, z: 1 });
  const depthSamples = chosenDepthSamples ?? config?.depthSamples ?? 256;

  // Applied straight to the engine rather than through the scene, because it
  // is a property of how the picture is viewed and not of any layer in it.
  React.useEffect(() => {
    if (viewer) stretchTheDisplay(viewer, displayScales);
  }, [viewer, displayScales]);
  // Opening or closing images talks to Python, so the button is held while that
  // happens and anything that went wrong is shown rather than swallowed.
  const [storeBusy, setStoreBusy] = React.useState(false);
  // The in-page load window's folder listing; null while it is closed.
  const [loadListing, setLoadListing] = React.useState<Listing | null>(null);
  // Which channel the block of controls is acting on. Held by name rather than by
  // position in the list, because the list is rebuilt whenever something is opened
  // or closed: a position would still be a valid number afterwards and would
  // quietly refer to a different channel, so the sliders would go on working while
  // adjusting something the operator was not looking at.
  const [selectedKey, setSelectedKey] = React.useState<string | null>(null);
  const [barOpen, setBarOpen] = React.useState(true);
  // Which edge the bar of controls sits on, decided when the viewer is started.
  const onLeft = config?.panelSide === "left";
  // The acquisition the view should be pointed at, once its pieces are on
  // screen. Opening used to leave the camera where it was, which on a machine
  // holding one picture at the stage's coordinates and another at zero means
  // opening the second and seeing black (2026-08-21).
  const [lookAt, setLookAt] = React.useState<string | null>(null);
  const groupsBefore = React.useRef<string[] | null>(null);
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
    if (!engine || !lookAt || !config) return undefined;
    const named = config.layers
      .filter((spec, i) => (spec.group || "") === lookAt && included.has(i))
      .map((spec) => engineName(spec));
    if (!named.length) return undefined;
    // Tried again while the answer is "not yet": the bounds this reads are
    // the sources' own, and a source that has not arrived has none to give.
    const soon = window.setInterval(() => {
      if (lookAtWhatOpened(engine, named)) setLookAt(null);
    }, 900);
    return () => window.clearInterval(soon);
    // Deliberately not depending on the scene: this effect is declared above
    // it, and naming it here reaches a constant before it exists -- which
    // took the whole page down with "Cannot access 'scene' before
    // initialization" (2026-08-21).
  }, [engine, lookAt, config, included]);

  // Everything the engine should be showing, in the order it should be drawn.
  //
  // This is worked out here, on its own, so that it is recomputed only when
  // something about the picture has genuinely changed. Note what it does *not*
  // depend on: the list of drawn targets. Those live in the engine once their
  // layer exists, and the list beside the image is a reflection of them — so
  // drawing one must not send the whole scene back through here.
  const scene = React.useMemo(() => {
    if (!config || layerState.length !== config.layers.length) return null;
    const indices = [...included];
    const layers: SceneLayer[] = layersFor({ ...config, layers: indices.map(i => config.layers[i]) }, mode,
                                           indices.map(i => layerState[i]), groupState, groupOrder, volumeMode,
                                           { gain: volumeGain, attenuation: volumeAttenuation,
                                             depthSamples });
    if (targets.layer) layers.push(targets.layer);
    return layers;
  }, [
    config,
    mode,
    layerState,
    included,
    groupState,
    groupOrder,
    volumeMode,
    volumeGain,
    volumeAttenuation,
    depthSamples,
    targets.layer,
  ]);

  // Let the engine settle on a starting magnification once the images have said
  // how big they are. Declared ahead of the effect that adds the layers so the
  // waiting is in place before there is anything to wait for; see drawing/framing.ts for
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
    if (!engine) return undefined;
    setFramed(false);
    return chooseScaleWhenTheImagesAreMeasured(engine, () => {
      // An engine built afresh after a close opens where the view was before it.
      const camera = cameraOnReopen.current;
      if (camera) {
        restoreCamera(engine.viewer, camera);
        cameraOnReopen.current = null;
      }
      setFramed(true);
    });
  }, [engine]);

  React.useEffect(() => {
    if (!engine || !viewer || !config || !scene) return undefined;
    // Where the operator is looking, remembered by axis name rather than by
    // position in the list. Adding an acquisition can introduce an axis the view
    // did not have before, which shifts every axis after it along one place; going
    // by name means the view still comes back to the same plane rather than to
    // whatever now happens to sit in that slot.
    const space = viewer.navigationState.position.coordinateSpace.value;
    const looking: Record<string, number> | null = space?.valid
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

    // What the browser tests look in on, and handy for inspection.
    Object.assign(window, {
      zmartViewer: viewer,
      zmartConfig: config,
      zmartMode: mode,
      zmartLayerState: layerState,
      // The descriptions the panel hands the engine, exactly as they are handed
      // over. This exists for one test and is worth the line: the primitives' `sync.ts`
      // carries every field of a description onto the live layer by hand, one
      // line each, so a field added to `drawing/layers.ts` and forgotten there is
      // dropped in silence -- three controls were found dead that way in a single
      // afternoon. A test can only guard against the *next* one if it can ask
      // what fields are in a description rather than being told a list somebody
      // has to remember to update. See
      // tests/test_no_setting_is_dropped_on_the_way_to_the_engine.py.
      zmartScene: scene,
      // How many stores are still queued to be handed to the engine. Asked as a
      // question rather than left as a number, because the answer changes while a
      // large folder is loading and nothing re-runs this to keep a number up to
      // date. Zero means every position the panel knows about has reached the
      // engine.
      zmartSourcesWaiting: () => engine.sourcesStillWaiting(),
      // How many times the viewer has asked the engine to let go of decoded
      // image, and how many sources it asked the last time. The browser tests
      // read this to tell an announcement that did nothing from one that did
      // something that did not help.
      zmartLetGo: engine.lettingGo,
      zmartSourceRefreshing: engine.sourceRefreshing,
    });

    syncView(engine, {
      transparentBackground: config.transparentBackground === true,
      layout: mode === "volume" ? VOLUME_LAYOUT : SLICE_LAYOUT,
      // The engine's own furniture -- the yellow data-bounds box and the axis
      // lines -- is off unless asked for. We are supplying the interface.
      chrome: config.chrome ?? false,
    });
    // An announcement means something on disk has changed, and it may be something
    // no description of the scene would show -- a frame added to a store already
    // open. So the descriptions are read again on the pass that follows one.
    const reread = takeRereadWanted();
    const reshaped = engine.syncLayers(scene, { reread });
    const refreshed = engine.sourceRefreshing.sources.length;
    // What the browser tests count: how much of the scene changed shape.
    Object.assign(window, { zmartLayersReshaped: reshaped });

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
    return whenTheSourcesHaveSettled(engine, () => true, () => {
      const now = viewer.navigationState.position.coordinateSpace.value;
      if (now?.ids?.join(",") !== axesAtArming) lookAgain();
    });
  }, [engine, viewer, scene, config, mode, layerState]);

  // The furthest any open acquisition has got in time. Several may be running at
  // once and one can be a frame ahead of another, so the slider follows the
  // longest -- a frame that exists somewhere is worth being able to reach.
  const framesAvailable = React.useMemo(() => {
    const counts = (config?.layers || [])
      .map((spec) => spec.frames)
      .filter((n): n is number => typeof n === "number" && n > 0);
    return counts.length ? Math.max(...counts) : null;
  }, [config]);

  // The moments live runs have written, where they have gaps; see committedTimeRanges.
  const timeRanges = React.useMemo(() => committedTimeRanges(config), [config]);

  // Where the chosen channel now sits in the list. Falling back to the first row
  // means closing the channel that was being adjusted leaves the controls on
  // something real rather than on nothing.
  const selected = React.useMemo(() => {
    const rows = config?.layers || [];
    const at = rows.findIndex((spec) => layerKey(spec) === selectedKey);
    return at >= 0 && included.has(at) ? at : ([...included][0] ?? 0);
  }, [config, selectedKey, included]);

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
  const aboutTheChannel = (spec: LayerSpec) =>
    `${(spec.sources || [spec.source])[0]}#${spec.channelIndex ?? ""}`;
  const touchedWindows = React.useRef(new Set<string>());
  const askedForAWindow = React.useRef(new Set<string>());
  React.useEffect(() => {
    (config?.layers || []).forEach((spec, i) => {
      if (spec.kind === "segmentation" || spec.window || spec.histogram) return;
      if (layerState[i]?.window) return;
      const about = aboutTheChannel(spec);
      if (touchedWindows.current.has(about)) return;
      if (askedForAWindow.current.has(about)) return;
      askedForAWindow.current.add(about);
      (async () => {
        const answer = await measureChannel(spec, [[0, 0], [1, 1]]);
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
        <NeuroglancerView onEngine={setEngine} generation={engineGeneration} veiled={!framed} />
        <div style={styles.topBar}>
          {views.map(({ id, label, keys }) => (
            <label key={id} style={{ display: "flex", alignItems: "center", gap: 6 }}>
              {views.length > 1 ? label : "View"}
              <select aria-label={`${label} view`} value={chosenViews[id]} disabled={mode !== "flat"}
                style={topBarStyles.button}
                onChange={event => setRequestedViews(previous => ({ ...previous, [id]: event.target.value }))}>
                {keys.map(key => <option key={key} value={key}>{VIEW_NAMES[key]}</option>)}
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
            slider, and a single plane gets no Z slider.

            The depth slider stands OVER the right-hand edge of the drawing
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
            ranges={timeRanges}
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
                if ("folders" in listing) setLoadListing(listing);
                else setStoreNotice(listing.error);
                setStoreBusy(false);
              }}
              onCloseGroup={async (group) => {
                setStoreBusy(true);
                setStoreNotice(null);
                const result = await closeGroup(group);
                if ("error" in result) {
                  setStoreNotice(result.error);
                  setStoreBusy(false);
                  return;
                }
                if (result.config) applyConfig(result.config);
                // Where the view was, so the engine built afresh below opens
                // there rather than on an overview of what is left.
                cameraOnReopen.current = viewer && result.config?.layers.length
                  ? captureCamera(viewer) : null;
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
                setEngine(null);
                setEngineGeneration((count) => count + 1);
                setStoreBusy(false);
              }}
              onChange={setLayer}
              onWindow={(i, window) => {
                // Marked touched before anything else: the automatic
                // windowing above must never answer over an operator's own
                // setting, however the timing falls.
                const spec = config.layers[i];
                if (spec) touchedWindows.current.add(aboutTheChannel(spec));
                setLayer(i, { window });
              }}
              onMeasureHere={async (i) => {
                // What Auto reads: the pixels of the part on screen, asked of
                // the server, which has the store. The panel has the picture
                // drawn but not the numbers behind it -- and what is on the
                // canvas has already been windowed and coloured. The window
                // belongs to the picture, so it goes to the layer; the
                // histogram belongs to the panel that drew it, so it goes back
                // as the answer. An empty view moves nothing: a press that
                // measured no imaged pixels has nothing to say.
                const spec = config.layers[i];
                if (!spec) return null;
                const box = viewer && whatIsOnScreen(viewer);
                if (!box) return null;
                const answer = await measureChannel(spec, box);
                if (!answer || answer.empty || !answer.window) return null;
                setLayer(i, { window: answer.window });
                return answer;
              }}
              displayScales={displayScales}
            >
              <VolumeMode
                volumeMode={volumeMode}
                onVolumeMode={setVolumeMode}
                gain={volumeGain}
                onGain={setVolumeGain}
                attenuation={volumeAttenuation}
                onAttenuation={setVolumeAttenuation}
                depthSamples={depthSamples}
                onDepthSamples={setChosenDepthSamples}
                displayScales={displayScales}
                onDisplayScales={setDisplayScales}
              />
            </LayerPanel>
          )}
          {config?.canSelect && <TargetsPanel {...targets.panel} />}
        </aside>
      )}
      {loadListing && (
        <LoadWindow
          listing={loadListing}
          onNavigate={async (path) => {
            const listing = await listFolders(path);
            /* Closed while the walk was answered: it stays closed. */
            setLoadListing((current) => {
              if (!current) return current;
              return "folders" in listing ? listing : { ...current, error: listing.error };
            });
          }}
          onOpened={(opened) => {
            applyConfig(opened);
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
  // Along the bottom, the width of the stage.
  timeControl: {
    position: "absolute",
    left: 14,
    right: 14,
    bottom: 14,
    zIndex: 10,
    display: "grid",
    justifyItems: "stretch",
  },
} satisfies Record<string, CSSProperties>;
