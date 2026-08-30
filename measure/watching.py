"""Watching a browser draw: the helpers every picture measurement shares.

Which renderer really drew (and saying so out loud, because software drawing
differs from the card by more than tenfold), the launch arguments that keep
frames uncapped, the page-side probes for drawing and settling, and the
browser launchers themselves.
"""

from __future__ import annotations

import statistics  # noqa: F401  (used by frame arithmetic below)
import threading
import time
from pathlib import Path

from zmart_viewer.server import make_server

#: How long a drawing sample runs, wherever frames are counted.
SAMPLE_SECONDS = 3.0

UNCAPPED = ["--disable-gpu-vsync", "--disable-frame-rate-limit"]

# How the browser is launched: the machine's own graphics card, if it has one.
#
# **This used to force software drawing and no longer does**, which changes what
# every number here means. `--use-gl=angle --use-angle=swiftshader` made the
# engine draw on the CPU, so that a machine with a card and one without would
# report comparable figures — right for a regression guard, and wrong for telling
# an operator what their own machine does. On 6 August 2026 a whole afternoon of
# frame figures was quoted as "this machine" when every one of them described a
# CPU rasteriser: the same box reports `NVIDIA T400 4GB … D3D11` unforced and
# `SwiftShader driver` forced.
#
# Anything already written down in this repository was measured in software and
# does not compare with what this now produces. `SOFTWARE_ARGS` reproduces the old
# behaviour when a comparison with those figures is what is wanted.
BROWSER_ARGS = ["--ignore-gpu-blocklist", *UNCAPPED]

# The fallback, and the way back to the old figures.
#
# Chromium falls back to SwiftShader on its own where a card cannot be used, so
# this is needed only when the browser will not give WebGL at all, and when
# somebody deliberately wants the software numbers.
SOFTWARE_ARGS = ["--use-gl=angle", "--use-angle=swiftshader", "--ignore-gpu-blocklist", *UNCAPPED]

# Renderer names that mean the picture was drawn on the processor. Matched
# loosely and in lower case, because each driver writes its name its own way and
# buries it inside a longer string.
SOFTWARE_RENDERERS = ("swiftshader", "llvmpipe", "software", "microsoft basic")

# Asking the browser what actually drew. Kept beside the arguments because the
# two belong together: asking for a card is not the same as getting one.
WHAT_IS_DRAWING = """() => {
  const canvas = document.createElement('canvas');
  const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
  if (!gl) return 'no webgl at all';
  const named = gl.getExtension('WEBGL_debug_renderer_info');
  return named ? gl.getParameter(named.UNMASKED_RENDERER_WEBGL) : '(masked)';
}"""


def is_drawn_in_software(renderer: str) -> bool:
    """Whether this renderer name is one of the processor's rather than a card's.

    A name that will not say — some browsers mask it — is **not** reported as
    software. Saying so would be a guess dressed as a measurement, and would send
    somebody looking for a fault on a machine that has a perfectly good card. The
    name itself is printed either way, so a reader can see the question went
    unanswered.
    """
    lowered = (renderer or "").lower()
    return any(one in lowered for one in SOFTWARE_RENDERERS)


def what_drew(browser) -> str:
    """The renderer this browser really got, asked rather than assumed."""
    page = browser.new_page()
    try:
        page.goto("about:blank")
        return str(page.evaluate(WHAT_IS_DRAWING))
    finally:
        page.close()


def say_what_is_drawing(browser) -> str:
    """Print which renderer drew, and hand it back for a table to carry.

    Printed on every run without being asked for. A measurement that quietly fell
    back to software looks exactly like one that did not, and the two differ by
    more than tenfold — so the fallback has to announce itself or it is a trap
    rather than a kindness.
    """
    renderer = what_drew(browser)
    how = "IN SOFTWARE" if is_drawn_in_software(renderer) else "on the card"
    print(f"drawing {how}: {renderer}", flush=True)
    if is_drawn_in_software(renderer):
        print(
            "  (figures from software drawing say nothing about how this "
            "machine performs; they are for comparison with other machines)",
            flush=True,
        )
    return renderer


