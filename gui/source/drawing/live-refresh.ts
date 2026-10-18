/** Pure decisions used by the manifest-driven refresh path.
 *
 * Kept free of React, the DOM and Neuroglancer so the safety rules can be
 * exercised in a plain Node process as well as in the real browser.
 */

import type { Config } from "../asking-python.ts";
import { layerKey } from "./layers.ts";

/** A half-open run of moments, `[start, stop)`, that has been written. */
export interface TimeRange {
  start: number;
  stop: number;
}

/** One store of a live run, as the server's live state describes it. */
export interface LiveSource {
  source_id: string;
  url: string;
  revision: number;
  layout_revision: number;
  committed_time_ranges: TimeRange[];
}

/** One live run, as the server's live state describes it. */
export interface LiveRun {
  dataset: number;
  run_id: string;
  revision: number;
  layout_revision: number;
  freshness: "current" | "degraded";
  error?: string;
  sources: LiveSource[];
}

/** The live state the server answers: every live run it has open. */
export interface LiveStateSet {
  schema: string;
  runs: LiveRun[];
}

/** A source's revision, by its stable identity, with the address it is read from. */
export type SourceRevisions = Map<string, { url: string; revision: number | undefined }>;

/** The revisions last applied, by identity and by address alike. */
export type RememberedRevisions = Map<string, number | undefined>;

/** Return why a live-state answer is unsafe to apply, or null when sound. The
    answer is the server's, so every field is checked rather than trusted. */
export function liveStateProblem(previous: LiveStateSet | null | undefined, next: LiveStateSet | null | undefined): string | null {
  if (next == null) return null;
  if (next.schema !== "zmart-live-frontend-state-set/1" || !Array.isArray(next.runs)) {
    return "the server returned an unknown live-state description";
  }
  const before = new Map(
    (previous?.runs || []).map((run) => [run.dataset, run]),
  );
  const seen = new Set<number>();
  for (const run of next.runs) {
    if (
      !Number.isInteger(run.dataset)
      || seen.has(run.dataset)
      || typeof run.run_id !== "string"
      || !run.run_id
      || !Number.isInteger(run.revision)
      || run.revision < 0
      || !Number.isInteger(run.layout_revision)
      || run.layout_revision < 0
      || !["current", "degraded"].includes(run.freshness)
      || !Array.isArray(run.sources)
    ) {
      return "the server returned internally inconsistent live state";
    }
    seen.add(run.dataset);
    if (run.freshness === "degraded") {
      return run.error || "the publication record cannot currently be read safely";
    }
    const was = before.get(run.dataset);
    if (was && run.run_id !== was.run_id) {
      return `the opened run changed identity from ${was.run_id} to ${run.run_id}`;
    }
    if (was && (run.revision < was.revision || run.layout_revision < was.layout_revision)) {
      return `the publication state for ${run.run_id} regressed`;
    }
    const oldSources = new Map((was?.sources || []).map((source) => [source.source_id, source]));
    const sourceIds = new Set<string>();
    for (const source of run.sources) {
      if (
        typeof source.source_id !== "string"
        || !source.source_id
        || sourceIds.has(source.source_id)
        || typeof source.url !== "string"
        || !source.url
        || !Number.isInteger(source.revision)
        || source.revision < 0
        || source.revision > run.revision
        || !Number.isInteger(source.layout_revision)
        || source.layout_revision < 0
        || source.layout_revision > run.layout_revision
        || !Array.isArray(source.committed_time_ranges)
      ) {
        return `source state for ${run.run_id} is inconsistent`;
      }
      sourceIds.add(source.source_id);
      let stop = -1;
      for (const range of source.committed_time_ranges) {
        if (
          !Number.isInteger(range.start)
          || !Number.isInteger(range.stop)
          || range.start < 0
          || range.stop <= range.start
          || range.start < stop
        ) {
          return `committed time availability for ${source.source_id} is invalid`;
        }
        stop = range.stop;
      }
      const old = oldSources.get(source.source_id);
      if (old && old.url !== source.url) {
        return `stable source ${source.source_id} changed its URL`;
      }
      if (old && source.revision < old.revision) {
        return `source ${source.source_id} regressed`;
      }
    }
  }
  return null;
}

/** Expand settled half-open ranges without pretending gaps were committed. */
export function momentsInRanges(ranges: readonly (TimeRange | null | undefined)[] | null | undefined): number[] | null {
  if (!Array.isArray(ranges)) return null;
  const moments: number[] = [];
  let previous = -1;
  for (const range of ranges) {
    if (
      !range
      || !Number.isInteger(range.start)
      || !Number.isInteger(range.stop)
      || range.start < 0
      || range.stop <= range.start
      || range.start < previous
    ) {
      return [];
    }
    for (let moment = range.start; moment < range.stop; moment += 1) moments.push(moment);
    previous = range.stop;
  }
  return moments;
}

