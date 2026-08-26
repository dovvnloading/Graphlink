/** Wraps a Blob in an object URL and drives a temporary, never-attached-to-
 * view anchor's download through a programmatic click - the standard "save
 * this blob as a file" browser pattern, first established here for
 * downloadTextFile below and independently reimplemented byte-for-byte in
 * ImageNodeView.tsx's handleExportImage and ChartNodeView.tsx's
 * downloadChartExport before both were switched to call this instead. The
 * object URL is revoked immediately after the click to avoid leaking it (the
 * click itself is synchronous, so the browser has already captured what it
 * needs from the URL by the time revokeObjectURL runs on the next line). */
export function downloadBlob(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(objectUrl);
}

/** R7.5a: the one place Chat/Code node Export turns raw text into a
 * downloaded file. Shared here rather than duplicated per node view since
 * both Export actions are otherwise byte-identical. */
export function downloadTextFile(content: string, filename: string): void {
  downloadBlob(new Blob([content], { type: "text/plain" }), filename);
}
