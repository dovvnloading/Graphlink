import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { WsTransport } from "../../lib/ws/transport";
import { CodeExecutionApprovalPanel, type CodeExecutionKind } from "./CodeExecutionApprovalPanel";
import { ExecutionLimitsProvider } from "./ExecutionLimitsContext";

type StateListener = (payload: Record<string, unknown>) => void;

/** A transport whose "execution-limits" snapshot is delivered SYNCHRONOUSLY
 * on subscribe - unlike sceneStore.test.ts's own makeFakeTransport (which
 * hands back a `listeners` map for the test to fire manually), this panel's
 * tests only ever care about "given this text, is it rendered" - there is
 * no separate update-over-time behavior worth exercising here (that is
 * ExecutionLimitsContext.test.tsx's own job). */
function renderPanelWithExecutionLimits(
  overrides: Partial<Parameters<typeof CodeExecutionApprovalPanel>[0]> = {},
  limitsPayload: Record<string, unknown> | null = {
    schemaVersion: 1,
    minCompatibleSchemaVersion: 1,
    revision: 1,
    codeSandboxResourceLimitsText:
      "Execution is capped at approximately 2 GB of memory and 64 concurrent processes. Binary packages only.",
  },
) {
  const onApprove = vi.fn();
  const onDeny = vi.fn();
  const props = {
    nodeId: "n0",
    kind: "pycoder" as CodeExecutionKind,
    code: "print('hello')",
    awaitingApproval: true,
    busy: false,
    onApprove,
    onDeny,
    ...overrides,
  };
  const transport = {
    subscribe: vi.fn((topic: string, listener: StateListener) => {
      if (topic === "execution-limits" && limitsPayload) listener(limitsPayload);
      return () => {};
    }),
  } as unknown as WsTransport;
  const { container } = render(
    <ExecutionLimitsProvider transport={transport}>
      <CodeExecutionApprovalPanel {...props} />
    </ExecutionLimitsProvider>,
  );
  return { onApprove, onDeny, container };
}

// Rendered standalone, with NO <OverlayProvider> ancestor (post-review
// architecture correction - see this component's own module doc for FIX A/
// FIX B): unlike GitlinkNodeView's own Apply confirmation, this is no longer
// a <Dialog> from the R2.1 overlay system at all, so there is nothing here
// that would throw without a provider.

function renderPanel(overrides: Partial<Parameters<typeof CodeExecutionApprovalPanel>[0]> = {}) {
  const onApprove = vi.fn();
  const onDeny = vi.fn();
  const props = {
    nodeId: "n0",
    kind: "pycoder" as CodeExecutionKind,
    code: "print('hello')",
    awaitingApproval: true,
    busy: false,
    onApprove,
    onDeny,
    ...overrides,
  };
  const { container } = render(<CodeExecutionApprovalPanel {...props} />);
  return { onApprove, onDeny, container };
}