/** Pair each stable source identity and URL with its separately carried revision. */
export function sourceRevisionsFor(
  urls: readonly string[], sourceIds: readonly (string | undefined)[] | null | undefined,
  sourceRevisions: readonly number[] | null | undefined,
): SourceRevisions {
  if (!Array.isArray(sourceRevisions)) return new Map();
  return new Map(
    urls.map((url, at) => [
      Array.isArray(sourceIds) ? (sourceIds[at] ?? url) : url,
      { url, revision: sourceRevisions[at] },
    ]),
  );
}

/** Select only sources whose valid committed revision moved strictly forwards. */
export function advancingSourceRevisions(previous: RememberedRevisions | null | undefined, current: SourceRevisions) {
  if (!previous) return [];
  return [...current.entries()].filter(([identity, now]) => {
    const before = previous.get(identity) ?? previous.get(now.url);
    return typeof now.revision === "number" && Number.isFinite(now.revision)
      && typeof before === "number" && Number.isFinite(before)
      && now.revision > before;
  });
}

/** Remember both identity and stable URL, allowing either to match the next pass. */
export function rememberedSourceRevisions(current: SourceRevisions): RememberedRevisions {
  return new Map(
    [...current.entries()].flatMap(([identity, value]) => [
      [identity, value.revision],
      [value.url, value.revision],
    ]),
  );
}

/**
 * Identify memoized metadata and decoded holders for exactly one stable source.
 *
 * The source address ends at its store folder, including a trailing slash, so a
 * similarly prefixed neighbour cannot match. Returning keys rather than mutating
 * the map keeps this policy independently testable.
 */
export function memoEntriesForStableSource(questions: Iterable<string>, url: string) {
  const folder = url.split("|")[0];
  const found: { metadata: string[]; decoded: string[] } = { metadata: [], decoded: [] };
  if (!folder) return found;
  for (const question of questions) {
    if (!question.includes(folder)) continue;
    const kind = question.includes('"constructorId"') ? "decoded" : "metadata";
    found[kind].push(question);
  }
  return found;
}

/**
 * Did an acquisition that was already on screen, but had nothing in it, just gain
 * its first image?
 *
 * This is the signal behind a fault that cost an operator a whole session. The
 * viewer notices an acquisition the instant its *description* lands, which is the
 * earliest possible moment and is deliberate — waiting would mean a run appearing
 * in the panel some seconds after it started. But at that moment there is no
 * picture behind it yet. The engine looks, finds nothing, and remembers that with
 * no time limit; so when the image arrived a moment later the panel went on
 * showing black, for as long as the page stayed open. The acquisition was listed,
 * its eye was open, and nothing said why it was empty. Reloading the page showed
 * the data perfectly, which is the tell: only the open viewer was stuck.
 *
 * The engine has a way to be told to forget what it has decoded, and the question
 * is when to use it. Doing so on every announcement is far too blunt — it throws
 * away everything fetched, and during a run announcements arrive constantly, so a
 * position arriving would cost a refetch of the whole view. What is wanted is the
 * narrow case: a store the panel already knew about, which had no measurable
 * picture in it, and now has one.
 *
 * That transition is visible in the answer the server gives. A store with nothing
 * written yet has no histogram — there were no pixels to measure — and no count of
 * moments. Once an image lands, both appear. A store arriving for the first time
 * is not this: it was not there before, so the engine has decoded nothing for it
 * and has nothing to forget.
 */
export function anyStoreGainedItsFirstImage(previous: Config | null, loaded: Config): boolean {
  if (!previous) return false;
  const before = new Map((previous.layers || []).map((spec) => [layerKey(spec), spec]));
  return (loaded.layers || []).some((spec) => {
    const was = before.get(layerKey(spec));
    if (!was) return false; // newly arrived, so nothing has been decoded for it
    const hadNothing = was.histogram == null && was.frames == null;
    const hasSomethingNow = spec.histogram != null || spec.frames != null;
    return hadNothing && hasSomethingNow;
  });
}

/**
 * The moments written so far, as merged half-open ranges, or null where nothing
 * open is a live run.
 *
 * Manifest runs may publish timepoints with gaps, so a single high-water
 * number is not an honest description. The explicit ranges are read directly
 * from the authoritative live state, not from declared array shape or a
 * convenience field on a layer row. Ordinary folders alongside it keep their
 * counted limit, as a range from the first moment.
 */
export function committedTimeRanges(config: Config | null): TimeRange[] | null {
  const rows = config?.layers || [];
  const liveRuns = config?.liveState?.runs;
  if (!Array.isArray(liveRuns) || !liveRuns.length) return null;
  const ranges = [
    ...liveRuns.flatMap((run) => (
      run.sources || []
    ).flatMap((source) => source.committed_time_ranges || [])),
    ...rows
      .filter((spec) => spec.liveRunId == null)
      .flatMap((spec) => (
        typeof spec.frames === "number" && spec.frames > 0
          ? [{ start: 0, stop: spec.frames }]
          : []
      )),
  ]
    .map((range) => ({ start: range.start, stop: range.stop }))
    .sort((one, other) => one.start - other.start || one.stop - other.stop);
  const merged: TimeRange[] = [];
  for (const range of ranges) {
    const last = merged[merged.length - 1];
    if (last && range.start <= last.stop) last.stop = Math.max(last.stop, range.stop);
    else merged.push(range);
  }
  return merged;
}
