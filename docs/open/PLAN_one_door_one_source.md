# One door, one source: the data-loading refactor

> Written 2026-08-29, after reading the code and the design record as they
> stand on this branch. This is a plan, not a description of what exists.
> It answers one request: a unified way to load data — linked or baked —
> that is the same way whether the data is finished or still being
> acquired, organized into about ten self-contained files, ready to be
> embedded in the operator window later. Nothing here contradicts the
> decisions already made in `docs/how_it_works/ARCHITECTURE.md`,
> `docs/open/DECISION_finish_the_migration_to_one_live_path.md` or
> `docs/open/PLAN_two_viewers_one_contract.md`; it finishes them.

## The want, stated once

Loading data into the viewer should be one operation with one choice.
Point it at data, and it becomes a view: either **linked** — a virtual
OME-Zarr whose pieces are the positions' own bytes, built in a second,
nothing copied — or **baked** — the same view with its coarse pyramid
computed once and kept as files, which is what a survey at scale wants.
Either way the viewer opens it with the right settings (the display
window, the channel names and colours, the five axes t, c, z, y, x, the
voxel sizes and translations), efficiently, at any scale.

A live acquisition is not a different operation. The view is made the
same way, on the fly, at the start of the run; from then on there is
**one source per acquisition**, and a change to the run updates that
source in place and invalidates what the browser holds of it. If it
works for plain loading, the same path works for live — live is just a
source whose revision still moves.

Later — not in this plan's chapters, but shaping them — the same canvas
is embedded in the operator window in place of the JPEG overview,
exactly as `PLAN_two_viewers_one_contract.md` lays out. So the pieces
must end up importable and self-contained.

## The rule this plan is written under

**Simple by having one path, never by dropping what opens today.**
Everything the viewer opens now — a single store, a folder of position
stores, a plate, a transfer, a live run, a replay, a built scene —
still opens after every chapter below. What gets removed is mechanism,
not capability: the second and third way of doing the same thing, the
watcher beside the watcher, the cache with its own private forgetting.

The same goes for robustness. A stack of catch-and-fall-back layers is
not robustness; it is the place where breakage hides. Where the code
today tries one thing, catches broadly, and quietly does another (the
run door's `except Exception: open the positions instead` is the
pattern), the one door instead **classifies first and acts once**: it
decides what the path is, does the one right thing for that kind, and
when something genuinely cannot be opened it says so plainly at the
door — the style the plate refusal and the relink refusal already have.
Backup mechanisms that exist to absorb the failures of a mechanism
beside them go away with that mechanism; they are the skew the operator
has been feeling.

## What the investigation found

**Loading is one idea implemented in six places.** A path becomes a
picture through `/api/stores/open` (which quietly resolves plates and
runs of positions through `_the_scene_behind_a_plate` and
`_the_scene_behind_a_run`, both living inside `server.py`), through the
build tab (`/api/stores/construct`), through the replay door, through
the CLI `--data` argument at startup, through the live registry binding
a run, and through the demos. Each door repeats part of the
classification — is this a store, a folder of stores, a plate, a
transfer, a live run, an already-built scene? — and they have already
drifted once (the run door that answered "no OME-Zarr image was found"
to a finished replay, found on the workstation 2026-08-19).

**The two products already exist; they are not yet one choice.** The
linked view (`zmart-links.json`, answered by `linking.py`) and the
declared/composed picture (`declare.py`, `composer.py`, `served.py`,
with the bake as its hard-copy option) both work and both are measured.
But which one a given door produces is decided differently at each
door, and the bake is asked for in three different vocabularies
(`include a hard copy` on the build tab, `bake` in the open payload,
`bake=` on the live declaration).

**Serving answers the same question four ways in a fixed order.**
`_serve_from_data` tries the live gateway's byte-range answer, then the
pointer map, then the composed piece, then the manifest-governed file.
The order is correct and the code is careful; what is wrong is that the
four answers live in three files plus the server, so nobody can read
"where does a piece come from" in one place.

**Invalidation is many mechanisms where one is needed.** This is the
finding that matters most. The static path needs almost nothing: files
never change, the browser caches, done. The moment a run is replayed or
watched, all of this lights up at once:

- a `FolderWatcher` inferring changes from the disk,
- a `ManifestWatcher` reading the publication marker,
- SSE announcements nudging the page,
- `/api/live-state` with its own schema and client-side safety rules
  (`live-refresh.js`), per-source revisions remembered in a `WeakMap`,
- `invalidateCache()` calls on the engine's sources,
- and four server-side caches — the contrast measurements
  (`forget_measurements`), the described-store cache
  (`forget_described`), the composer's slab cache and its piece index —
  each with its own private forgetting.

