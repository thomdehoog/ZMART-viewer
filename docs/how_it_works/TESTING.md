# Testing the viewer

## The short version

From the repository folder, install the viewer with its test tools, and the
browser the picture tests drive:

```
pip install -e .[dev]
python -m playwright install chromium
python -m pytest tests
```

The built page is already in the repository (`gui/built/`), so the tests need
no Node or npm. You need them only after changing the GUI's JavaScript; then
run `npm install && npm run build` first.

The browser tests open the real viewer in a headless browser and read the
pixels it drew. Where no browser can be started they skip, and the end of the
run says plainly that no picture was looked at. **Before approving a release,
run with a browser and make that a failure instead:**

```
ZMART_REQUIRE_BROWSER=1 python -m pytest tests
```

Anything you add after the command goes straight to pytest, so you can run just
part of the suite while you work:

```
python -m pytest tests -k omezarr     # only the OME-Zarr tests
python -m pytest tests -v              # one line per test
```

## Manifest-driven production refresh

The focused non-browser path for the live publication integration is:

```bash
python -m pytest -q \
  tests/test_manifest_driven_refresh.py \
  tests/test_frontend_live_refresh_contract.py \
  tests/test_live_publication_gateway.py
```

The real production page scenarios are in
`tests/test_manifest_refresh_browser.py`. They open the shipped backend and
frontend, use the production `LivePublisher`, photograph Neuroglancer, and cover
position publication, an uncommitted cached-empty region, timepoint publication,
replacement generations, operator-state survival, selective requests across two
runs, SSE loss, fallback and reconnection. Run them with a browser-required flag
on a machine intended to qualify the viewer:

```bash
ZMART_REQUIRE_BROWSER=1 python -m pytest -q \
  tests/test_manifest_refresh_browser.py
```

A skip is not visual verification. The positive assertion that committed A
remains measurably bright while uncommitted B stays invisible is load-bearing:
it prevents a black screen from satisfying the publication gate.

## What runs, and what skips

The suite is written so a plain machine stays green and a capable machine tests
more — nothing fails just because a piece is absent; it *skips* with a clear
reason. Three things decide what runs:

- **Always.** The data-reading tests (finding channels in a store, choosing a
  contrast window, serving chunks safely) need only Python with numpy and zarr.
  These run everywhere.
- **When the page is built, and a browser can be started.** The browser tests
  load the real viewer and check that pixels actually reach the renderer.
  The page must be built first (see above); without it they skip. They also
  need a Chromium, and the suite goes to some trouble to find one — see
  "Finding a browser this machine already has" below.
- **When there is a graphics card.** Without one, the browser draws in
  software. The tests still pass, but they then measure this machine's
  arithmetic rather than a real screen, and the end of the run says so.

## Finding the limit on how many positions a browser will carry

One test is left out of an ordinary run because it takes many minutes rather
than seconds. It is worth knowing about, because it measures the number the
viewer's safety margin is built on.

A folder of more than roughly six hundred and eighty positions used to draw only
part of the specimen and say nothing at all about the rest — the browser starts
refusing requests once too many are waiting, and a refused request looks to the
drawing engine like a position that cannot be read. The viewer now hands the
positions over in groups and lets each group finish, which keeps the queue short
enough that nothing is refused.

That protection is only as good as the size of a group, so two things guard it.
The ordinary run checks that the size the viewer ships with still leaves room
beneath the measured limit — that one is instant and it is what fails if somebody
raises the number. The measurement itself is opt-in:

```
ZMART_FIND_THE_LIMIT=1 python -m pytest tests -s -k finds_the_limit
```

It turns the pacing off and opens folders of increasing size until positions
start going missing, narrowing down until it has the boundary, and then opens a
folder well past that limit with the pacing on to confirm every position still
arrives. The `-s` is worth having: it prints what it found at each step.

Run it when the browser is updated, when the viewer moves to a different drawing
engine, or when somebody wants to raise the group size.

**The answer depends on the machine, and by a lot.** Run on the sandbox this
project's tests are developed on, the browser carried four thousand positions
unpaced without losing a single one — the search never found a limit at all,
where the figure the viewer's margin is built on is six hundred and eighty. Both
numbers are real; they are simply different machines. So a run of this test tells
you about *that* machine, and the figure worth trusting for the lab is the one
measured on the acquisition PC. The margin the ordinary run checks against stays
at the smaller, more cautious number for exactly this reason: a viewer that is
safe on the slowest machine is safe everywhere.

## Finding a browser this machine already has

Playwright downloads its own Chromium and will only launch that one exact build.
That is usually fine: `playwright install chromium` fetches it once. But
some machines cannot download one — a lab PC behind a policy that blocks it, or a
container that ships a browser of its own — and on those machines Playwright
refuses to start the perfectly good Chromium sitting right there, because its build
number is not the one it expected.

