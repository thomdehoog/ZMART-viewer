// Shared embedding API v1. The host supplies its own Neuroglancer transform constructor.
export const EMBEDDING_API_VERSION = 1;

// View identity belongs to the acquisition metadata, never its filename.
export const VIEW_LABELS = {
  slice: "Slice — selected Z plane",
  top: "Top — hold boundary planes",
  min: "Projection — minimum",
  max: "Projection — maximum",
  sum: "Projection — sum",
};

export function viewKey(view) {
  return view.type === "projection" ? view.method : view.type;
}

function acquisitionKey(spec) {
  return JSON.stringify([spec.group, spec.view.acquisition]);
}

export function viewChoices(layers) {
  const choices = new Map();
  for (const spec of layers) {
    const { view } = spec;
    if (!view) continue;
    const id = acquisitionKey(spec);
    if (!choices.has(id)) choices.set(id, { acquisition: view.acquisition, group: spec.group, keys: new Set() });
    choices.get(id).keys.add(viewKey(view));
  }
  return [...choices].map(([id, {acquisition, group, keys}]) => ({
    id, acquisition, label: [...choices.values()].filter(c => c.acquisition === acquisition).length > 1
      ? `${group} / ${acquisition}` : acquisition,
    keys: Object.keys(VIEW_LABELS).filter(key => keys.has(key)),
  }));
}

export function selectedViews(layers, requested = {}) {
  return Object.fromEntries(viewChoices(layers).map(({ id, keys }) => [
    id, keys.includes(requested[id]) ? requested[id] : keys[0],
  ]));
}

export function inSelectedView(spec, selected) {
  return !spec.view || selected[acquisitionKey(spec)] === viewKey(spec.view);
}

/** Keep the last complete framebuffer during an explicitly requested Z change.
 * No extra reads or timer: the normal chunk arrivals schedule the next draw.
 * Call cancel before changing XY framing or the layer composition.
 */
export function holdCompleteSlice(sliceView, changed = () => {}) {
  const original = sliceView.updateRendering;
  let pending = false, size;
  const setPending = value => {
    if (pending === value) return;
    pending = value;
    changed(value);
  };
  sliceView.updateRendering = function () {
    const { width, height } = this.projectionParameters.value;
    if (pending && size[0] === width && size[1] === height && !this.isReady()) return;
    original.call(this);
    setPending(false);
  };
  return {
    get pending() { return pending; },
    request() {
      if (pending || sliceView.renderingStale || !sliceView.isReady()) return;
      const { width, height } = sliceView.projectionParameters.value;
      size = [width, height];
      setPending(true);
    },
    cancel() { setPending(false); },
    dispose() { sliceView.updateRendering = original; setPending(false); },
  };
}

