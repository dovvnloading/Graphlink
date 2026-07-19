import { ComposerState, initialComposerState } from "./bridgeTypes";
import { isQWebChannelAvailable, connectQWebChannel } from "../../lib/bridge-core/transport";
import { checkSchemaCompatibility } from "../../lib/bridge-core/schemaVersion";
import { validateComposerState } from "../../lib/bridge-core/generated/composer-state";

type StateListener = (state: ComposerState) => void;

/**
 * Why a payload was rejected, for the visible error state. `null` means the
 * last payload was fine.
 *
 * This exists because the previous behavior - returning null from parseState()
 * and letting the caller skip the listener call - meant a malformed or
 * version-mismatched payload froze the UI silently, with no signal to the user
 * or to a developer that anything had gone wrong.
 */
export interface BridgeRejection {
  kind: "version" | "shape" | "parse";
  reason: string;
  details: string[];
}

export type RejectionListener = (rejection: BridgeRejection | null) => void;

interface QtSignal<T> {
  connect(listener: (value: T) => void): void;
  disconnect?: (listener: (value: T) => void) => void;
}

interface QtComposerObject {
  stateChanged: QtSignal<string>;
  ready: () => void;
  updateDraft: (text: string) => void;
  send: () => void;
  cancel: (requestId?: string) => void;
  reviewContext: () => void;
  requestAttachment: () => void;
  stageTextAttachment: (text: string) => void;
  removeContextItem: (itemId: string) => void;
  selectModel: (modelId: string) => void;
  setReasoningLevel: (level: string) => void;
  openSettings: () => void;
  openModelSelector: () => void;
  openReasoningSelector: () => void;
  resize: (height: number) => void;
}

export interface ComposerBridge {
  ready(): void;
  updateDraft(text: string): void;
  send(): void;
  cancel(requestId?: string): void;
  reviewContext(): void;
  requestAttachment(): void;
  stageTextAttachment(text: string): void;
  removeContextItem(itemId: string): void;
  selectModel(modelId: string): void;
  setReasoningLevel(level: string): void;
  openSettings(): void;
  openModelSelector(): void;
  openReasoningSelector(): void;
  resize(height: number): void;
  dispose(): void;
}

type ParseOutcome =
  | { ok: true; state: ComposerState }
  | { ok: false; rejection: BridgeRejection };

/**
 * Parse and vet an incoming payload.
 *
 * Every rejection path now returns a REASON rather than a bare null, so the
 * caller can surface it. The old implementation collapsed "not valid JSON",
 * "wrong schema version", and "missing required fields" into a single `null`
 * that the caller silently dropped.
 *
 * Shape validation uses the generated validator (composer-state.ts), which is
 * produced from the same Python dataclasses that define the payload - so this
 * check cannot drift from what the desktop side actually sends without the
 * staleness pytest failing first.
 */
function parseState(payload: string): ParseOutcome {
  let parsed: unknown;
  try {
    parsed = JSON.parse(payload);
  } catch (error) {
    return {
      ok: false,
      rejection: {
        kind: "parse",
        reason: "The desktop app sent an update that could not be read as JSON.",
        details: [error instanceof Error ? error.message : String(error)],
      },
    };
  }

  const version = checkSchemaCompatibility(parsed);
  if (!version.compatible) {
    return {
      ok: false,
      rejection: { kind: "version", reason: version.reason, details: [] },
    };
  }

  const validated = validateComposerState(parsed);
  if (!validated.ok) {
    return {
      ok: false,
      rejection: {
        kind: "shape",
        reason: "The update from the desktop app did not match the expected format.",
        // Bounded: a badly wrong payload can produce a very long list, and the
        // error state shows these to a human.
        details: validated.errors.slice(0, 8),
      },
    };
  }

  return { ok: true, state: validated.value as unknown as ComposerState };
}

class MockComposerBridge implements ComposerBridge {
  private state: ComposerState = structuredClone(initialComposerState);
  private readonly listener: StateListener;

  constructor(listener: StateListener) {
    this.listener = listener;
  }

  ready(): void {
    this.listener(this.state);
  }

  updateDraft(text: string): void {
    this.state = {
      ...this.state,
      revision: this.state.revision + 1,
      draft: { ...this.state.draft, text },
      request: {
        ...this.state.request,
        canSend: text.trim().length > 0 || this.state.context.reviewAvailable,
      },
    };
    this.listener(this.state);
  }

  send(): void {
    if (!this.state.request.canSend) return;
    this.state = {
      ...this.state,
      revision: this.state.revision + 1,
      request: {
        ...this.state.request,
        id: "preview-request",
        state: "generating",
        message: "Preview mode — waiting for the desktop bridge",
        canSend: false,
        canCancel: true,
      },
    };
    this.listener(this.state);
  }

