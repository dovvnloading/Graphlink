/** R7.5a: the one place Chat/Code node Export turns raw text into a
 * downloaded file - a Blob -> object URL -> temporary anchor click, the same
 * "save this blob as a file" pattern ImageNodeView.tsx's handleExportImage
 * already established for images (object URL revoked immediately after the
 * synchronous click, since the browser has already captured what it needs by
 * then). Shared here rather than duplicated per node view since both Export
 * actions are otherwise byte-identical. */
export function downloadTextFile(content: string, filename: string): void {
  const blob = new Blob([content], { type: "text/plain" });
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(objectUrl);
}
