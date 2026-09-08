# Optional coarse baking: 8,362-position manual run

Date: 2026-09-08. Experimental local implementation; not a release qualification.

## Setup

The user ran the mock target-acquisition operator with optional overview baking
enabled, following an earlier successful 1,536-tiles-per-well trial. The larger
scan planned 8,362 positions with a reported 1,024 micrometre frame. The fixed
specimen canvas was X [0, 120000], Y [0, 80000] micrometres.

This was a user-driven run, not an automated benchmark. The agent's automated
scaling tests remain capped at 100 positions. No larger ladder was started.

Tested worktrees were `codex/optional-live-bake` in ZMART-viewer (base 97f3ce3)
and `codex/operator-optional-bake` in ZMART-microscopy (base 98e698ba), with the
optional-bake and asynchronous publication changes still uncommitted during
the run. This evidence therefore describes that working-tree implementation,
not those base commits alone and not a released version of 0.2.1.

## Observations

- The user reported that acquisition kept progressing while the picture updated
  in batches. There were pauses followed by substantial visual catch-up.
- Apparent lag differed by zoom/pyramid level. Screenshots alone did not
  establish the cause or prove a consistently bounded backlog.
- The final screenshot shows the overview filled, with no remaining planned
  grid gaps at that zoom and the scan no longer running.
- After the run, the user explicitly confirmed that zooming into the final
  acquired corner and back out showed that the pyramid levels had caught up.

Read-only inspection after completion confirmed:

| Check | Result |
| --- | --- |
| Completed position stores on disk | 8,362 |
| Positions in the baked publication manifest | 8,362 |
| Final aggregate publication revision | 92 |
| Pending-publication marker | Absent |
| Final publication timestamp (machine local, Europe/Zurich) | 2026-09-08 15:30:53 |

The bridge log also contains **five occurrences** of
`OSError: [Errno 36] Resource deadlock avoided`, originating in Windows
`msvcrt.locking(..., LK_LOCK, ...)` while image requests waited on the bake
lock. Those requests were answered as temporarily unavailable. The run
eventually completed, but these errors must not be described as an error-free
qualification or assumed to explain all observed delays.

No timings separated baking, HTTP fetching and GPU rendering. The final
cross-zoom check is user-confirmed manual evidence, not an automated pixel
comparison. Black-pixel opacity and sparse alpha have separate targeted tests;
the full rectangular screenshot alone does not establish them.

## Preserved local evidence

The run remains under
`C:/ProgramData/MinicondaZMB/home/t.de/operator-optional-bake-demo-20260908/runs/target-acquisition_96978f`.
Its overview publication is
`positions/overview/.zmart-viewer/overview.ome.zarr/publication.json`.

Copies of the selected screenshots and bridge log are retained outside Git at
`C:/ProgramData/MinicondaZMB/home/t.de/optional-bake-8362-evidence-20260908/`.
The final screenshot was supplied as `Screenshot 2026-09-08 153715.png`.
No image data or screenshots are committed to the repository.

## Follow-up scope

Review the Windows lock contention and the operator's coverage-source row
translation independently, with focused regressions before code commits.
Keep baking optional and off by default. Whole-source client refresh remains
the accepted initial scope.

See [the shared rendering design](../design/shared_acquisition_rendering.md)
for the intended follow-up beyond the temporary overview-only integration.
