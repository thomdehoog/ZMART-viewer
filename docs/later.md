# Things for later

Ideas and improvements that were discussed and deliberately left for later.
Each says what happens today, what would change, and why it waits. When one
is taken up, it moves out of this list and into the work.

## Memory is limited per picture, not per viewer

**Today.** Each open picture may keep up to 1 GiB of decoded image blocks and
256 MiB of working space in memory, and the viewer remembers up to 200,000
array descriptions. One picture stays well within a microscope computer's
memory. A viewer left open for a long session with many pictures open adds
these limits up, picture by picture.

**What would change.** The viewer would have one budget for all its pictures
together, and would let go of the blocks looked at longest ago, whichever
picture they belong to.

**Why it waits.** It has not been a problem in practice, and choosing one
budget well needs a measurement of long sessions on a real microscope
computer. Raised as N7 in `REVIEW_2026-10-10.md`.

## Python 3.13 and newer

**Today.** The viewer installs on Python 3.11 and 3.12 only
(`requires-python = ">=3.11,<3.13"`). Forced onto Python 3.13 it installs,
imports and starts, but its tests have not been run there.

**What would change.** The tests would be run on Python 3.13, and the upper
limit raised once they pass.

**Why it waits.** It needs a Python 3.13 environment with the browser tests
working, and the other ZMART parts the viewer is used with should move at the
same time. Raised under S11 in `REVIEW_2026-10-10.md`.

## The record of a live run is rewritten whole on every commit

**Today.** While a run is live, every commit rewrites `signed.json`, the small
file that says which positions have been written, and pushes it to disk. It
lists every position of the run, so it grows with the run, and so does the
cost of rewriting it on each commit.

**What would change.** Each commit would add only what changed, or the
record would be split so that one commit touches only a small file.

**Why it waits.** It matters only for very large surveys, many thousands of
positions, and it changes the record that a run's safety rests on, so it
needs its own careful design and test. Raised under N5 in
`REVIEW_2026-10-10.md`.

## The coverage program lives in each page instead of in the engine

**Today.** The program that paints acquired ground, so a picture can be drawn
see-through where nothing was imaged, is written out in this window's page
(`gui/source/drawing/layers.ts`) and again in ZMART-interface's engine. It is
part of how the engine draws, like the programs in `engine/drawing/programs.js`,
not a choice either page makes.

**What would change.** It would move into `engine/drawing/`, and both pages
would take it from there, as part of the drawing primitives both pages are to
share. The colours each page offers -- its palette, how a picked colour is
spelled, where the contrast window sits over its histogram -- are not part of
this: each page owns its own controls and tells the engine the values to draw
with (decided with Thom, 2026-10-18).

**Why it waits.** It changes what the interface takes from the viewer, so it
moves with the primitives and an interface release.

