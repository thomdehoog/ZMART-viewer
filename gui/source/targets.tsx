/**
 * The places the operator marks on the image: drawn in the engine, listed in
 * the panel beside it, and saved to a file beside the images.
 *
 * The engine owns the targets once their layer exists; the list is a
 * reflection of what is in it, and the file follows the list. This hook holds
 * all of that -- what was read back, what is drawn, the drawing tool, the
 * save -- and hands the page two things: the description of the layer the
 * targets are drawn on, for the scene, and what the targets panel shows.
 */

import React from "react";
import type { UserLayer } from "neuroglancer/unstable/layer/index.js";
import type { Viewer } from "neuroglancer/unstable/viewer.js";
import { PlacePointTool, PlaceBoundingBoxTool } from "neuroglancer/unstable/ui/annotations.js";
import { loadTargets, saveTargets } from "./asking-python.ts";
import type { TargetLayer } from "../../engine/drawing/primitives/description.ts";
import type { TargetsPanelProps } from "./TargetsPanel.tsx";

/** One place the operator has marked, as the engine's annotation layer holds it. */
export interface Target {
  id: string;
  type: string;
  description?: string;
  /** Where a point is, along the view's axes in order. */
  point?: number[];
  /** A box's two opposite corners. */
  pointA?: number[];
  pointB?: number[];
}

/** Where the last save of the targets stands. */
export type SaveState =
  | { status: "idle" }
  | { status: "saving" }
  | { status: "saved" }
  | { status: "error"; message: string };

/** The drawing tool the operator has picked, if any. */
export type TargetTool = "point" | "box" | null;

// The layer the operator's marked places are drawn on.
const TARGET_LAYER = "Targets";

/** The layer the targets are drawn on, as the engine is told to draw it. */
function annotationLayer(targets: Target[], color: string, visible: boolean): TargetLayer {
  return {
    type: "annotation",
    name: TARGET_LAYER,
    source: "local://annotations",
    annotations: targets,
    annotationColor: color,
    visible,
  };
}

// Neuroglancer's annotation layer ships no declaration, so what this file uses
// of it is named here, and the layer is taken as one in `targetLayerOf`.

/** A held reference to one annotation, to be let go of once used. */
interface TargetReference {
  value: Target | null | undefined;
  dispose(): void;
}

/** The annotations the operator draws into: the engine's own copy of the targets. */
interface TargetSource {
  changed: { add(listener: () => void): () => void };
  toJSON(): Target[];
  getReference(id: string): TargetReference;
  delete(reference: TargetReference): void;
  update(reference: TargetReference, value: Target): void;
}

/** What the annotation layer has beyond any layer: its annotations, and picking one out. */
interface AnnotationLayerMembers {
  localAnnotations?: TargetSource;
  annotationStates?: { states?: { source: { readonly?: boolean } }[] };
  selectAnnotation(state: unknown, id: string, pin: boolean): void;
}

/** The layer the targets are drawn on, if the engine has built it. */
function targetLayerOf(viewer: Viewer | null): (UserLayer & AnnotationLayerMembers) | undefined {
  const layer = viewer?.layerManager.getLayerByName(TARGET_LAYER)?.layer;
  return (layer ?? undefined) as (UserLayer & AnnotationLayerMembers) | undefined;
}

/**
 * The targets, drawn on `viewer`.
 *
 * Returns `layer`, the description of the layer the targets are drawn on --
 * null until the saved ones have been read back, so whatever was saved is
 * present from the moment the layer exists -- and `panel`, what the targets
 * panel shows and what its buttons do.
 */
