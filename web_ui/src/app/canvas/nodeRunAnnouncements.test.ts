import { describe, expect, it } from "vitest";
import { describeNodeRunTransition } from "./nodeRunAnnouncements";
import type { SceneNodeRow } from "../../lib/bridge-core/generated/scene-state";

function row(overrides: Partial<SceneNodeRow> = {}): SceneNodeRow {
  return {
    id: "n1",
    kind: "code_sandbox",
    title: "",
    pendingRequestId: null,
    ...overrides,
  } as SceneNodeRow;
}

describe("describeNodeRunTransition (ADR-012 stage 12.3)", () => {
  it("says nothing for a brand-new node (no prior row)", () => {
    expect(describeNodeRunTransition(undefined, row({ pendingRequestId: "r1" }))).toBeNull();
  });

  it("announces a run starting", () => {
    const prev = row({ pendingRequestId: null });
    const next = row({ pendingRequestId: "r1" });
    expect(describeNodeRunTransition(prev, next)).toBe("Code sandbox run started");
  });

  it("announces a plain run completing", () => {
    const prev = row({ pendingRequestId: "r1" });
    const next = row({ pendingRequestId: null });
    expect(describeNodeRunTransition(prev, next)).toBe("Code sandbox run completed");
  });

  it("announces a code_sandbox run failing via codeSandboxError", () => {
    const prev = row({ kind: "code_sandbox", pendingRequestId: "r1" });
    const next = row({ kind: "code_sandbox", pendingRequestId: null, codeSandboxError: "boom" });
    expect(describeNodeRunTransition(prev, next)).toBe("Code sandbox run failed");
  });

  it("announces a gitlink operation failing via gitlinkError", () => {
    const prev = row({ kind: "gitlink", pendingRequestId: "r1" });
    const next = row({ kind: "gitlink", pendingRequestId: null, gitlinkError: "boom" });
    expect(describeNodeRunTransition(prev, next)).toBe("Git operation failed");
  });

  it("announces a code review failing via codeReviewError", () => {
    const prev = row({ kind: "code_review", pendingRequestId: "r1" });
    const next = row({ kind: "code_review", pendingRequestId: null, codeReviewError: "boom" });
    expect(describeNodeRunTransition(prev, next)).toBe("Code review failed");
  });

  it("announces a harness run failing via harnessStatus", () => {
    const prev = row({ kind: "harness", pendingRequestId: "r1" });
    const next = row({ kind: "harness", pendingRequestId: null, harnessStatus: "failed" });
    expect(describeNodeRunTransition(prev, next)).toBe("Agent run failed");
  });

  it("does not treat a codeSandboxError on an UNRELATED node kind as a failure signal", () => {
    // codeSandboxError living on a non-code_sandbox row would be a wire-shape
    // bug elsewhere, not something this function should paper over by
    // treating it as a real transition - only the matching kind's own error
    // field counts, per its own KIND_LABELS-keyed branch.
    const prev = row({ kind: "chat", pendingRequestId: "r1" });
    const next = row({ kind: "chat", pendingRequestId: null, codeSandboxError: "leftover" });
    expect(describeNodeRunTransition(prev, next)).toBe("Chat response completed");
  });

  it("falls back to the raw kind string for an unlabeled kind", () => {
    const prev = row({ kind: "mystery_kind", pendingRequestId: "r1" });
    const next = row({ kind: "mystery_kind", pendingRequestId: null });
    expect(describeNodeRunTransition(prev, next)).toBe("mystery_kind completed");
  });

  it("says nothing when pendingRequestId is unchanged and builderStatus is unchanged", () => {
    const prev = row({ pendingRequestId: "r1", title: "old title" });
    const next = row({ pendingRequestId: "r1", title: "new title" });
    expect(describeNodeRunTransition(prev, next)).toBeNull();
  });

  it("announces a build starting/completing/failing via builderStatus", () => {
    const running = describeNodeRunTransition(
      row({ kind: "plan", builderStatus: "awaiting_start" }),
      row({ kind: "plan", builderStatus: "running" }),
    );
    expect(running).toBe("Build running");

    const done = describeNodeRunTransition(
      row({ kind: "plan", builderStatus: "running" }),
      row({ kind: "plan", builderStatus: "done" }),
    );
    expect(done).toBe("Build complete");

    const failed = describeNodeRunTransition(
      row({ kind: "plan", builderStatus: "running" }),
      row({ kind: "plan", builderStatus: "failed" }),
    );
    expect(failed).toBe("Build failed");
  });

  it("says nothing for a builderStatus transition into an unmapped value", () => {
    const message = describeNodeRunTransition(
      row({ kind: "plan", builderStatus: "planning" }),
      row({ kind: "plan", builderStatus: "awaiting_tool_approval" }),
    );
    expect(message).toBeNull();
  });
});
