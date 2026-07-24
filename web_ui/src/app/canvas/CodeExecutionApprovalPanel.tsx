import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";

/**
 * The mandatory human-approval gate (Qt-removal plan R5.4, corrected in the
 * R5.4 post-review fix pass) - rendered by BOTH PyCoderNodeView and
 * CodeSandboxNodeView whenever their own node-kind-specific
 * `*AwaitingApproval` flag is true. One component, not two copies, because
 * the gate itself (show the pending code, get an explicit yes/no, forward
 * ONLY a requestId never anything content-bearing) is identical across both
 * plugins - only the warning sentence (and, for code_sandbox, a requirements
 * disclosure) differs, and legacy's own sentences must survive VERBATIM (see
 * WARNING_TEXT below) rather than be paraphrased into something friendlier
 * than what is actually true.
 *
 * ARCHITECTURE (post-review correction - do not revert this): this is
 * DELIBERATELY NOT built on the shared Dialog/useOverlays() primitive from
 * ../overlays/overlays, even though every other modal in this app is. Two
 * independent reasons, both fatal for THIS specific dialog:
 *
 * 1. Dialog gives every surface a working Escape key, scrim-click, and a
 *    close (X) button - all three call overlays.close() directly, NONE of
 *    which resolve onApprove/onDeny. For a chrome dialog (Settings, Search)
 *    that is exactly correct: dismissing it does nothing because there was
 *    nothing pending. For THIS dialog it is a real bug: dismissing it left
 *    the node genuinely stuck - *_awaiting_approval stays true server-side,
 *    the approval_future stays pending forever (no timeout by design), and
 *    the busy marker stays claimed - with nothing that ever reopens the
 *    dialog (the old reopening effect only fired on the false-to-true
 *    transition, which had already happened). Legacy's own QMessageBox (the
 *    thing this replaces) never had this problem: a QMessageBox with a
 *    defined default button resolves Escape/Alt+F4 to that default button's
 *    action, so it is never left unresolved by a dismissal gesture. THE FIX:
 *    this component has ZERO passive-dismissal affordances. No Escape
 *    handler, no scrim-click-to-close, no X/close button. The ONLY two ways
 *    to make this panel disappear are clicking Approve or clicking Deny -
 *    the correct, literal reading of "mandatory approval".
 *
 * 2. overlays.open()/isOpen() is a single global "one surface open at a
 *    time" registry - correct for app-chrome overlays like Settings/Search,
 *    which really are mutually exclusive, but WRONG for per-node approval
 *    dialogs: two different nodes can legitimately need review at the same
 *    time (a user starting Py-Coder runs on two different nodes a few
 *    seconds apart is a completely ordinary sequence, not a hypothetical).
 *    Under the old design, the second node's overlays.open() call would
 *    silently steal the visible slot from the first, and the losing node's
 *    dialog would never come back (same "reopening effect only fires once"
 *    gap as above). THE FIX: because this is now a small, self-contained
 *    modal - its own fixed-position overlay + scrim, rendered directly by
 *    whichever NodeView needs it, conditionally, whenever ITS OWN
 *    awaitingApproval flag is true - there is no shared global slot to
 *    collide over in the first place. Each node's instance is naturally
 *    independent; React mounts one per node that needs it. See
 *    CodeExecutionApprovalPanel.test.tsx's two-simultaneous-instances test
 *    and PyCoderNodeView.test.tsx/CodeSandboxNodeView.test.tsx for the
 *    regression guard.
 *
 * Server-initiated, not user-initiated: unlike GitlinkNodeView's own Apply
 * confirmation (which the user opens by clicking a button), this pause is
 * something the BACKEND decided to interrupt the user with - a code
 * execution is sitting there waiting on a human yes/no with the full
 * privileges of the user's own account. There is no auto-open effect to
 * write here at all (unlike the old Dialog-backed version) - this component
 * simply renders its modal markup whenever awaitingApproval is true and
 * renders nothing otherwise; that IS "auto-open", with no separate act of
 * registering itself anywhere.
 *
 * CRITICAL security property, enforced structurally, not just by convention:
 * onApprove/onDeny are BOTH zero-argument callbacks. This component has no
 * prop, no state, no code path capable of naming a DIFFERENT requestId than
 * whatever the caller (SceneCanvas's toFlowNodes) already closed over from
 * the CURRENT scene snapshot's own pendingRequestId for this node - see
 * SceneCanvas.tsx's own onApprove/onDeny closures for where that id is
 * actually read and forwarded to sceneStore's approveCodeExecution/
 * denyCodeExecution. The UI layer is structurally unable to approve or deny
 * a request other than the one the server is actually holding.
 *
 * Security note, not a style preference: the pending code is rendered
 * through the exact same react-markdown + remarkGfm + rehypeHighlight
 * pipeline every sibling node view uses (see GitlinkNodeView's own
 * toDiffFence/CodeNodeView's own toFencedCodeBlock for the same technique
 * applied to different content) - no rehype-raw, no
 * dangerouslySetInnerHTML anywhere in this file. Fencing it as ```python
 * only affects syntax-colorization; the pipeline never interprets any of it
 * as live markup either way.
 *
 * Requirements disclosure (post-review FIX C): CodeSandboxNode's one
 * mandatory approval step must show the requirements.txt-style manifest that
 * will be pip-installed (from PyPI, potentially running arbitrary
 * build-backend code) immediately after Approve, before the reviewed code
 * itself ever runs - legacy's own approval dialog
 * (graphlink_window_actions.py) built and displayed a package-summary string
 * enumerating every declared package before asking Yes/No, and this omission
 * was a real security-relevant regression, not a cosmetic gap. `requirements`
 * is optional and is only ever rendered for kind="code_sandbox" (pycoder has
 * no such concept) - CodeSandboxNodeView passes data.codeSandboxRequirements
 * straight through, since that field already reflects the exact manifest
 * run_code_sandbox reads synchronously at the moment Run is dispatched (no
 * new backend wiring was needed for this).
 */

