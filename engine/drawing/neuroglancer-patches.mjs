/**
 * The edits ZMART makes to the pinned neuroglancer, as tables both pages apply.
 *
 * Two pages draw with the same neuroglancer 2.41.2: the viewer's own window and
 * the operator page of ZMART-interface. Both need the same edits, and each
 * edit is a short table entry -- which file, the text to find, what to put in
 * its place, and a mark that says it is already done. The tables live here, in
 * the installed package, so that both page builds read the same ones; before,
 * the interface kept the transparency edits as a hand-aligned copy, and a copy
 * kept in line by hand drifts.
 *
 * - `transparencyPatches` keep an image's own transparency where the host asked
 *   for a transparent background, so a picture can sit over a page's ground
 *   and show it through only where nothing was imaged.
 * - `refreshPatches` make a refresh replace pixels instead of removing them, so
 *   a live run does not flash on every commit. The viewer's window applies
 *   them; a page with its own way of keeping the picture on screen may not.
 * - The growth edits, which let a picture grow while on screen, are in
 *   `neuroglancer-growth.mjs` beside this file.
 *
 * Why each edit is needed is told at length in the viewer's
 * `gui/source/scripts/patch_neuroglancer.mjs`, which applies them all.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

// An older generation of this patch added parallel refresh machinery beside
// the stock function instead of correcting it: a named-chunk RPC, a per-key
// pump, a probe, and an override that took the whole-source refresh away from
// `invalidateSourceCache` altogether. A tree still carrying that would end up
// with both, and the override would win, so this refuses rather than pretends.
const SUPERSEDED = "zmartPumpRefreshesWithoutDeadline";

/** The edits that keep an image's transparency, for a host that asks for it. */
export function transparencyPatches(lib) {
  return [
  // Keep an opaque image opaque under translucent annotations and scale bars.
  ...[
    { indent: "      ", drawing: "annotations" },
    { indent: "        ", drawing: "scale bars" },
  ].map(({ indent, drawing }) => {
    const anchor = `${indent}gl.blendFunc(\n${indent}  WebGL2RenderingContext.SRC_ALPHA,\n${indent}  WebGL2RenderingContext.ONE_MINUS_SRC_ALPHA\n${indent});`;
    const marker = `${indent}// Preserve image alpha under ${drawing}.`;
    return {
      file: join(lib, "sliceview", "panel.js"), marker, anchor,
      replacement: `${anchor}\n${marker}\n${indent}if (this.context.transparentBackground) gl.blendFuncSeparate(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA, gl.ONE, gl.ONE_MINUS_SRC_ALPHA);`,
    };
  }),
  {
    file: join(lib, "display_context.js"),
    marker: "if (!this.transparentBackground)",
    anchor: `    this.gl.clearColor(1, 1, 1, 1);
    this.gl.colorMask(false, false, false, true);
    gl.clear(gl.COLOR_BUFFER_BIT);
    this.gl.colorMask(true, true, true, true);`,
    replacement: `    if (!this.transparentBackground) {
      this.gl.clearColor(1, 1, 1, 1);
      this.gl.colorMask(false, false, false, true);
      gl.clear(gl.COLOR_BUFFER_BIT);
      this.gl.colorMask(true, true, true, true);
    }`,
  },
  {
    file: join(lib, "sliceview", "panel.js"),
    marker: "if (this.context.transparentBackground) backgroundColor.fill(0)",
    anchor: `    backgroundColor[3] = 1;`,
    replacement: `    backgroundColor[3] = 1;
    if (this.context.transparentBackground) backgroundColor.fill(0);`,
  },
  {
    file: join(lib, "sliceview", "frontend.js"),
    marker: "sampledColor.a = float(sampledColor.a > 0.0)",
    anchor: `  sampledColor = uBackgroundColor;
}
emit(sampledColor * uColorFactor, 0u);`,
    replacement: `  sampledColor = uBackgroundColor;
}
// The embedding surface uses binary coverage, independently of channel weights.
if (uBackgroundColor.a == 0.0) sampledColor.a = float(sampledColor.a > 0.0);
emit(sampledColor * uColorFactor, 0u);`,
  },
  ];
}