describe("CodeExecutionApprovalPanel", () => {
  // -- visibility -----------------------------------------------------------

  it("renders nothing at all when awaitingApproval is false", () => {
    renderPanel({ awaitingApproval: false });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("auto-opens (no button click needed) the instant awaitingApproval is true", () => {
    renderPanel({ awaitingApproval: true });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  // -- FIX A regression guard: zero passive-dismissal affordances -----------

  it("FIX A: there is no close/X button anywhere - Deny and Approve are the only two RESOLVING buttons rendered", () => {
    // The node redesign migrated this panel's code display to NodeMarkdown,
    // which renders its own "Copy" button inside the code block - a real,
    // legitimate 3rd button, but not a resolution mechanism: it neither
    // dismisses the panel nor calls onApprove/onDeny, so FIX A's actual
    // security property (the ONLY two ways to make this panel go away are
    // Approve and Deny) still holds - this test now asserts that precisely,
    // rather than a stale exact-button-count that predates Copy existing.
    renderPanel();
    const buttons = screen.getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual(["Copy", "Deny", "Approve"]);
    expect(screen.queryByRole("button", { name: /close/i })).toBeNull();
    expect(screen.queryByLabelText(/close/i)).toBeNull();
  });

  it("FIX A: clicking Copy does not resolve the panel (not Approve, not Deny, stays open)", async () => {
    const user = userEvent.setup();
    const { onApprove, onDeny } = renderPanel();
    await user.click(screen.getByRole("button", { name: "Copy code" }));
    expect(onApprove).not.toHaveBeenCalled();
    expect(onDeny).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("FIX A: pressing Escape does NOT dismiss the panel and does NOT call onApprove/onDeny", () => {
    const { onApprove, onDeny } = renderPanel();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onApprove).not.toHaveBeenCalled();
    expect(onDeny).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("FIX A: clicking the backdrop/scrim does NOT dismiss the panel and does NOT call onApprove/onDeny", () => {
    const { onApprove, onDeny } = renderPanel();
    const dialog = screen.getByRole("dialog");
    const scrim = dialog.parentElement!;
    expect(scrim).not.toBe(document.body);
    fireEvent.pointerDown(scrim);
    fireEvent.click(scrim);
    expect(onApprove).not.toHaveBeenCalled();
    expect(onDeny).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("FIX A: focuses the Deny button on mount (the safe default for a stray Enter keypress)", () => {
    renderPanel();
    expect(screen.getByRole("button", { name: "Deny" })).toHaveFocus();
  });

  // -- FIX B regression guard: independent instances, no shared slot --------

  it("FIX B: two simultaneously-open panels for different nodes are both rendered, and each one's Approve/Deny fires only its own callback", async () => {
    const user = userEvent.setup();
    const nodeA = { onApprove: vi.fn(), onDeny: vi.fn() };
    const nodeB = { onApprove: vi.fn(), onDeny: vi.fn() };

    render(
      <>
        <CodeExecutionApprovalPanel
          nodeId="node-a"
          kind="pycoder"
          code="print('a')"
          awaitingApproval
          busy={false}
          onApprove={nodeA.onApprove}
          onDeny={nodeA.onDeny}
        />
        <CodeExecutionApprovalPanel
          nodeId="node-b"
          kind="code_sandbox"
          code="print('b')"
          awaitingApproval
          busy={false}
          onApprove={nodeB.onApprove}
          onDeny={nodeB.onDeny}
        />
      </>,
    );

    expect(screen.getAllByRole("dialog")).toHaveLength(2);

    const approveButtons = screen.getAllByRole("button", { name: "Approve" });
    expect(approveButtons).toHaveLength(2);
    await user.click(approveButtons[0]);
    expect(nodeA.onApprove).toHaveBeenCalledOnce();
    expect(nodeB.onApprove).not.toHaveBeenCalled();

    // Both panels remain mounted (nothing "stole" the other's slot) - the
    // second one's Deny is independently clickable and only fires its own
    // callback.
    const denyButtons = screen.getAllByRole("button", { name: "Deny" });
    expect(denyButtons).toHaveLength(2);
    await user.click(denyButtons[1]);
    expect(nodeB.onDeny).toHaveBeenCalledOnce();
    expect(nodeA.onDeny).not.toHaveBeenCalled();

    expect(screen.getAllByRole("dialog")).toHaveLength(2);
  });

  // -- kind-specific warning copy (regression guard against softening it) ---

  it("SECURITY-COPY: PyCoder shows the exact legacy phrase 'there is no sandboxing'", () => {
    renderPanel({ kind: "pycoder" });
    expect(screen.getByText(/there is no sandboxing/)).toBeInTheDocument();
    expect(
      screen.getByText(
        /This will run AI-generated Python code in a persistent local session with the full privileges of your user account \(there is no sandboxing\)\. If execution fails, you will be asked to approve each automatically repaired version before it runs\./,
      ),
    ).toBeInTheDocument();
  });

  it("SECURITY-COPY: Code-Sandbox shows the exact legacy phrase 'isolates installed packages, not the operating system'", () => {
    renderPanel({ kind: "code_sandbox" });
    expect(
      screen.getByText(/isolates installed packages, not the operating system/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /This will run Python code inside an isolated virtual environment with the full privileges of your user account \(the environment isolates installed packages, not the operating system\)\./,
      ),
    ).toBeInTheDocument();
  });

  it("does not show the other kind's warning sentence", () => {
    renderPanel({ kind: "pycoder" });
    expect(screen.queryByText(/isolates installed packages/)).toBeNull();
  });

  // -- ADR-005 stage 5.4: backend-computed resource-limits addendum ---------

  it("shows the resource-limits text from the execution-limits topic regardless of kind", () => {
    // PLAN-2026-08-24 H5: Py-Coder retired - this component's own "pycoder"
    // kind arm has no live caller left, so the resource-limits sentence no
    // longer varies by kind; both fixtures below render the same text.
    renderPanelWithExecutionLimits({ kind: "code_sandbox" });
    expect(
      screen.getByText(
        "Execution is capped at approximately 2 GB of memory and 64 concurrent processes. Binary packages only.",
      ),
    ).toBeInTheDocument();
  });

  it("renders no resource-limits paragraph when no ExecutionLimitsProvider is present (existing standalone renderPanel helper)", () => {
    // Every other test in this file uses the plain renderPanel() helper with
    // no Provider ancestor at all - confirms that degrades gracefully to
    // "no addendum shown", not a crash or a blank/misleading paragraph.
    renderPanel({ kind: "pycoder" });
    expect(screen.queryByText(/Execution is capped/)).toBeNull();
  });

  it("renders no resource-limits paragraph when the topic's snapshot has blank text", () => {
    renderPanelWithExecutionLimits(
      { kind: "pycoder" },
      {
        schemaVersion: 1,
        minCompatibleSchemaVersion: 1,
        revision: 1,
        codeSandboxResourceLimitsText: "",
      },
    );
    expect(screen.queryByText(/Execution is capped/)).toBeNull();
  });

  it("REVIEW-FIX: the resource-limits paragraph carries the muted informational class, not the warning's alarm class", () => {
    // Adversarial review found that no test scoped an assertion to either
    // paragraph's actual className, so a mutation swapping
    // code-exec-approval-warning <-> code-exec-approval-resource-limits
    // (e.g. from a future refactor consolidating the two, or copy-pasting
    // WARNING_TEXT's own paragraph as a template) would pass every
    // text-content-only test in this file undetected - see styles.css's
    // own comment on why these two colors are deliberately different
    // (alarming vs. reassuring information).
    // Queries document.body, not the RTL `container` return value: this
    // panel renders via createPortal(..., document.body) (see the
    // component's own module doc), so its DOM nodes live outside the
    // container render() normally mounts into.
    renderPanelWithExecutionLimits({ kind: "pycoder" });
    const resourceLimitsP = document.body.querySelector(".code-exec-approval-resource-limits");
    const warningP = document.body.querySelector(".code-exec-approval-warning");
    expect(resourceLimitsP).not.toBeNull();
    expect(warningP).not.toBeNull();
    expect(resourceLimitsP).toHaveTextContent(/Execution is capped/);
    expect(resourceLimitsP!.className).not.toContain("code-exec-approval-warning");
    expect(warningP!.className).not.toContain("code-exec-approval-resource-limits");
    expect(warningP).toHaveTextContent(/there is no sandboxing/);
  });

  // -- FIX C regression guard: code_sandbox requirements/repair disclosure --

  it("FIX C: Code-Sandbox warning also discloses that repaired code needs its own approval", () => {
    renderPanel({ kind: "code_sandbox" });
    expect(
      screen.getByText(/you will be asked to approve each automatically repaired version before it runs/),
    ).toBeInTheDocument();
  });

  it("FIX C: renders a labeled 'Packages to be installed' block for code_sandbox when requirements is supplied", () => {
    renderPanel({ kind: "code_sandbox", requirements: "numpy\npandas==2.2.0" });
    expect(screen.getByText("Packages to be installed")).toBeInTheDocument();
    expect(screen.getByText(/numpy/)).toBeInTheDocument();
    expect(screen.getByText(/pandas==2\.2\.0/)).toBeInTheDocument();
  });

  it("FIX C: does not render the Packages block when requirements is blank or omitted", () => {
    renderPanel({ kind: "code_sandbox", requirements: "" });
    expect(screen.queryByText("Packages to be installed")).toBeNull();
    renderPanel({ kind: "code_sandbox", requirements: undefined });
    expect(screen.queryByText("Packages to be installed")).toBeNull();
  });

  it("FIX C: never renders the Packages block for pycoder, even if a requirements value were somehow supplied", () => {
    renderPanel({
      kind: "pycoder",
      ...({ requirements: "numpy" } as Partial<Parameters<typeof CodeExecutionApprovalPanel>[0]>),
    });
    expect(screen.queryByText("Packages to be installed")).toBeNull();
  });

  // -- ADR-005 stage 5.5: source-build escalation checkbox --------------------

  it("renders the source-build checkbox for code_sandbox alongside a non-blank Packages block", () => {
    renderPanel({ kind: "code_sandbox", requirements: "numpy" });
    expect(
      screen.getByRole("checkbox", { name: /Allow building packages from source/ }),
    ).toBeInTheDocument();
  });

  it("does not render the source-build checkbox for code_sandbox when requirements is blank or omitted", () => {
    renderPanel({ kind: "code_sandbox", requirements: "" });
    expect(screen.queryByRole("checkbox")).toBeNull();
    renderPanel({ kind: "code_sandbox", requirements: undefined });
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("never renders the source-build checkbox for pycoder, even if requirements were somehow supplied", () => {
    renderPanel({
      kind: "pycoder",
      ...({ requirements: "numpy" } as Partial<Parameters<typeof CodeExecutionApprovalPanel>[0]>),
    });
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("the checkbox reflects the current allowSourceBuilds prop value", () => {
    renderPanel({ kind: "code_sandbox", requirements: "numpy", allowSourceBuilds: true });
    expect(screen.getByRole("checkbox")).toBeChecked();
  });

  it("defaults to unchecked when allowSourceBuilds is omitted", () => {
    renderPanel({ kind: "code_sandbox", requirements: "numpy" });
    expect(screen.getByRole("checkbox")).not.toBeChecked();
  });

  it("toggling the checkbox calls onToggleAllowSourceBuilds with the new value, immediately - not deferred to Approve", async () => {
    const user = userEvent.setup();
    const onToggleAllowSourceBuilds = vi.fn();
    renderPanel({
      kind: "code_sandbox",
      requirements: "numpy",
      allowSourceBuilds: false,
      onToggleAllowSourceBuilds,
    });
    await user.click(screen.getByRole("checkbox"));
    expect(onToggleAllowSourceBuilds).toHaveBeenCalledExactlyOnceWith(true);
  });

  it("the checkbox is disabled while busy, matching Approve/Deny", () => {
    renderPanel({ kind: "code_sandbox", requirements: "numpy", busy: true });
    expect(screen.getByRole("checkbox")).toBeDisabled();
  });

  it("clicking the checkbox does not call onApprove or onDeny", async () => {
    const user = userEvent.setup();
    const { onApprove, onDeny } = renderPanel({ kind: "code_sandbox", requirements: "numpy" });
    await user.click(screen.getByRole("checkbox"));
    expect(onApprove).not.toHaveBeenCalled();
    expect(onDeny).not.toHaveBeenCalled();
  });

  // -- code rendering + security ---------------------------------------------

  it("renders the pending code verbatim through the markdown pipeline as a syntax-highlighted fenced block", () => {
    renderPanel({ code: "def add(a, b):\n    return a + b" });
    // rehype-highlight splits the line across several <span> tokens, so the
    // full phrase is never one text node - assert against the code block's
    // combined textContent instead (same approach GitlinkNodeView's own
    // diff-rendering test uses via document.querySelector("pre code")).
    const codeBlock = document.querySelector("pre code");
    expect(codeBlock).not.toBeNull();
    expect(codeBlock!.textContent).toContain("def add(a, b):");
    expect(codeBlock!.textContent).toContain("return a + b");
  });

  it("SECURITY: pending code containing a literal <img onerror> tag never becomes a real rendered img element", () => {
    renderPanel({ code: '<img src="x" onerror="alert(1)">\nprint("hi")' });
    expect(document.querySelector("img")).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
  });

  // -- Approve/Deny: zero-argument callbacks only ----------------------------

  it("Approve calls onApprove with NO arguments", async () => {
    const user = userEvent.setup();
    const { onApprove } = renderPanel();
    await user.click(screen.getByRole("button", { name: "Approve" }));
    expect(onApprove).toHaveBeenCalledExactlyOnceWith();
  });

  it("Deny calls onDeny with NO arguments", async () => {
    const user = userEvent.setup();
    const { onDeny } = renderPanel();
    await user.click(screen.getByRole("button", { name: "Deny" }));
    expect(onDeny).toHaveBeenCalledExactlyOnceWith();
  });

  // -- busy gate --------------------------------------------------------------

  it("both Approve and Deny are disabled while busy is true", () => {
    renderPanel({ busy: true });
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Deny" })).toBeDisabled();
  });

  it("both buttons are enabled when busy is false", () => {
    renderPanel({ busy: false });
    expect(screen.getByRole("button", { name: "Approve" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Deny" })).toBeEnabled();
  });

  // -- R8a finding #17: focus trap must exclude disabled buttons ------------

  it("R8a finding #17: while busy, Tab from the code display does not leak focus out of the panel", () => {
    // Before this fix, FOCUSABLE included disabled buttons: with busy=true
    // both Deny and Approve are disabled, so "last" was the disabled
    // Approve button. Tab from the code display (first, not last) never
    // matched the wrap condition, preventDefault() never fired, and the
    // browser's own native Tab order - which correctly skips disabled
    // buttons - walked straight out of the panel (its only other two
    // stops both being disabled) onto whatever's next in the document.
    // This is the one thing this panel exists to make impossible during a
    // mandatory security approval.
    renderPanel({ busy: true });
    const codeDisplay = document.querySelector(".code-exec-approval-code") as HTMLElement;
    const dialog = screen.getByRole("dialog");
    codeDisplay.focus();
    expect(document.activeElement).toBe(codeDisplay);

    fireEvent.keyDown(dialog, { key: "Tab" });

    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it("R8a finding #17: a focus escape past the trap is pulled back into the panel (backstop)", () => {
    // Simulates a leak the primary trap missed by some cause other than the
    // now-fixed disabled-button one. This panel shares overlays.tsx's own
    // Tab-gated useFocusEscapeBackstop (see that hook's doc comment) rather
    // than a hand-written unconditional listener - an earlier draft of this
    // fix used an unconditional one and infinite-looped the instant two
    // panels were open at once (see the "FIX B" test below), each treating
    // the other's redirect as its own escape. The gating means the backstop
    // only acts right after a real Tab keydown - fireEvent.keyDown(document,
    // ...), not user.keyboard, for the same reason as overlays.test.tsx's
    // twin of this test: dispatching directly at document arms the flag via
    // this hook's capture-phase listener without also reaching the panel's
    // own bubble-phase trap, isolating "Tab pressed but the primary trap
    // missed it" from "Tab was handled correctly."
    renderPanel({ busy: false });
    const outside = document.createElement("button");
    document.body.appendChild(outside);
    try {
      fireEvent.keyDown(document, { key: "Tab" });
      outside.focus();
      expect(screen.getByRole("dialog").contains(document.activeElement)).toBe(true);
    } finally {
      outside.remove();
    }
  });
});