export type CodeExecutionKind = "pycoder" | "code_sandbox";

// Verbatim legacy sentences (graphlink_window_actions.py, confirmed during
// the R5.4 design/recon pass and reused unchanged in backend/agents.py's own
// module comment) - do NOT soften or paraphrase either of these. The
// "there is no sandboxing" / "isolates installed packages, not the operating
// system" substrings are the load-bearing honesty this copy exists for, and
// are directly regression-tested in CodeExecutionApprovalPanel.test.tsx. The
// repair-loop-reexecution sentence is appended to BOTH kinds (post-review
// FIX C) since backend/agents.py's start_code_sandbox_run has the exact same
// "approve once, repair-and-retry under that one approval" behavior as
// start_pycoder_run's ai_driven path - omitting it for code_sandbox would be
// disclosing only half of what one Approve click actually authorizes.
const WARNING_TEXT: Record<CodeExecutionKind, string> = {
  pycoder:
    "This will run AI-generated Python code in a persistent local session with the full privileges of your user account (there is no sandboxing). If execution fails, automatically repaired versions of this code may run under this same approval.",
  code_sandbox:
    "This will run Python code inside an isolated virtual environment with the full privileges of your user account (the environment isolates installed packages, not the operating system). If execution fails, automatically repaired versions of this code may run under this same approval.",
};

const DIALOG_TITLE: Record<CodeExecutionKind, string> = {
  pycoder: "Approve Py-Coder Execution?",
  code_sandbox: "Approve Sandbox Execution?",
};

/** Wraps the pending code in a markdown fenced ```python code block so
 * ReactMarkdown + rehype-highlight can syntax-highlight it for free - same
 * technique CodeNodeView's own toFencedCodeBlock / GitlinkNodeView's own
 * toDiffFence use for their own content. */
function toPythonFence(code: string): string {
  return "```python\n" + code + "\n```";
}

// Scoped to THIS panel's own three intended focus stops (the code display,
// Deny, Approve) - deliberately narrower than overlays.tsx's own shared
// FOCUSABLE selector (which also matches inputs/selects/links/textareas),
// since nothing else in this panel's markup is ever meant to receive focus.
const FOCUSABLE = 'button, [tabindex]:not([tabindex="-1"])';