# Watching for drawing itself, rather than for the browser offering a moment in
# which drawing could have happened.
#
# This must be installed before the page makes its drawing context, so it is added
# as an init script rather than evaluated after loading. It counts calls to every
# way WebGL can be asked to draw; the count is read once a frame and only the
# change matters, so the wrapping costs an increment per call.
#
# Why it exists: counting frames alone measures the browser's own clock. A page
# drawing nothing at all still reports about 58 frames a second, because an idle
# `requestAnimationFrame` callback is offered on schedule and costs nothing to
# serve. Measured on 6 August 2026 with the view-nudging turned off: zero draw
# calls in three seconds, 57.7 frames a second reported.
WATCH_THE_DRAWING = """() => {
  window.__gl = 0;
  const count = (which) => {
    for (const context of [window.WebGLRenderingContext,
                           window.WebGL2RenderingContext]) {
      if (!context || !context.prototype[which]) continue;
      const was = context.prototype[which];
      context.prototype[which] = function (...given) {
        window.__gl += 1;
        return was.apply(this, given);
      };
    }
  };
  for (const which of ["drawArrays", "drawElements", "drawArraysInstanced",
                       "drawElementsInstanced", "drawRangeElements"]) {
    count(which);
  }
}"""

# Counting frames the way the browser really draws them. Started once and only
# once: two loops would both count and read as the page having doubled its rate.
#
# `__gl_at` records how much drawing had been done by each frame, so that a frame
# in which the picture was redrawn can afterwards be told from one in which the
# browser merely offered the chance. Without `WATCH_THE_DRAWING` installed the
# count stays at nought and every frame reads as idle, which is the honest answer
# to "how long did a drawing frame take" when nothing was watching.
COUNT_FRAMES = """() => {
  if (window.__counting) return;
  window.__counting = true;
  window.__drawn = 0;
  window.__at = [];
  window.__gl_at = [];
  const tick = (now) => {
    window.__drawn += 1;
    window.__at.push(now);
    window.__gl_at.push(window.__gl || 0);
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}"""

# Keep the view moving while frames are counted. A page with nothing to redraw is
# not being asked the question: the cost being looked for is paid per frame, so
# there have to be frames.
KEEP_MOVING = """() => {
  if (window.__nudging) return;
  window.__nudging = true;
  let forward = true;
  window.__nudge = setInterval(() => {
    const nav = window.zmartViewer.navigationState;
    const at = Float32Array.from(nav.position.value);
    at[at.length - 1] += forward ? 0.5 : -0.5;
    forward = !forward;
    nav.position.value = at;
  }, 16);
}"""

# How many pictures the viewer has been *given*. Counted from the engine rather than
# from what we asked for -- but **not** how many of them loaded, which this comment
# used to claim. A source is pushed into `dataSources` synchronously, before it has
# resolved, and one that fails stays in the list for ever with an error on it and is
# still counted here. Read it as an upper bound, and use `EVERY_SOURCE_RESOLVED`
# below to know whether it means anything yet.
HELD = """() => window.zmartViewer.layerManager.managedLayers
           .filter((managed) => managed.layer && managed.layer.type === 'image')
           .reduce((total, managed) => total + managed.layer.dataSources.length, 0)"""

# Every source has resolved -- succeeded or failed -- rather than merely been handed
# over.
#
# **`zmartSourcesWaiting()` reaching nought does not mean the run is open.** It counts
# the URLs the page has still to pass to the engine, so it empties when the last one
# has been handed over and not when it has been read. Measured on a ladder of
# positions it returned with **thirty of a hundred** resolved and **three hundred of
# four hundred**, and an opening timed against it read 3.64 s where the truth was
# 8.96. Anything that waits on it alone is looking at a page that is still filling in
# -- which for a measurement is an optimistic number, and for a test is an assertion
# about a picture that has not arrived.
#
# A source carries a `loadState` once it has resolved and once it has failed, so
# waiting for all of them to have one is the honest condition. Pair it with the wait
# on `zmartSourcesWaiting()`: that one says the list is complete, this one says the
# list was read.
EVERY_SOURCE_RESOLVED = """() => {
  const sources = window.zmartViewer.layerManager.managedLayers
    .filter((managed) => managed.layer && managed.layer.type === 'image')
    .flatMap((managed) => managed.layer.dataSources);
  return sources.length > 0 && sources.every((s) => s.loadState !== undefined);
}"""