The consequence is worse than an error would be. Every test that looks at the
picture skips, and the run goes green having never drawn a pixel.

So before giving up, the suite looks for a Chromium the machine already has. It
searches wherever `PLAYWRIGHT_BROWSERS_PATH` points, and `/opt/pw-browsers`, and
takes the newest build it finds. No build number is written down anywhere, so this
keeps working as browsers are updated. Playwright's own browser is still tried
first, so nothing changes on an ordinary machine.

If that search picks the wrong one, or finds nothing on a machine you know has a
browser, name the one you want:

```
ZMART_CHROMIUM=/path/to/chrome python -m pytest tests
```

Naming a file that does not exist means "there is no browser here", which is a
useful way to see for yourself what a browser-less machine gets.

## Making a run fail if it never looked at a picture

About a third of this suite opens a real browser and reads the pixels it drew —
199 tests of 554 when this was last counted, on 2026-07-31 — and that third is the
only part that catches the fault this project keeps meeting: a picture that is
silently absent, with every piece fetched, every layer built, and the engine
reporting itself perfectly content.

If no browser can be started, or the page was never built, all of those tests skip
— and the run would otherwise report the same comfortable green as one that looked
and was satisfied. On a laptop without Node that is exactly right; on a machine
that is *supposed* to draw, it is the suite quietly stopping doing the one thing it
is for.

Two things guard that, and the first applies everywhere. **Any** run in which the
picture was not looked at ends with a banner saying so:

```
================================ NO PICTURE WAS LOOKED AT ================================
199 tests that open a real browser and read the pixels it drew were skipped.
Why:
  - no usable Chromium on this machine: BrowserType.launch: Executable doesn't exist at …
…
```

The run is still green, because a plain checkout is allowed to be missing a
browser. But nobody can now read that green as "the viewer draws correctly", which
is the whole point.

The second is for machines that really should be able to draw — a CI runner, the
microscope PC. On those, set:

```
ZMART_REQUIRE_BROWSER=1 python -m pytest tests
```

and a run where the pixel tests did not happen **fails**, saying why. The project's
own CI sets it, which is what makes that job mean anything. Leave it unset on a
plain checkout and the run still passes, banner and all.

Both halves are themselves tested, in `tests/test_the_run_says_when_it_never_drew.py`.
Those tests start a second pytest on a machine arranged to look as though it has no
browser at all, and check that the banner appears, that the plain run still passes,
and that the strict one fails. A safeguard nobody has watched work is only a
comment.

## Keeping an eye on whether the viewer still draws quickly

`tests/test_the_drawing_keeps_up.py` measures how much of its own drawing rate the
viewer keeps when ten times as many positions are open. It is a comparison rather
than a number, so it means the same thing on a laptop and on the microscope PC — if
the whole machine is slow, both halves are slow and the ratio does not move.

There are two tests in it and they say different things. One holds the line where
the viewer is today, so that a further slide is noticed. The other states the rate
that is actually wanted and is **expected to fail**, because the viewer pays a cost
per position on every frame and that is not fixed; the test itself
says why, and why the fix is an architectural change. The day somebody does fix it, that
test will start passing, the run will say so, and the marker should come off.

## Confirming the graphics card is really being used

At the end of every run that opened a browser, the suite says which renderer
drew the pictures. For a second opinion outside the tests, open `chrome://gpu`
in the same browser and look for "Hardware accelerated" next to WebGL2.

## A note on speed

Where these tests are slow, it is almost always the **software** rendering path:
with no GPU, WebGL runs on the CPU, so the render tests take minutes rather than
seconds. That is a property of the machine, not the viewer — on hardware with a
graphics card the same tests, and the viewer itself, run far faster. The test
*results* (correct channels, safe serving, pixels reaching the renderer) hold on
any machine; only the *timings* change.

## On a managed Windows lab PC

A managed Windows PC may block programs that were downloaded into a user
profile or a temporary folder (AppLocker). Both the page build (Node, Vite,
esbuild) and the browser tests (Playwright's Chromium) start such programs, so
keep the Python environment, Node, the Playwright browser and the checkout
beneath a folder the PC allows. A checkout under `C:\tmp`, a mapped network
drive, or a browser download under `%LOCALAPPDATA%` may install without
complaint and then fail with `spawn UNKNOWN`. Point Playwright at an allowed
folder before installing its browser:

```bat
set PLAYWRIGHT_BROWSERS_PATH=C:\an\allowed\folder\ms-playwright
python -m playwright install chromium
```

## Seeing it for real

Testing aside, to actually look at a real acquisition through the viewer:

```
zmart-viewer /path/to/acquisition.ome.zarr
```

This opens the store through the neuroglancer engine, streaming it out-of-core,
in a native window (falling back to a browser).
