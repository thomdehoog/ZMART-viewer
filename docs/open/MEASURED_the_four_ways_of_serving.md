# Measured: the four ways of serving, small to ten thousand

> Written 2026-08-30 for the adoption question: *if ten positions a second
> come in, does the viewer at some point pay a price so everything becomes
> slow and unresponsive?* Harnesses in `measure/`:
> `measure_one_more_position.py`, `measure_the_four_ways_of_serving.py`,
> `measure_the_relinked_row.py`, `measure_loading_per_format.py`. Fixtures
> are written once and reused; storm replacements reuse the fixture's own
> folders, sentinel-filled so every storm ends with a freshness assertion
> against served pixels. A probe that answers absent is counted, never
> averaged in.

## The short answer

Correctness never degrades: at every size and rate the picture stayed
provably current (the freshness sentinel served after every storm), and
bursts coalesce into single derives instead of queueing. **New landings**
— the case smart microscopy lives in — are flat to 10,000 and cheap.
**Full-frame replacement storms** are where latency is bought: fine at
400, noticeable at 2,500, and at 10,000 the baked live picture stalls for
tens of seconds per derive while the unbaked one degrades to
half-a-second answers. The recipe that falls out: serve a live run
unbaked (`linked_view="at_run_end"`), bake once at run end (53 s at
10,000), and treat mid-run bake as a cold-open optimisation for quiet
runs, never for churning ones.

## One more landing, 400 to 10,000: flat where it must be

One position landing on a warm survey, five real writer landings per rung,
medians (`measure_one_more_position.py`):

| positions | writer landing | derive | tiles re-read | sweep counter |
|---:|---:|---:|---:|---:|
| 400 | 2,100–2,400 ms | 11 ms | 0 | 5N exactly |
| 2,500 | 2,149 ms | 25 ms | 0 | 5N exactly |
| 10,000 | 2,327 ms | 76 ms | 0 | 5N exactly |

The writer's landing cost is **flat**: landing onto 10,000 costs what it
costs onto 400. The derive re-reads **zero** tiles at every size — gated
count-wise in `test_one_more_landing_reads_one_tile_no_matter_the_survey`.
What grows is bookkeeping only: ~7 µs per position per derive (the world
frame is rebuilt each derive). Per landing nobody notices; it matters only
multiplied by a sustained commit rate, below.

## Serving a quiet survey (static rows)

Through the door's own functions, per piece:

| positions | mode | cold first | warm median | declare |
|---:|---|---:|---:|---:|
| 10,000 | baked (files) | 3.4 s | **0.15 ms** | 53 s fresh bake, once |
| 10,000 | unbaked (composed) | 13.1 s | 4.1 ms | 3.8 s |

A run that has grown past its recorded link map refuses zero-copy serving
until re-linked — governance failing closed, by design — and the governed
picture serves it meanwhile.

## The zero-copy linked row: two doors, one clear winner

After an honest re-link, byte ranges through the writer's gateway against
the viewer's own door (`pieces.link_a_finished_run`, this repo), measured
end to end, read included (`measure_the_relinked_row.py`):

| positions | door | link cost | first answer | warm median |
|---:|---|---:|---:|---:|
| 400 | gateway | 0.3 s re-link | 372 ms | 0.74 ms |
| 400 | **viewer** | 0.3 s | **7.9 ms** | **0.10 ms** |
| 2,500 | gateway | 2.1 s re-link | 4.3 s | 1.0 ms |
| 2,500 | **viewer** | 1.5 s | **57 ms** | **0.09 ms** |
| 10,000 | gateway | 20.8 s re-link | **47 s** | 3.1 ms |
| 10,000 | **viewer** | 4.8 s | **209 ms** | **0.10 ms** |

The gateway's first answer walks the run's whole history — the cost that
grows to a minute; the viewer's first answer reads its own map, built once
at link time with the shard indexes already inside. The viewer's link cost
is linear (0.3 → 1.5 → 4.8 s) and below the writer's own re-link at every
size. Staleness stays honest either way: a commit after linking silences
the viewer's pointers until the map is remade, and the governed picture
serves meanwhile — gated in the free-placement suite's linked row, along
with a whole-canvas byte sweep against the independent paste.

