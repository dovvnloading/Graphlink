import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CodeExecutionApprovalPanel, type CodeExecutionKind } from "./CodeExecutionApprovalPanel";

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
  render(<CodeExecutionApprovalPanel {...props} />);
  return { onApprove, onDeny };
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

  it("FIX A: there is no close/X button anywhere - Approve and Deny are the only two buttons rendered", () => {
    renderPanel();
    const buttons = screen.getAllByRole("button");
    expect(buttons).toHaveLength(2);
    expect(buttons.map((b) => b.textContent)).toEqual(["Deny", "Approve"]);
    expect(screen.queryByRole("button", { name: /close/i })).toBeNull();
    expect(screen.queryByLabelText(/close/i)).toBeNull();
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
        /This will run AI-generated Python code in a persistent local session with the full privileges of your user account \(there is no sandboxing\)\. If execution fails, automatically repaired versions of this code may run under this same approval\./,
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

  // -- FIX C regression guard: code_sandbox requirements/repair disclosure --

  it("FIX C: Code-Sandbox warning also discloses the repair-loop re-execution risk", () => {
    renderPanel({ kind: "code_sandbox" });
    expect(
      screen.getByText(/automatically repaired versions of this code may run under this same approval/),
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
});
