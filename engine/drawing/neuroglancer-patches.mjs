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
 * Both tables are upstream pull requests, applied to the compiled package
 * exactly as they compile:
 *
 * - `transparencyPatches` is google/neuroglancer#1128: the viewer option
 *   `transparentBackground` leaves cross-section panels transparent where they
 *   show no data, and translucent content keeps its alpha. The host sets it
 *   through the viewer option, not on the display.
 * - `refreshPatches` is google/neuroglancer#1126: a refreshed source keeps
 *   displaying its chunks until each is replaced by its re-download, or
 *   expired if it is not re-downloaded, so a live run does not flash on every
 *   commit. The viewer's window applies them; a page with its own way of
 *   keeping the picture on screen may not. The last of them is ZMART's own,
 *   not yet sent upstream: a download a later refresh cancelled never writes
 *   into its chunk.
 * - The growth edits, which let a picture grow while on screen, are in
 *   `neuroglancer-growth.mjs` beside this file.
 *
 * How they are applied is told in the viewer's
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

/** google/neuroglancer#1128: the `transparentBackground` viewer option. */
export function transparencyPatches(lib) {
  const sliceViewPanel = join(lib, "sliceview", "panel.js");
  return [
  {
    file: join(lib, "display_context.js"),
    marker: "  transparentBackground = false;",
    anchor: `  force3DHistogramForAutoRange = false;
`,
    replacement: `  force3DHistogramForAutoRange = false;
  /**
   * If \`true\`, cross-section panels leave the canvas transparent where they show no data and keep
   * the alpha of translucent content, so that an embedding page shows through.  The 3D panel stays
   * opaque.  Intended for embedding Neuroglancer over other content.  Set through the viewer's
   * \`transparentBackground\` option.
   */
  transparentBackground = false;
`,
  },
  {
    file: join(lib, "display_context.js"),
    marker: "if (!this.transparentBackground) {",
    anchor: `    gl.disable(gl.SCISSOR_TEST);
    this.gl.clearColor(1, 1, 1, 1);
    this.gl.colorMask(false, false, false, true);
    gl.clear(gl.COLOR_BUFFER_BIT);
    this.gl.colorMask(true, true, true, true);
`,
    replacement: `    if (!this.transparentBackground) {
      gl.disable(gl.SCISSOR_TEST);
      this.gl.clearColor(1, 1, 1, 1);
      this.gl.colorMask(false, false, false, true);
      gl.clear(gl.COLOR_BUFFER_BIT);
      this.gl.colorMask(true, true, true, true);
    }
`,
  },
  {
    file: join(lib, "perspective_view", "panel.js"),
    marker: "if (this.context.transparentBackground) {",
    anchor: `    return true;
  }
  drawSliceViews(renderContext) {`,
    replacement: `    if (this.context.transparentBackground) {
      gl.colorMask(false, false, false, true);
      gl.clearColor(0, 0, 0, 1);
      gl.clear(WebGL2RenderingContext.COLOR_BUFFER_BIT);
      gl.colorMask(true, true, true, true);
    }
    return true;
  }
  drawSliceViews(renderContext) {`,
  },
  {
    file: sliceViewPanel,
    marker: "function sliceViewPanelEmitPremultipliedColor(builder) {",
    anchor: `const tempVec3 = vec3.create();
`,
    replacement: `function sliceViewPanelEmitPremultipliedColor(builder) {
  builder.addOutputBuffer("vec4", "out_fragColor", null);
  builder.addFragmentCode(\`
void emit(vec4 color, highp uint pickId) {
  out_fragColor = vec4(color.rgb * color.a, color.a);
}
\`);
}
const tempVec3 = vec3.create();
`,
  },
  {
    file: sliceViewPanel,
    marker: "this.premultipliedSliceViewRenderHelper = ",
    anchor: `    viewer.wireFrame.changed.add(`,
    replacement: `    this.premultipliedSliceViewRenderHelper = this.registerDisposer(
      SliceViewRenderHelper.get(
        this.gl,
        sliceViewPanelEmitPremultipliedColor,
        this.viewer,
        false
      )
    );
    viewer.wireFrame.changed.add(`,
  },
  {
    file: sliceViewPanel,
    marker: "\n  premultipliedSliceViewRenderHelper;\n",
    anchor: `  sliceViewRenderHelper;
`,
    replacement: `  sliceViewRenderHelper;
  premultipliedSliceViewRenderHelper;
`,
  },
  {
    file: sliceViewPanel,
    marker: "const sliceViewRenderHelper = this.context.transparentBackground",
    anchor: `    backgroundColor[3] = 1;
    this.offscreenFramebuffer.bindSingle(0 /* COLOR */);
    this.sliceViewRenderHelper.draw(`,
    replacement: `    backgroundColor[3] = this.context.transparentBackground ? 0 : 1;
    this.offscreenFramebuffer.bindSingle(0 /* COLOR */);
    const sliceViewRenderHelper = this.context.transparentBackground ? this.premultipliedSliceViewRenderHelper : this.sliceViewRenderHelper;
    sliceViewRenderHelper.draw(`,
  },
  // After the blend for annotations, and the one for scale bars.
  ...["      ", "        "].map((indent) => {
    const anchor = `${indent}gl.blendFunc(\n${indent}  WebGL2RenderingContext.SRC_ALPHA,\n${indent}  WebGL2RenderingContext.ONE_MINUS_SRC_ALPHA\n${indent});\n`;
    const replacement = `${anchor}${indent}this.keepAlphaOfTranslucentContent();\n`;
    return { file: sliceViewPanel, marker: replacement, anchor, replacement };
  }),
  {
    file: sliceViewPanel,
    marker: "  keepAlphaOfTranslucentContent() {",
    anchor: `  ensureBoundsUpdated() {`,
    replacement: `  // With a transparent background, blends alpha with (ONE, ONE_MINUS_SRC_ALPHA), so that
  // translucent content keeps its alpha.  Blending alpha with SRC_ALPHA would square it.
  keepAlphaOfTranslucentContent() {
    if (!this.context.transparentBackground) return;
    this.gl.blendFuncSeparate(
      WebGL2RenderingContext.SRC_ALPHA,
      WebGL2RenderingContext.ONE_MINUS_SRC_ALPHA,
      WebGL2RenderingContext.ONE,
      WebGL2RenderingContext.ONE_MINUS_SRC_ALPHA
    );
  }
  ensureBoundsUpdated() {`,
  },
  {
    file: join(lib, "viewer.js"),
    marker: `new TrackableBoolean(key !== "transparentBackground")`,
    anchor: `  "showPanelBorders"
];
export function makeViewerUIConfiguration() {
  return Object.fromEntries(
    VIEWER_UI_CONFIG_OPTIONS.map((key) => [key, new TrackableBoolean(true)])
  );`,
    replacement: `  "showPanelBorders",
  "transparentBackground"
];
export function makeViewerUIConfiguration() {
  return Object.fromEntries(
    VIEWER_UI_CONFIG_OPTIONS.map((key) => [
      key,
      new TrackableBoolean(key !== "transparentBackground")
    ])
  );`,
  },
  {
    file: join(lib, "viewer.js"),
    marker: "const updateTransparentBackground = ",
    anchor: `    this.updateShowBorders();
    this.registerActionListeners();
`,
    replacement: `    this.updateShowBorders();
    const { transparentBackground } = this.uiConfiguration;
    const updateTransparentBackground = () => {
      display.transparentBackground = transparentBackground.value;
      display.scheduleRedraw();
    };
    updateTransparentBackground();
    this.registerDisposer(
      transparentBackground.changed.add(updateTransparentBackground)
    );
    this.registerActionListeners();
`,
  },
  ];
}

