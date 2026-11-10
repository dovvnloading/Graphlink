/** Split Gitlink's existing full diff into review-only per-file sections.
 *
 * Presentation-only, NEVER an authorization source. The backend independently
 * verifies the approved file set. Unknown or old diff formats fall back to
 * the complete diff instead of showing a misleading partial excerpt.
 */
export interface PreviewChange {
  path: string;
  operation: string;
}

/** Mirror the backend's display_review_path without interpreting file text.
 * Normal ASCII paths stay readable; control and non-ASCII code units are
 * visible JSON escapes, including bidirectional formatting and newlines.
 * Selection still sends the original exact path.
 */
export function displayGitlinkPath(path: string): string {
  if (/^[\x20-\x7e]*$/.test(path)) return path;
  return JSON.stringify(path).replace(/[\u007f-\uffff]/g, (char) =>
    "\\u" + char.charCodeAt(0).toString(16).padStart(4, "0"));
}

export function splitGitlinkPreview(
  fullPreview: string,
  files: readonly PreviewChange[],
): string[] | null {
  if (!fullPreview || files.length === 0) return null;
  const positions: number[] = [];
  let cursor = 0;
  for (const file of files) {
    const heading = "### " + displayGitlinkPath(file.path) + " [" + file.operation + "]";
    // _build_gitlink_preview_text emits headers on their own lines.
    let start = fullPreview.indexOf(heading, cursor);
    while (start >= 0 && start !== 0 && fullPreview[start - 1] !== "\n") {
      start = fullPreview.indexOf(heading, start + 1);
    }
    if (start < 0 || !fullPreview.startsWith("\n", start + heading.length)) return null;
    positions.push(start);
    cursor = start + heading.length + 1;
  }
  if (positions[0] !== 0) return null;
  return positions.map((start, index) => {
    const end = index < positions.length - 1 ? positions[index + 1] : fullPreview.length;
    return fullPreview.slice(start, end).trim();
  });
}
