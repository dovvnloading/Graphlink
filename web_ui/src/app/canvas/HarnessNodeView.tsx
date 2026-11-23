import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type RefObject } from "react";
import { createPortal } from "react-dom";
import { useReactFlow, type Node, type NodeProps } from "@xyflow/react";
import { motionDuration } from "../reducedMotion";
import { useModelCapabilityPreview, useComposerModelStore } from "../context/ModelCapabilityContext";
import { OverlayProviderIfMissing, useOverlays } from "../overlays/overlays";
import { ModelPicker } from "../chrome/ModelPicker";
import type { ComposerStore } from "../chrome/composerStore";
import { CollapseToggleButton } from "./CollapseToggleButton";
import type { MenuPosition } from "./menuPosition";
import { NodeMenu } from "./NodeMenu";
import { NodeHeading } from "./NodeHeading";
import { NodeShell } from "./NodeShell";
import { useLodVisibility } from "./useLodVisibility";
import { HarnessWorkspaceFiles } from "./AgentWorkspaceBrowser";
import type { HarnessWorkspaceListing } from "./harnessWorkspaceFiles";
import { MAX_HARNESS_TASK_LENGTH, exceedsHarnessTaskLimit } from "../../lib/harnessTaskLimit";
import { Icon } from "../ui/icons/Icon";
import { IconButton } from "../ui/IconButton";
import { Button } from "../ui/Button";
import { MenuItem } from "../ui/Menu";
import { Select } from "../ui/Select";
import { Checkbox } from "../ui/Checkbox";
import { TextField } from "../ui/TextField";
import { TextArea } from "../ui/TextArea";
import { Tooltip } from "../ui/Tooltip";
import { Badge, StatusPill } from "../ui/Badge";
import { InlineAlert } from "../ui/InlineAlert";
import { Disclosure } from "../ui/Disclosure";
import { Spinner } from "../ui/Spinner";
import { formatDuration, formatTokens } from "../ui/format";

/**
 * PLAN-2026-08-24 H1: the workspace agent node. Everything rendered here
 * is real backend state off the wire row (HarnessState -> the harness*
 * fields); every button fires a real intent through sceneStore. The
 * conversation itself deliberately never crosses the wire (it lives in
 * the node's workspace transcript) - what this card shows is the LAST
 * task's prompt and reply plus the run's own activity log, the same
 * "render surface only" posture the wire contract documents.
 *
 * Layout and class vocabulary mirror PlanNodeView (status band, detail
 * line, the shared chat-node-tool-invocations activity disclosure,
 * plan-node button classes) rather than inventing a parallel idiom.
 */

/** Screen-space pan to keep the approval decision above the composer and
 * inside the canvas. Does not change the user's chosen zoom level. */
export function approvalPanDelta(
  actions: Pick<DOMRect, "left" | "right" | "top" | "bottom">,
  stage: Pick<DOMRect, "left" | "right" | "top" | "bottom">,
  margin = 20,
): { x: number; y: number } {
  const availableLeft = stage.left + margin;
  const availableRight = stage.right - margin;
  const availableTop = stage.top + margin;
  const availableBottom = stage.bottom - margin;
  return {
    x: actions.right > availableRight ? availableRight - actions.right :
      actions.left < availableLeft ? availableLeft - actions.left : 0,
    y: actions.bottom > availableBottom ? availableBottom - actions.bottom :
      actions.top < availableTop ? availableTop - actions.top : 0,
  };
}

export interface HarnessHook {
  phase: "before_tool" | "after_tool" | "end_run";
  command: string;
  enabled: boolean;
}

export interface HarnessActivityRowData {
  tool: string;
  summary: string;
  outcome: string;
  elapsedMs: number;
}

export interface HarnessPlanStepData {
  text: string;
  status: string;
}

export interface HarnessNodeData extends Record<string, unknown> {
  title?: string;
  harnessGoal: string;
  harnessReply: string;
  harnessStatus: string;
  harnessStatusDetail: string;
  harnessRunId: string;
  harnessActivity: HarnessActivityRowData[];
  harnessAwaitingApproval: boolean;
  harnessApprovalToolName: string;
  harnessApprovalSummary: string;
  harnessApprovalId: string;
  harnessApprovalSessionOffered: boolean;
  harnessApprovalNetworkOffered: boolean;
  harnessPlan: HarnessPlanStepData[];
  harnessAwaitingQuestion: boolean;
  harnessQuestion: string;
  harnessContextTokens: number;
  harnessMaxContextTokens: number;
  harnessCompactions: number;
  harnessWorkspacePath: string;
  harnessWorkspaceActive: string;
  harnessShell: string;
  harnessShellAllowRules: string[];
  harnessExecutionProfile: string;
  harnessPermissionMode: string;
  harnessTaskPermissionMode: string;
  harnessNetworkAlways: boolean;
  harnessMaxTurns: number;
  harnessSpentTurns: number;
  harnessSpentTokens: number;
  isCollapsed: boolean;
  pendingRequestId: string | null;
  onToggleCollapse: () => void;
  onDelete: () => void;
  onSend: (text: string) => Promise<boolean>;
  onCancel: () => void;
  onUndoRun: () => void;
  onApproveTool: (approvalId?: string) => void;
  onApproveNetwork: (approvalId: string, scope: "once" | "task") => void;
  onApproveToolForSession: (approvalId?: string) => void;
  onDenyTool: (approvalId?: string) => void;
  onAnswerQuestion: (answer: string) => void;
  onPickWorkspace: () => void;
  onUseScratch: () => void;
  onListWorkspaceFiles: (path: string, cursor?: string) => Promise<HarnessWorkspaceListing>;
  onOpenWorkspaceFile: (path: string, expectedRoot: string) => Promise<void>;
  onAddWorkspaceFileToKnowledge: (path: string, expectedRoot: string) => Promise<unknown>;
  onSubscribeKnowledgeIngest: (listener: (payload: Record<string, unknown>) => void) => () => void;
  onWorkspaceTrustChanged: (listener: () => void) => () => void;
  onRevealWorkspace: () => void;
  onSetShell: (shell: string) => void;
  onRemoveShellAllowRule: (rule: string) => void;
  onSetExecutionProfile: (profile: string) => void;
  onSetPermissionMode: (mode: string) => void;
  onSetNetworkAlways: (enabled: boolean) => void;
  onGetIsolationEngine: () => Promise<string | null>;
  onGetHooks: () => Promise<HarnessHook[]>;
  onSetHooks: (hooks: HarnessHook[]) => Promise<void>;
}

