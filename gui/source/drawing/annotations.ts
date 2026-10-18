/**
 * The viewer's drawn targets, as a layer of its own kind.
 *
 * The drawing primitives build this layer, order it and show or hide it; its
 * colour is the viewer's own setting, written here through the primitives'
 * `ownSettings` hook. Only this window draws targets, so none of it is shared.
 */

import type { UserLayer } from "neuroglancer/unstable/layer/index.js";
import type { PageLayer } from "../../../engine/drawing/primitives/description.ts";

/**
 * The targets' layer, as the page adds it to the scene. Only its colour and
 * whether it shows are carried across; what is drawn on it belongs to the engine.
 */
export interface TargetLayer extends PageLayer {
  type: "annotation";
  source: string;
  annotations: unknown;
  annotationColor?: string;
}

/** The annotation layer's colour, which neuroglancer's declarations leave out. */
interface AnnotationLayerSettings {
  annotationDisplayState: { color: { restoreState(value: unknown): void } };
}

/**
 * Write the targets' layer's own settings: its colour.
 *
 * Note what is deliberately absent: the annotations themselves. Once the
 * layer exists, the drawings inside it belong to the engine, and the panel's
 * list is a reflection of them rather than the other way round. Writing them
 * back here is what used to erase whatever had just been drawn.
 */
export function writeTheTargetsSettings(layer: UserLayer, description: PageLayer): void {
  const targets = description as TargetLayer;
  if (targets.type !== "annotation" || !targets.annotationColor) return;
  (layer as UserLayer & AnnotationLayerSettings).annotationDisplayState.color.restoreState(
    targets.annotationColor,
  );
}
