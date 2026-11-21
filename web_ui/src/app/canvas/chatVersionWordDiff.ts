/** Word-level, lossless text comparison for two saved chat replies.
 * Computation belongs in chatVersionWordDiff.worker.ts, never in React render.
 * The bounded LCS matrix prevents pathological rewrites from exhausting RAM;
 * very different large middles are represented as changed spans instead.
 */
export type ChatDiffKind = "same" | "removed" | "added";

export interface ChatDiffChunk {
  kind: ChatDiffKind;
  text: string;
}

export interface ChatWordDiff {
  left: ChatDiffChunk[];
  right: ChatDiffChunk[];
}

// Keep ALL whitespace and punctuation so joining each pane reproduces its
// original response byte-for-byte. Unicode words stay together.
const TOKENS = /\s+|[\p{L}\p{N}_]+|[^\p{L}\p{N}_\s]+/gu;
const MAX_LCS_CELLS = 1_000_000;

function add(chunks: ChatDiffChunk[], kind: ChatDiffKind, text: string): void {
  if (!text) return;
  const last = chunks[chunks.length - 1];
  if (last?.kind === kind) last.text += text;
  else chunks.push({ kind, text });
}

export function diffChatVersionWords(before: string, after: string): ChatWordDiff {
  const leftTokens = before.match(TOKENS) ?? [];
  const rightTokens = after.match(TOKENS) ?? [];
  const left: ChatDiffChunk[] = [];
  const right: ChatDiffChunk[] = [];

  let prefix = 0;
  while (prefix < leftTokens.length && prefix < rightTokens.length &&
    leftTokens[prefix] === rightTokens[prefix]) prefix++;
  let suffix = 0;
  while (prefix + suffix < leftTokens.length && prefix + suffix < rightTokens.length &&
    leftTokens[leftTokens.length - suffix - 1] === rightTokens[rightTokens.length - suffix - 1]) suffix++;

  const sameStart = leftTokens.slice(0, prefix).join("");
  add(left, "same", sameStart);
  add(right, "same", sameStart);

  const a = leftTokens.slice(prefix, leftTokens.length - suffix);
  const b = rightTokens.slice(prefix, rightTokens.length - suffix);
  const n = a.length;
  const m = b.length;
  if ((n + 1) * (m + 1) > MAX_LCS_CELLS) {
    // Fast, truthful fallback: do not claim an expensive minimal diff.
    // Prefix/suffix are still exact, and every changed word remains visible.
    add(left, "removed", a.join(""));
    add(right, "added", b.join(""));
  } else {
    const width = m + 1;
    const lcs = new Uint32Array((n + 1) * width);
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        lcs[i * width + j] = a[i] === b[j]
          ? lcs[(i + 1) * width + j + 1] + 1
          : Math.max(lcs[(i + 1) * width + j], lcs[i * width + j + 1]);
      }
    }
    let i = 0;
    let j = 0;
    while (i < n || j < m) {
      if (i < n && j < m && a[i] === b[j]) {
        add(left, "same", a[i]);
        add(right, "same", a[i]);
        i++;
        j++;
      } else if (i < n && (j === m || lcs[(i + 1) * width + j] >= lcs[i * width + j + 1])) {
        add(left, "removed", a[i++]);
      } else {
        add(right, "added", b[j++]);
      }
    }
  }

  const sameEnd = suffix ? leftTokens.slice(leftTokens.length - suffix).join("") : "";
  add(left, "same", sameEnd);
  add(right, "same", sameEnd);
  return { left, right };
}
