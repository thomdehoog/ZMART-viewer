# Start here — ZMART visualization work (index)

**If you are an AI agent or a new maintainer picking this up, read this file
first.** It is the map: what exists, why, and which document to open for what.
Read the linked docs in the order below.

## What this is, in one paragraph

This repository is the **viz-studio viewer**: a React app that embeds the
neuroglancer engine to view large, 3-D, multi-channel images (OME-Zarr),
intended to grow into the single image viewer for the whole ZMART workflow. It
runs with no microscope (demo mode). It was split out of the ZMART-microscopy
repository, which keeps everything that touches the instrument — the drivers,
the run control, the writer, and the mature **target-acquisition webapp** — and
which this repository depends on for following a live run (the
`zmart-microscopy` dependency in `pyproject.toml`).

## Read these, in this order

There are four, and they answer different questions. The list is deliberately
short. There are a dozen more documents beside this one, and they are listed
further down under "the rest of the documents"; the older planning documents are
listed after that as history, and reading those first would leave you with a
picture of the design that is a few months out of date.

1. **`README.md`** — what the viewer is and how to run the demo (build
   the frontend, launch the window). Read this to *run* it.
2. **`docs/how_it_works/DATA_LAYOUT.md`** — the design record: how a smart-microscopy run
   is written to disk and how the viewer presents it, with the reasoning for each
   decision and what was tried and rejected. One store per position, each carrying
   its own place on the stage; how much to open is the operator's choice; and the
   case to be good at is one image added to over time. Every claim about cost in it
   was measured rather than assumed. **Read this before changing how acquisitions
   are saved.**
3. **`docs/open/NEXT_STEPS.md`** — what is known to be unfinished or wrong, and
   what to pick up next. Read this before starting work, so you are not solving
   something already understood.
4. **`docs/how_it_works/TESTING.md`** — how to run the tests, and what each group of them
   is actually for.

If you want the reasoning behind the engine choice itself — why neuroglancer —
`docs/how_it_works/DRAWING_ENGINES.md` covers it, and
`docs/history/OPTIONS.md` records the three arrangements that were built and
measured since. The original engine-choice session write-up (2026-07-23) stayed
in the ZMART-microscopy repository with the rest of the pre-split history.

## Showing many positions as one picture

This is the largest single piece of work here and it has its own small
shelf of documents. The problem it solves: a plate of thousands of positions is
thousands of separate stores, and a drawing engine that builds layers per store
cannot open that. The answer is to leave the positions exactly where the microscope
wrote them and add a **view** beside them — a complete OME-Zarr image whose pixels
are the positions' own files, so nothing is copied at any zoom.

Read the first one; open the others when you have the question they answer.

| | The question it answers |
|---|---|
| `docs/open/HANDOVER_a_view_that_writes_nothing.md` | **Start here.** What was built, every measurement out to ten thousand positions, and the two things that still block real data. |
| `docs/history/PLAN_showing_many_stores_as_one.md` | **The next piece of work**, written out step by step: making a real acquisition open, when a drifting stage puts every position slightly off the grid. |
| `docs/how_it_works/HOW_OURS_DIFFERS_FROM_OME_ZARR.md` | Whether a colleague can open our runs in napari or Fiji. One real divergence, four that only look like one. |
| `docs/how_it_works/LESSONS_ome_zarr_and_neuroglancer.md` | The things that cost us time — each one a mistake actually made here rather than a general warning. Worth reading before writing any OME-Zarr of your own. |
| `docs/history/PLAN_nothing_copied_at_all.md` | Why a view need not write even the zoomed-out copies, and what the acquisition has to do for that to hold. |
| `docs/open/OPEN_a_run_that_changes_while_you_watch.md` | An open question: a new position works, but re-imaging one is not noticed by a viewer watching the run. |
| `docs/how_it_works/INTEROP.md` | What happens when other software reads what we write, measured against a real mesoSPIM transfer. |
| `docs/how_it_works/LINKING_INSTEAD_OF_COPYING.md` | How the idea was arrived at, including the claim that was wrong and what it cost. Reasoning rather than description. |
| `docs/open/HANDOVER_overlapping_runs.md` | The older arrangement that copies, and the measurements behind the decision not to. |
| `docs/history/WHAT_CAN_BE_SIMPLIFIED.md` | Where the code can get smaller, what was found broken while looking, and — the useful half — what looks like waste and is load-bearing. **Read section 4 before deleting anything.** |
| `docs/how_it_works/TILES_IN_ONE_STORE.md` | The bench measurements of three ways to give the viewer one source without discarding the overlap. |

## The rest of the documents beside this one

The four above are enough to run the viewer and to work on it. These others were
written since, each answering one question in depth, and they are listed here so
that a newcomer can find the right one rather than opening all of them. Read one
when you have the question it answers.

