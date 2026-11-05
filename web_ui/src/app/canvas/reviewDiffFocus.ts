/** Locate the file/hunk that actually contains a new-side line in a
 * GitHub unified diff. This is a read-only view of the already-fetched text;
 * it never constructs a URL from an LLM-provided path and never renders HTML.
 * Unknown paths/lines return null, so the UI can fall back to the full diff.
 */
export function reviewDiffContext(diff: string, path: string, line: number): string | null {
  if (!path || !diff) return null;
  const starts = [...diff.matchAll(/^diff --git /gm)].map((match) => match.index);
  if (starts.length === 0) return null;

  for (let index = 0; index < starts.length; index++) {
    const section = diff.slice(starts[index], starts[index + 1] ?? diff.length);
    const rows = section.split("\n");
    const headerForPath = rows.some((row) =>
      row === "+++ b/" + path || row === '+++ "b/' + path + '"' ||
      (line <= 0 && (row === "--- a/" + path || row === '--- "a/' + path + '"')),
    );
    if (!headerForPath) continue;
    if (line <= 0) return section;

    let newLine: number | null = null;
    let hunkStart = -1;
    for (let i = 0; i < rows.length; i++) {
      const hunk = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(rows[i]);
      if (hunk) {
        newLine = Number(hunk[1]);
        hunkStart = i;
        continue;
      }
      if (newLine == null) continue;
      const row = rows[i];
      if (row.startsWith("+") && !row.startsWith("+++")) {
        if (newLine === line) return makeExcerpt(rows, hunkStart, i);
        newLine += 1;
      } else if (row.startsWith(" ")) {
        if (newLine === line) return makeExcerpt(rows, hunkStart, i);
        newLine += 1;
      } else if (row.startsWith("-") || row.startsWith("\\ No newline at end of file")) {
        continue;
      } else {
        newLine = null;
      }
    }
    return null;
  }
  return null;
}

function makeExcerpt(rows: string[], hunkStart: number, focusedIndex: number): string {
  // Anchor to the real hunk header and preserve just the nearby actual
  // +/- lines. The source remains text in a fenced diff, not executable HTML.
  const start = Math.max(hunkStart + 1, focusedIndex - 5);
  const end = Math.min(rows.length, focusedIndex + 6);
  return [rows[hunkStart], ...rows.slice(start, end)].join("\n");
}
