import React from "react";
import { askToStop, constructionStatus, openPath, startConstruction, tryNativeChooser } from "./asking-python.js";

/**
 * The load window: pick the thing to look at, and the window says what it is.
 *
 * The path box takes a typed or pasted path (Enter goes there); the rows are
 * the folders at that path, each wearing a tag for what it is — a built view,
 * one image, a plate, or raw positions. Choosing a row says in one line what
 * Open will do with it: a view, image or plate opens directly, and raw
 * positions are shown through a view built over them on the spot (with the
 * two build questions — where to save it, and whether to bake the
 * low-resolution mosaic — laid out beforehand, already answered). Errors
 * from the walk, the build or the open all show inside the window, where the
 * operator is looking.
 */
// What each kind of row IS — the little tag on the row, and the sentence
// beneath the list once it is chosen. The kinds come from the server's
// listing (see _serve_list_folders); a row with no kind is just a place to
// walk into. A "run" covers both shapes raw data takes: a plain folder of
// position stores, and a bare zarr group wrapping them.
const ROW_KINDS = {
  // A view is at heart a LINK file: a small description pointing at the raw
  // data, copying nothing. Baking only adds one hard-copied piece — the
  // low-resolution mosaic (the ⚡ in the list) — and everything at full
  // resolution stays linked either way. The words below lead with that,
  // because "built" made it sound like the data had been duplicated.
  // Two families only, as the operator settled it (2026-08-23): a row is
  // either ZARR — data, whatever shape it takes — or ZMARTVIEW. The
  // sentence still tells the shapes apart, because Open treats them
  // differently; the tag deliberately does not.
  view: { tag: "zmartview",
          said: "a view — links to the raw data, nothing duplicated; it opens directly" },
  image: { tag: "zarr", said: "one image — it opens directly" },
  plate: { tag: "zarr", said: "a plate of wells — it opens directly" },
  run: { tag: "zarr",
         said: "raw positions from the microscope — Open links them together "
           + "into one picture for this session; nothing is copied and "
           + "nothing is kept unless the mosaic is asked for" },
};