Three writer-side scale findings ride along, all handed over in
`HANDOVER_the_pointer_map_decides_on_day_zero.md`: constructing a
10,000-position `LivePublisher` cost 128 s at full CPU in
`place_the_positions`' quadratic neighbour sweep — **fixed and validated
here** (frame-sized buckets; placements proven identical; 128 s → 19 s,
the sweep itself 0.8 s), the patch in the handover; the per-landing
~2.3 s is flat but rate-limiting; and the gateway's first answer per
reader walks the whole history.

## The storm: 10 commits a second, a viewer watching the whole time

Full-frame **replacements** at 10/s for 300 commits — deliberately harsher
than reality: the real writer lands one position per ~2.3 s, so this is
the manifest pressed ~25× harder while every commit changes every pixel of
a frame. One thread commits, one thread asks the door for a coarse and a
level-0 piece. Every storm asserted freshness
(`measure_the_four_ways_of_serving.py`).

| positions | mode | coarse median (p90 / max) | level-0 median | derive under fire | commits absorbed per derive |
|---:|---|---|---:|---:|---:|
| 400 | baked | 44 ms (53 / 155) | 5.3 ms | 44 ms | 1.0 |
| 400 | unbaked | 4.3 ms (18 / 145) | 3.3 ms | 5.4 ms | 1.0 |
| 2,500 | baked | 138 ms (265 / 3,910) | 172 ms | 113 ms | 4.8 |
| 2,500 | unbaked | 180 ms (2,038 / 4,769) | 160 ms | 445 ms | 3.7 |
| 10,000 | baked | 3.5 s (7.0 / 7.0) | 15.8 s | 31.1 s | 150 |
| 10,000 | unbaked | 480 ms (1,906 / 7,401) | 487 ms | 410 ms | 2.5 |

What the numbers say:

- **The system never falls over.** Even the 31-second derive at the
  10,000-baked corner absorbed 150 coalesced commits and came back
  current; the ingest thread kept committing throughout. Falling behind
  makes the next derive absorb more, never queue more.
- **The spikes are the pyramid's top.** A full-frame replacement dirties
  one piece at *every* baked level, and the top piece spans the whole
  canvas, so the per-commit bake patch recomposes survey-wide ground.
  That is why baked-under-churn inverts: the mode with the fastest quiet
  reads (0.15 ms) has the slowest churn.
- **Unbaked live serving is the churn mode.** At 10,000 positions and a
  replacement rate 25× beyond the writer's real cadence it still answered
  in ~0.5 s median. At the real cadence (one landing per ~2.3 s, each
  touching only its own footprint) the derive is 76 ms and the answers
  are milliseconds.
- **Ingest throttles honestly under overload**: the unbaked 10,000 storm
  sustained ~3.2 commits/s, not 10 — compose work and commits share one
  process. A real deployment separates writer and viewer processes.

## If sustained replacement churn at 10,000 becomes a real workload

Two viewer-side improvements are known and scoped, in order of value:
move the per-commit bake patch off the derive (or bake all levels except
the top few), and reuse the installed world frame across derives when the
layout has not moved so the ~7 µs/position sweep becomes O(change).
Writer-side, ten commits a second needs parallel writers or a leaner
publish — `publish()` walks the full event history three times per commit
(handed over).

## Loading per format (static; live is the writer's own format)

`measure_loading_per_format.py` — nine scattered, overlapping positions
per case, three stamped bodies reused; every awkward store through the
same door:

| case | declare | linked cold | linked warm | bake | baked warm |
|---|---:|---:|---:|---:|---:|
| 0.4 scattered | 17 ms | 81 ms | 2.6 ms | 53 ms | 0.07 ms |
| 0.5 scattered | 12 ms | 67 ms | 2.6 ms | 46 ms | 0.07 ms |
| 0.5 grown (t, c) | 13 ms | 63 ms | 2.5 ms | 100 ms | 0.07 ms |
| 0.4 uint8 | 14 ms | 70 ms | 2.5 ms | 52 ms | 0.07 ms |

All sixteen loadable awkward stores (t-of-one, no pyramid, one channel
kept, one plane, uint8, float32 — each in both generations — plus
undressed, wrapped group, spaces and unicode in names) declare in 3–4 ms
and serve at ~2.5 ms composed / ~0.07 ms baked. The one refusal is the
flat two-axis store, refused **in words** at the built-picture door (it
still opens as a single source) — the honest refusal the free-placement
gate installed.

## What building this found

The benchmark's own crashes were fixture damage from killed harness
processes (partial generation copies), not viewer faults — and the viewer
refused every damaged store loudly, with the exact path, rather than
serving wrong pixels. The harness now verifies copies by file list, never
by the folder existing.
