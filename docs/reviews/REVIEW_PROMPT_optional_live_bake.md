# Review: optional completed-position coarse baking

Branch: `codex/optional-live-bake`, based on viewer 0.2.1 (`18bd328`).
Review the branch diff, especially `zmart_viewer/published.py` and its use of
the existing composer, baker and server. Do not modify files, commit, or push.
Return actionable findings as file/line, severity and a concrete failure case.

Prefer simple, contained fixes at the shared viewer/storage boundary. Assess
correctness, readability, ownership, efficiency and maintainability; identify
unnecessary abstractions, duplicated paths and fragile special cases. The
operator announces completed writes; it must not construct the overview or
proxy image chunks.

## Intended behavior

- Off by default. A fixed specimen canvas determines aggregate geometry.
- Fine chunks read original positions; no level-0 mosaic copy is created.
- Completed revision snapshots patch affected stored coarse chunks. Identical
  snapshots do no pixel work. The client refreshes the whole aggregate source.
- Coverage comes from acquired footprints, never brightness. Acquired black
  pixels remain opaque; unacquired ground remains transparent.
- Interrupted publication fails closed until retry, including when a partly
  published append is withdrawn before retry.

The large `building.py` diff mostly moves existing methods into a shared
`ComposedPicture` base. Check that governed/growing-run behavior is preserved.
Check the server's existing notification path rather than proposing another
polling system. Check reader locking, retirement/retry, C/Z/T placement,
coarse-level translations and coverage, and input snapshot ownership.

## Scope and evidence

`tests/test_published_transfer.py` covers changed/unchanged chunks, recovery,
fixed offset geometry, distinct C/Z/T values, API validation and real browser
requests/pixels in both bake modes. Its browser comparison uses the same camera
and verifies visible image pixels before comparing a rewrite. Other relevant
tests are `test_acquisition_coverage.py`, `test_bake_lock.py`,
`test_a_governed_picture_is_baked_per_commit.py`,
`test_a_grown_run_is_baked_per_commit.py`, `test_transparent_2d_browser.py`,
`test_server.py`, `test_manifest_driven_refresh.py`,
`test_frontend_live_refresh_contract.py` and `test_source_refresh_demo.py`.

Use an already built viewer page for browser tests, and the machine's approved
Python/Conda/browser paths. Keep automated scaling at or below 100 positions;
do not run the long benchmark ladder. Keep screenshots and datasets out of Git.

Limitations are deliberate and must remain visible: complete unrotated
rectangular positions, fixed matching C/Z/T geometry, at least two mean-reduced
native pyramid levels, and at least one completed position. Automatic folder
mode detects metadata timestamps; explicit revisions are needed for chunk-only
rewrites. Whole-source invalidation can refetch unchanged aggregate chunks;
publication does not promise atomic cross-request display of every pyramid
level. Already-sparse resolved targets are not supported by this adapter.

See `docs/measured/optional_bake_8362_positions.md` for the user-run trial,
including its original lock errors. This is not a production benchmark or a
claim that the current branch was the exact code running during that trial.
`docs/design/shared_acquisition_rendering.md` describes later shared coverage
work; it is not implemented functionality or scope to add during this review.

Operator integration is separate. Its remote workflow branch has advanced to
`feffb418`; integration must use a new branch, not modify that workflow branch.
