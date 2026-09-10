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
    if (!choices.has(id)) choices.set(id, { acquisition: view.acquisition, keys: new Set() });
    choices.get(id).keys.add(viewKey(view));
  }
  return [...choices].map(([id, {acquisition, keys}]) => ({
    id, acquisition, keys: Object.keys(VIEW_LABELS).filter(key => keys.has(key)),
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