/**
 * google/neuroglancer#1126: a refreshed source keeps displaying its chunks.
 *
 * The worker's edits are made in its module and, where it exists, in the
 * worker bundle compiled from it (`also`). Their marks are code, not comments,
 * because esbuild strips comments when it compiles the bundle.
 */
export function refreshPatches(lib) {
  const backend = join(lib, "chunk_manager", "backend.js");
  const workerBundle = join(lib, "chunk_worker.bundle.js");
  const frontend = join(lib, "chunk_manager", "frontend.js");
  const decoders = join(lib, "sliceview", "backend_chunk_decoders", "postprocess.js");
  return [
  // A cancelled download stops before it writes. Two refreshes in quick
  // succession cancel the first one's re-download and start another; but the
  // decoding is handed to a helper worker, and a task already given to a free
  // worker is not taken back on cancelling. Left alone it finished anyway and
  // wrote its older pixels into the chunk, and when that came after the
  // second download's write, the older picture was what reached the screen
  // and stayed there (tests/test_a_cancelled_download_never_lands.py).
  {
    file: decoders, also: workerBundle,
    marker: "signal?.throwIfAborted();\n    chunk.data = data;",
    anchor: `  } else {
    chunk.data = data;
  }
}`,
    replacement: `  } else {
    signal?.throwIfAborted();
    chunk.data = data;
  }
}`,
  },
  {
    file: backend, also: workerBundle,
    marker: "\n  staleFrontendCopy = false;\n",
    anchor: `  downloadAbortController = void 0;
`,
    replacement: `  downloadAbortController = void 0;
  /**
   * Indicates that the frontend still holds a copy of this chunk from before the most recent
   * \`invalidateSourceCache\` call.  The frontend keeps displaying that copy until it is either
   * replaced by the re-downloaded chunk or expired, if the chunk is not re-downloaded.
   */
  staleFrontendCopy = false;
`,
  },
  {
    file: backend, also: workerBundle,
    marker: "this.staleFrontendCopy = false;",
    anchor: `    this.requestedState = ChunkState.NEW;
    this.newRequestedState = ChunkState.NEW;
  }
`,
    replacement: `    this.requestedState = ChunkState.NEW;
    this.newRequestedState = ChunkState.NEW;
    this.staleFrontendCopy = false;
  }
`,
  },
  {
    file: backend, also: workerBundle,
    marker: "this.expireStaleFrontendCopy(chunk);\n      const { source } = chunk;",
    anchor: `    if (chunk.state === ChunkState.QUEUED && chunk.priorityTier === ChunkPriorityTier.RECENT) {
`,
    replacement: `    if (chunk.state === ChunkState.QUEUED && chunk.priorityTier === ChunkPriorityTier.RECENT) {
      this.expireStaleFrontendCopy(chunk);
`,
  },
  {
    file: backend, also: workerBundle,
    marker: "  expireStaleFrontendCopy(chunk) {",
    anchor: `    chunk.state = newState;
    this.adjustCapacitiesForChunk(chunk, true);
    this.addChunkToQueues_(chunk);
    this.scheduleUpdate();
  }
`,
    replacement: `    chunk.state = newState;
    if (chunk.staleFrontendCopy) {
      switch (newState) {
        case ChunkState.SYSTEM_MEMORY:
        case ChunkState.GPU_MEMORY:
          chunk.staleFrontendCopy = false;
          break;
        case ChunkState.QUEUED:
        case ChunkState.DOWNLOADING:
          break;
        case ChunkState.SYSTEM_MEMORY_WORKER:
          if (chunk.requestedState === ChunkState.GPU_MEMORY) break;
          this.expireStaleFrontendCopy(chunk);
          break;
        default:
          this.expireStaleFrontendCopy(chunk);
      }
    }
    this.adjustCapacitiesForChunk(chunk, true);
    this.addChunkToQueues_(chunk);
    this.scheduleUpdate();
  }
  /**
   * Tells the frontend to drop the copy of \`chunk\` retained by \`invalidateSourceCache\`, because
   * the chunk will not be re-downloaded.
   */
  expireStaleFrontendCopy(chunk) {
    if (!chunk.staleFrontendCopy) return;
    chunk.staleFrontendCopy = false;
    this.rpc.invoke("Chunk.update", {
      id: chunk.key,
      state: ChunkState.EXPIRED,
      source: chunk.source.rpcId
    });
  }
`,
  },
  {
    file: backend, also: workerBundle,
    marker: "chunk.staleFrontendCopy = true;",
    anchor: `          chunk.freeSystemMemory();
          break;
      }
      this.updateChunkState(chunk, ChunkState.QUEUED);
    }
    this.rpc.invoke("Chunk.update", { source: source.rpcId });
`,
    replacement: `          chunk.freeSystemMemory();
          break;
        case ChunkState.SYSTEM_MEMORY:
        case ChunkState.GPU_MEMORY:
          chunk.staleFrontendCopy = true;
          break;
      }
      this.updateChunkState(chunk, ChunkState.QUEUED);
    }
`,
  },
  {
    file: frontend,
    // This edit only REMOVES lines, so the lines' absence is its mark.
    gone: "} else if (update.id === void 0) {",
    anchor: `    } else if (update.id === void 0) {
      for (const chunkKey of source.chunks.keys()) {
        source.deleteChunk(chunkKey);
      }
      visibleChunksChanged = true;
`,
    replacement: "",
  },
  {
    file: frontend,
    marker: "if (source.chunks.has(key)) {",
    anchor: `        if (update.new) {
`,
    replacement: `        if (update.new) {
          if (source.chunks.has(key)) {
            source.deleteChunk(key);
          }
`,
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
