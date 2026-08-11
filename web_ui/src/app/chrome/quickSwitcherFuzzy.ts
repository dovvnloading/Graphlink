/**
 * ADR-020 stage 20.5: the quick switcher's fuzzy title match - a plain,
 * dependency-free subsequence matcher (every character of the query must
 * appear in the target, in order, not necessarily contiguous - the same
 * matching contract VS Code's/Sublime's own "Go to File" use), not a fuzzy-
 * scoring library. Split out as a pure function so the matching/ranking
 * logic is unit-testable without mounting QuickSwitcherDialog.tsx.
 *
 * Lower score = better match, so a plain ascending sort ranks the best
 * match first: an exact-prefix match (score includes PREFIX_BONUS, a large
 * negative offset) always outranks a non-prefix match regardless of span,
 * and among same-prefix-ness matches a tighter span (query characters found
 * closer together in the target) outranks a looser one.
 */

const PREFIX_BONUS = -1000;

/** Null when `query`'s characters do not all appear, in order, in `target`.
 * An empty query matches everything with score 0 - every candidate ties, so
 * a stable sort leaves the caller's own input order (recency, here)
 * untouched. */
export function fuzzyScore(query: string, target: string): number | null {
  const q = query.toLowerCase();
  if (q.length === 0) return 0;
  const t = target.toLowerCase();

  let qi = 0;
  let firstIndex = -1;
  let lastIndex = -1;
  for (let ti = 0; ti < t.length && qi < q.length; ti++) {
    if (t[ti] === q[qi]) {
      if (firstIndex === -1) firstIndex = ti;
      lastIndex = ti;
      qi++;
    }
  }
  if (qi < q.length) return null;

  const spread = lastIndex - firstIndex + 1 - q.length;
  const prefixBonus = firstIndex === 0 ? PREFIX_BONUS : 0;
  return prefixBonus + spread * 10 + firstIndex;
}

/** Filters `items` to those whose `text(item)` fuzzy-matches `query`, sorted
 * best-match-first. `Array.prototype.sort` is a stable sort (guaranteed
 * since ES2019), so a blank query - every item scores 0 and ties - preserves
 * `items`' own input order rather than reshuffling it. */
export function fuzzyFilterAndSort<T>(query: string, items: readonly T[], text: (item: T) => string): T[] {
  const trimmed = query.trim();
  return items
    .map((item) => ({ item, score: fuzzyScore(trimmed, text(item)) }))
    .filter((entry): entry is { item: T; score: number } => entry.score !== null)
    .sort((a, b) => a.score - b.score)
    .map((entry) => entry.item);
}
