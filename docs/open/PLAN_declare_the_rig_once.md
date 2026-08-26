# Plan: declare the rig once, and commit what points

> Written 2026-08-26, at the end of the day the replay stopped rewriting the
> dataset it was asked to show. The operator's sentence: *"it's just making a
> virtual view on the fly where you keep adding stuff and publish in the
> meantime — really it's not harder than that."* This is what falls out of
> taking that seriously.

## What is wrong, in one measurement

The replay now copies nothing, and still costs more than it should:

    declaring over all 256 positions, once      0.23 s
    the replay declared 256 times                 36 s

A declaration costs about 0.9 ms per position **present**, and the replay pays
it once per arrival over a growing set. That is N²/2 work where the default
door does N — at 256 positions, 128 times more than necessary, and every pass
re-reads positions it has already read.

This is a shape this project has met before. It is exactly the
`linked_view="per_publish"` fault: an O(survey) pass on every change, when the
change is one position. Same lesson, different file — **do work proportional
to what changed, not to what exists.**

## Why it re-declares at all

The building path has no notion of "showing". `declare_a_built_picture` reads
a folder and composes from whatever it finds, so the only way to make one more
position appear is to change what is in the folder — and changing the folder
means declaring again. Declaring over positions that are not there yet does
not work either: the declaration reads each one's description in order to
place it.

So the re-declaration is not laziness. It is the only lever the building path
offers.

## The other path already has the answer

A **governed** picture is declared once and a manifest says which positions are
currently visible. The rig is fixed from the first frame; each commit makes one
more position real. That is the live path, and it is what a microscope drives.

It lacks exactly one thing: the publisher can only make a position visible by
**writing** it. `LivePublisher` has one door for a position —
`write_a_position(position_id, pixels)` — and no way to say *this position is
visible, and its pixels are already over there*.

Everything else is in place. `zmart_storage.linked` has `PlacedTile` and
`LinkedView`; pointing is what the whole project is built on. The publisher is
the one component that cannot.

## The change

Give the publisher a second door: **commit a position that points.**

    run.adopt_a_position(position_id, store, timepoint=0)

It does everything `write_and_publish` does except write pixels: places the
position, records where its bytes are, and commits. The manifest, the
visibility rules, withdrawal, replacement, later-wins overlap and rollback all
work unchanged, because none of them care who wrote the bytes.

Then the three things this repository does become one mechanism:

| | rig | commits |
|---|---|---|
| normal viewing | declared once | all of them, at once |
| a replay | declared once | one at a time, on a schedule |
| a real acquisition | declared once | one as each is written |

Same picture, same path, same code. The only differences are *when* the
commits happen and *who* wrote the pixels. The replay stops being a separate
tool and becomes the live path with a timer.

## What it buys

- The re-declaration disappears. A replay costs one declaration and N cheap
  commits: seconds, not 36, and it stops getting quadratically worse.
- The building path's growth machinery can retire. One way to grow a picture,
  not two.
- A replay finally rehearses the thing worth rehearsing — the manifest, the
  commits, the visibility gate — which the folder-and-re-declare version does
  not touch at all.

## What has to be settled before it is built

1. **Where a pointed position's bytes are recorded.** The manifest names
   positions; it will have to name a path for adopted ones, and refuse a path
   that moves.
2. **Whether the composer can read a position outside the run's own folder.**
   It reads placed tiles today; this is the same question the traversal guard
   answers for the server, and the answer must be one rule, not two.
3. **What happens when an adopted position's store disappears** mid-run. A
   written position cannot vanish; a pointed one can. Fail closed, and say so.
4. **The coarsest pyramid level.** The operator's own note: it wants baking on
   the fly. That is a separate chapter and does not block this one.

## Not doing

- Making the building path grow incrementally. It would work, and it would
  leave two ways of growing a picture. The point of this is to have one.
- Touching the writer's pixel path. Nothing here changes how a microscope's
  bytes are written.
