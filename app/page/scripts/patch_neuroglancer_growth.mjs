import { join } from "node:path";

// Bounds-only Zarr growth retains the loaded source and its GPU chunks. Layout
// changes still use Neuroglancer's ordinary source replacement. These patches
// run before worker precompilation on a fresh install, like the refresh patches.
export function growthPatches(lib) {
  return [
    {
      file: join(lib, "layer", "layer_data_source.js"),
      marker: "  stableStringify,",
      anchor: "  verifyBoolean,",
      replacement: "  stableStringify,\n  verifyBoolean,",
    },
    {
      file: join(lib, "datasource", "zarr", "frontend.js"),
      marker: "  stableStringify,",
      anchor: "  parseQueryStringParameters,",
      replacement: "  stableStringify,\n  parseQueryStringParameters,",
    },
    {
      file: join(lib, "layer", "layer_data_source.js"),
      marker: "async refreshMetadata(refreshed",
      anchor: "  set spec(spec) {\n    const { layer } = this;",
      replacement: `  async refreshMetadata(refreshed = new Set()) {
    this.metadataRefresh?.abort();
    const held = this.loadState_;
    const owner = this.refCounted_;
    if (!held || held.error) {
      this.metadataRefresh = void 0;
      this.spec = { ...this.spec };
      return;
    }
    const request = this.metadataRefresh = new AbortController();
    const cancel = () => request.abort();
    owner.registerDisposer(cancel);
    const spec = this.spec;
    try {
      const fresh = await this.layer.manager.dataSourceProviderRegistry.get({
        url: spec.url, signal: request.signal,
        globalCoordinateSpace: this.layer.manager.root.coordinateSpace,
        transform: spec.transform, state: spec.state,
        progressListener: this.progressListener,
      });
      if (request.signal.aborted || this.loadState_ !== held || owner.wasDisposed) return;
      const pairs = held.subsources.map(old => [old, fresh.subsources.find(next => next.id === old.subsourceEntry.id)]);
      if (pairs.length !== fresh.subsources.length || pairs.some(([old, next]) => {
        if (!next) return true;
        const volume = old.subsourceEntry.subsource.volume;
        return volume ? !volume.canExtend?.(next.subsource.volume) : old.subsourceEntry.id !== "bounds";
      })) {
        const reusable = new Set();
        for (const [old, next] of pairs) {
          for (const sources of old.subsourceEntry.subsource.volume?.sourceCache?.values() ?? []) {
            for (const { chunkSource } of sources.flat()) {
              const metadata = next?.subsource.volume?.multiscale.scales.find(
                scale => scale.url === chunkSource.parameters.url)?.metadata;
              if (metadata && stableStringify(metadata) === stableStringify(chunkSource.parameters.metadata)) {
                reusable.add(chunkSource);
              }
            }
          }
        }
        this.spec = { ...this.spec };
        // Detach old render consumers before invalidating any reusable cache.
        // Otherwise they request the replacement pixels a second time.
        for (const chunkSource of reusable) {
          if (chunkSource.wasDisposed || refreshed.has(chunkSource)) continue;
          refreshed.add(chunkSource);
          chunkSource.invalidateCache();
        }
        return true;
      }
      const transform = new WatchableCoordinateSpaceTransform(fresh.modelTransform);
      transform.spec = held.transform.spec;
      for (const [old, next] of pairs) {
        const volume = old.subsourceEntry.subsource.volume;
        if (volume) volume.extendBounds(next.subsource.volume, refreshed);
        else old.subsourceEntry = next;
      }
      held.transform.defaultTransform = transform.defaultTransform;
      held.transform.value = transform.value;
      // Extents changed even when the coordinate mapping did not. Recompute
      // clipping bounds without replacing these render layers or their chunks.
      for (const renderLayer of this.layer.renderLayers) {
        if (pairs.some(([old]) => old.subsourceEntry.subsource.volume === renderLayer.multiscaleSource)) {
          renderLayer.transform?.changed.dispatch();
        }
      }
      this.messages.clearMessages();
      this.changed.dispatch();
      return true;
    } catch (error) {
      if (request.signal.aborted || this.loadState_ !== held || owner.wasDisposed) return;
      this.messages.addMessage({ severity: MessageSeverity.error, message: formatErrorMessage(error) });
      this.changed.dispatch();
      return false;
    } finally {
      owner.unregisterDisposer(cancel);
      if (this.metadataRefresh === request) this.metadataRefresh = void 0;
    }
  }
  set spec(spec) {
    this.metadataRefresh?.abort();
    const { layer } = this;`,
    },
    {
      file: join(lib, "datasource", "zarr", "frontend.js"),
      marker: "extendBounds(other, refreshed)",
      anchor: "  volumeType;\n  get dataType() {",
      replacement: `  volumeType;
  sourceCache = new Map();
  canExtend(other) {
    if (!(other instanceof MultiscaleVolumeChunkSource)) return false;
    const layout = ({ coordinateSpace, dataType, scales }) => ({
      names: coordinateSpace.names, units: coordinateSpace.units,
      scales: coordinateSpace.scales, dataType,
      levels: scales.map(({ metadata: { shape, ...metadata }, ...scale }) => ({ ...scale, metadata })),
    });
    return stableStringify(layout(this.multiscale)) === stableStringify(layout(other.multiscale)) &&
      this.multiscale.scales.every((scale, level) => scale.metadata.shape.every(
        (size, dimension) => size <= other.multiscale.scales[level].metadata.shape[dimension]));
  }
  extendBounds(other, refreshed) {
    const next = other.multiscale;
    for (const sources of this.sourceCache.values()) {
      for (const { chunkSource: source } of sources.flat()) {
        const metadata = next.scales.find(scale => scale.url === source.parameters.url).metadata;
        const { spec } = source;
        const permutation = metadata.codecs.layoutInfo[0].physicalToLogicalDimension;
        const upper = Float32Array.from(spec.upperVoxelBound, (_, i) => metadata.shape[permutation[spec.rank - 1 - i]]);
        spec.upperVoxelBound.set(upper);
        for (let i = 0; i < spec.rank; i++) spec.upperChunkBound[i] = Math.ceil(upper[i] / spec.chunkDataSize[i]);
        source.parameters.metadata = metadata;
        const invalidate = !refreshed.has(source);
        refreshed.add(source);
        source.rpc.invoke("zarr/extendBounds", { id: source.rpcId, upper, shape: metadata.shape, invalidate });
      }
    }
    this.multiscale = next;
  }
  get dataType() {`,
    },
    {
      file: join(lib, "datasource", "zarr", "frontend.js"),
      marker: "const cached = this.sourceCache.get(key)",
      anchor: "  getSources(volumeSourceOptions) {\n    return transposeNestedArrays(",
      replacement: `  getSources(volumeSourceOptions) {
    const key = stableStringify(volumeSourceOptions);
    const cached = this.sourceCache.get(key);
    if (cached) return cached.map(level => level.map(source => ({
      ...source, chunkSource: source.chunkSource.addRef(),
    })));
    const sources = transposeNestedArrays(`,
    },
    {
      file: join(lib, "datasource", "zarr", "frontend.js"),
      marker: "this.sourceCache.set(key, sources)",
      anchor: "    );\n  }\n}\nfunction getJsonResource",
      replacement: `    );
    this.sourceCache.set(key, sources);
    for (const { chunkSource } of sources.flat()) chunkSource.registerDisposer(() => {
      if (this.sourceCache.get(key) === sources) this.sourceCache.delete(key);
    });
    return sources;
  }
}
function getJsonResource`,
    },
    {
      file: join(lib, "datasource", "zarr", "backend.js"),
      marker: 'import { registerRPC, registerSharedObject }',
      anchor: 'import { registerSharedObject } from "#src/worker_rpc.js";',
      replacement: 'import { registerRPC, registerSharedObject } from "#src/worker_rpc.js";',
    },
    {
      file: join(lib, "datasource", "zarr", "backend.js"),
      marker: 'registerRPC("zarr/extendBounds"',
      anchor: "export let ZarrVolumeChunkSource = class",
      replacement: `registerRPC("zarr/extendBounds", function({ id, upper, shape, invalidate }) {
  const source = this.get(id);
  if (!source) return; // The owner may have closed while this update was in flight.
  const { spec } = source;
  spec.upperVoxelBound.set(upper);
  for (let i = 0; i < spec.rank; i++) spec.upperChunkBound[i] = Math.ceil(upper[i] / spec.chunkDataSize[i]);
  source.parameters.metadata.shape = shape;
  // A partially filled boundary chunk may now contain additional frames.
  if (invalidate) source.chunkManager.queueManager.invalidateSourceCache(source);
});
export let ZarrVolumeChunkSource = class`,
    },
  ];
}