/**
 * The edits that make a refresh replace pixels rather than remove them.
 *
 * The worker's edit is made in its module and, where it exists, in the worker
 * bundle compiled from it (`also`), because the bundle is compiled once and
 * recompiled from itself after that.
 */
export function refreshPatches(lib) {
  const workerBundle = join(lib, "chunk_worker.bundle.js");
  return [
  {
    file: join(lib, "chunk_manager", "backend.js"),
    also: workerBundle,
    // This edit REMOVES a line, so there is nothing new to look for -- and a
    // comment could not do the job anyway: esbuild strips comments when it
    // flattens the worker bundle, so a comment marker is present in the module
    // and absent in the bundle built from it. The line's absence is the mark.
    gone: `this.rpc.invoke("Chunk.update", { source: source.rpcId });`,
    // The line is unique in both the module and the compiled worker bundle,
    // so it is its own anchor. Anchoring on the whole function instead made
    // the patch look far larger than it is, and would drift the day anything
    // unrelated inside that function changed.
    anchor: `    this.rpc.invoke("Chunk.update", { source: source.rpcId });
`,
    replacement: `    // A refresh replaces pixels; it must not remove them.
    //
    // Removed here: a key-less Chunk.update naming only the source, which
    // told the page to drop its whole copy. The page dropped it at once and
    // the fresh bytes arrived milliseconds later, so between the two there
    // was nothing to draw and the picture flashed -- on a static dataset
    // never, on a live acquisition once per commit. Each chunk's own update
    // still arrives as it downloads, which is what makes the page's copy
    // fresh; the whole-copy drop only ever made it briefly absent.
`,
  },
  {
    file: join(lib, "chunk_manager", "frontend.js"),
    marker: "Object.assign(chunk, source.getChunk(update))",
    anchor: `        if (update.new) {
          chunk = source.getChunk(update);
          source.addChunk(key, chunk);
        } else {`,
    replacement: `        if (update.new) {
          // Fresh bytes for a chunk this page already holds are written INTO
          // the held object rather than replacing it, so the render layer's
          // reference stays valid and the ordinary state transition uploads
          // the new texture. Replacing the object instead passed the short
          // no-blink gate and still left a 20-per-second commit storm drawing
          // 72% of what a reload drew: the layers went on pointing at objects
          // the source had already forgotten.
          chunk = source.chunks.get(key);
          if (chunk !== void 0) {
            if (chunk.state === ChunkState.GPU_MEMORY) {
              chunk.freeGPUMemory(this.gl);
            }
            Object.assign(chunk, source.getChunk(update));
          } else {
            chunk = source.getChunk(update);
            source.addChunk(key, chunk);
          }
        } else {`,
  },
  ];
}

/**
 * Apply edits to the neuroglancer in `lib`'s package, each once.
 *
 * An edit already in place is left alone. An edit whose text can no longer be
 * found is not guessed at: neuroglancer has changed, and a build that went on
 * would draw wrongly without saying so.
 *
 * @param patches entries from the tables above.
 * @param modulesOnly leave the compiled worker bundle alone; on a fresh install
 *   it does not exist yet, and the first compile carries the module edits in.
 * @param say where to report each file, `console.log` by default.
 * @returns what went wrong, one sentence per file; empty when all went well.
 */
export function applyPatches(patches, { modulesOnly = false, say = console.log } = {}) {
  const problems = [];
  for (const patch of patches) {
    const targets = modulesOnly ? [patch.file] : [patch.file, patch.also];
    for (const file of targets.filter(Boolean)) {
      const held = readFileSync(file, "utf8");
      const done = patch.gone ? !held.includes(patch.gone) : held.includes(patch.marker);
      if (done) {
        say(`already patched: ${file}`);
        continue;
      }
      if (held.includes(SUPERSEDED)) {
        problems.push(`${file} still carries the superseded patch generation. Reinstall the `
          + "package so this one applies to stock sources: rm -rf node_modules/neuroglancer && npm install");
        continue;
      }
      if (!held.includes(patch.anchor)) {
        problems.push(`the anchor no longer matches in ${file} -- the pinned neuroglancer `
          + "version has changed. Re-verify the patch against the new source before building.");
        continue;
      }
      writeFileSync(file, held.replace(patch.anchor, patch.replacement));
      say(`patched: ${file}`);
    }
  }
  return problems;
}