export interface CodeExecutionApprovalPanelProps {
  /** No longer used to register with any shared overlay registry (there is
   * none here anymore - see module doc). Kept as a per-node identifier for
   * the rendered DOM (data-node-id) so two simultaneously-open instances for
   * different nodes are independently addressable in tests/devtools. Never
   * sent anywhere over the wire. */
  nodeId: string;
  kind: CodeExecutionKind;
  code: string;
  awaitingApproval: boolean;
  /** code_sandbox ONLY (post-review FIX C) - the pending requirements.txt-
   * style manifest that will be pip-installed immediately after Approve,
   * before the reviewed code itself ever runs. Ignored entirely for
   * kind="pycoder" (no such concept there). Rendered only when non-blank. */
  requirements?: string;
  /** Disables both buttons while the caller's own click handler is waiting
   * out the brief window between a click and the next scene snapshot
   * reflecting it - prevents a double-fire (e.g. Approve clicked twice
   * before awaitingApproval flips back to false). Owned and reset by the
   * caller (PyCoderNodeView/CodeSandboxNodeView), not by this component -
   * it has no way to know when "the next scene snapshot" has arrived on its
   * own. */
  busy: boolean;
  onApprove: () => void;
  onDeny: () => void;
}

export function CodeExecutionApprovalPanel({
  nodeId,
  kind,
  code,
  awaitingApproval,
  requirements,
  busy,
  onApprove,
  onDeny,
}: CodeExecutionApprovalPanelProps) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const denyButtonRef = useRef<HTMLButtonElement | null>(null);

  // Focus the Deny button the instant the panel appears - the safe default a
  // stray Enter/Space keypress lands on. There is no dismiss path here (see
  // module doc), so unlike overlays.tsx's own Dialog (which focuses whatever
  // the FIRST focusable descendant happens to be, close button included),
  // this panel focuses a SPECIFIC button deliberately rather than generically
  // walking the DOM for one.
  useEffect(() => {
    if (awaitingApproval) denyButtonRef.current?.focus();
  }, [awaitingApproval]);

  // Tab focus trap scoped to this panel's own three focusable stops (the
  // scrollable code display, Deny, Approve) - Tab/Shift+Tab must never let
  // focus escape to the rest of the page while a mandatory approval is
  // pending. Deliberately has NO Escape branch at all (unlike overlays.tsx's
  // own Dialog focus trap, which lives alongside that component's Escape-
  // closes-everything effect) - see module doc's FIX A.
  useEffect(() => {
    if (!awaitingApproval) return;
    const panel = panelRef.current;
    if (!panel) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Tab") return;
      const focusables = [...panel!.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    panel.addEventListener("keydown", onKeyDown);
    return () => panel.removeEventListener("keydown", onKeyDown);
  }, [awaitingApproval]);

  if (!awaitingApproval) return null;

  const showRequirements = kind === "code_sandbox" && !!requirements?.trim();

  return (
    // Deliberately NO onClick/onPointerDown handler on this scrim - clicking
    // it does nothing at all (no light-dismiss), unlike overlays.tsx's own
    // .overlay-scrim, whose onPointerDown closes on a direct hit. Reusing the
    // .overlay-scrim/.overlay-dialog class names purely for visual
    // consistency (fixed full-viewport centering, matching colors/shadows) -
    // this div is NOT registered with, and shares no state with, the
    // useOverlays() registry those classes were originally styled for.
    <div className="overlay-scrim code-exec-approval-scrim" data-node-id={nodeId}>
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={DIALOG_TITLE[kind]}
        tabIndex={-1}
        className="overlay-dialog code-exec-approval-dialog"
      >
        <header className="overlay-dialog-header">
          <span className="overlay-dialog-title">{DIALOG_TITLE[kind]}</span>
        </header>
        <div className="overlay-dialog-body">
          <p className="code-exec-approval-warning">{WARNING_TEXT[kind]}</p>
          {showRequirements && (
            <div className="code-exec-approval-requirements">
              <span className="code-exec-approval-requirements-label">Packages to be installed</span>
              <pre className="code-exec-approval-requirements-list">{requirements}</pre>
            </div>
          )}
          <div
            className="chat-node-content code-exec-approval-code"
            tabIndex={0}
            role="region"
            aria-label="Pending code"
          >
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeHighlight]}>
              {toPythonFence(code)}
            </ReactMarkdown>
          </div>
          <div className="code-exec-approval-actions">
            <button
              ref={denyButtonRef}
              type="button"
              className="code-exec-approval-deny-btn"
              disabled={busy}
              onClick={() => onDeny()}
            >
              Deny
            </button>
            <button
              type="button"
              className="code-exec-approval-approve-btn"
              disabled={busy}
              onClick={() => onApprove()}
            >
              Approve
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
