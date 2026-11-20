/** Wire validation and Document View formatting for agent workspace files. */

export interface HarnessWorkspaceEntry {
  name: string;
  path: string;
  isDir: boolean;
  size: number | null;
}

export interface HarnessWorkspaceListing {
  root: string;
  isScratch: boolean;
  path: string;
  entries: HarnessWorkspaceEntry[];
  truncated: boolean;
}

export interface HarnessWorkspaceDocument {
  path: string;
  content: string;
  truncated: boolean;
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function parseHarnessWorkspaceListing(value: unknown): HarnessWorkspaceListing {
  if (!record(value) || typeof value.root !== "string" ||
      typeof value.isScratch !== "boolean" || typeof value.path !== "string" ||
      typeof value.truncated !== "boolean" || !Array.isArray(value.entries)) {
    throw new Error("Invalid workspace folder response.");
  }
  const entries: HarnessWorkspaceEntry[] = value.entries.map((entry: unknown) => {
    if (!record(entry) || typeof entry.name !== "string" ||
        typeof entry.path !== "string" || typeof entry.isDir !== "boolean" ||
        !(entry.size === null ||
          (typeof entry.size === "number" && Number.isSafeInteger(entry.size) && entry.size >= 0))) {
      throw new Error("Invalid workspace file entry.");
    }
    return { name: entry.name, path: entry.path, isDir: entry.isDir, size: entry.size };
  });
  return { root: value.root, isScratch: value.isScratch, path: value.path, truncated: value.truncated, entries };
}

export function parseHarnessWorkspaceDocument(value: unknown): HarnessWorkspaceDocument {
  if (!record(value) || typeof value.path !== "string" ||
      typeof value.content !== "string" || typeof value.truncated !== "boolean") {
    throw new Error("Invalid workspace document response.");
  }
  return { path: value.path, content: value.content, truncated: value.truncated };
}

/** Keep markdown native; fence every other text file so source code, JSON,
 * and extracted Office text display verbatim instead of interpreting markup.
 * A fence longer than the longest run of backticks in the content prevents
 * source code from accidentally closing the preview. */
export function harnessFileDocumentMarkdown(document: HarnessWorkspaceDocument): string {
  const markdown = /\\.(md|markdown|mdx)$/i.test(document.path);
  const text = document.content || "(Empty file)";
  const longestFence = [...text.matchAll(/`+/g)].reduce((longest, match) => Math.max(longest, match[0].length), 0);
  const fence = "`".repeat(Math.max(3, longestFence + 1));
  const body = markdown ? text : `${fence}\n${text}\n${fence}`;
  return document.truncated ? `${body}\n\n> Preview truncated at 40,000 characters.` : body;
}
