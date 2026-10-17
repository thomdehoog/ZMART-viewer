// -- asking Python to do things -----------------------------------------------
//
// The short conversations with the server, kept together here and out of the
// components that hold them. Every one answers with something the interface can show
// even when it went wrong, because a button that silently does nothing is the
// hardest kind of fault for an operator to make sense of.

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
export async function fetchConfig() {
  try {
    const response = await fetch("/api/config");
    if (!response.ok) return null;
    return await response.json();
  } catch {
    return null;
  }
}

export async function fetchLiveState(etag = null) {
  try {
    const headers = etag ? { "If-None-Match": etag } : {};
    const response = await fetch("/api/live-state", { headers });
    if (response.status === 304) return { unchanged: true, etag };
    if (!response.ok) return null;
    return {
      unchanged: false,
      etag: response.headers.get("ETag"),
      state: await response.json(),
    };
  } catch {
    return null;
  }
}

// The desktop window can show the operating system's own folder chooser; a
// page in a plain browser cannot, and used to fall back to a bare prompt
// asking for a path typed blind. Now the page draws its own load window
// instead (see LoadWindow.jsx), walking the server's folders by listing
// them through the API.
export async function tryNativeChooser() {
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

/**
 * Post a JSON body and read the JSON answer: `{ ok, answer }`, or null when the
 * server did not answer at all -- the connection dropped, or it is gone. Every
 * request below says what that means for its own button, so none of them can
 * leave one waiting for ever.
 */
async function post(path, body = {}) {
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return { ok: response.ok, answer: await response.json().catch(() => null) };
  } catch {
    return null;
  }
}

/** What a request says when the server never answered it. */
const UNANSWERED = "the viewer's server did not answer";

// A measurement that cannot be taken leaves the picture as it is, which is the
// honest answer to a button that had nothing to read.
export async function measureHere(asked) {
  const asking = await post("/api/measure", asked);
  return asking?.ok ? asking.answer : null;
}

export async function openPath(path) {
  const asking = await post("/api/stores/open", { path });
  if (!asking) return { error: `could not open ${path}: ${UNANSWERED}` };
  if (!asking.ok) {
    return {
      error: asking.answer?.error || `could not open ${path}`,
      relink: asking.answer?.relink || null,
    };
  }
  return { config: asking.answer };
}

export async function startConstruction(path, viewerFolder, bake, name) {
  const asking = await post("/api/stores/construct", { path, viewer_folder: viewerFolder, bake, name });
  if (!asking) return { error: `the construction could not start: ${UNANSWERED}` };
  if (!asking.ok) return { error: asking.answer?.error || "the construction could not start" };
  return { started: true };
}

// Null when the poll went unanswered: the build may well still be running, so
// the page asks again rather than calling it failed.
export async function constructionStatus() {
  const asking = await post("/api/stores/construct-status");
  if (!asking) return null;
  return asking.answer ?? { state: "error", error: "unreadable answer" };
}

// Ask the running build to stop at its next step. Cooperative: the step in
// flight finishes whole, and a stopped build removes its half-made scene.
export async function askToStop(what) {
  await post(`/api/stores/${what}-cancel`);
}

export async function listFolders(path) {
  const asking = await post("/api/stores/list", path ? { path } : {});
  if (!asking) return { error: `the folders could not be listed: ${UNANSWERED}` };
  if (!asking.ok) return { error: asking.answer?.error || "the folders could not be listed" };
  return asking.answer;
}

export async function closeGroup(group) {
  const asking = await post("/api/stores/close", { group });
  if (!asking) return { error: `could not close ${group}: ${UNANSWERED}` };
  if (!asking.ok) return { error: asking.answer?.error || `could not close ${group}` };
  return { config: asking.answer };
}

// The targets saved beside the images, as ``{ targets }``, or ``{ error }`` when
// the file is there but could not be read. A failed read is never an empty list:
// that list would be saved back over the file and lose what is in it.
export async function loadTargets() {
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