function LoadWindow({ listing, onNavigate, onOpened, onConstructed, onCancel,
                     onArriving }) {
  const [busy, setBusy] = React.useState(false);
  const [openError, setOpenError] = React.useState(null);
  // Raw data being constructed into a viewer: which folder, where the
  // viewer's files go, and whether the pieces are prebaked now or made on
  // the fly later. Null while the operator is still walking folders.
  const [constructing, setConstructing] = React.useState(null);
  // The row the operator clicked last: highlighted in the list, and what
  // the Open button below the list acts on. Cleared when the folder or the
  // tab changes, because the list under it has changed.
  const [selected, setSelected] = React.useState(null);
  const polling = React.useRef(null);

  const navigate = (path) => {
    setSelected(null);
    onNavigate(path);
  };

  React.useEffect(() => () => clearInterval(polling.current), []);

  // Opening a store directly -- and a viewer whose raw data has moved
  // answers with a relink ask, which becomes the pane below, prefilled.
  const openStore = async (path) => {
    setBusy(true);
    let result;
    try {
      result = await openPath(path);
    } finally {
      // Whatever happened, the Open button is free again.
      setBusy(false);
    }
    if (result.config) {
      onOpened(result.config);
    } else if (result.relink) {
      setConstructing({
        relink: true,
        name: result.relink.name,
        data: result.relink.was,
        // Where the view stands is the server's word: cutting at "/" here
        // dropped the last letter of every Windows path.
        destination: result.relink.parent,
        bake: result.relink.baked,
      });
    } else {
      setConstructing(null);
      setOpenError(result.error);
    }
  };

  // The kept way to open a run: build the view — with its baked mosaic, at
  // the place the operator chose — and open what was built, as one press.
  // The plain way is not here at all: an untouched Open goes straight to
  // openStore, and the server composes a link file it keeps to itself and
  // forgets when it closes (the operator's rule, 2026-08-23).
  const openRun = async () => {
    const { data, destination, bake } = constructing;
    setConstructing((current) => ({ ...current, running: true, fraction: 0,
                                    error: null }));
    const begun = await startConstruction(data, destination, bake);
    if (begun.error) {
      setConstructing((current) => ({ ...current, running: false, error: begun.error }));
      return;
    }
    polling.current = setInterval(async () => {
      const status = await constructionStatus();
      if (status.state === "running") {
        setConstructing((current) => current && { ...current, fraction: status.fraction || 0 });
        return;
      }
      clearInterval(polling.current);
      if (status.state === "done") {
        await openStore(status.store);
      } else if (status.state === "cancelled") {
        setConstructing((current) => current &&
          { ...current, running: false, fraction: 0 });
      } else {
        setConstructing((current) => current &&
          { ...current, running: false,
            error: status.error || "the view could not be built" });
      }
    }, 350);
  };

  const start = async () => {
    const { data, destination, bake, relink, name } = constructing;
    setConstructing((current) => ({ ...current, running: true, fraction: 0,
                                    error: null, stopped: false }));
    const begun = await startConstruction(data, destination, bake,
                                          relink ? name : undefined);
    if (begun.error) {
      setConstructing((current) => ({ ...current, running: false, error: begun.error }));
      return;
    }
    polling.current = setInterval(async () => {
      const status = await constructionStatus();
      if (status.state === "running") {
        setConstructing((current) => current && { ...current, fraction: status.fraction || 0 });
      } else {
        clearInterval(polling.current);
        if (status.state === "done") {
          setConstructing((current) => current &&
            { ...current, running: false, built: status.store });
        } else if (status.state === "cancelled") {
          setConstructing((current) => current &&
            { ...current, running: false, fraction: 0, stopped: true });
        } else {
          setConstructing((current) => current &&
            { ...current, running: false, error: status.error || "the build failed" });
        }
      }
    }, 350);
  };

  // The pieces the relink pane and the build blocks share, written once.
  const saveField = constructing && (
    <>
      <input
        type="text"
        value={constructing.destination}
        onChange={(event) => setConstructing(
          (current) => ({ ...current, destination: event.target.value }))}
        aria-label="scene folder"
        title="Where the scene's own files are written. The raw data is read and never changed"
        style={{ ...styles.loadPath, marginBottom: 0, flex: 1 }}
      />
      <button
        type="button"
        onClick={async () => {
          const chosen = await tryNativeChooser();
          if (chosen.path) setConstructing(
            (current) => ({ ...current, destination: chosen.path }));
        }}
        aria-label="choose where to save the scene"
        title="Pick the folder with the operating system's own chooser"
        style={styles.loadCancel}
      >
        Choose…
      </button>
    </>
  );
  const buildParts = constructing && (
    <>
      {/* The scene links to the raw data no matter what; that part is
          stated in the info line, not asked. The one question is whether
          the zoomed-out overview -- the low-resolution top of the scene's
          pyramid -- is kept now as a hard copy on disk, or composed from
          the raw data when someone looks. The recommendation is measured,
          not guessed: on the lab workstation, rendering on the card, the
          bake costs 5.7 s at 1,024 positions of 384-pixel test tiles where
          the unbaked first look costs 7.7 s -- the crossover, at roughly
          150 megapixels of survey, a few dozen full camera frames. At
          4,096 tiles it is 19 s of build against a 39 s first look, and
          the unbaked scene pays that again on every cold open. "Well under
          one percent" is the pyramid's own arithmetic: the bake keeps the
          levels holding at most 1% of the full-resolution voxels
          (PINNED_SHARE in composer.py) plus the shrinking tail above them,
          about half a percent typically, 1.33% at the geometric worst. */}
      <div style={styles.constructRow}>
        <label style={styles.constructChoice}>
          <input
            type="checkbox"
            checked={constructing.bake}
            onChange={(event) => setConstructing(
              (current) => ({ ...current, bake: event.target.checked }))}
            disabled={constructing.running || !!constructing.built}
            aria-label="include a hard copy of the low-resolution overview"
            title="The zoomed-out picture is computed once now and kept as files, so the whole survey opens instantly. Left unchecked, it is composed from the raw data the first time it is looked at"
          />
          include a hard copy of the low-resolution overview
        </label>
      </div>
      <div style={styles.constructNote}>
        A scene is assembled by linking the raw data into a virtual
        OME-Zarr, so nothing is copied. We found that also building the
        low-resolution overview as a hard copy, well under one percent of
        the data, dramatically improves the experience: the build is a
        one-time cost, and the positions then load instantly. Without it,
        the overview is computed the first time you look.
      </div>
      <div style={styles.loadActions}>
        {!constructing.built ? (
          <button
            type="button"
            onClick={start}
            disabled={constructing.running}
            aria-label="build the scene"
            title={constructing.bake
              ? "Compute the zoomed-out picture now and keep it: takes time once, opens instantly ever after"
              : "Write only the scene's description; everything is composed as it is looked at"}
            style={styles.loadOpen}
          >
            {constructing.running ? "building…" : "Build"}
          </button>
        ) : null}
        {constructing.running && (
          <button
            type="button"
            onClick={() => askToStop("construct")}
            aria-label="stop the build"
            title="Stop the build at its next step. Nothing half-made is kept; building again starts fresh"
            style={{ ...styles.loadCancel, marginLeft: 8 }}
          >
            Stop
          </button>
        )}
        {constructing.built ? (
          <button
            type="button"
            onClick={() => openStore(constructing.built)}
            aria-label="show the scene"
            title="The scene is built; open it in the image data"
            style={styles.loadOpen}
          >
            Show
          </button>
        ) : null}
      </div>
      {constructing.stopped && !constructing.running && (
        <div style={styles.loadEmptyNote} role="status">
          the build was stopped, and nothing half-made was kept
        </div>
      )}
      {/* The bar stays once the build is done, standing full: a finished
          build should look finished, not vanish. */}
      {(constructing.running || constructing.built) && (
        <div
          style={styles.progressTrack}
          role="progressbar"
          aria-label="construction progress"
          aria-valuenow={constructing.built ? 100
            : Math.round((constructing.fraction || 0) * 100)}
        >
          <div style={{ ...styles.progressFill,
                        width: `${constructing.built ? 100
                          : Math.round((constructing.fraction || 0) * 100)}%` }} />
        </div>
      )}
      {constructing.error && (
        <div style={styles.loadError} role="alert">{constructing.error}</div>
      )}
    </>
  );

  return (
    <div style={styles.loadShade}>
      <div role="dialog" aria-label="load data" style={styles.loadWindow}>
        <div style={styles.loadHead}>
          <span style={styles.loadTitle}>load data</span>
        </div>
        <>
        <div style={{ display: "contents" }}>
        <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
          <input
            key={listing.path}
            type="text"
            defaultValue={listing.path}
            onKeyDown={(event) => {
              if (event.key === "Enter") navigate(event.currentTarget.value);
            }}
            aria-label="folder path"
            title="The folder being looked at. Type or paste a path and press Enter"
            style={{ ...styles.loadPath, marginBottom: 0, flex: 1 }}
          />
          <button
            type="button"
            onClick={async () => {
              const chosen = await tryNativeChooser();
              if (chosen.path) {
                // Land on the parent: the picked folder then sits in the
                // list as an ordinary row, wearing its own Open button.
                // The parent is the server's word, never worked out here:
                // slicing at "/" mangled every Windows path.
                navigate(chosen.parent || chosen.path);
              } else if (chosen.window) {
                setOpenError(
                  "no system folder chooser here — type a path above or walk the folders below");
              }
            }}
            aria-label="choose a folder"
            title="Pick the folder with the operating system's own chooser"
            style={styles.loadCancel}
          >
            Choose folder…
          </button>
        </div>
        <div style={styles.loadList}>
          {/* The way back up, before anything else: without it the window
              could only ever descend, and an operator landed inside the
              demo's folder had no way to reach the data beside it short of
              typing a path (met 2026-08-23). */}
          {listing.parent && (
            <button
              type="button"
              onClick={() => navigate(listing.parent)}
              aria-label="up one folder"
              title="Go up to the folder holding this one"
              style={styles.loadRow}
            >
              <span style={styles.loadRowName}>..</span>
            </button>
          )}
          {/* One click selects a row and highlights it, the way the
              operating system's own choosers behave; a double click steps
              into a folder or opens an image at once. What the chosen tab
              is looking for floats to the top; the plain folders to walk
              into follow. */}
          {[...listing.folders].sort((a, b) => {
            // What the chosen tab can act on floats to the top; the plain
            // folders to walk into follow.
            const wanted = (folder) => (Boolean(folder.kind) ? 0 : 1);
            return wanted(a) - wanted(b) || a.name.localeCompare(b.name);
          }).map((folder) => (
            <button
              key={folder.name}
              type="button"
              onClick={() => {
                setSelected(folder);
                setOpenError(null);
                // Raw positions are always shown through a view, so choosing
                // a run lays out that view's two questions at once — where
                // its files go, and whether the low-resolution mosaic is
                // built now — with plain answers already filled in. Open
                // then does the rest in one press.
                setConstructing(folder.kind === "run" ? {
                  data: `${listing.path}/${folder.name}`,
                  name: folder.name,
                  destination: `${listing.path}/${folder.name}/scenes`,
                  bake: false,
                } : null);
              }}
              onDoubleClick={() =>
                ["view", "image", "plate"].includes(folder.kind)
                  ? openStore(`${listing.path}/${folder.name}`)
                  : navigate(`${listing.path}/${folder.name}`)
              }
              aria-label={folder.name}
              aria-pressed={selected?.name === folder.name}
              style={{ ...styles.loadRow,
                       ...(selected?.name === folder.name
                         ? styles.loadRowChosen : null) }}
              title={ROW_KINDS[folder.kind]
                ? `${ROW_KINDS[folder.kind].said}. Click to select it`
                : "Click to select this folder; double-click to look inside it"}
            >
              <span style={styles.loadRowName}>{folder.name}</span>
              {/* A view wears one tag always, and a second when it keeps
                  its precomputed mosaic — the fast-opening one. Either
                  both, or only zmartview. */}
              {folder.kind === "view" && folder.baked && (
                <span
                  style={styles.loadRowTag}
                  title="keeps its precomputed low-resolution mosaic on disk, so it opens fast"
                >
                  ⚡ precomputed mosaic
                </span>
              )}
              {ROW_KINDS[folder.kind] && (
                <span style={styles.loadRowTag}>{ROW_KINDS[folder.kind].tag}</span>
              )}
            </button>
          ))}
          {!listing.folders.length && (
            <div style={styles.loadEmptyNote}>no folders in here</div>
          )}
        </div>
        </div>
        {/* What the chosen row IS, said under the list in one line — so a
            row that offers nothing is never a silent mystery. */}
        {selected && (
          <div style={styles.loadEmptyNote} role="status">
            {ROW_KINDS[selected.kind]?.said
              ?? "a plain folder — double-click to look inside"}
          </div>
        )}
        {/* A chosen run needs no questions answered: Open shows it through a
            link file the viewer keeps to itself and throws away when it
            closes — nothing lands on the operator's disk uninvited. The one
            choice on offer is the mosaic; only ticking it asks where the
            kept view should live. */}
        {constructing && !constructing.relink && (
          <div style={styles.constructPane}>
            <div style={styles.constructRow}>
              <label style={styles.constructChoice}>
                <input
                  type="checkbox"
                  checked={constructing.bake}
                  onChange={(event) => setConstructing(
                    (current) => ({ ...current, bake: event.target.checked }))}
                  disabled={constructing.running}
                  aria-label="build the low-resolution mosaic and keep the view"
                  title="The zoomed-out mosaic is computed once now and kept as files beside the view's link file, so it opens fast ever after — views wearing ⚡ in the list have it. Left unchecked, nothing is written to your data: the viewer links the positions together for this session and forgets it after"
                />
                build the low-resolution mosaic and keep the view (opens fast ever after)
              </label>
            </div>
            {constructing.bake && (
              <label style={styles.constructRow}>
                <span style={styles.constructLabel}
                      title="The view is a small file of links to the raw data plus the baked mosaic — this is where they live">
                  save the view in
                </span>
                {saveField}
              </label>
            )}
            {constructing.running && (
              <div
                style={styles.progressTrack}
                role="progressbar"
                aria-label="construction progress"
                aria-valuenow={Math.round((constructing.fraction || 0) * 100)}
              >
                <div style={{ ...styles.progressFill,
                              width: `${Math.round((constructing.fraction || 0) * 100)}%` }} />
              </div>
            )}
            {constructing.error && (
              <div style={styles.loadError} role="alert">{constructing.error}</div>
            )}
          </div>
        )}
        {!constructing?.relink && (
          <div style={styles.loadActions}>

            <button
              type="button"
              onClick={async () => {
                // With a row selected, that row is what opens. With nothing
                // selected, the folder being LOOKED AT can itself be the
                // image — the operator typed a store's path, or walked into
                // one — and then the button opens that folder. Without this
                // a store entered directly showed only the scale arrays
                // inside it, none of them openable (2026-08-23).
                const where = selected ? `${listing.path}/${selected.name}` : listing.path;
                // Raw positions are shown through a link file the server
                // composes for itself and forgets when it closes — a plain
                // Open keeps nothing on the operator's disk. Only the ticked
                // mosaic box builds a view that is KEPT, at the place the
                // operator chose. A view, an image or a plate opens directly.
                if (constructing?.bake) {
                  openRun();
                  return;
                }
                openStore(where);
              }}
              disabled={busy
                        || constructing?.running
                        // The folder being STOOD IN counts as much as a
                        // chosen row — the window lands inside the dataset,
                        // and a dead button at the landing spot read as the
                        // whole door being broken (2026-08-23).
                        || !(selected
                          ? selected.kind
                          : ["view", "image", "plate"].includes(listing.kind))}
              aria-label={selected ? `open ${selected.name}`
                : listing.kind ? "open this folder"
                : "open the selection"}
              title={"Open this and show it. A view, an image or a plate opens "
                + "directly; raw positions are shown through a view built "
                + "over them first"}
              style={styles.loadOpen}
            >
              {busy || constructing?.running ? "…" : "Open"}
            </button>
            {constructing?.running && (
              <button
                type="button"
                onClick={() => askToStop("construct")}
                aria-label="stop the build"
                title="Stop building the view at its next step. Nothing half-made is kept"
                style={{ ...styles.loadCancel, marginLeft: 8 }}
              >
                Stop
              </button>
            )}
            <button
              type="button"
              onClick={onCancel}
              aria-label="cancel loading"
              style={{ ...styles.loadCancel, marginLeft: 8 }}
            >
              Cancel
            </button>
          </div>
        )}
        {constructing?.relink && (
          <div style={styles.constructPane}>
            <div style={{ ...styles.constructTitle, display: "flex",
                          justifyContent: "space-between", alignItems: "center" }}>
              <span>point to the raw data for {constructing.name}</span>
              <button
                type="button"
                onClick={onCancel}
                aria-label="cancel loading"
                style={styles.loadCancel}
              >
                Cancel
              </button>
            </div>
            <label style={styles.constructRow}>
              <span style={styles.constructLabel}>raw data</span>
              <input
                type="text"
                value={constructing.data}
                onChange={(event) => setConstructing(
                  (current) => ({ ...current, data: event.target.value }))}
                aria-label="raw data folder"
                title="Where the raw data lives now. The viewer was built from a folder that is no longer there"
                style={{ ...styles.loadPath, marginBottom: 0, flex: 1 }}
              />
            </label>
            <label style={styles.constructRow}>
              <span style={styles.constructLabel}>save scene in</span>
              {saveField}
            </label>
            {buildParts}
          </div>
        )}
        </>
        {(listing.error || openError) && (
          <div style={styles.loadError} role="alert">
            {listing.error || openError}
          </div>
        )}
      </div>
    </div>
  );
}