export function useTargets(viewer: Viewer | null, kept: boolean | null): { layer: TargetLayer | null; panel: TargetsPanelProps } {
  const [targets, setTargets] = React.useState<Target[]>([]);
  const [targetsLoaded, setTargetsLoaded] = React.useState(false);
  const [targetColor, setTargetColor] = React.useState("#ffd34d");
  const [targetsVisible, setTargetsVisible] = React.useState(true);
  const [activeTool, setActiveTool] = React.useState<TargetTool>(null);
  // What happened the last time the targets were saved, shown in the panel so a
  // failed save cannot pass unnoticed.
  const [saveState, setSaveState] = React.useState<SaveState>({ status: "idle" });
  const annotationSource = React.useRef<TargetSource | null>(null);
  // How to stop listening to the annotation source we are currently following.
  const stopListening = React.useRef<(() => void) | null>(null);
  // The targets read back from the previous session. These are handed to the
  // engine once, when the layer that holds them is built. Kept aside from the
  // living list of targets on purpose: that list changes with every scribble, and
  // if the description of the scene depended on it, drawing would rebuild the very
  // layer being drawn into.
  const targetsFromDisk = React.useRef<Target[]>([]);
  // The target list as last read from or written to disk, as text, so a save is
  // only sent for a real change. Null until the engine's list is first seen.
  const lastSavedText = React.useRef<string | null>(null);
  // True when the saved file could not be read: nothing may be saved over it.
  const targetsUnreadable = React.useRef(false);

  // The targets drawn in a previous session, read once. The images are not read
  // here: they arrive when the page's connection to the server opens, which is
  // the moment we know the server is actually answering. Only where the page
  // keeps targets (the window's own server says so): asking a server that has no
  // file of them was a failed request in every other interface's console.
  // `kept` is null until the page's settings have come: nothing is decided, and
  // no layer made, before then.
  React.useEffect(() => {
    if (kept === null) return undefined;
    if (!kept) {
      setTargetsLoaded(true);
      return undefined;
    }
    let cancelled = false;
    loadTargets().then((answer) => {
      if (cancelled) return;
      if ("error" in answer) {
        // Drawing still works, but nothing is saved for the rest of this
        // session, and the panel says why.
        targetsUnreadable.current = true;
        setSaveState({ status: "error", message: answer.error });
      }
      const savedTargets = "targets" in answer ? answer.targets : [];
      targetsFromDisk.current = savedTargets;
      setTargets(savedTargets);
      setTargetsLoaded(true);
    });
    return () => {
      cancelled = true;
    };
  }, [kept]);

  // The layer holding drawn targets is added once the saved ones have been read
  // back, so whatever was saved is present from the moment the layer exists.
  const layer = React.useMemo(
    () => (targetsLoaded ? annotationLayer(targetsFromDisk.current, targetColor, targetsVisible) : null),
    [targetsLoaded, targetColor, targetsVisible],
  );

  // Start listening to the layer that holds drawn targets, so the list beside the
  // image follows what is actually in the engine. The layer's store of annotations
  // is created a moment after the layer itself, which is why this waits for it
  // rather than assuming it is there.
  React.useEffect(() => {
    if (!viewer || !targetsLoaded) return undefined;
    const connect = () => {
      const source = targetLayerOf(viewer)?.localAnnotations;
      if (!source) return false; // not built yet; look again shortly
      if (source === annotationSource.current) return true; // already listening
      // Let go of the previous one first. This only happens if the layer really
      // was rebuilt, which is rare now, but leaving the old listener attached
      // would mean two of them writing to the same list.
      if (stopListening.current) stopListening.current();
      annotationSource.current = source;
      stopListening.current = source.changed.add(() => setTargets(source.toJSON()));
      Object.assign(window, { zmartAnnotationSource: source });
      const present = source.toJSON();
      // What is on disk, as the engine holds it: only a list that differs from
      // this is worth saving, so opening the viewer writes nothing.
      if (lastSavedText.current === null) lastSavedText.current = JSON.stringify(present);
      setTargets(present);
      return true;
    };
    if (connect()) return undefined;
    const waiting = setInterval(() => {
      if (connect()) clearInterval(waiting);
    }, 50);
    return () => clearInterval(waiting);
  }, [viewer, targetsLoaded, layer]);

  // Saving happens on its own, shortly after any change, so the operator never
  // has to remember to. That makes it all the more important to *show* whether it
  // worked: a target list that silently failed to save looks exactly like one
  // that saved, right up until the acquisition is reopened and the targets are
  // gone.
  React.useEffect(() => {
    if (!kept || !targetsLoaded || targetsUnreadable.current) return undefined;
    const text = JSON.stringify(targets);
    if (lastSavedText.current === null || text === lastSavedText.current) return undefined;
    let cancelled = false;
    const timer = setTimeout(async () => {
      setSaveState({ status: "saving" });
      const refused = await saveTargets(targets);
      if (cancelled) return;
      if (refused === null) {
        lastSavedText.current = text;
        setSaveState({ status: "saved" });
      } else {
        setSaveState({ status: "error", message: refused });
      }
    }, 150);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [targets, targetsLoaded]);

  // The drawing tool goes on the layer the targets are drawn on. That layer is
  // built by the page's pass over the scene, which can come a moment after this
  // -- on a fresh engine it always does -- so it is looked for again shortly
  // until it is there, the same way the list above waits for it.
  React.useEffect(() => {
    if (!viewer) return undefined;
    const arm = () => {
      const drawnOn = targetLayerOf(viewer);
      if (!drawnOn) return false;
      drawnOn.tool.value =
        activeTool === "point"
          ? new PlacePointTool(drawnOn, {})
          : activeTool === "box"
            ? new PlaceBoundingBoxTool(drawnOn, {})
            : undefined;
      return true;
    };
    if (arm()) return undefined;
    const waiting = setInterval(() => {
      if (arm()) clearInterval(waiting);
    }, 50);
    return () => clearInterval(waiting);
  }, [viewer, activeTool, targetsLoaded]);

  const deleteTarget = (id: string) => {
    const source = annotationSource.current;
    const reference = source?.getReference(id);
    if (!source || !reference) return;
    source.delete(reference);
    reference.dispose();
  };
  /**
   * Show a target: pick it out, and move the view to where it is.
   *
   * Both halves are needed. Picking it out on its own leaves the view exactly
   * where it was, and a target is usually on some other plane of the stack — so
   * clicking it in the list looked, from the operator's side, like the button
   * simply did nothing. Moving the view is what makes it a way of getting back to
   * a place you marked earlier, which is the whole point of marking it.
   */
  const selectTarget = (id: string) => {
    const drawnOn = targetLayerOf(viewer);
    const state = drawnOn?.annotationStates?.states?.find((entry) => !entry.source.readonly);
    if (!viewer || !drawnOn || !state) return;
    drawnOn.selectAnnotation(state, id, true);
    Object.assign(window, { zmartSelectedTarget: id });

    const target = targets.find((entry) => entry.id === id);
    // A point sits at one place; a box is a corner and its opposite, so the
    // middle of the two is the place to go.
    const corner = target?.pointB;
    const where =
      target?.point ||
      (target?.pointA && corner
        ? target.pointA.map((value, axis) => (value + corner[axis]) / 2)
        : null);
    if (!where) return;
    const position = viewer.navigationState.position;
    const moved = Float32Array.from(position.value);
    // A target is recorded against the same axes the view uses, in the same
    // order, so this is a straight copy of as much of it as there is room for.
    where.slice(0, moved.length).forEach((value, axis) => {
      if (Number.isFinite(value)) moved[axis] = value;
    });
    position.value = moved;
  };
  // Give a target a name. Neuroglancer owns the annotation itself, so the new
  // description is written back through the annotation source rather than kept
  // beside it -- that way the engine, the list, and the saved file cannot drift
  // apart, and the change reaches the sidecar by the same route as everything
  // else the operator draws.
  const describeTarget = (id: string, description: string) => {
    const source = annotationSource.current;
    const reference = source?.getReference(id);
    if (!source || !reference?.value) return;
    source.update(reference, { ...reference.value, description });
    reference.dispose();
  };

  return {
    layer,
    panel: {
      targets,
      activeTool,
      color: targetColor,
      visible: targetsVisible,
      saveState,
      onTool: setActiveTool,
      onColor: setTargetColor,
      onVisible: () => setTargetsVisible((value) => !value),
      onSelect: selectTarget,
      onDelete: deleteTarget,
      onDescribe: describeTarget,
    },
  };
}
