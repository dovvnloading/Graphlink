import { describe, expect, it } from "vitest";
import { diffChatVersionWords, type ChatDiffChunk } from "./chatVersionWordDiff";

function text(chunks: ChatDiffChunk[]): string {
  return chunks.map((part) => part.text).join("");
}

describe("saved chat reply word diff", () => {
  it("marks a changed word on its own side, preserving unchanged words", () => {
    const diff = diffChatVersionWords("one two three", "one bright three");
    expect(diff.left).toEqual([
      { kind: "same", text: "one " },
      { kind: "removed", text: "two" },
      { kind: "same", text: " three" },
    ]);
    expect(diff.right).toEqual([
      { kind: "same", text: "one " },
      { kind: "added", text: "bright" },
      { kind: "same", text: " three" },
    ]);
  });

  it("identical replies contain no invented edits", () => {
    expect(diffChatVersionWords("same\n\nreply", "same\n\nreply")).toEqual({
      left: [{ kind: "same", text: "same\n\nreply" }],
      right: [{ kind: "same", text: "same\n\nreply" }],
    });
  });

  it("handles additions, deletions, Unicode and Markdown without losing text", () => {
    const oldText = "Hi, naïve 🐈!\n**old**  tail";
    const newText = "Hi, café 🐈!\n**new** tail plus";
    const diff = diffChatVersionWords(oldText, newText);
    expect(text(diff.left)).toBe(oldText);
    expect(text(diff.right)).toBe(newText);
    expect(diff.left.some((part) => part.kind === "removed")).toBe(true);
    expect(diff.right.some((part) => part.kind === "added")).toBe(true);
  });

  it("keeps a tiny edit in a very long reply precise by trimming shared tokens", () => {
    const sharedStart = "common word ".repeat(9000);
    const sharedEnd = "\nending line".repeat(2000);
    const diff = diffChatVersionWords(sharedStart + "old" + sharedEnd, sharedStart + "new" + sharedEnd);
    expect(diff.left.filter((part) => part.kind === "removed")).toEqual([{ kind: "removed", text: "old" }]);
    expect(diff.right.filter((part) => part.kind === "added")).toEqual([{ kind: "added", text: "new" }]);
  });

  it("bounds worst-case completely rewritten long replies without truncation", () => {
    const before = "a ".repeat(1800);
    const after = "z ".repeat(1800);
    const diff = diffChatVersionWords(before, after);
    expect(text(diff.left)).toBe(before);
    expect(text(diff.right)).toBe(after);
    expect(diff.left.some((part) => part.kind === "removed")).toBe(true);
    expect(diff.right.some((part) => part.kind === "added")).toBe(true);
  });
});