export default LoadWindow;

const styles = {
  // The load window and the shade behind it. The shade keeps the picture
  // visible but plainly not the thing being interacted with.
  loadShade: {
    position: "fixed",
    inset: 0,
    zIndex: 60,
    background: "var(--shade-bg)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  loadWindow: {
    width: 480,
    maxWidth: "88vw",
    maxHeight: "72vh",
    display: "flex",
    flexDirection: "column",
    background: "var(--card-bg)",
    border: "1px solid var(--panel-border)",
    borderRadius: 8,
    boxShadow: "0 12px 40px var(--shadow-color)",
    padding: "12px 14px",
    font: "13px/1.4 system-ui, -apple-system, 'Segoe UI', sans-serif",
    color: "var(--text-primary)",
  },
  loadHead: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    paddingBottom: 10,
  },
  loadTitle: {
    font: "600 11px/1 system-ui, sans-serif",
    letterSpacing: ".08em",
    textTransform: "uppercase",
    color: "var(--text-faint)",
  },
  loadCancel: {
    padding: "4px 10px",
    border: "1px solid var(--control-border)",
    borderRadius: 4,
    background: "var(--control-bg)",
    color: "var(--text-secondary)",
    font: "600 11px/1 system-ui, sans-serif",
    cursor: "pointer",
  },
  loadPath: {
    boxSizing: "border-box",
    width: "100%",
    background: "var(--input-bg)",
    border: "1px solid var(--subtle-border)",
    borderRadius: 4,
    color: "var(--text-primary)",
    font: "12px/1.4 ui-monospace, monospace",
    padding: "6px 8px",
    marginBottom: 8,
  },
  loadList: {
    flex: 1,
    minHeight: 120,
    overflowY: "auto",
    border: "1px solid var(--subtle-border)",
    borderRadius: 4,
    background: "var(--panel-bg)",
  },
  loadRow: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    width: "100%",
    textAlign: "left",
    background: "none",
    border: "none",
    borderBottom: "1px solid var(--subtle-border)",
    color: "var(--text-primary)",
    font: "12px/1.4 system-ui, sans-serif",
    padding: "6px 10px",
    cursor: "pointer",
  },
  // The name takes the room; the tag sits at the right edge saying what the
  // row IS (view, image, plate, raw), and a view that keeps its baked
  // low-resolution mosaic wears ⚡ beside its name — the fast-opening one.
  loadRowName: { flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis" },
  loadRowTag: {
    flexShrink: 0,
    color: "var(--text-faint)",
    font: "600 10px/1 system-ui, sans-serif",
    textTransform: "uppercase",
    letterSpacing: "0.06em",
    border: "1px solid var(--subtle-border)",
    borderRadius: 4,
    padding: "2px 6px",
  },
  loadOpen: {
    flexShrink: 0,
    padding: "3px 10px",
    border: "1px solid var(--accent)",
    borderRadius: 4,
    background: "var(--accent-selection)",
    color: "var(--accent-selection-text)",
    font: "600 11px/1 system-ui, sans-serif",
    cursor: "pointer",
  },
  loadEmptyNote: {
    padding: "10px 12px",
    color: "var(--text-muted)",
    font: "12px/1.4 system-ui, sans-serif",
  },
  loadRowChosen: {
    background: "var(--accent-selection)",
    color: "var(--accent-selection-text)",
  },
  loadActions: { display: "flex", justifyContent: "flex-end", marginTop: 8 },
  constructPane: {
    marginTop: 10,
    padding: "10px 12px",
    border: "1px solid var(--panel-border)",
    borderRadius: 6,
    background: "var(--panel-bg)",
    display: "flex",
    flexDirection: "column",
    gap: 8,
  },
  constructTitle: {
    font: "600 11px/1 system-ui, sans-serif",
    letterSpacing: ".06em",
    textTransform: "uppercase",
    color: "var(--text-muted)",
  },
  constructRow: { display: "flex", alignItems: "center", gap: 10 },
  constructLabel: {
    font: "600 10px/1 system-ui, sans-serif",
    letterSpacing: ".04em",
    textTransform: "uppercase",
    color: "var(--text-muted)",
    flexShrink: 0,
  },
  constructNote: {
    font: "11px/1.5 system-ui, sans-serif",
    color: "var(--text-muted)",
  },
  constructChoice: {
    display: "flex",
    alignItems: "center",
    gap: 5,
    font: "12px/1.2 system-ui, sans-serif",
    color: "var(--text-primary)",
  },
  progressTrack: {
    height: 8,
    borderRadius: 4,
    background: "var(--input-bg)",
    border: "1px solid var(--subtle-border)",
    overflow: "hidden",
  },
  progressFill: {
    height: "100%",
    background: "var(--accent)",
    transition: "width 200ms linear",
  },
  loadError: {
    marginTop: 8,
    padding: "7px 9px",
    border: "1px solid var(--danger-border)",
    borderRadius: 4,
    background: "var(--danger-bg)",
    color: "var(--danger-text)",
    font: "12px/1.5 system-ui, sans-serif",
  },
};