| | The question it answers |
|---|---|
| `docs/history/WHERE_THINGS_STAND.md` | What was done, measured and decided in the last long session, written for somebody arriving the next morning. **The best single thing to read after the four above.** |
| `docs/open/FAULTS.md` | What is broken and how we know. Every entry says whether it was measured or only reasoned about, and the measured ones carry the reproduction that was run. |
| `docs/how_it_works/ARCHITECTURE.md` | The shape the viewer is meant to have — a thin wrapper around a drawing engine — and where the code still departs from it. |
| `docs/how_it_works/DRAWING_ENGINES.md` | Which engine draws the picture, and why there may end up being two. |
| `docs/history/OPTIONS.md` | Three ways of putting acquired image underneath the operator's own drawing, built so they could be compared rather than argued about. |
| `docs/how_it_works/LAYERS.md` | What is drawn on top of what, and where the line falls between the engine's work and ours. |
| `docs/how_it_works/THE_CANVAS.md` | The shape the smart-microscopy front end is meant to take: three layers in one coordinate system. |
| `docs/how_it_works/CONTROLS.md` | How the viewer is driven — every mouse gesture and key, and why each is what it is. Flat view only. |
| `docs/history/LIVE_MODE_PLAN.md` | A proposal, not yet built, for one store per acquisition type declared at the start. Worth reading for what its reviewers disproved. |
| `docs/open/HANDOVER_3D.md` | Where three-dimensional navigation got to, and the three commands for getting the volume view up. |
| `docs/history/PLAN_three_unfinished.md` | Three pieces of work on the `viewer-plus-scanfields` branch, revised through four rounds of review. |

## Kept as history, not as description

These were written before or during the build and are left in place because the
reasoning in them is worth having. They are **not** descriptions of what exists
now, and each says so at the top. Read them if you want to know how a decision was
reached; do not read them to find out how the viewer works.

| | What it was for | What has since changed |
|---|---|---|
| `docs/history/PLAN.md` | The plan to review before building the spike: the stack, the risks, the sequence. | The spike was built. The architecture it describes is right; the storage layout it assumes is superseded by `docs/how_it_works/DATA_LAYOUT.md`. |
| `docs/measured/SPIKE_RESULTS.md` | What the spike established, honestly, including the worker-bundling bug. | Still accurate about the spike. The viewer has grown a great deal since. |
| `docs/history/INTEGRATION_PLAN.md` | The plan for turning the prototype into the real viewer. | Carried out, with the stage-moving part deliberately dropped. |
| `docs/history/INTEGRATION_ROADMAP.md` | The decision to make this the one image viewer for the whole workflow. | Still the intent. Not yet done. |
| `docs/history/PLAN_keeping_the_overlap.md` | Two ways of keeping the overlap, weighed against each other after two reviews. | Neither was built. Superseded by the view described above, which keeps the overlap by not showing it. |
| `docs/history/PLAN_the_pluggable_viewer.md` | A plan for making the drawing engine replaceable, rewritten against two critiques. | The reasoning stands; `docs/how_it_works/ARCHITECTURE.md` and `docs/how_it_works/DRAWING_ENGINES.md` describe where it actually got to. |

## Current status

- The control panel is **built**. The viewer renders the demo volume
  end to end (acceptance test PASS, 270/270 chunks) and offers per-channel
  visibility, colour and colour maps, contrast with a histogram and an auto
  button, opacity per channel and per acquisition type, synchronized Z and T
  controls, a 2-D/3-D toggle, segmentation masks, and a writable annotation layer
  with a targets list that saves beside the data. Acquisitions written while it is
  open are picked up on their own.

  The viewer deliberately does **not** talk to the microscope, and there is no
  `/api/goto`: targets are saved to a file and the control application acts on
  them. That separation is what lets the viewer be opened on any data, anywhere,
  including beside a running experiment. A test asserts that no
  stage-moving endpoint exists, so this cannot drift back.

  A run in progress is followed properly: the application driving the microscope
  says when a position is ready, the page is told over one connection it holds open,
  and only what actually changed is added to the picture rather than the scene being
  rebuilt. Closing an acquisition hands the memory back.

  What remains is moving the workflow's overview onto OME-Zarr/neuroglancer (see
  the roadmap), and writing the OME-Zarr ourselves rather than relying on what the
  mesoSPIM produces. Still demo and manual data only: not yet wired to the
  acquisition workflow. `docs/open/NEXT_STEPS.md` has the honest list.
- The native window has now been opened on Windows (2026-07-23) and works. That
  first run found the interaction bug described in `docs/measured/SPIKE_RESULTS.md`: the
  volume rendered but nothing responded to the mouse, because the default input
  bindings were never installed. Fixed, and now covered by
  `tests/test_interaction.py`.

## What stayed in the ZMART-microscopy repository

This repository was narrowed to the viewer on purpose, so the parts of ZMART
that drive the instrument are **not** here: `zmart_drivers`, `zmart_controller`,
the `workflows/` folder with the target-acquisition webapp, the writer in
`zmart_storage`, and the older `docs/` tree all live in ZMART-microscopy. The
`parked/` folder — the three-way drawing comparison, its harness, and the
prototype page — stayed there too, as did the probe write-ups (`SANDWICH.md`,
`LAYER_STACK.md`) and the pre-split branch history. So if a document beside
this one points at one of those paths and you cannot find it, that is why:
nothing has been lost, it is simply in the other repository.