Each piece was added for a reason and each reason was real. But they
add up to a second, heavier viewer that exists only while data moves,
and that is the complexity the operator feels. The one mechanism that
is actually right — a source keeps its URL, its revision advances, the
client drops only that source's chunks — already exists inside
`engine.js` and `live-refresh.js`. The plan below keeps that one and
retires the rest into it.

**Settings mostly travel correctly, with one known structural fault.**
Channel names and colours are read from inside the stores; the display
window comes from the store's own `window` where it declares one, else
from `contrast.py` measuring pixels in Python. `ARCHITECTURE.md` §2
already establishes that measuring in Python is the one violation of
the wrapper rule and traces nearly every contrast bug to it, and
records (verified 2026-08-21) what moving it into the engine takes.
The axes contract is right — t, c, z, y, x, singletons included — and
growth along c and t through the served picture is planned but not
built (`PLAN_the_picture_grows_c_and_t.md`).

**The code is not yet shaped like something we would hand over.** The
backend is fourteen files importing each other by bare name through
`sys.path` insertions; `pyproject.toml` honestly says `packages = []`.
`server.py` is 2,731 lines holding HTTP plumbing, scene resolution,
build- and replay-job management, measurement orchestration and the
whole config document. `ARCHITECTURE.md` §8 draws a file map that names
files which no longer exist. And the migration decided on 2026-08-13 is
landed in `live_config.py` but its decision doc still lists a test
(`test_manifest_refresh_browser.py`) pinning the wiring it replaced.

**What already works must be treated as load-bearing.** The live
display scripts exist, are committed, and work — the replay, the demos
(`run_live_run_demo.py`, `show_a_run_growing.py`), the measured
zero-transient churn at 6,400 positions, the flat per-publish writer.
The refactor moves code; it must not lose a single one of those
behaviours, and the test suite plus the `measure/` harness are the
instruments that hold it to that.

## The target

### One door

One function, in one file:

    load(path, *, bake: bool = False) -> Dataset

It classifies the path — single store, folder of position stores, HCS
plate, transfer, live run root, or an already-built view — reuses an
existing view when one honestly matches, builds one when none does
(linked always; baked when asked), and hands back a dataset holding
**exactly one source**: the view store. Every entry — both UI tabs, the
CLI, `/api/stores/open`, the replay, the live binding, the demos —
calls this function and nothing else. The classification logic exists
today; it is gathered, not invented.

The choice is one boolean with one name, `bake`, everywhere it appears:
build tab checkbox, open payload, live declaration. Linked is the
default because it is instant and copies nothing; baked is the
recommendation the build tab already makes for anything an operator
will return to. Both produce a view a later `load` opens identically —
a view is self-contained and names its data, per the contract.

### One source, one invalidation

Live stops being a mode. The server keeps a **source registry**: every
open dataset's one source, with a monotonically increasing revision.
A static source simply never advances. When a run's record moves — the
publication marker changes, or an announcement arrives — the registry
bumps that source's revision and sends one message over the one SSE
channel: *source N is at revision R*. The client's whole live logic
becomes: same URL, higher revision → `invalidateCache()` on that
source's layer; nothing else changes, nothing is rebuilt. That code
exists in `engine.js`; what is deleted is everything around it — the
separate folder-versus-manifest watcher split becomes an internal
detail of the registry, and the live-state document shrinks to the
registry's plain answer.

Server-side caches follow the same rule instead of each owning a
forgetting: every cache keys on (source identity, revision). A bump
makes stale entries unreachable and the bespoke `forget_*` calls go.

The replay then needs nothing of its own: it writes through the real
publisher, the record moves, the registry notices, one message goes
out. A rehearsal exercises the identical path an acquisition does —
which was always its point — and adds no machinery.

### Settings come from the view

A view's description carries everything the frontend needs to open it
right: axes (always the five, in order), scales and translations per
level, channel names and colours, and the display window the store asks
for. Python-side pixel measurement (`contrast.py`) is demoted to the
fallback for stores that declare no window, and the separate chapter
`ARCHITECTURE.md` §2 already specifies — moving the histogram onto the
engine's GPU — deletes it entirely when that afternoon is spent. This
plan does not do that work; it only stops anything else depending on
where the window comes from.

### Five dimensions as an invariant, not a feature

