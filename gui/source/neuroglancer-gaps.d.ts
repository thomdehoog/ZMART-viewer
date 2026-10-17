/**
 * The one neuroglancer module this page uses that ships no declaration.
 *
 * Neuroglancer 2.41.2 has a type file beside nearly every module, but not
 * beside the annotation tools the page places targets with. The members the
 * ZMART edits add to the compiled package (`engine/drawing/neuroglancer-*.mjs`)
 * are named where they are read, by the one file that reads each.
 */

declare module "neuroglancer/unstable/ui/annotations.js" {
  import type { UserLayer } from "neuroglancer/unstable/layer/index.js";
  import type { Tool } from "neuroglancer/unstable/ui/tool.js";

  /** The tool that places a point where the operator clicks. */
  export class PlacePointTool extends Tool<UserLayer> {
    constructor(layer: UserLayer, options?: unknown);
  }

  /** The tool that places a box between two clicks. */
  export class PlaceBoundingBoxTool extends Tool<UserLayer> {
    constructor(layer: UserLayer, options?: unknown);
  }
}