  cancel(): void {
    this.state = {
      ...this.state,
      revision: this.state.revision + 1,
      request: {
        ...this.state.request,
        id: null,
        state: "canceled",
        message: "Request canceled",
        canSend: true,
        canCancel: false,
      },
    };
    this.listener(this.state);
  }

  reviewContext(): void {}
  requestAttachment(): void {}
  stageTextAttachment(text: string): void {
    const normalized = String(text || "");
    if (!normalized.trim()) return;
    const lineCount = normalized.split("\n").length;
    const attachmentId = `paste-${this.state.revision + 1}`;
    const attachment = {
      id: attachmentId,
      name: `Pasted Text (${lineCount} lines).txt`,
      kind: "document",
      tokenCount: 0,
      preparationState: "ready",
      contextLabel: "Text",
    };
    this.state = {
      ...this.state,
      revision: this.state.revision + 1,
      context: {
        ...this.state.context,
        items: [...this.state.context.items, attachment],
        reviewAvailable: true,
      },
      request: { ...this.state.request, canSend: true },
    };
    this.listener(this.state);
  }
  removeContextItem(): void {}
  selectModel(modelId: string): void {
    const option = this.state.route.modelOptions.find((item) => item.id === modelId);
    this.state = {
      ...this.state,
      revision: this.state.revision + 1,
      route: {
        ...this.state.route,
        modelId,
        modelValue: modelId,
        modelLabel: option?.label || modelId,
        modelOptions: this.state.route.modelOptions.map((item) => ({
          ...item,
          active: item.id === modelId,
        })),
      },
    };
    this.listener(this.state);
  }

  setReasoningLevel(level: string): void {
    const normalized = level.toLowerCase() === "thinking" ? "Thinking" : "Quick";
    this.state = {
      ...this.state,
      revision: this.state.revision + 1,
      route: {
        ...this.state.route,
        reasoning: {
          ...this.state.route.reasoning,
          level: normalized,
          label: normalized,
        },
      },
    };
    this.listener(this.state);
  }

  openSettings(): void {}
  openModelSelector(): void {}
  openReasoningSelector(): void {}
  resize(): void {}
  dispose(): void {}
}

export function createComposerBridge(
  listener: StateListener,
  onRejection?: RejectionListener,
): ComposerBridge {
  const fallback = new MockComposerBridge(listener);

  if (!isQWebChannelAvailable()) {
    return fallback;
  }

  let remote: QtComposerObject | null = null;
  let connected = false;
  let pendingHeight: number | null = null;
  const stateListener = (payload: string) => {
    const outcome = parseState(payload);
    if (outcome.ok) {
      listener(outcome.state);
      // Clear any previously-shown error once a good payload arrives, so a
      // transient bad update doesn't strand the UI in the error state.
      onRejection?.(null);
      return;
    }
    // Loud in the console for a developer, and surfaced on screen by the
    // caller for a user - never silently dropped, which was the old behavior.
    console.error(
      `[composer bridge] rejected payload (${outcome.rejection.kind}): ${outcome.rejection.reason}`,
      outcome.rejection.details,
    );
    onRejection?.(outcome.rejection);
  };

  connectQWebChannel((objects) => {
    remote = objects.composerBridge as QtComposerObject;
    remote.stateChanged.connect(stateListener);
    connected = true;
    remote.ready();
    if (pendingHeight !== null) remote.resize(pendingHeight);
  });

  const call = <K extends keyof QtComposerObject>(
    method: K,
    ...args: QtComposerObject[K] extends (...values: infer A) => void ? A : never
  ) => {
    if (connected && remote && typeof remote[method] === "function") {
      (remote[method] as (...values: unknown[]) => void)(...args);
    }
  };

  return {
    ready: () => call("ready"),
    updateDraft: (text) => call("updateDraft", text),
    send: () => call("send"),
    cancel: (requestId) => call("cancel", requestId),
    reviewContext: () => call("reviewContext"),
    requestAttachment: () => call("requestAttachment"),
    stageTextAttachment: (text) => call("stageTextAttachment", text),
    removeContextItem: (itemId) => call("removeContextItem", itemId),
    selectModel: (modelId) => call("selectModel", modelId),
    setReasoningLevel: (level) => call("setReasoningLevel", level),
    openSettings: () => call("openSettings"),
    openModelSelector: () => call("openModelSelector"),
    openReasoningSelector: () => call("openReasoningSelector"),
    resize: (height) => {
      pendingHeight = height;
      call("resize", height);
    },
    dispose: () => {
      remote?.stateChanged.disconnect?.(stateListener);
      remote = null;
    },
  };
}
