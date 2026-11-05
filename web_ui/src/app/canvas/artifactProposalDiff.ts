/** Bounded, honest change-region preview for the Artifact review card.
 *
 * Only a common unchanged prefix and suffix are omitted. The middle is
 * displayed as before/after, not incorrectly labelled as a minimal line
 * diff when two distant changes have unchanged lines between them.
 * Accepted content is never truncated; this cap is only for the UI.
 */
export interface ArtifactDiffPreview {
  leadingUnchangedLines: number;
  trailingUnchangedLines: number;
  before: string;
  after: string;
  omittedBeforeLines: number;
  omittedAfterLines: number;
  clippedBeforeChars: boolean;
  clippedAfterChars: boolean;
  changed: boolean;
}

export function artifactDiffPreview(
  current: string, proposed: string, maximumShownLines = 90,
): ArtifactDiffPreview {
  if (current === proposed) {
    return {
      leadingUnchangedLines: 0,
      trailingUnchangedLines: 0,
      before: "",
      after: "",
      omittedBeforeLines: 0,
      omittedAfterLines: 0,
      clippedBeforeChars: false,
      clippedAfterChars: false,
      changed: false,
    };
  }
  const beforeLines = current.split("\n");
  const afterLines = proposed.split("\n");
  let leading = 0;
  while (
    leading < beforeLines.length &&
    leading < afterLines.length &&
    beforeLines[leading] === afterLines[leading]
  ) leading += 1;

  let trailing = 0;
  while (
    trailing < beforeLines.length - leading &&
    trailing < afterLines.length - leading &&
    beforeLines[beforeLines.length - trailing - 1] === afterLines[afterLines.length - trailing - 1]
  ) trailing += 1;

  const beforeChanged = beforeLines.slice(leading, beforeLines.length - trailing);
  const afterChanged = afterLines.slice(leading, afterLines.length - trailing);
  const cap = Math.max(1, Math.min(200, maximumShownLines));
  const beforeExcerpt = beforeChanged.slice(0, cap).join("\n");
  const afterExcerpt = afterChanged.slice(0, cap).join("\n");
  return {
    leadingUnchangedLines: leading,
    trailingUnchangedLines: trailing,
    before: beforeExcerpt.slice(0, 5000),
    after: afterExcerpt.slice(0, 5000),
    omittedBeforeLines: Math.max(0, beforeChanged.length - cap),
    omittedAfterLines: Math.max(0, afterChanged.length - cap),
    clippedBeforeChars: beforeExcerpt.length > 5000,
    clippedAfterChars: afterExcerpt.length > 5000,
    changed: true,
  };
}