Every view is declared t, c, z, y, x whatever the data holds, and no
stored unit spans t or c (the contract's standing gate). The one door
asserts it; the composer and the bake preserve it. The c-and-t growth
work stays its own plan and rides this one path when it comes — one of
the reasons to have one path is that feature work lands once.

## The file map: about ten, each self-contained

One installable package, `zmart_viewer/`, real imports, no `sys.path`
insertions, `pyproject.toml` packaging it. The frontend keeps its look
and its files — the interface is liked as it is — with `engine.js` and
`live-refresh.js` shrinking to the one invalidation rule.

| File | Job | Built from |
|---|---|---|
| `server.py` | HTTP only: routes, static files, ranges, caching headers, SSE, the traversal guard | `server.py` (plumbing) |
| `api.py` | the JSON endpoints, each a thin call into the modules below; build/replay job bookkeeping | `server.py` (handlers) |
| `library.py` | what is open: datasets, numbering, acquisition identity read from inside stores | `library.py`, `stores.py` |
| `loading.py` | **the one door**: classify a path, reuse or build its view, return the dataset | scene logic now in `server.py`, callers of `declare` |
| `building.py` | writing a view: declare the description, write the link map, bake the pyramid, patch per commit | `declare.py`, bake half of `governed.py` |
| `pieces.py` | answering "where is this piece": pointer, composed, or governed bytes — the serving ladder in one place | `linking.py`, `served.py`, serving half of `governed.py` |
| `compose.py` | geometry and on-the-fly building: the mosaic arrangement and the composer with its slab cache | `mosaic.py`, `composer.py` |
| `live.py` | the source registry: revisions, the record watch, announcements in and the one message out | `live_config.py`, `announcements.py` |
| `settings.py` | the config document for the frontend; display windows, with measurement as fallback | `contrast.py`, `build_config` from `server.py` |
| `rehearsal.py` | the replay, unchanged in spirit: real publisher, one position at a time | `rehearsal.py` |
| `launcher.py` | the window and the CLI entry | `launcher.py` |

Eleven, honestly counted, from fourteen — and `server.py` falls from
2,731 lines to plumbing. Each file is readable alone: one job, its own
docstring stating it, importable by the operator window without
dragging the rest along. If a later pass wants fewer still, `api.py`
folds into `server.py` and `settings.py` dies with `contrast.py` when
the engine takes the histogram; the map above is deliberately not
squeezed past what keeps each file one idea.

The boundaries `PLAN_two_viewers_one_contract.md` needs fall out
directly: `building.py` is the view builder a CLI can wrap; `pieces.py`
+ `server.py` are the serving doors; the frontend build is the canvas
component the operator window embeds.

## The order of work

Each chapter leaves the whole test suite green and the demos working;
none starts before the previous one has. The suite (a hundred-odd
files) and the `measure/` numbers — zero transients on the watched
churn, the flat writer, landing-to-visible around a quarter second at
6,400 — are the gates, and a chapter that cannot keep them does not
land.

**1. The package.** Mechanical: create `zmart_viewer/`, move the
fourteen files under it unmodified, fix imports to real ones, delete
the `sys.path` insertions, set `packages` in `pyproject.toml`, point
the scripts and tests at the package. No logic changes. This chapter
exists so that every later diff is about behaviour, never about paths.

**2. One door.** Extract `loading.py`; move the plate/run/live/view
classification out of `server.py` into it; make every entry call it;
unify the bake flag under one name. Delete the duplicated resolution.
A new test opens the same folder through every door and asserts the
identical view is reached.

**3. One source, one invalidation.** Build the source registry in
`live.py` over the code already there; fold the folder and manifest
watchers into it as inputs; reduce the live-state answer and
`live-refresh.js` to URL + revision with the existing safety checks on
identity and regression kept; retire `test_manifest_refresh_browser.py`
as its own decision doc already instructs, replaced by a test that
drives a replay and counts exactly one message per commit.

**4. Caches key on revision.** The contrast cache, the described-store
cache and the composer's indexes adopt (source, revision) keys; the
`forget_*` calls and their call sites go. This is where the felt
complexity actually leaves.

**5. The merge into the map.** Collapse to the file map above:
`pieces.py` gathers the serving ladder, `building.py` the writing,
`api.py` the handlers. This is the chapter that reads as "the refactor"
but is deliberately last of the code chapters: by now every seam it
cuts along already exists.

**6. Settings.** The view's description becomes the one place the
frontend reads its opening settings from; measurement becomes the
declared fallback. The engine-histogram afternoon from
`ARCHITECTURE.md` §2 stays parked as its own piece of work with its own
gate (a 16-bit specimen photographed before and after).

**7. The operator window.** Out of scope to build here, but chapter 5's
boundaries are checked against it: the canvas component builds and
mounts standalone against a running server, so replacing the JPEG
overview is an embedding task, not another refactor.

## What is deliberately not done

- No engine workarounds and no chasing the engine's limits — §2's rule
  stands, and the scale items it struck stay struck.
- No second contract, no mode flag, no live switch: live remains a fact
  the record reveals, observed by the registry.
- No new UI. The interface stays as it is; only the load window's two
  build vocabularies become one.
- No rewrite of the composer, the gateway or the publisher. The
  measured, review-hardened cores move house; they do not change.
