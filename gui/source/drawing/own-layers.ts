/**
 * The viewer's own kinds of layer: the drawn targets and the masks.
 *
 * The drawing primitives build these layers, hand them their stores, order
 * them and show or hide them; their other settings are this window's own,
 * written here through the primitives' `ownSettings` hook. Only this window
 * draws targets or masks, so none of it is shared.
 */

import type { UserLayer } from "neuroglancer/unstable/layer/index.js";
import type { WatchableValueInterface } from "neuroglancer/unstable/trackable_value.js";
import type { EngineLayer, PageLayer } from "../../../engine/drawing/primitives/description.ts";

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

/**
 * A segmentation mask: every object gets its own colour and one can be picked
 * out. Brightness and contrast mean nothing on an identity number; how
 * strongly it is painted over the image is the only thing there is to set.
 */
export interface MaskLayer extends PageLayer, Pick<EngineLayer,
  "persistentFlat" | "boundaryHeld" | "frameCounts" | "sourceIds" | "sourceRevisions"
  | "sourceGeometryRevisions" | "localPosition"> {
  type: "segmentation";
  source: string[];
  selectedAlpha: number;
  notSelectedAlpha: number;
}

/** The annotation layer's colour, which neuroglancer's declarations leave out. */
interface AnnotationLayerSettings {
  annotationDisplayState: { color: { restoreState(value: unknown): void } };
}

/** The mask layer's two strengths, which neuroglancer's declarations leave out. */
interface SegmentationLayerSettings {
  displayState: {
    selectedAlpha: WatchableValueInterface<number>;
    notSelectedAlpha: WatchableValueInterface<number>;
  };
}

/**
 * Write the settings of one of this window's own layers.
 *
 * Note what is deliberately absent for the targets: the annotations
 * themselves. Once the layer exists, the drawings inside it belong to the
 * engine, and the panel's list is a reflection of them rather than the other
 * way round. Writing them back here is what used to erase whatever had just
 * been drawn.
 */
export function writeTheViewersOwnSettings(layer: UserLayer, description: PageLayer): void {
  if (description.type === "annotation") {
    const targets = description as TargetLayer;
    if (!targets.annotationColor) return;
    (layer as UserLayer & AnnotationLayerSettings).annotationDisplayState.color.restoreState(
      targets.annotationColor,
    );
  } else if (description.type === "segmentation") {
    const mask = description as MaskLayer;
    const settings = (layer as UserLayer & SegmentationLayerSettings).displayState;
    settings.selectedAlpha.value = mask.selectedAlpha;
    settings.notSelectedAlpha.value = mask.notSelectedAlpha;
  }
}
