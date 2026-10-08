// The layer the operator's marked places are drawn on, and how it is described
// to the drawing engine.
export const TARGET_LAYER = "Targets";

export function annotationLayer(targets, color, visible) {
  return {
    type: "annotation",
    name: TARGET_LAYER,
    source: "local://annotations",
    annotations: targets,
    annotationColor: color,
    visible,
  };
}
