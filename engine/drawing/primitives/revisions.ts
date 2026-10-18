/**
 * Which stores have committed something new, and what the engine remembers
 * about one store.
 *
 * Pure decisions, kept free of the engine and the browser so a plain Node
 * process can check them as well as the real page.
 */

/** A source's revision, by its stable identity, with the address it is read from. */
export type SourceRevisions = Map<string, { url: string; revision: number | undefined }>;

/** The revisions last applied, by identity and by address alike. */
export type RememberedRevisions = Map<string, number | undefined>;

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
