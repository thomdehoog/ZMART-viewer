// -- asking Python to do things -----------------------------------------------
//
// The short conversations with the server, kept together here and out of the
// components that hold them. Every one answers with something the interface can show
// even when it went wrong, because a button that silently does nothing is the
// hardest kind of fault for an operator to make sense of.
//
// The shapes of what the server answers are declared here too, beside the
// requests that bring them in: what is open (`Config`) and each row of it.

import type { Rgb } from "./colours.ts";
import type { Window } from "./contrast-window.ts";
import type { LiveStateSet } from "./drawing/live-refresh.ts";
import type { Target } from "./targets.tsx";

/** Which of a run's named pictures a row is: a slice, the top, or a projection. */
export interface View {
  acquisition: string;
  type: string;
  method?: string;
}

/** A channel's brightness as measured: the counts per bin from `low` to `high`,
    and the window Auto would choose. */
export interface Histogram extends Window {
  counts: number[];
  autoWindow?: Window | null;
}

/** One channel row of what is open, as the server describes it. */
export interface LayerSpec {
  name: string;
  group?: string;
  kind?: "image" | "segmentation";
  source?: string;
  sources?: string[];
  coverageSources?: string[];
  /**
   * Which place each store shows, in the order of `sources`: stores of
   * different channels showing the same position carry the same name.
   */
  sourcePlaces?: string[];
  sourceIds?: string[];
  sourceRevisions?: number[];
  sourceGeometryRevisions?: number[];
  frameCounts?: number[];
  frames?: number | null;
  /** Per store, whether it is one plane or a stack: the flat view draws them apart. */
  sourceDepths?: ("flat" | "stack")[];
  view?: View;
  histogram?: Histogram | null;
  window?: Window | null;
  volumeWindow?: Window | null;
  /** The whole range this channel's numbers live in, where the run says. */
  range?: Window | null;
  opaque?: boolean;
  localPosition?: number[];
  channelIndex?: number | null;
  liveRunId?: string | null;
  color?: Rgb | null;
  /** False where the run says this channel was switched off at the instrument. */
  active?: boolean;
}

/** What is open, as the server says it: the rows, and how the page should behave. */
export interface Config {
  layers: LayerSpec[];
  groups?: string[];
  liveState?: LiveStateSet | null;
  live?: boolean;
  canOpen?: boolean;
  chrome?: boolean;
  depthSamples?: number;
  transparentBackground?: boolean;
  /** Whether the operator may mark targets on the image. */
  canSelect?: boolean;
  /** Which edge the bar of controls sits on. */
  panelSide?: "left" | "right";
}

/**
 * Ask the server what is open, or return null if it cannot be reached.
 *
 * Returning null rather than an invented set of images is the point. An earlier
 * version answered a failure with a made-up volume that did not exist, so a
 * server that had stopped answering looked exactly like data that had failed to
 * load: a black screen and nothing to read. Saying plainly that the server could
 * not be reached is far more use to someone at two in the morning wondering
 * whether their experiment is still running.
 */
