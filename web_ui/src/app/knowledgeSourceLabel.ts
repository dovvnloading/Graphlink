/** A source URI identifies the stored file, but is not a safe UI label.
 * Old saved citation ledgers may still contain absolute POSIX/Windows paths.
 * Keep web links intact; do not show a user's home directory for local files.
 */
export function knowledgeSourceLabel(source: string, title = ""): string {
  if (/^(graph|branch):/.test(source)) return title || "Chat";
  if (/^https?:\/\//i.test(source)) return source;
  const normalized = source.replace(/\\/g, "/");
  if (normalized.startsWith("/") || normalized.startsWith("file://") || /^[A-Za-z]:\//.test(normalized)) {
    return normalized.replace(/\/+$/, "").split("/").pop() || title || source;
  }
  return source || title;
}
