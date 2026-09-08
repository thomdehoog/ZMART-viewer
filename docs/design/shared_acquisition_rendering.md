# Shared acquisition rendering and optional coarse baking

Date: 2026-09-08
Status: Design note for later work; not an implementation request or release claim.

## Intent

Use the same rendering policy throughout overview, focus and target acquisition. Workflow steps should not decide how the viewer composes images. Different storage layouts may require different readers, but those differences should remain behind a shared viewer/storage interface.

The user's preference is one consistent way of doing things, with a small, contained implementation at the right abstraction level rather than workflow-specific exceptions.

## Current limitation

The implementation context at the time of this discussion describes optional baking as restricted to overview acquisition. This is a temporary correctness restriction, not the desired final design.

The new baking path treats each completed position as a fully acquired rectangle. Target acquisition uses a different resolved display-store layout, which can contain unacquired gaps inside the store's bounds. Treating those bounds as acquired coverage could make the gaps opaque.

These details must be checked against the current code when work resumes. This side conversation did not independently inspect the storage implementation. Do not assume that focus has the same layout as either overview or targets.

## Proposed shared contract

Every acquisition exposes image data, spatial placement, acquired coverage and completed-change information through the shared viewer/storage boundary. Reuse existing representations and APIs wherever they already express these concepts; this does not require a new framework or a new persisted mask for every dataset.

- Fine views read original position data without copying a full-resolution mosaic.
- Optional coarse views read a combined, chunked overview.
- Unacquired areas remain transparent. Acquired pixels, including genuinely black pixels, remain opaque.
- Coverage is acquisition geometry or explicit acquired-pixel information, not a test of image brightness. Store bounds are sufficient only where full rectangular acquisition is guaranteed.
- Spatial placement, channels, Z and T remain correct across resolution transitions.
- Baking has the same meaning across workflow steps. A shared capability check may reject unsupported data with a clear reason; it must not silently assume coverage or substitute a workflow-name check.

Storage-specific readers may translate existing layouts into this contract. The operator supplies acquisition state and completed-write notifications; it does not build mosaics or implement separate rendering policies for overview, focus and targets.

## Update semantics to preserve

Keep the existing asynchronous, coalesced publication work and direct image reads from the viewer's HTTP server. Acquisition callbacks and bridge status requests must not perform viewer I/O.

Idle polling must not invalidate or refetch image data. An actual change should follow one effective invalidation path, without a second refresh on the next poll. New independent position sources should not invalidate existing sources.

Whole-source refresh for a changed source is the accepted initial scope. Do not make chunk-selective client invalidation a prerequisite for consistent rendering. Coarse baking should still rewrite only affected stored chunks where supported; distinguish that storage behavior from client cache invalidation.

## Smallest sound next step

1. Inspect how overview, focus and target stores currently represent placement, acquired coverage and completed changes. Trace the existing rendering readers, not just metadata.
2. Identify the exact coverage information available in target resolved stores, including whether it remains distinguishable from genuinely black acquired pixels. Do not infer a reliable mask from brightness.
3. Reuse that coverage representation in the shared composition/baking path. If it is missing, propose the smallest explicit storage-contract change before implementing it.
4. Verify the common path for each layout, then remove the overview-only restriction. Keep one user-facing baking option rather than per-step exceptions.

Avoid parallel implementations, unnecessary fine-resolution copies, scattered special cases and speculative compatibility layers. Different readers are justified by different data representations, not by workflow names.

## Acceptance evidence

- For overview, focus and targets, compare baking off and on using the same acquired data.
- Use sparse acquisitions with visible gaps and genuinely black acquired pixels. Measure rendered alpha/pixels, not only metadata or viewer-object identity.
- Check fine detail and placement, coarse requests using the aggregate source, and appearance and coverage across zoom transitions, including channels, Z and T.
- Check append and rewrite behavior, unchanged stored coarse chunks, no idle image refetches and no duplicate invalidation after publication.
- Confirm the bridge remains responsive during slow publication and the viewer eventually catches up.
- Record request counts, pixel comparisons and screenshots. Keep scaling tests at or below 100 positions; no long benchmark ladder.

## Open questions

- What is the authoritative acquired-coverage representation for each current storage layout?
- Can existing coverage readers support resolved target stores without introducing another representation?
- Does coarse coverage reduction preserve opaque acquired black pixels and transparent gaps at the intended resolution?
- Does focus acquisition satisfy the same shared contract without additional handling?

This note was originally saved outside both repositories to avoid changing the ongoing implementation, branches or running operator session. It is now archived here for the incremental review handoff; its status remains design for later work, not implemented functionality.
