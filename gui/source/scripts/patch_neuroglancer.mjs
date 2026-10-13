/**
 * Patches against the pinned Neuroglancer: live refresh and opt-in 2D transparency.
 *
 * Both are upstream pull requests, applied to the compiled package exactly as
 * they compile: google/neuroglancer#1126 (a refreshed source keeps displaying
 * its chunks until they are replaced) and google/neuroglancer#1128 (the viewer
 * option `transparentBackground`).
 *
 * Transparency applies only when the host sets the viewer option
 * `transparentBackground`. The host must also supply geometry-based coverage
 * for sparse mosaics; intensity zero cannot distinguish their gaps from
 * acquired black pixels. The engine serves that coverage beside each image
 * (engine/serving/coverage.py).
 *
 * The edits themselves are tables in engine/drawing/neuroglancer-patches.mjs,
 * which ZMART-interface's page build applies from the installed viewer, so the
 * two pages cannot drift apart. This script applies them to the viewer's own
 * copy of neuroglancer.
 *
 * The refresh edits say: **a refresh should replace pixels, not remove them.**
 * Stock Neuroglancer refreshes a chunk source by re-queueing its chunks and
 * then telling the page to drop every chunk it holds. Between the drop and the
 * refetch there is nothing left to draw, so the picture empties and refills.
 * On a static dataset that never happens; on a live acquisition it happens on
 * every commit, and the operator watches the specimen flash. With the edits
 * the page keeps each chunk it holds until the re-downloaded one replaces it,
 * or until the worker expires it because it will not be downloaded again.
 *
 * The gate that holds it is `tests/test_the_screen_never_goes_black.py`,
 * which measured the fault it cures: one whole frame at 0% lit, about 17 ms,
 * invisible to a screenshot and plain to the eye.
 *
 * **Worker patches target the module AND the compiled bundle, and the build
 * runs this AFTER precompiling.** The precompile step flattens the worker's
 * import stub into a self-contained bundle ON ITS FIRST RUN and thereafter
 * recompiles that flattened bundle — module changes never enter the worker
 * again. That cost a night once: patch generations that ran only in the
 * module files while the worker executed the first-ever compile, and every
 * measurement of them was a measurement of nothing. On a fresh install the
 * postinstall patches the modules, the first precompile carries them into the
 * bundle, and the bundle entries here find their markers already present.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { applyGrowthPatches } from "../../../engine/drawing/neuroglancer-growth.mjs";
import {
  applyPatches, refreshPatches, transparencyPatches,
} from "../../../engine/drawing/neuroglancer-patches.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const lib = join(here, "..", "node_modules", "neuroglancer", "lib");

// On a fresh install the worker bundle is still Neuroglancer's un-flattened
// import stub: the compiled bundle only comes into being when the build's
// precompile step runs. Demanding the bundle's anchors at postinstall time
// therefore failed every clean `npm ci` -- the anchors cannot exist yet.
// So postinstall runs with --modules-only and patches just the module files,
// which the first precompile then carries into the bundle it creates; the
// full pass, bundle checks included, runs during `npm run build` where the
// bundle really exists and a missing anchor really is a version change.
const modulesOnly = process.argv.includes("--modules-only");

applyGrowthPatches(lib);
const problems = applyPatches([...transparencyPatches(lib), ...refreshPatches(lib)], { modulesOnly });
if (!modulesOnly && !readFileSync(join(lib, "chunk_worker.bundle.js"), "utf8").includes('"zarr/extendBounds"')) {
  problems.push("The precompiled Neuroglancer worker lacks bounds refresh. Run npm ci, then npm run build.");
}
for (const problem of problems) console.error(problem);
if (problems.length) process.exit(1);