def how_long_a_drawing_frame_took(at: list[float], drawn_by: list[int]) -> dict:
    """Split frames into the ones that drew the picture and the ones that idled.

    Args:
        at: when each frame was offered, in milliseconds.
        drawn_by: how many drawing calls the page had made by that frame.

    Returns:
        ``drawing_ms``, the middle interval among those in which the picture was
        actually redrawn; ``idle_ms``, the middle interval among the rest; and
        ``drawing_frames``, how many of the intervals drew anything.

    The two populations are far apart -- about 0.8 milliseconds against 17.3 on
    the machine this was written for -- so averaging across both, which is what
    counting frames does, buries the quantity being measured inside a constant
    twenty times larger. That is why a run of ten thousand positions and a run of
    one read the same: they are the same in that average whatever drawing costs.

    ``drawing_ms`` is ``None`` when nothing was drawn at all. Reporting nought
    there would make a page that drew nothing look like the quickest result on the
    table, which is exactly the way this measurement has been fooled before.
    """
    if len(at) != len(drawn_by):
        raise ValueError(
            f"{len(at)} frame times against {len(drawn_by)} drawing counts; they "
            "are read from the same page and must line up, so a mismatch means "
            "the page was read wrongly rather than that it behaved oddly"
        )
    drew, idled = [], []
    for earlier, later, before, after in zip(at, at[1:], drawn_by, drawn_by[1:], strict=False):
        (drew if after > before else idled).append(later - earlier)

    def middle(gaps):
        return sorted(gaps)[len(gaps) // 2] if gaps else None

    return {
        "drawing_ms": middle(drew),
        "idle_ms": middle(idled),
        "drawing_frames": len(drew),
    }


def a_browser(headed: bool = False):
    """A headless Chromium drawing uncapped, on the card where there is one.

    The card is asked for and software is the fallback -- see ``another_browser``
    and ``_launched_with`` for how the asking works and why the build matters.
    Uncapped because a frame rate measured against the display's rhythm cannot
    be climbed past it; see ``BROWSER_ARGS``. A machine whose policy blocks the
    browser that was downloaded is offered the one it already has. Whichever is
    got, ``say_what_is_drawing`` announces it, because a silent fallback to
    software is indistinguishable from a card and worth more than tenfold in
    the figures.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit(
            "playwright is not installed, so no browser could be driven. "
            "Install it with `pip install playwright`."
        ) from None
    started = sync_playwright().start()
    try:
        return started, another_browser(started, headed)
    except SystemExit:
        started.stop()
        raise


def another_browser(started, headed: bool = False):
    """A further browser from a driver that is already running.

    Two drivers cannot run at once -- the synchronous playwright API refuses to be
    started inside its own event loop -- so anything wanting a second browser, as
    a cold opening does, has to launch it from the driver already there. The
    browser itself is a new process either way, which is what makes it cold.

    The card is asked for first and software is the fallback, taken only when the
    browser gives no WebGL at all. Chromium falls back to SwiftShader by itself on
    a machine whose card cannot be used, so this second attempt is for the case
    where even that did not happen -- and when it is taken, it is said out loud by
    whoever reports the renderer, because a silent fallback is indistinguishable
    from a card and worth more than tenfold in the figures.
    """
    launched = _launched_with(started, BROWSER_ARGS, headed)
    if what_drew(launched) == "no webgl at all":
        launched.close()
        launched = _launched_with(started, SOFTWARE_ARGS, headed)
    return launched


def _launched_with(started, args, headed: bool = False):
    """A browser with these arguments, or the Chromium the machine already has.

    The full Chromium build is asked for by name (``channel="chromium"``),
    because what Playwright launches by default in headless mode is its
    *headless shell* -- a build that cannot use a graphics card at all. That is
    what was really behind the 6 August 2026 finding that a headless browser
    reports SwiftShader whatever arguments it is given while a window reaches
    the card: the window was launched from the full build, the headless run
    from the shell, and the build was the difference rather than the window.
    Measured on the same machine on 14 August 2026: the full build's headless
    reports `NVIDIA T400 4GB ... D3D11`, exactly as the window does. So
    ``headed`` is no longer the only road to the card; it stays for watching a
    run with your own eyes, and costs what it always did -- a window on
    somebody's desk that anything typed into disturbs.

    A machine holding only the shell (an older download, a trimmed cache) still
    measures: the named build is asked for first and the default is the
    fallback, and whoever reports the renderer says which one drew.
    """
    try:
        return started.chromium.launch(headless=not headed, channel="chromium", args=list(args))
    except Exception:
        pass
    try:
        return started.chromium.launch(headless=not headed, args=list(args))
    except Exception:
        from conftest import find_a_chromium

        already_here = find_a_chromium()
        if already_here is None:
            raise SystemExit(
                "no Chromium on this machine that could be driven, so frames "
                "cannot be counted here."
            ) from None
        return started.chromium.launch(
            executable_path=str(already_here), headless=not headed, args=list(args)
        )


def how_it_drew(browser, built_dist: Path, folder: Path, store, expect: int) -> dict:
    """Open what ``store`` names, wait until it is all there, then watch it draw.

    Waiting matters, and it is measured separately rather than folded in. Counting
    frames while stores are still arriving would measure the *loading*, which is a
    different question — and, at these sizes, the one that takes all the time.

    Returns how long the opening took, and then what the drawing looked like once
    it had settled: how many frames, how often they came, and — the number that
    says how it *feels* — the longest the picture ever sat still.
    """
    server = make_server(
        port=0,
        data_dir=folder,
        site_dir=built_dist,
        store=store,
        live=False,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = browser.new_page(viewport={"width": 900, "height": 700})
    # Every request the page makes, counted as it is issued. All of them go to
    # the one server started above, so the count is the traffic this picture
    # costs -- first the requests opening took, then the ones made while the
    # view was being watched, told apart by reading the counter at each stage.
    asked = {"requests": 0}
    page.on("request", lambda _: asked.update(requests=asked["requests"] + 1))
    try:
        opening = time.time()
        page.goto(
            f"http://127.0.0.1:{server.server_address[1]}",
            wait_until="domcontentloaded",
        )
        page.wait_for_function("() => window.zmartViewer !== undefined", timeout=60_000)
        page.wait_for_function(f"{HELD} >= {expect}", timeout=300_000)
        page.wait_for_function("() => window.zmartSourcesWaiting() === 0", timeout=300_000)
        page.wait_for_function(EVERY_SOURCE_RESOLVED, timeout=600_000)
        opened = time.time() - opening
        asked_opening = asked["requests"]

        page.wait_for_timeout(3000)
        page.evaluate(KEEP_MOVING)
        page.evaluate(COUNT_FRAMES)
        page.evaluate("() => { window.__drawn = 0; window.__at = []; }")
        page.wait_for_timeout(int(SAMPLE_SECONDS * 1000))
        drawn = int(page.evaluate("() => window.__drawn"))
        at = [float(n) for n in page.evaluate("() => window.__at")]
        page.evaluate("() => clearInterval(window.__nudge)")

        # How long each frame took, in milliseconds. The *middle* of these says the
        # rate the viewer is really holding; the *largest* says the worst pause an
        # operator would feel, which is what makes a viewer feel broken even when
        # the average looks respectable.
        gaps = sorted(later - earlier for earlier, later in zip(at, at[1:], strict=False))
        middle = gaps[len(gaps) // 2] if gaps else 0.0
        worst = gaps[-1] if gaps else 0.0
        # How much of the screen actually has specimen on it. Without this the
        # whole measurement can be flattered by drawing nothing: an empty panel
        # redraws beautifully. It is reported on every row so that a suspiciously
        # good rate can always be checked against whether there was a picture.
        from pixels import fraction_lit

        return {
            "lit": fraction_lit(page),
            "opened": opened,
            "frames": drawn,
            "per_second": drawn / SAMPLE_SECONDS,
            "usual_ms": middle,
            "worst_ms": worst,
            "asked_opening": asked_opening,
            "asked_watching": asked["requests"] - asked_opening,
        }
    finally:
        page.close()
        server.shutdown()
        thread.join(timeout=5)


def frames_counted(browser, built_dist: Path, folder: Path, store, expect: int) -> int:
    """Just the number of frames, for callers that want only the comparison."""
    return how_it_drew(browser, built_dist, folder, store, expect)["frames"]
