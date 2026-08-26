# How a replay shows a dataset arriving, one position at a time

A replay takes a folder of positions that already exists and shows them
appearing on screen one after another, in the order the stage scanned them —
a survey filling in, with no microscope in the room.

    python demos/replay.py "D:/some-scan" --every 1

That is the whole command. It declares the picture, opens its own window,
waits for the page, and reveals. Nothing is copied: the dataset is left
exactly as it was found, and what the replay writes is the picture's
description, kilobytes of it.

| flag | what it does |
|---|---|
| `--every` | seconds between positions (default 1). See **the limit** below. |
| `--settle` | how long the empty room is shown first (default 2) |
| `--picture` | where the description goes (default: beside the dataset) |
| `--range LOW,HIGH` | brightness, if the measured one is wrong |
| `--port` | which door the viewer answers on (default 8848) |
| `--chrome` | neuroglancer's own bounding box and axis lines. **Not** "use the Chrome browser." |

Measured on the real Thy1 transfer — 6 tiles, 38.0 GB — a replay writes
0.02 MB and touches the dataset not at all.

---

## The two ideas it is built from

### 1. The room is declared before anything is shown

Every position goes in first as a **description alone**: its `zarr.json` and
one per pyramid level, copied into a folder of stubs. Eight kilobytes standing
for a seven-gigabyte tile. Only then is the picture declared over them, so it
has its final extent before a single pixel exists.

This is not an optimisation. It is forced, by the page:

> Neuroglancer draws what fits inside the size it currently believes the
> picture is, and it learns a new size only by **re-resolving its source**.
> Re-resolving throws the loaded source away before it reads the new
> description, so there is a gap with nothing to draw.

That gap is the flicker of 2026-08-23, measured at 100–300 ms of black on
every landing. A picture whose extent never changes never asks the question.

Both metadata generations are understood: version 3's `zarr.json`, and version
0.4's flat `.zattrs` beside `.zgroup` and a `.zarray` per level.

### 2. A position arrives as a rename, never a copy

Revealing swaps the stub for a junction pointing at the real position:

```
_junction(spot.arriving, position)   # mklink /J — the real pixels
os.replace(spot, spot.leaving)       # atomic
os.replace(spot.arriving, spot)      # atomic
shutil.rmtree(spot.leaving)
```

Two renames rather than "delete the stub, then link", because deleting first
leaves a moment when the path resolves to nothing at all, and a request landing
in that moment gets an exception instead of "nothing here".

The order is read from each position's own translation, not from its filename —
an exporter's naming is whatever it felt like, and watching a survey fill in
that order tells an operator nothing.

---

## Making the new pixels visible: touch, do not re-declare

A served picture is remembered until the **stat of its own description moves**
(`app/picture/served.py`, `_the_pictures_mark`). That mark is how the server
knows to build the picture again and pick up what has landed.

Every arrival used to re-declare the whole picture to move it. But the room is
declared from every position up front, so a stub and the real tile describe the
same shape in the same place — measured, the file that came out was
**byte-identical**. The re-declaration's only effect was the timestamp, and it
paid a read of every position's description to achieve it.

    a reveal, at 25 / 100 / 400 positions
      re-declaring   90 / 220 / 616 ms     grows with the survey
      touching       20 /  20 /  19 ms     flat

The touch itself is 62–228 µs; the 20 ms that remains is the reveal — two
renames and dropping the stub. A 400-position replay went from 246 s of reveal
work to 7.8 s, and what would have been about 39 hours at 10,000 positions
became about three and a half minutes.

Pinned by `test_a_replay_declares_the_picture_once`, which **counts
declarations rather than timing them**: a clock says "fast on this machine
today", a count says "revealing one position does not read all of them".

---

## The limit, which is the page's and not the replay's

An announcement invalidates the **whole** picture — `letGoOfDecodedPieces`
drops every decoded holder in the scene — so the page re-composes pieces that
did not change. Only what is on screen is refetched, about nine pieces, and
that number follows the window rather than the dataset. But each is composed
cold:

| | cold | warm |
|---|---|---|
| coarsest piece | 92–220 ms | 9–85 ms |
| piece at full detail | 94–703 ms | 6–33 ms |

Nine pieces at 100–200 ms, partly in parallel, is roughly **half a second per
full refresh** — about 2 per second, the same ceiling found from the writing
side on 2026-08-12.

So:

- **1 s** between positions: comfortable.
- **0.5 s**: the floor. Still one-by-one.
- **0.25 s and below**: announcements arrive faster than a refresh completes,
  each one restarting the last. Nothing shows until they stop, and then
  everything appears at once.

The cure is region granularity — invalidate the patch that changed, not the
scene. Not a faster reveal: the reveal is never the cost.

Baking would make each piece cheaper to fetch but not make the page fetch
fewer of them, so it raises the ceiling rather than removing it. Deferred.

---

## What was tried first, so it is not tried again

| how the picture grew | extent | what an operator saw |
|---|---|---|
| far corners down first | full from frame one | grows — but starts with three tiles already showing, in a nonsense order |
| row-major, top-left first | starts one tile wide | everything after the first falls off the edge until F5 |
| **every position declared, then revealed** | full from frame one | **grows correctly from empty** |

Two dead ends came with the first two, both from guessing which positions bound
the picture: reading a corner off the `Tile` instead of `tile.copies[0]`, and
assuming two opposite corners bound a box when one position can hold the
smallest *y* and another the smallest *x*. Declaring the room removed the
question rather than answering it.

An earlier replay did the opposite of all this: it handed every position's
pixels to the live writer, which wrote them all again with a fresh pyramid —
1.25× the dataset on disk, 161 s for 87 MB, and it inherited every rule the
writer has (a grid to sit on, a legal chunk size, whole-number halving, an
overlap band that fits), so it refused datasets the viewer opens without
complaint. None of that is needed to look at data that already exists.

`adopt_a_position` — letting the publisher commit a position that points
instead of writing — was designed to make the re-declaration cheap. The touch
made it unnecessary. It is recorded in `docs/open/PLAN_declare_the_rig_once.md`
as a design idea with nothing depending on it, along with the reason it could
not have served this replay anyway: a live position is `(t, c, z, y, x)` on the
run's own profile, an ordinary OME-Zarr tile is `(z, y, x)`, and adopting
arbitrary data would have meant conforming it — which is the copying that was
removed.

---

## Making a bigger survey to test with

There is no large multi-position dataset on this workstation, so one is made by
pointing many descriptions at a few real tiles: per slot, a `zarr.json` of its
own carrying the slot's translation, and a junction per pyramid level to a real
tile's pixels. 100 positions cost 136 KB and stand for 634 GB.

Beware measuring the result with `rglob`: a Windows junction does not report as
a symlink, so the walk goes straight through it and reports the whole dataset.
Count only the files directly inside each slot.

---

## Where the pieces live

| | |
|---|---|
| the tool | `demos/replay.py` |
| the gates | `tests/test_a_dataset_is_relived_as_a_live_run.py` (six) |
| what makes a picture rebuild | `app/picture/served.py`, `_the_pictures_mark` |
| what an announcement drops | `app/page/src/engine.js`, `letGoOfDecodedPieces` |
| the remaining limit | `docs/open/PLAN_declare_the_rig_once.md` |