export async function fetchConfig(): Promise<Config | null> {
  try {
    const response = await fetch("/api/config");
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

export async function fetchLiveState(etag: string | null = null) {
  try {
    const headers: Record<string, string> = etag ? { "If-None-Match": etag } : {};
    const response = await fetch("/api/live-state", { headers });
    if (response.status === 304) return { unchanged: true as const, etag };
    if (!response.ok) return null;
    return {
      unchanged: false as const,
      etag: response.headers.get("ETag"),
      state: await response.json() as LiveStateSet,
    };
  } catch {
    return null;
  }
}

// The desktop window can show the operating system's own folder chooser; a
// page in a plain browser cannot, and used to fall back to a bare prompt
// asking for a path typed blind. Now the page draws its own load window
// instead (see LoadWindow.tsx), walking the server's folders by listing
// them through the API.
export async function tryNativeChooser(): Promise<{ cancelled: true } | { path: string; parent: string } | { window: true }> {
  try {
    const response = await fetch("/api/browse", { method: "POST" });
    const answer = await response.json().catch(() => null);
    if (answer?.cancelled) return { cancelled: true };
    if (response.ok && answer?.path) {
      return { path: answer.path, parent: answer.parent };
    }
  } catch {
    // fall through to the in-page window
  }
  return { window: true };
}

/** A view whose raw data has moved: its name, where its data was, the folder it
    stands in, and whether it keeps a baked mosaic. */
export interface Relink {
  name: string;
  was: string;
  parent: string;
  baked: boolean;
}

/** What a refusal carries: the server's own sentence, and for an open, what to relink. */
interface Refused {
  error?: string;
  relink?: Relink;
}

/** A measurement of the pixels in a box: the window and histogram found there, or
    `empty` where the box held no pixels. */
export interface Measured {
  empty?: true;
  window?: Window;
  histogram?: Histogram;
}

/** How far a build of a view has got. */
export interface BuildStatus {
  state: "running" | "done" | "cancelled" | "error" | string;
  fraction?: number;
  store?: string;
  error?: string;
}

/** One folder in a listing; `kind` says what it holds when it is something to open. */
export interface ListedFolder {
  name: string;
  kind?: string;
  /** A view that keeps its precomputed low-resolution mosaic. */
  baked?: boolean;
}

/** A folder as the load window lists it. */
export interface Listing {
  path: string;
  parent?: string | null;
  /** What the folder itself is, when it can be opened as it stands. */
  kind?: string;
  folders: ListedFolder[];
  /** Why the last walk went wrong, kept beside the listing it left in place. */
  error?: string;
}

/**
 * Post a JSON body and read the JSON answer: `{ ok, status, answer }`, or null when the
 * server did not answer at all -- the connection dropped, or it is gone. Every
 * request below says what that means for its own button, so none of them can
 * leave one waiting for ever.
 */
async function post<Answer>(path: string, body: object = {}): Promise<{ ok: boolean; status: number; answer: Answer | null } | null> {
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return { ok: response.ok, status: response.status, answer: await response.json().catch(() => null) as Answer | null };
  } catch {
    return null;
  }
}

/** What a request says when the server never answered it. */
const UNANSWERED = "the viewer's server did not answer";

/** A share of the picture, as fractions of its own bounds: `[[y0, x0], [y1, x1]]`. */
export type Box = [[number, number], [number, number]];

// Measure the brightness of one channel inside `box`. A measurement that cannot
// be taken leaves the picture as it is, which is the honest answer to a button
// that had nothing to read.
export async function measureChannel(spec: LayerSpec, box: Box) {
  const asking = await post<Measured>("/api/measure", {
    source: (spec.sources || [spec.source])[0],
    sources: spec.sourceDepths ? spec.sources : undefined,
    channel: spec.channelIndex ?? null,
    box,
  });
  return asking?.ok ? asking.answer : null;
}

export async function openPath(path: string): Promise<{ error: string; relink?: Relink | null } | { config: Config }> {
  const asking = await post<Config & Refused>("/api/stores/open", { path });
  if (!asking) return { error: `could not open ${path}: ${UNANSWERED}` };
  if (!asking.ok) {
    return {
      error: asking.answer?.error || `could not open ${path}`,
      relink: asking.answer?.relink || null,
    };
  }
  return { config: asking.answer! };
}

export async function startConstruction(
  path: string, viewerFolder: string, bake: boolean, name?: string,
): Promise<{ error: string } | { started: true }> {
  const asking = await post<Refused>("/api/stores/construct", { path, viewer_folder: viewerFolder, bake, name });
  if (!asking) return { error: `the construction could not start: ${UNANSWERED}` };
  if (!asking.ok) return { error: asking.answer?.error || "the construction could not start" };
  return { started: true };
}

// Null when the poll went unanswered: the build may well still be running, so
// the page asks again rather than calling it failed.
export async function constructionStatus(): Promise<BuildStatus | null> {
  const asking = await post<BuildStatus>("/api/stores/construct-status");
  if (!asking) return null;
  return asking.answer ?? { state: "error", error: "unreadable answer" };
}

// Ask the running build to stop at its next step. Cooperative: the step in
// flight finishes whole, and a stopped build removes its half-made scene.
export async function askToStop(what: string): Promise<void> {
  await post(`/api/stores/${what}-cancel`);
}

export async function listFolders(path: string | null): Promise<{ error: string } | Listing> {
  const asking = await post<Listing & Refused>("/api/stores/list", path ? { path } : {});
  if (!asking) return { error: `the folders could not be listed: ${UNANSWERED}` };
  if (!asking.ok) return { error: asking.answer?.error || "the folders could not be listed" };
  return asking.answer!;
}

export async function closeGroup(group: string): Promise<{ error: string } | { config: Config }> {
  const asking = await post<Config & Refused>("/api/stores/close", { group });
  if (!asking) return { error: `could not close ${group}: ${UNANSWERED}` };
  if (!asking.ok) return { error: asking.answer?.error || `could not close ${group}` };
  return { config: asking.answer! };
}

// The targets saved beside the images, as ``{ targets }``, or ``{ error }`` when
// the file is there but could not be read. A failed read is never an empty list:
// that list would be saved back over the file and lose what is in it.
export async function loadTargets(): Promise<{ error: string } | { targets: Target[] }> {
  try {
    const response = await fetch("/api/annotations");
    const answer = await response.json().catch(() => null);
    if (!response.ok) {
      return { error: answer?.error || `the saved targets could not be read (${response.status})` };
    }
    return { targets: answer?.annotations || [] };
  } catch {
    return { error: "the saved targets could not be read: the server did not answer" };
  }
}

// Save the targets beside the images. Null when they were saved; otherwise what
// went wrong, in words the panel can show.
export async function saveTargets(targets: readonly Target[]): Promise<string | null> {
  const asking = await post<Refused>("/api/annotations", { version: 1, annotations: targets });
  // Almost always the server having gone away -- worth saying plainly rather
  // than leaving the panel looking as though all is well.
  if (!asking) return "could not reach the server";
  if (!asking.ok) return asking.answer?.error || `save failed (${asking.status})`;
  return null;
}