export function keepDepthLocal(layer, viewer = null, makeTransform = null) {
  // Top samples a clamped local Z while retaining its native global slider range.
  // Persistent flats use the same local axis but do not follow the slider.
  let nativeSpace;
  const follow = () => {
    if (!viewer) return;
    const global = viewer.navigationState.position;
    const gs = global.coordinateSpace.value, ls = layer.localCoordinateSpace.value;
    const gi = gs.names.indexOf("z"), li = ls.names.indexOf("z'");
    const ni = nativeSpace?.names.indexOf("z") ?? -1;
    if (gi < 0 || li < 0 || ni < 0) return;
    // The local combiner may express this source in another acquisition's
    // units. Clamp on its native plane lattice before converting to those units.
    const bounds = nativeSpace.bounds;
    const offset = bounds.voxelCenterAtIntegerCoordinates[ni] ? 0 : 0.5;
    const lo = Math.ceil(bounds.lowerBounds[ni] - offset) + offset;
    const hi = Math.ceil(bounds.upperBounds[ni] - offset) - 1 + offset;
    const nativeZ = global.value[gi] * gs.scales[gi] / nativeSpace.scales[ni];
    const z = Math.min(hi, Math.max(lo, nativeZ)) * nativeSpace.scales[ni] / ls.scales[li];
    const position = Float32Array.from(layer.localPosition.value);
    position[li] = z;
    if (position[li] !== layer.localPosition.value[li]) layer.localPosition.value = position;
  };
  if (viewer) {
    layer.registerDisposer(viewer.navigationState.position.changed.add(follow));
    layer.registerDisposer(layer.localCoordinateSpace.changed.add(follow));
  }
  for (const source of layer.dataSources) {
    let native, lastDefault;
    const place = () => {
      const transform = source.loadState?.transform;
      const space = transform?.outputSpace.value;
      if (viewer && transform) {
        const original = transform.defaultTransform;
        if (!native) {
          native = makeTransform(original);
          layer.registerDisposer(viewer.layerSpecification.coordinateSpaceCombiner.bind(native.outputSpace));
        } else if (original !== lastDefault) {
          native.defaultTransform = original;
          native.reset();
        }
        lastDefault = original;
        nativeSpace = original.outputSpace;
      }
      if (!space?.names.includes("z")) return;
      transform.restoreState({ ...transform.toJSON(), outputDimensions:
        Object.fromEntries(space.names.map((name, i) =>
          [name === "z" ? "z'" : name, [space.scales[i], space.units[i]]])),
      });
      follow();
    };
    layer.registerDisposer(source.changed.add(place));
    place();
  }
}

function forgetWhatWasReadAbout(chunkManager, url) {
  const remembered = chunkManager?.memoize?.map;
  if (!remembered) return;
  // The address the panel writes ends in the name of the reader to use --
  // ".../pos001.ome.zarr/|zarr2:". The files themselves sit under the part before
  // that, which ends in a slash, so it cannot accidentally match a differently
  // named store that merely starts the same way (pos001 against pos0011).
  const folder = url.split("|")[0];
  if (!folder) return;
  for (const question of [...remembered.keys()]) {
    if (!question.includes(folder)) continue;
    // Anything naming a class of object is a holder of decoded image, not a file
    // that was read. Leave those exactly where they are; see above for why.
    if (question.includes('"constructorId"')) continue;
    remembered.delete(question);
  }
}

const pendingGeometry = new WeakMap();
export const geometryRefreshPending = source => pendingGeometry.has(source);
export function refreshGeometry(source, chunkManager, refreshed, forgotten) {
  const previous = pendingGeometry.get(source);
  if (previous) clearTimeout(previous.timer);
  const attempt = {};
  pendingGeometry.set(source, attempt);
  let retry = false;
  const read = async () => {
    // A newly selected view can still hold decoded chunks from its previous
    // visit. Wait for its initial source binding so refreshMetadata can refresh
    // those reusable chunks as well as the metadata.
    if (!source.loadState && !source.wasDisposed) await new Promise(resolve => {
      const done = () => {
        stop();
        source.unregisterDisposer(done);
        resolve();
      };
      const stop = source.changed.add(() => { if (source.loadState) done(); });
      source.registerDisposer(done);
    });
    if (pendingGeometry.get(source) !== attempt) return;
    if (source.layer.wasDisposed || !source.layer.dataSources.includes(source)) {
      pendingGeometry.delete(source);
      return;
    }
    const store = source.spec.url.split("|")[0];
    if (retry || !forgotten.has(store)) {
      forgetWhatWasReadAbout(chunkManager, store);
      forgotten.add(store);
    }
    const completed = await source.refreshMetadata(refreshed);
    if (pendingGeometry.get(source) !== attempt) return;
    if (completed === false) {
      // Retry a failed request, not an unchanged acquisition or periodic cache refresh.
      retry = true;
      attempt.timer = setTimeout(read, 1000);
    } else {
      pendingGeometry.delete(source);
      source.changed.dispatch();
    }
  };
  return read();
}