export type HarnessFlowNode = Node<HarnessNodeData, "harness">;

/** Same outside-click/Escape dismiss pattern every sibling node menu uses
 * (ChatNodeMenu/ArtifactNodeMenu/CodeSandboxNodeMenu/PlanNodeMenu/...). */
// -- card-level menu -------------------------------------------------------

function HarnessNodeMenu({
  position,
  isCollapsed,
  onToggleCollapse,
  onDelete,
  onClose,
}: {
  position: MenuPosition;
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  return (
    <NodeMenu position={position} onClose={onClose} className="chat-node-menu">
      <MenuItem icon="minimize" label={isCollapsed ? "Expand" : "Collapse"} onSelect={onToggleCollapse} />
      <MenuItem label="Delete node" destructive onSelect={onDelete} />
    </NodeMenu>
  );}

/** Node settings are portaled to the document, not rendered inside the
 * transformed React Flow node. Otherwise fixed coordinates drift with zoom. */
function AgentSettingsPopover({
  position, shell, rules, onSetShell, onRemoveRule, profile, onSetExecutionProfile,
  permissionMode, onSetPermissionMode, allowNetwork, onSetNetworkAlways, onGetIsolationEngine,
  onGetHooks, onSetHooks, onClose, triggerRef,
}: {
  position: MenuPosition;
  shell: string;
  rules: string[];
  onSetShell: (shell: string) => void;
  onRemoveRule: (rule: string) => void;
  profile: string;
  onSetExecutionProfile: (profile: string) => void;
  onSetPermissionMode: (mode: string) => void;
  permissionMode: string;
  allowNetwork: boolean;
  onSetNetworkAlways: (enabled: boolean) => void;
  onGetIsolationEngine: () => Promise<string | null>;
  onGetHooks: () => Promise<HarnessHook[]>;
  onSetHooks: (hooks: HarnessHook[]) => Promise<void>;
  onClose: () => void;
  triggerRef: RefObject<HTMLButtonElement | null>;
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [placement, setPlacement] = useState(position);
  // undefined = checking, null = neither Docker nor Podman on the backend PATH.
  const [engine, setEngine] = useState<string | null | undefined>(undefined);
  const [hooks, setHooks] = useState<HarnessHook[] | null>(null);
  const [hooksError, setHooksError] = useState("");
  const [savingHooks, setSavingHooks] = useState(false);
  const [savedHooks, setSavedHooks] = useState(false);
  useEffect(() => {
    let active = true;
    void onGetHooks()
      .then((loaded) => { if (active) setHooks(loaded); })
      .catch(() => { if (active) setHooksError("Could not load agent hooks."); });
    return () => { active = false; };
  }, [onGetHooks]);
  const changeHook = (index: number, patch: Partial<HarnessHook>) => {
    setHooks((previous) => previous?.map((item, i) => i === index ? { ...item, ...patch } : item) ?? null);
    setSavedHooks(false);
  };
  const saveHooks = async () => {
    if (hooks === null || hooks.some((hook) => !hook.command.trim())) return;
    setSavingHooks(true);
    setHooksError("");
    try {
      await onSetHooks(hooks);
      setSavedHooks(true);
    } catch {
      setHooksError("Could not save agent hooks; no changes were applied.");
    } finally {
      setSavingHooks(false);
    }
  };
  useEffect(() => {
    let mounted = true;
    void onGetIsolationEngine()
      .then((found) => { if (mounted) setEngine(found); })
      .catch(() => { if (mounted) setEngine(null); });
    return () => { mounted = false; };
  }, [onGetIsolationEngine]);
  const windows = /Windows|Win32|Win64/i.test(
    typeof navigator === "undefined" ? "" : navigator.userAgent + " " + navigator.platform,
  );
  // auto is a persisted, cross-platform choice, resolved at task launch.
  const selected = windows
    ? shell === "cmd" || shell === "git-bash" ? shell : "powershell"
    : "bash";

  useLayoutEffect(() => {
    const rect = panelRef.current?.getBoundingClientRect();
    if (!rect) return;
    const x = Math.min(
      Math.max(8, position.x), Math.max(8, window.innerWidth - rect.width - 8),
    );
    const y = position.y + rect.height + 8 > window.innerHeight
      ? Math.max(8, position.y - rect.height - 28)
      : position.y;
    setPlacement({ x, y });
  }, [position.x, position.y, engine, hooks]);

  useEffect(() => {
    panelRef.current?.querySelector<HTMLElement>(".gl-select-trigger:not([disabled])")?.focus();
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as HTMLElement;
      if (panelRef.current?.contains(target) || triggerRef.current?.contains(target) || target.closest(".gl-select-panel")) return;
      onClose();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
      } else if (event.key === "Tab" && panelRef.current) {
        // A portaled dialog must not lose its keyboard user behind the
        // canvas. Keep focus in the popup and restore the trigger on close.
        const controls = Array.from(panelRef.current.querySelectorAll<HTMLElement>(
          "button:not([disabled]), select:not([disabled]), input:not([disabled]), textarea:not([disabled])",
        ));
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (first && last) {
          if (event.shiftKey && (document.activeElement === first ||
              !panelRef.current.contains(document.activeElement))) {
            event.preventDefault();
            last.focus();
          } else if (!event.shiftKey && (document.activeElement === last ||
              !panelRef.current.contains(document.activeElement))) {
            event.preventDefault();
            first.focus();
          }
        }
      }
    };
    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    const trigger = triggerRef.current;
    return () => {
      document.removeEventListener("pointerdown", onPointerDown, true);
      document.removeEventListener("keydown", onKeyDown, true);
      if (trigger?.isConnected) trigger.focus();
    };
  }, [onClose, triggerRef]);

  return createPortal(
    <div
      ref={panelRef}
      className="harness-node-settings-popover nodrag nowheel"
      role="dialog"
      aria-label="Agent settings"
      aria-modal="true"
      style={{ position: "fixed", left: placement.x, top: placement.y }}
    >
      <div className="harness-node-settings-heading">
        <strong>Agent settings</strong>
        <IconButton size="sm" icon="close" label="Close agent settings" onClick={onClose} />
      </div>
      <div className="harness-node-settings-field">
        <Select label="Shell" className="nodrag nowheel" value={selected} disabled={!windows}
          onChange={onSetShell} options={windows ? [
            { value: "powershell", label: "PowerShell (default)" },
            { value: "cmd", label: "Command Prompt (cmd)" },
            { value: "git-bash", label: "Git Bash" },
          ] : [{ value: "bash", label: "Bash (default)" }]} />
      </div>
      <div className="harness-node-settings-field">
        <Select label="Execution profile" className="nodrag nowheel"
          value={profile === "isolated" ? "isolated" : "host"}
          onChange={(value) => onSetExecutionProfile(value)} options={[
            { value: "host", label: "Host (default)" },
            { value: "isolated", label: "Isolated (Docker / Podman)", disabled: engine === null },
          ]} />
      </div>
      <div className="harness-node-settings-field">
        <Select label="Permission mode" className="nodrag nowheel" value={permissionMode}
          onChange={(value) => onSetPermissionMode(value)} options={[
            { value: "ask", label: "Ask — approve each action" },
            { value: "auto_edit", label: "Auto-edit — workspace file writes" },
            { value: "autonomous", label: "Autonomous — Isolated only", disabled: profile !== "isolated" },
          ]} />
      </div>
      <p>Scripts, Python execution, dangerous commands, outside-workspace paths and network requests still ask. A looser mode takes effect on the next task. Reopening a graph resets permissions to Ask.</p>
      <Checkbox className="harness-node-settings-field nodrag"
        label="Allow network for every task"
        checked={allowNetwork && permissionMode !== "autonomous"}
        disabled={profile !== "isolated" || permissionMode === "autonomous"}
        onChange={(event) => onSetNetworkAlways(event.target.checked)} />
      {engine === undefined ? (
        <p role="status">Checking Docker / Podman availability…</p>
      ) : engine === null ? (
        <p role="status">No container engine found. Install Docker Desktop (Windows/macOS), Docker Engine (Linux), or Podman to use Isolated execution. Isolated commands never fall back to Host.</p>
      ) : (
        <p role="status">{engine === "docker" ? "Docker" : "Podman"} detected. Isolated runs shell and Python in a container (1 CPU, 512 MiB RAM; workspace at /workspace). Shell networking is {permissionMode === "autonomous" ? "approval-only for each network command" : allowNetwork ? "allowed for every task by the node setting" : "off by default and requestable with per-command approval"}. shell.session and python.exec stay offline; python.install may separately download approved PyPI wheels. Approved Web Research and provider calls still use the backend network. Pull python:3.12-slim with {engine} before your first run.</p>
      )}
      <p>Network settings apply only to Isolated shell.exec. Permissions must be selected and grants approved again after reopening a saved or imported graph. Live changes apply from the next task.</p>
      <div className="harness-node-allow-rules">
        <strong>Allowed exact commands</strong>
        {rules.length === 0 ? (
          <p>No saved rules for this agent.</p>
        ) : (
          <ul aria-label="Allowed exact commands">
            {rules.map((rule) => {
              const parts = rule.split("\t");
              const scoped = parts.length === 5 && parts[0] === "v2";
              const ruleShell = scoped ? parts[1] : parts[0];
              const command = scoped ? parts[4] : parts.slice(1).join("\t");
              const context = scoped ? `${parts[2]} · workspace-bound` : "legacy (inactive)";
              return (
                <li key={rule}>
                  <span className="harness-node-allow-rule-text">
                    <Tooltip label={command}><code>{command}</code></Tooltip>
                    <small>{ruleShell} · {context}</small>
                  </span>
                  <button
                    type="button"
                    className="nodrag"
                    aria-label={`Remove rule: ${command} (${ruleShell})`}
                    onClick={() => onRemoveRule(rule)}
                  >
                    Remove
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <section className="harness-node-hooks" aria-label="Workspace hooks">
        <strong>Workspace hooks</strong>
        <p className="harness-node-hooks-warning">
          Warning: enabled hooks run local commands with your workspace access.
          They use the selected Host or Isolated shell, the normal policy and
          a fresh approval for every invocation, and a 10-second timeout.
          No credentials, model content or tool arguments are supplied to hooks.
          Every execution and its result are recorded in activity and transcript.
          A nonzero before-tool exit blocks the tool and displays its output
          as the reason; successful after-tool output adds a note.
          Changes take effect on the next task.
        </p>
        {hooks === null ? (
          <p role="status">Loading hooks…</p>
        ) : (
          <>
            {hooks.length === 0 && <p>No hooks configured. Hooks are off by default.</p>}
            <ul aria-label="Agent hooks">
              {hooks.map((hook, index) => (
                <li key={index} className="harness-node-hook-row">
                  <Checkbox className="harness-node-hook-enabled nodrag"
                    label={`Enable hook ${index + 1}`} checked={hook.enabled}
                    onChange={(event) => changeHook(index, { enabled: event.target.checked })} />
                  <Select className="harness-node-settings-field nodrag nowheel"
                    label={`Hook phase ${index + 1}`} value={hook.phase}
                    onChange={(value) => changeHook(index, { phase: value as HarnessHook["phase"] })}
                    options={[
                      { value: "before_tool", label: "Before tool (can block)" },
                      { value: "after_tool", label: "After tool (adds note)" },
                      { value: "end_run", label: "End of run" },
                    ]} />
                  <TextField className="harness-node-settings-field nodrag nowheel"
                    label={`Hook command ${index + 1}`} maxLength={4000}
                    value={hook.command} onValueChange={(value) => changeHook(index, { command: value })}
                    placeholder="Command to run in workspace" />
                  <button
                    type="button"
                    className="harness-node-hook-remove nodrag"
                    aria-label={"Remove hook " + (index + 1)}
                    onClick={() => {
                      setHooks((current) => current?.filter((_, i) => i !== index) ?? null);
                      setSavedHooks(false);
                    }}
                  >
                    Remove hook
                  </button>
                </li>
              ))}
            </ul>
            <div className="harness-node-hook-actions">
              <button
                type="button"
                className="nodrag"
                disabled={hooks.length >= 8 || savingHooks}
                onClick={() => {
                  setHooks((current) => [...(current ?? []), {
                    phase: "before_tool", command: "", enabled: false,
                  }]);
                  setSavedHooks(false);
                }}
              >
                Add hook
              </button>
              <button
                type="button"
                className="nodrag"
                disabled={savingHooks || hooks.some((hook) => !hook.command.trim())}
                onClick={() => { void saveHooks(); }}
              >
                {savingHooks ? "Saving…" : "Save hooks"}
              </button>
            </div>
            {savedHooks && <p role="status">Hooks saved for the next task.</p>}
          </>
        )}
        {hooksError && <p role="alert">{hooksError}</p>}
      </section>
    </div>,
    document.body,
  );
}

const UNDOABLE_RUN_STATUSES = new Set(["done", "failed", "stopped", "interrupted"]);

const STATUS_LABELS: Record<string, string> = {
  idle: "Ready",
  running: "Working…",
  done: "Done",
  failed: "Failed",
  stopped: "Stopped",
  interrupted: "Interrupted",
};

/**
 * What the status band says. A parked run is still `running` on the wire -
 * correctly, since the run owns its slot and Stop still applies - but
 * "Working…" is the wrong thing to tell someone whose answer is the only
 * reason it is not working. Nothing is happening until they act, and the
 * one line they are already watching should be what says so.
 */
function statusLabel(data: HarnessNodeData): string {
  if (data.harnessStatus === "running") {
    if (data.harnessAwaitingApproval) return "Waiting for your approval";
    if (data.harnessAwaitingQuestion) return "Waiting for your answer";
  }
  return STATUS_LABELS[data.harnessStatus] ?? data.harnessStatus;
}

/** Parked reads as a warning, not as progress - the same semantic the
 * approval panel's own border already uses. */
function statusClass(data: HarnessNodeData): string {
  const parked = data.harnessAwaitingApproval || data.harnessAwaitingQuestion;
  return parked && data.harnessStatus === "running"
    ? "harness-node-status-waiting"
    : `harness-node-status-${data.harnessStatus}`;
}

// A run binds its root once, at the start, and every tool in it is
// confined to that root - so rebinding mid-run is not a thing that can
// mean anything. The controls say that rather than being inertly grey.
const WORKSPACE_LOCKED_HINT = "The workspace is fixed while the agent is working - stop it first";

// Every non-running status accepts a follow-up: the transcript is the
// resume point, so done/failed/stopped/interrupted/idle all continue the
// same conversation (intents_harness.send refuses only a busy node).
function acceptsInput(status: string): boolean {
  return status !== "running";
}

/**
 * Unified diffs get line-level color while new-file previews stay literal:
 * their full content may itself begin with "+" or "-", which is NOT a diff.
 * The output is text-only React children (never interpreted as HTML).
 */
function renderApprovalSummary(summary: string, toolName: string) {
  const firstLineEnd = summary.indexOf("\n");
  if (
    (toolName !== "fs.write" && toolName !== "fs.edit") ||
    firstLineEnd === -1 ||
    !summary.slice(firstLineEnd + 1).startsWith("--- a/")
  ) {
    return summary;
  }
  const lines = summary.split("\n");
  // A large replacement can have tens of thousands of lines. Keep the
  // complete disclosure visible without mounting a span for every line.
  if (lines.length > 2_000) return summary;
  return lines.map((line, index) => {
    let className = "";
    if (line.startsWith("--- ") || line.startsWith("+++ ")) {
      className = "harness-approval-diff-header";
    } else if (line.startsWith("+")) {
      className = "harness-approval-diff-added";
    } else if (line.startsWith("-")) {
      className = "harness-approval-diff-removed";
    } else if (line.startsWith("@@")) {
      className = "harness-approval-diff-hunk";
    }
    return (
      <span key={index} className={className}>
        {line}{index < lines.length - 1 ? "\n" : ""}
      </span>
    );
  });
}

function AgentModelTrigger({ store, name }: { store: ComposerStore; name: string }) {
  const composer = useSyncExternalStore(store.subscribe, store.getComposer);
  const overlays = useOverlays();
  return (
    <button
      type="button"
      className="harness-node-model-trigger nodrag"
      data-overlay-trigger={name}
      aria-label="Choose agent model"
      aria-haspopup="dialog"
      aria-expanded={overlays.isOpen(name)}
      disabled={!composer.request.canSend}
      onClick={() => overlays.toggle(name, "popover")}
    >
      <span>{composer.route.modelLabel || composer.route.modelId || "Choose model"}</span>
      <Icon name="chevron-down" size="sm" />
    </button>
  );
}

function HarnessNodeViewInner({ data, selected, id }: NodeProps<HarnessFlowNode>) {
  const collapsed = useLodVisibility() || data.isCollapsed;
  const running = data.harnessStatus === "running";
  const modelCapabilities = useModelCapabilityPreview();
  const modelStore = useComposerModelStore();
  const overlays = useOverlays();
  const modelPickerName = `agent-model-${id}`;
  const [draft, setDraft] = useState("");
  const [draftError, setDraftError] = useState("");
  const [sending, setSending] = useState(false);
  const [answerDraft, setAnswerDraft] = useState("");
  const [submittedApprovalId, setSubmittedApprovalId] = useState("");
  const submittedApprovalRef = useRef("");
  const approvalActionsRef = useRef<HTMLDivElement>(null);
  const flow = useReactFlow();
  // Derive the default open state during render; streaming events must not cause an extra commit.
  const [activityChoice, setActivityChoice] = useState<{ runId: string; open: boolean } | null>(null);
  const activityOpen = activityChoice?.runId === data.harnessRunId
    ? activityChoice.open : running && data.harnessActivity.length > 0;
  const setActivityOpen = (open: boolean) => setActivityChoice({ runId: data.harnessRunId, open });
  const activityListRef = useRef<HTMLDivElement>(null);
  const activityErrorCount = data.harnessActivity.filter((row) => row.outcome === "error").length;
  const [menuPosition, setMenuPosition] = useState<MenuPosition | null>(null);
  const [settingsPosition, setSettingsPosition] = useState<MenuPosition | null>(null);
  // A nested Select's exit updates overlays; keep the close callback stable
  // so the settings effect does not restore focus to its opener mid-edit.
  const closeSettings = useCallback(() => setSettingsPosition(null), []);
  const settingsTriggerRef = useRef<HTMLButtonElement>(null);
  const shellApproval = data.harnessApprovalToolName === "shell.exec";
  // Every human consent prompt carries a unique ID, not just Python, Web
  // and shell.exec. The UI must never submit an unbound approval for files
  // or interactive shell commands.
  const sessionGrantTool = ![
    "shell.exec", "shell.session", "python.exec", "python.install",
    "web.fetch", "web.search",
  ].includes(data.harnessApprovalToolName) && !data.harnessApprovalToolName.startsWith("fal.");
  const fileApproval = data.harnessApprovalToolName === "fs.write" || data.harnessApprovalToolName === "fs.edit";
  // Never ask someone to approve an unseen mutation. The backend explains
  // when it cannot safely read the existing file to build the preview.
  const previewUnavailable = fileApproval &&
    data.harnessApprovalSummary.slice(data.harnessApprovalSummary.indexOf("\n") + 1)
      .startsWith("Preview unavailable:");
  const decisionPending = !data.harnessApprovalId || submittedApprovalId === data.harnessApprovalId;

  function decideTool(approved: boolean, alwaysAllow = false): void {
    const decide = alwaysAllow ? data.onApproveToolForSession : approved ? data.onApproveTool : data.onDenyTool;
    const approvalId = data.harnessApprovalId;
    if (!approvalId || submittedApprovalRef.current === approvalId) return;
    submittedApprovalRef.current = approvalId;
    setSubmittedApprovalId(approvalId);
    decide(approvalId);
  }

  function approveNetwork(scope: "once" | "task"): void {
    if (!data.harnessApprovalNetworkOffered) return;
    const approvalId = data.harnessApprovalId;
    if (!approvalId || submittedApprovalRef.current === approvalId) return;
    submittedApprovalRef.current = approvalId;
    setSubmittedApprovalId(approvalId);
    data.onApproveNetwork(approvalId, scope);
  }

  // The panel may grow below the React Flow stage, which ends ABOVE the
  // composer. Focusing Deny alone does not move the transformed canvas.
  // Use the actual on-screen action rect so the buttons, not just the
  // Agent's header, are visible at every viewport size and zoom.
  const revealApprovalActions = useCallback((): void => {
    if (!data.harnessAwaitingApproval || collapsed) return;
    const actions = approvalActionsRef.current;
    const stage = actions?.closest(".react-flow");
    if (!actions || !stage) return;
    const { x, y } = approvalPanDelta(actions.getBoundingClientRect(), stage.getBoundingClientRect());
    if (x !== 0 || y !== 0) {
      const viewport = flow.getViewport();
      void flow.setViewport({ ...viewport, x: viewport.x + x, y: viewport.y + y },
        { duration: motionDuration(200) });
    }
  }, [data.harnessAwaitingApproval, collapsed, flow]);

  // Deny is the safe default. preventScroll avoids document/composer jumps;
  // React Flow's own transform is moved instead.
  useLayoutEffect(() => {
    if (data.harnessAwaitingApproval && !collapsed) {
      revealApprovalActions();
      document.getElementById("harness-node-deny-approval")?.focus({ preventScroll: true });
    }
  }, [data.harnessAwaitingApproval, data.harnessApprovalId, collapsed, revealApprovalActions]);

  // The composer can grow after the tool has already parked, shrinking the
  // actual graph stage. An open activity log or file preview can likewise
  // grow the card. Keep the decision within the newly measured bounds,
  // without reacting to ordinary user-initiated panning.
  useEffect(() => {
    if (!data.harnessAwaitingApproval || collapsed || typeof ResizeObserver === "undefined") return;
    const actions = approvalActionsRef.current;
    const stage = actions?.closest(".react-flow");
    const card = actions?.closest(".scene-node-card");
    if (!stage || !card) return;
    const observer = new ResizeObserver(() => revealApprovalActions());
    observer.observe(stage);
    observer.observe(card);
    return () => observer.disconnect();
  }, [data.harnessAwaitingApproval, data.harnessApprovalId, collapsed, revealApprovalActions]);

  // A new run opens its activity naturally through the derived open state.
  // A manual collapse remains closed until the run ID changes.

  // Same follow-the-log behavior as PlanNodeView's activity list: pinned
  // to the newest row only while running and only while open.
  useEffect(() => {
    if (running && activityOpen) {
      activityListRef.current?.scrollTo({ top: activityListRef.current.scrollHeight });
    }
  }, [data.harnessActivity, running, activityOpen]);

  async function sendDraft(): Promise<void> {
    const text = draft.trim();
    if (!text || sending || data.pendingRequestId || !acceptsInput(data.harnessStatus)) return;
    if (exceedsHarnessTaskLimit(text)) {
      setDraftError(`Agent tasks cannot exceed ${MAX_HARNESS_TASK_LENGTH.toLocaleString()} characters. Shorten the task; your draft is preserved.`);
      return;
    }
    setSending(true);
    setDraftError("");
    try {
      const admitted = await data.onSend(text);
      if (admitted) {
        // The user may keep editing while the admission reply is in flight.
        setDraft((current) => current === draft ? "" : current);
      } else {
        setDraftError("This agent task was not started. Another agent may be running; your draft is preserved.");
      }
    } catch {
      setDraftError("No send acknowledgment arrived. The task may already have started; your draft is preserved. Retrying this draft uses the same operation ID, so it cannot start that task twice.");
    } finally {
      setSending(false);
    }
  }

  function sendAnswer(): void {
    const text = answerDraft.trim();
    if (!text) return;
    data.onAnswerQuestion(text);
    setAnswerDraft("");
  }

  return (
    <NodeShell
      kindClassName="harness-node"
      selected={!!selected}
      collapsed={collapsed}
      onMenuChange={(position) => {
        setMenuPosition(position);
        setSettingsPosition(null);
      }}
      header={
        <NodeHeading className="plan-node-title">
          <Badge className="plan-node-badge">{data.title === "Browser Agent" ? "Browser Agent" : "Agent"}</Badge>
          <span className="plan-node-goal">{data.harnessGoal || "Untitled task"}</span>
          {modelStore && <AgentModelTrigger store={modelStore} name={modelPickerName} />}
          <button ref={settingsTriggerRef} type="button"
            className="harness-node-settings-trigger nodrag"
            aria-label="Agent settings" aria-haspopup="dialog" aria-expanded={Boolean(settingsPosition)}
            onClick={(event) => {
              const rect = event.currentTarget.getBoundingClientRect();
              setMenuPosition(null);
              setSettingsPosition((current) => current ? null : { x: rect.left, y: rect.bottom + 4 });
            }}>
            <Icon name="settings" size="sm" />
          </button>
          <CollapseToggleButton isCollapsed={data.isCollapsed} onToggleCollapse={data.onToggleCollapse} />
        </NodeHeading>
      }
      bodyClassName="plan-node-body"
      menu={
        menuPosition && (
          <HarnessNodeMenu
            position={menuPosition}
            isCollapsed={data.isCollapsed}
            onToggleCollapse={data.onToggleCollapse}
            onDelete={data.onDelete}
            onClose={() => setMenuPosition(null)}
          />
        )
      }
      footer={
        <>
        {settingsPosition && (
          <AgentSettingsPopover
            position={settingsPosition}
            shell={data.harnessShell}
            rules={data.harnessShellAllowRules}
            onSetShell={data.onSetShell}
            onRemoveRule={data.onRemoveShellAllowRule}
            profile={data.harnessExecutionProfile}
            onSetExecutionProfile={data.onSetExecutionProfile}
            permissionMode={data.harnessPermissionMode}
            onSetPermissionMode={data.onSetPermissionMode}
            allowNetwork={data.harnessNetworkAlways}
            onSetNetworkAlways={data.onSetNetworkAlways}
            onGetIsolationEngine={data.onGetIsolationEngine}
            onGetHooks={data.onGetHooks}
            onSetHooks={data.onSetHooks}
            onClose={closeSettings}
            triggerRef={settingsTriggerRef}
          />
        )}
        {modelStore && overlays.isOpen(modelPickerName) && (
          <ModelPicker key={modelPickerName} name={modelPickerName} store={modelStore} />
        )}
        </>
      }
    >
      <div className={`plan-node-status ${statusClass(data)}`}>
        {data.harnessStatus === "running" ? (
          <span className="gl-status-pill" role="status" aria-label={statusLabel(data)}>
            {data.harnessAwaitingApproval || data.harnessAwaitingQuestion
              ? <Icon name="status-approval" size="sm" /> : <Spinner size={12} decorative />}
            <span>{statusLabel(data)}</span>
          </span>
        ) : data.harnessStatus === "done" ? <StatusPill status="Done" />
          : data.harnessStatus === "failed" ? <StatusPill status="Failed" />
          : data.harnessAwaitingApproval ? <StatusPill status="Waiting for approval" />
          : <Badge>{statusLabel(data)}</Badge>}
      </div>
      {!running && !data.pendingRequestId && modelCapabilities?.tools === false && (
        <p className="harness-node-model-warning" role="status">
          {modelCapabilities.modelId} does not support tool calling.{" "}
          {modelCapabilities.suggestedToolModel
            ? `Try installed Ollama model ${modelCapabilities.suggestedToolModel.modelId} for the next task.`
            : "Choose an installed tool-capable model before the next task."}
        </p>
      )}
      {data.harnessRunId && (
        <p className="plan-node-detail">Task permission: {data.harnessTaskPermissionMode === "auto_edit" ? "Auto-edit" : data.harnessTaskPermissionMode === "autonomous" ? "Autonomous" : "Ask"}</p>
      )}
      {data.harnessStatusDetail && (
        <p className="plan-node-detail" role={data.harnessStatus === "failed" ? "alert" : undefined}>
          {data.harnessStatusDetail}
        </p>
      )}

      {/* Workspace binding. harnessWorkspacePath is the REQUEST (what the
          node asks for); a run only honors it if the folder is trusted, and
          reports the root it actually bound in harnessWorkspaceActive. So a
          NON-EMPTY active dir that does not match the request means a run
          happened and the grant did not apply on this machine. An empty one
          means no run has bound this node since the folder was picked -
          nothing has been refused yet, so nothing is warned about. */}
      <div className="harness-node-workspace">
        <Icon name="folder" size="sm" />
        {data.harnessWorkspacePath ? (
          <>
            <Tooltip label={data.harnessWorkspacePath}>
              <code className="harness-node-workspace-dir">
                {data.harnessWorkspacePath}
                {!running && data.harnessWorkspaceActive !== "" &&
                  data.harnessWorkspaceActive !== data.harnessWorkspacePath &&
                  " (not trusted on this machine — using scratch)"}
              </code>
            </Tooltip>
            <Tooltip label={running ? WORKSPACE_LOCKED_HINT : "Use scratch workspace"}>
              <Button size="sm" variant="ghost" className="nodrag"
                onClick={data.onUseScratch} disabled={running}>Use scratch</Button>
            </Tooltip>
          </>
        ) : (
          <>
            <code className="harness-node-workspace-dir">Scratch workspace</code>
            <Tooltip label={running ? WORKSPACE_LOCKED_HINT : "Choose a workspace folder"}>
              <Button size="sm" className="nodrag" onClick={data.onPickWorkspace}
                disabled={running}>Choose folder…</Button>
            </Tooltip>
          </>
        )}
        <IconButton size="sm" icon="folder-open" className="nodrag"
          label="Open workspace" onClick={data.onRevealWorkspace} />
      </div>
      <HarnessWorkspaceFiles
        key={data.harnessWorkspacePath}
        onList={data.onListWorkspaceFiles}
        onOpen={data.onOpenWorkspaceFile}
        onAddToKnowledge={data.onAddWorkspaceFileToKnowledge}
        onSubscribeKnowledgeIngest={data.onSubscribeKnowledgeIngest}
        onTrustChanged={data.onWorkspaceTrustChanged}
        onReveal={data.onRevealWorkspace}
      />

      {data.harnessReply && (
        <div className="harness-node-reply nowheel nodrag">{data.harnessReply}</div>
      )}

      <div className="plan-node-budgets">
        <span>
          Turns {data.harnessSpentTurns}
          {data.harnessMaxTurns > 0 ? ` (max ${data.harnessMaxTurns}/task)` : ""}
        </span>
        <span>Tokens {formatTokens(data.harnessSpentTokens)}</span>
        {data.harnessMaxContextTokens > 0 && (
          <Tooltip label={data.harnessCompactions > 0
            ? `History summarized ${data.harnessCompactions} times to stay within the context budget`
            : "System prompt, tool schemas and retained history within the model’s served context window"}>
            <span>
              Context {Math.min(100, Math.round((data.harnessContextTokens / data.harnessMaxContextTokens) * 100))}%
              {data.harnessCompactions > 0 && ` · compacted ${data.harnessCompactions}×`}
            </span>
          </Tooltip>
        )}
      </div>

      {/* §2.3's plan.update surface. Always expanded, unlike the activity
          log below: the checklist is the thing a person watching a long run
          actually wants visible, and it is short by construction (capped at
          20 rows backend-side). */}
      {data.harnessPlan.length > 0 && (
        <div className="harness-node-plan" role="list" aria-label="Agent checklist">
          {data.harnessPlan.map((step, index) => (
            <div
              key={`${index}-${step.text}`}
              role="listitem"
              className={`harness-node-plan-step harness-node-plan-step-${step.status}`}
            >
              <span className="harness-node-plan-marker" aria-hidden="true">
                {step.status === "active" ? <Spinner size={12} decorative /> : <Icon size="sm" name={step.status === "done" ? "status-success" : "status-pending"} />}
              </span>
              <span className="harness-node-plan-text">{step.text}</span>
            </div>
          ))}
        </div>
      )}

      {data.harnessActivity.length > 0 && (
        <Disclosure className="chat-node-tool-invocations plan-node-activity"
          open={activityOpen} onOpenChange={setActivityOpen}
          title={`${data.harnessActivity.length} activity ${data.harnessActivity.length === 1 ? "entry" : "entries"}${activityErrorCount ? ` · ${activityErrorCount} ${activityErrorCount === 1 ? "error" : "errors"}` : ""}`}>
          <div className="plan-node-activity-list nowheel nodrag" ref={activityListRef}>
            {data.harnessActivity.map((row, index) => (
              <div key={index} className={`chat-node-tool-invocation${row.outcome === "error" ? " error" : ""}`}>
                <div className="chat-node-tool-invocation-name">
                  {row.tool}
                  <span className="plan-node-activity-elapsed">
                    {row.outcome === "running" ? "Running · " : row.outcome === "stopped" ? "Stopped · " : ""}
                    {formatDuration(row.elapsedMs / 1000)}
                  </span>
                </div>
                <pre className="chat-node-tool-invocation-arguments">{row.summary}</pre>
              </div>
            ))}
          </div>
        </Disclosure>
      )}

      {data.harnessAwaitingApproval && (
        <div className="plan-node-approval" role="group" aria-label="Agent tool approval">
          <InlineAlert variant="warning"
            title={`The agent wants to run: ${data.harnessApprovalToolName}`}
            body={<>
              {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- scrollable summary must be keyboard-reachable */}
              <pre className="plan-node-approval-summary nowheel nodrag" tabIndex={0}
                role="region" aria-label="Proposed tool call">
                {renderApprovalSummary(data.harnessApprovalSummary, data.harnessApprovalToolName)}
              </pre>
              {previewUnavailable && (
                <span role="alert" className="harness-approval-preview-warning">
                  This file change could not be previewed. Deny the request and ask the agent to retry.
                </span>
              )}
              {shellApproval && data.harnessApprovalSessionOffered && (
                <span className="harness-approval-allow-explanation">
                  Always allow remembers this exact command (including any chain) only for this agent,
                  workspace, shell, and execution profile.
                </span>
              )}
            </>} />
          <div className="plan-node-approval-actions" ref={approvalActionsRef}>
            {data.harnessApprovalNetworkOffered ? (
              <>
                <Button size="sm" disabled={decisionPending}
                  onClick={() => approveNetwork("once")}>Allow network once</Button>
                {data.harnessTaskPermissionMode !== "autonomous" && (
                  <Button size="sm" disabled={decisionPending} onClick={() => approveNetwork("task")}>
                    Allow network for this task
                  </Button>
                )}
              </>
            ) : (
              <Button size="sm" variant="primary" disabled={decisionPending || previewUnavailable}
                onClick={() => decideTool(true)}>Approve once</Button>
            )}
            {data.harnessApprovalSessionOffered && (sessionGrantTool || shellApproval) && !previewUnavailable && (
              <Tooltip label={shellApproval
                ? "Remember this exact command in this workspace, shell, and execution profile"
                : "Stop asking for this tool for the rest of this agent’s session"}>
                <Button size="sm" disabled={decisionPending} onClick={() => decideTool(true, true)}>
                  {shellApproval ? "Always allow" : "Always allow this tool"}
                </Button>
              </Tooltip>
            )}
            <Button size="sm" id="harness-node-deny-approval" variant="secondary"
              onClick={() => decideTool(false)} disabled={decisionPending}>Deny</Button>
          </div>
        </div>
      )}

      {/* §2.3's user.ask: the run is parked on a human answer. Rendered as
          its own surface rather than reusing the composer below, which is
          for starting the NEXT task - answering is not sending. */}
      {data.harnessAwaitingQuestion && (
        <div className="plan-node-approval" role="group" aria-label="Agent question">
          <div className="plan-node-approval-title">The agent is asking:</div>
          <pre className="plan-node-approval-summary">{data.harnessQuestion}</pre>
          <div className="harness-node-composer">
            <TextArea className="harness-node-input nodrag nowheel"
              label="Answer to the agent's question" minRows={2} maxRows={4}
              placeholder="Type your answer…" value={answerDraft} onValueChange={setAnswerDraft}
              onKeyDown={(event) => {
                if (event.nativeEvent.isComposing) return;
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault(); sendAnswer();
                }
              }} />
            <div className="plan-node-approval-actions">
              <button
                type="button"
                className="plan-node-button plan-node-button-start nodrag"
                disabled={!answerDraft.trim()}
                onClick={sendAnswer}
              >
                Answer
              </button>
              <button
                type="button"
                className="plan-node-button plan-node-button-deny nodrag"
                onClick={() => {
                  setAnswerDraft("");
                  data.onAnswerQuestion("");
                }}
              >
                Dismiss
              </button>
            </div>
          </div>
        </div>
      )}

      {acceptsInput(data.harnessStatus) && (
        <div className="harness-node-composer">
          <TextArea className="harness-node-input nodrag nowheel"
            label="Follow-up message" minRows={2} maxRows={4}
            placeholder="Send a follow-up…" value={draft}
            onValueChange={(value) => { setDraft(value); setDraftError(""); }}
            onKeyDown={(event) => {
              if (event.nativeEvent.isComposing) return;
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault(); sendDraft();
              }
            }} />
          <Button size="sm" variant="primary" iconStart="send"
            disabled={!draft.trim() || sending || Boolean(data.pendingRequestId)}
            onClick={sendDraft}>Send</Button>
          {draftError && <p className="composer-agent-error" role="alert">{draftError}</p>}
        </div>
      )}

      {running && (
        <div className="plan-node-actions">
          <Button size="sm" variant="secondary" className="nodrag"
            iconStart="stop" onClick={data.onCancel}>Stop</Button>
        </div>
      )}
      {!running && data.harnessRunId && UNDOABLE_RUN_STATUSES.has(data.harnessStatus) && (
        <div className="plan-node-actions">
          <Tooltip label="Undo canvas changes made by this run. Workspace files are not reverted.">
            <Button size="sm" variant="secondary" className="nodrag"
              iconStart="undo" onClick={data.onUndoRun}>Undo run</Button>
          </Tooltip>
        </div>
      )}
    </NodeShell>
  );
}

function HarnessNodeViewWithOverlays(props: NodeProps<HarnessFlowNode>) {
  return (
    <OverlayProviderIfMissing>
      <HarnessNodeViewInner {...props} />
    </OverlayProviderIfMissing>
  );
}

export const HarnessNodeView = memo(HarnessNodeViewWithOverlays);
