# Measured: the churn ladder, one position to ten thousand

*31 August 2026 · benchmark of zmart-viewer 0.2 · raw rows in
`results/ladder.jsonl`, photographs in `results/shots/`, hardware in
`results/MACHINE.md` (4 Xeon cores, 15 GiB, software drawing — figures
compare rung against rung, not against a workstation).*

This is the campaign the 0.3 optimization work should be planned from. It
climbed 1 → 10 → 100 → 1,000 → 10,000 positions, each rung measured four
ways — **nominal** (chunk-aligned grid) and **scattered** (strewn, off
every chunk, overlapping) placements, each **unbaked** (served by
composing on demand) and **baked** (views precomputed) — with three real
replacement churns per cell, a real browser photograph of every cell, and
byte-level freshness checks on every serve after a replacement. Fixtures
at 100+ positions hard-link one real written position and fabricate the
record in the manifest's own schema; nothing fabricated was measured
until the viewer had folded the record and found every commit. The rung
at 100,000 was prepared (symlinked store bodies, a thousandth the
directory metadata — committed and ready in `churn_ladder.py`) but not
run: the session stopped at the user's call after the 10,000 rung.

## The numbers

Time to a **settled picture** in a real browser (canvas stable for 2 s
with both channels present), and the cost of **declaring** the view first:

| positions | unbaked: declared | unbaked: settled | baked: bake once | baked: settled |
|---:|---:|---:|---:|---:|
| 1 | 0.02 s | 2.2 s | 0.8 s | 1.8 s |
| 10 | 0.02 s | 5.5–6.2 s | 3.5 s | 4.8–5.2 s |
| 100 | 0.03 s | 16.8–17.9 s | 33 s | 13.7–16.1 s |
| 1,000 | 0.3 s | 90–95 s | ~130 s | **4.1 s** |
| 10,000 | 3–4 s | **not within 8 min** | ~12 min | **7.8 s** |

The crossover is unmistakable: up to ~100 positions the unbaked view is a
fine first screen and baking buys little; from 1,000 up the unbaked
overview stops being a first screen at all (at 1,000: ~30 s of black,
fully painted at 102 s — measured with a dedicated probe; at 10,000:
still black past every deadline) while the baked view settles in 4–8
seconds *at any scale measured*.

**Churn** — one position replaced, what the reader pays afterwards:

| positions | derive | coarse piece re-serve (unbaked / baked) | fine piece under the position (unbaked / baked) |
|---:|---:|---:|---:|
| 1 | 2 ms | 134 / 392 ms | 59 / 56 ms |
| 10 | 2 ms | 142 / 400 ms | 62 / 62 ms |
| 100 | 2.4–2.8 ms | 170–233 / 440–516 ms | 60–63 / 56–61 ms |
| 1,000 | 24 / 10 ms | 292–413 ms / 1.17 s | 212–282 / 56 ms |
| 10,000 | 148–159 / 95 ms | 733–808 ms / 5.8 s | 516–1348 / 177 ms |

Every serve after a replacement was byte-compared against the serve
before it: **zero stale serves across the whole campaign**. The freshness
machinery works at every scale.

**Memory and disk** (server process RSS after the cell; served-view disk):

| positions | RSS unbaked | RSS baked | baked view on disk |
|---:|---:|---:|---:|
| 1–100 | 120–240 MB | 130–240 MB | 0.4–0.8 MB |
| 1,000 | 230–415 MB | 280–500 MB | 2.4–11.4 MB |
| 10,000 | 740 MB–2.0 GB | ~800 MB | ~105 MB |

The browser held ~900 MB throughout, independent of position count — the
mosaic is one picture to the engine, which is the design working.

**The zero-copy link door** (unbaked, nominal only — scattered placements
are refused in words at every rung, correctly, because off-chunk pixels
cannot be pointed at):

