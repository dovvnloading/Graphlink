import { ComposerState, initialComposerState } from "./bridgeTypes";

type StateListener = (state: ComposerState) => void;

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
  removeContextItem: (itemId: string) => void;
  selectModel: (modelId: string) => void;
  setReasoningLevel: (level: string) => void;
  openSettings: () => void;
  openModelSelector: () => void;
  openReasoningSelector: () => void;
  resize: (height: number) => void;
}

interface QtWindow extends Window {
  qt?: { webChannelTransport?: unknown };
  QWebChannel?: new (
    transport: unknown,
    callback: (channel: { objects: { composerBridge: QtComposerObject } }) => void,
  ) => unknown;
}

export interface ComposerBridge {
  ready(): void;
  updateDraft(text: string): void;
  send(): void;
  cancel(requestId?: string): void;
  reviewContext(): void;
  requestAttachment(): void;
  removeContextItem(itemId: string): void;
  selectModel(modelId: string): void;
  setReasoningLevel(level: string): void;
  openSettings(): void;
  openModelSelector(): void;
  openReasoningSelector(): void;
  resize(height: number): void;
  dispose(): void;
}

function parseState(payload: string): ComposerState | null {
  try {
    const state = JSON.parse(payload) as ComposerState;
    if (state?.schemaVersion !== 1 || !state.draft || !state.request) return null;
    return state;
  } catch {
    return null;
  }
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

export function createComposerBridge(listener: StateListener): ComposerBridge {
  const fallback = new MockComposerBridge(listener);
  const globalWindow = window as QtWindow;

  if (!globalWindow.QWebChannel || !globalWindow.qt?.webChannelTransport) {
    return fallback;
  }

  let remote: QtComposerObject | null = null;
  let connected = false;
  let pendingHeight: number | null = null;
  const stateListener = (payload: string) => {
    const state = parseState(payload);
    if (state) listener(state);
  };

  new globalWindow.QWebChannel(globalWindow.qt.webChannelTransport, (channel) => {
    remote = channel.objects.composerBridge;
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
