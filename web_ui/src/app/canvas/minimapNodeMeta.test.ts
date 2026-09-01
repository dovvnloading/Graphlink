import { describe, expect, it } from "vitest";
import type { SceneNodeRow } from "../../lib/bridge-core/generated/scene-state";
import { minimapCategory, minimapState } from "./minimapNodeMeta";

/**
 * The map's information design, tested as the functions it is.
 *
 * React Flow's MiniMap draws only nodes from its own measured store, which
 * jsdom never populates - so a DOM assertion about what the map contains is
 * vacuous there. These are the decisions worth pinning; the drawing is
 * verified in a browser.
 */

function node(overrides: Partial<SceneNodeRow> = {}): SceneNodeRow {
  return { id: "n0", x: 0, y: 0, title: "", kind: "chat", ...overrides } as unknown as SceneNodeRow;
}

describe("minimapCategory", () => {
  it.each([
    ["chat", "conversation"],
    ["conversation", "conversation"],
    ["thinking", "conversation"],
    ["code", "conversation"],
    ["document", "content"],
    ["image", "content"],
    ["chart", "content"],
    ["note", "content"],
    ["artifact", "content"],
    ["html", "content"],
    ["plan", "tool"],
    ["harness", "tool"],
    ["web_research", "tool"],
    ["gitlink", "tool"],
    ["code_sandbox", "tool"],
    ["frame", "group"],
    ["container", "group"],
  ])("draws a %s node as %s", (kind, category) => {
    expect(minimapCategory(kind)).toBe(category);
  });

  it("falls back rather than dropping a kind it has never heard of", () => {
    // A node the map cannot classify is still a node whose position matters.
    expect(minimapCategory("some_future_kind")).toBe("conversation");
  });
});

describe("minimapState", () => {
  it.each([
    ["a build waiting on a tool", { builderAwaitingToolApproval: true }, "attention"],
    ["an agent waiting on approval", { harnessAwaitingApproval: true }, "attention"],
    ["an agent that asked a question", { harnessAwaitingQuestion: true }, "attention"],
    ["a sandbox waiting on approval", { codeSandboxAwaitingApproval: true }, "attention"],
    ["a failed build", { builderStatus: "failed" }, "failed"],
    ["a failed agent", { harnessStatus: "failed" }, "failed"],
    ["a research error", { researchError: "no network" }, "failed"],
    ["a sandbox error", { codeSandboxError: "boom" }, "failed"],
    ["a gitlink error", { gitlinkError: "bad token" }, "failed"],
    ["a running build", { builderStatus: "running" }, "running"],
    ["a planning build", { builderStatus: "planning" }, "running"],
    ["a running agent", { harnessStatus: "running" }, "running"],
    ["a node with a request in flight", { pendingRequestId: "req-1" }, "running"],
    ["an idle node", {}, "idle"],
  ])("marks %s as %s", (_label, fields, state) => {
    expect(minimapState(node(fields))).toBe(state);
  });

  it("ranks needing a human above failing, and failing above running", () => {
    // A build that is both running and parked on an approval is PARKED.
    // Reporting it as running would hide the only state that needs someone.
    expect(
      minimapState(node({ builderStatus: "running", builderAwaitingToolApproval: true })),
    ).toBe("attention");
    expect(minimapState(node({ builderStatus: "failed", harnessAwaitingQuestion: true }))).toBe(
      "attention",
    );
    expect(minimapState(node({ builderStatus: "failed", pendingRequestId: "req-1" }))).toBe(
      "failed",
    );
  });

  it("treats an empty error string as no error", () => {
    // Every error field on the wire row is a string that is empty when
    // there is nothing wrong, so a truthiness check is the contract.
    expect(minimapState(node({ codeSandboxError: "", gitlinkError: "", researchError: "" }))).toBe(
      "idle",
    );
  });
});
