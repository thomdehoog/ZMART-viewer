# Measured: the four ways of serving, small to ten thousand

> Written 2026-08-30 for the adoption question: *if ten positions a second
> come in, does the viewer at some point pay a price so everything becomes
> slow and unresponsive?* Harnesses in `measure/`:
> `measure_one_more_position.py`, `measure_the_four_ways_of_serving.py`,
> `measure_the_relinked_row.py`, `measure_loading_per_format.py`. Fixtures
> are written once and reused; storm replacements reuse the fixture's own
> folders, sentinel-filled so every storm ends with a freshness assertion
> against served pixels.

## One more landing, 400 to 10,000: flat where it must be

One position landing on a warm survey, five real writer landings per rung,
medians:

| positions | writer landing | derive | tiles re-read | sweep counter |
|---:|---:|---:|---:|---:|
| 400 | 2,100–2,400 ms | 11 ms | 0 | 5N exactly |
| 2,500 | 2,149 ms | 25 ms | 0 | 5N exactly |
| 10,000 | 2,327 ms | 76 ms | 0 | 5N exactly |

The writer's landing cost is **flat**: landing onto 10,000 costs what it
costs onto 400. The derive re-reads **zero** tiles at every size — gated
count-wise in `test_one_more_landing_reads_one_tile_no_matter_the_survey`.
What grows is bookkeeping only: ~7 µs per position per derive (the world
frame is rebuilt each derive), 76 ms at 10,000. Nothing anyone notices per
landing; it matters only multiplied by a sustained commit rate, below.

## The storm: 10 commits a second, a viewer watching the whole time

Full-frame **replacements** at 10/s for 30 s — deliberately harsher than
reality (the real writer lands one position per ~2.3 s; this is the
manifest pressed ~25× harder while every commit changes every pixel of a
frame). One thread commits, one thread asks the door for a coarse and a
level-0 piece. Every storm asserted freshness: the last sentinel really
served.

| positions | mode | coarse piece median (p90 / max) | level-0 median | derive under fire | commits absorbed per derive |
|---:|---|---|---:|---:|---:|
| 400 | baked | 44 ms (53 / 155) | 5.3 ms | 44 ms | 1.0 |
| 400 | unbaked | 4.3 ms (18 / 145) | 3.3 ms | 5.4 ms | 1.0 |
| 2,500 | baked | 138 ms (265 / 3,910) | 172 ms | 113 ms | 4.8 |
| 2,500 | unbaked | 180 ms (2,038 / 4,769) | 160 ms | 445 ms | 3.7 |
| 10,000 | baked | (see matrix log) | | | |
| 10,000 | unbaked | (see matrix log) | | | |

What the numbers say:

- **Correctness never degrades.** At every size and rate the picture is
  current — coalescing absorbs a burst into one derive (4.8 commits each at
  2,500), and the freshness sentinel is served every time.
- **Latency does degrade under full-frame churn.** At 2,500 positions and
  10 full replacements a second, piece answers slow to ~150–180 ms with
  multi-second spikes. The spikes are the pyramid's top: a full-frame
  replacement dirties one piece at *every* baked level, and the top piece
  spans the whole canvas, so patching it recomposes survey-wide ground.
- **Baking is for quiet data, not churn.** Under storm, the baked picture
  pays the patch on every derive; unbaked serving pays only the pieces
  actually asked for. Baked coarse reads when quiet: ~0.2 ms. Unbaked
  coarse when quiet: ~3–12 ms warm, seconds cold. The settled recipe
  stands: serve live runs unbaked (`linked_view="at_run_end"`), bake once
  at run end — mid-run bake buys cold-open speed and costs churn speed.
- **New landings are not replacements.** A landing composes only its own
  footprint (the gates above); sustained 10/s *landings* are writer-bound
  long before the viewer notices (the writer needs ~2.3 s per landing).

## The zero-copy linked row, and the gateway's price

The writer's linked plain-file view (`live.ome.zarr` + links) refuses once
the run has grown past its recorded link map — governance failing closed,
by design; the governed picture serves such a run. After an honest re-link
the byte-range door serves again (`measure_the_relinked_row.py`).

Two writer-side scale findings, handed over in
`HANDOVER_the_pointer_map_decides_on_day_zero.md`:

- `answer_from_a_live_run` costs **O(events) per request**: ~9 ms at 400,
  ~261 ms at 2,500, ~5.9 s at 10,000 per piece answer. External readers of
  a big run's linked view pay it on every chunk. The viewer's own governed
  serving does **not** pay this (a governed piece answers in ~1 ms).
- The real writer's per-landing ~2.3 s is flat but rate-limiting: ten
  landings a second needs parallel writers or a leaner publish
  (`publish()` walks the full event history three times per commit).

## Loading per format (static; live is the writer's own format)

See `measure_loading_per_format.py` — 0.4, 0.5, grown (t, c), uint8, and
every awkward store, each declared linked and baked through the one door,
cold and warm serving sampled. Results recorded beside the matrix log.