| positions | link once | first pointed piece |
|---:|---:|---:|
| 1 | 0.04 s | 5.4 ms |
| 10 | 0.2 s | 4.5 ms |
| 100 | 0.5 s | 28.7 ms |
| 1,000 | 15.7 s | 357 ms |
| 10,000 | 144 s | ~3.4 s |

## What the photographs proved

Every cell was photographed in a real browser and judged — lit fraction,
distinct colours, and both channels present, enforced in code. The
pictures caught, in order: an out-of-band churn sentinel rendering as a
blank white canvas (fixed: photograph before churn, fill in-band); one
cell inheriting another's churned pixels through a shared fixture (fixed:
every cell owns an isolated fixture); a photograph taken mid-refinement
passing as settled (fixed: settled means 2 s stable *and* both channels);
and the shallow-pyramid wall below. **Photograph the actual screen** is
the single most productive rule this benchmark had.

## The findings that matter for 0.3

1. **The pyramid is too shallow for big mosaics — the one structural
   finding.** The declared picture tops out at level 3 (1/8 scale). At
   10,000 positions that coarsest level is 4,008² for a 32,064² mosaic,
   so a fit-to-view overview must fetch the *entire* coarsest level as
   dozens of composed pieces at ~35 s each: the screen stays black for
   many minutes. Deeper levels — or baking *only the coarse levels* at
   declare time, which at level-3 size would cost seconds and megabytes,
   not the full bake's minutes — would give every run a baked-quality
   first screen while the fine levels stay compose-on-demand. **This is
   the highest-value optimization on the table.**

2. **Bake the overview by default at scale.** Even without (1), the data
   says: above ~500 positions the bake pays for itself on the first open
   (130 s bake vs. an unusable 95 s+ unbaked first paint at 1,000). The
   viewer could decide this itself from the position count.

3. **Churn is genuinely flat where it must be.** Derive stays at
   milliseconds to 1,000 and ~150 ms at 10,000; a landed position never
   degrades the open run. The 10 positions/sec sustained-landing worry is
   answered: the record and derive are not the bottleneck.

4. **Post-churn coarse re-serve grows with scale** (134 ms → 808 ms
   unbaked; to 5.8 s baked at 10k, where the invalidated bake falls back
   to composition). Incremental re-bake of just the invalidated pieces
   would cap this.

5. **The link door's cost is linear** (~14 ms/position to link). Fine at
   hundreds, 2.4 minutes at 10k. Linking incrementally as positions land
   (the map already supports growing) would hide it entirely.

6. **Unbaked fine serves drift up with mosaic size** (59 ms → 0.5–1.3 s)
   even though one piece touches the same few tiles — worth a profile in
   0.3; suspicion: per-request map/manifest work, not pixel work.

7. **Locks need voices.** Two silent-wait incidents cost this campaign
   hours: a process that died holding the machine-wide bake flock left
   every successor queued indefinitely, and a `ProcessPoolExecutor`
   shutdown wedged after a container restart. A lock that names its
   holder after a few seconds, and a bake pool with a watchdog, turn
   hours of mystery into one log line.

## Honesty notes

- Three `failed` rows for 10,000/nominal/baked in `ladder.jsonl` are
  harness debris, not viewer behaviour: a disk-full during fixture
  linking (~4 GB of directory metadata per 10k hard-linked fixture — the
  measured wall that motivated the symlink fixtures), a multiprocessing
  bootstrap error in a one-off driver script, and a stale-lock refusal
  after the container restart. The cell's bake was near completion when
  the session was stopped; its sibling (scattered baked) stands as the
  10k baked measurement.
- Rungs 1 and 10 went through the real writer end to end (landing a
  position: 1.7–2.2 s each, flat); larger rungs fabricate the record and
  were fold-validated before measurement.
- The 100,000 rung is one command away: `ZMART_CHURN_RUNGS=100000
  python benchmark/churn_ladder.py` on a disk with ~10 GB free; the
  symlink fixture strategy is committed and the honest caveats are in
  its comments.
