/**
 * Composer/token-counter/notification client store (Qt-removal plan R2.3).
 *
 * Same framework-free, validator-guarded pattern as SceneStore: bind topics,
 * expose the backend's registered intent surface 1:1.
 */

import { TOPIC_VALIDATORS } from "../../lib/api-contract/topics";
import type { AppComposerState } from "../../lib/bridge-core/generated/app-composer-state";
import type { TokenCounterState } from "../../lib/bridge-core/generated/token-counter-state";
import type { NotificationState } from "../../lib/bridge-core/generated/notification-state";
import type { WsTransport } from "../../lib/ws/transport";

// ADR-003 stage 3.1 review-fix: a native OS file dialog waits on the user,
// not the network - long enough that a person genuinely browsing for a file
// never trips the transport's ordinary request-timeout error path.
const NATIVE_DIALOG_TIMEOUT_MS = 5 * 60_000;

export const initialComposerState: AppComposerState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  draft: { id: "", text: "", contextMode: "branch", sendMode: "enter_to_send", restored: false },
  context: { anchor: null, items: [], totalTokens: 0, reviewAvailable: false },
  route: {
    mode: "ollama",
    provider: "Ollama (Local)",
    modelId: "",
    modelLabel: "",
    modelOptions: [],
    reasoning: { level: "off", label: "Off", options: [] },
    label: "Ollama (Local)",
    available: true,
    canChange: false,
  },
  request: { id: null, state: "idle", message: "", canSend: false, canCancel: false, canRetry: false },
  capabilities: {
    attachments: false,
    contextReview: false,
    routeSelection: false,
    modelSelection: false,
    reasoningSelection: true,
    settingsShortcut: true,
    cancellation: false,
  },
};

export const initialTokenCounterState: TokenCounterState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  inputTokens: 0,
  outputTokens: 0,
  contextTokens: 0,
  totalTokens: 0,
  // ADR-006 stage 6.8: provider-reported real usage (null/false until a
  // reply's provider reports counts - see backend/token_counter.py).
  promptTokens: null,
  completionTokens: null,
  usageIsReal: false,
  estimatedCostUsd: null,
  // ADR-016 stage 16.2: cumulative across every real-usage reply this
  // session has seen so far - see backend/token_counter.py.
  sessionPromptTokens: 0,
  sessionCompletionTokens: 0,
  sessionEstimatedCostUsd: 0,
};

export const initialNotificationState: NotificationState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  visible: false,
  message: "",
  msgType: "info",
};

type Listener = () => void;

export class ComposerStore {
  private composer: AppComposerState = initialComposerState;
  private tokenCounter: TokenCounterState = initialTokenCounterState;
  private notification: NotificationState = initialNotificationState;
  private streamText = "";
  private streamUnsub: (() => void) | null = null;
  private readonly listeners = new Set<Listener>();
  private readonly unsubscribers: Array<() => void> = [];

  constructor(private readonly transport: WsTransport) {}

  private bind<T>(topic: keyof typeof TOPIC_VALIDATORS, assign: (value: T) => void): () => void {
    return this.transport.subscribe(topic, (payload) => {
      const validated = TOPIC_VALIDATORS[topic](payload);
      if (validated.ok) {
        assign(validated.value as T);
        this.emit();
      } else {
        console.error(`[${topic}] rejected snapshot:`, validated.errors);
      }
    });
  }

  connect(): void {
    this.unsubscribers.push(
      this.bind<AppComposerState>("app-composer", (v) => {
        const prevId = this.composer.request.id;
        this.composer = v;
        this.syncStream(prevId, v.request.id);
      }),
      this.bind<TokenCounterState>("token-counter", (v) => (this.tokenCounter = v)),
      this.bind<NotificationState>("notification", (v) => (this.notification = v)),
    );
  }

  dispose(): void {
    for (const unsubscribe of this.unsubscribers) unsubscribe();
    this.unsubscribers.length = 0;
    this.streamUnsub?.();
    this.streamUnsub = null;
  }

  /** Keeps `streamText` bound to whichever request is currently in flight -
   * a fresh request.id subscribes to its own stream frames; the id
   * dropping back to null (idle/cancelled/errored) unsubscribes and clears
   * the buffer. See transport.ts's subscribeStream()/`kind:"stream"`. */
  private syncStream(prevId: string | null | undefined, nextId: string | null | undefined): void {
    if (nextId === prevId) return;
    this.streamUnsub?.();
    this.streamUnsub = null;
    this.streamText = "";
    if (nextId) {
      this.streamUnsub = this.transport.subscribeStream(nextId, (delta, _done, reset) => {
        if (reset) this.streamText = "";
        else this.streamText += delta;
        this.emit();
      });
    }
  }

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getComposer = (): AppComposerState => this.composer;
  getTokenCounter = (): TokenCounterState => this.tokenCounter;
  getNotification = (): NotificationState => this.notification;
  getStreamText = (): string => this.streamText;

  private emit(): void {
    for (const listener of [...this.listeners]) listener();
  }

  // -- intents (backend/composer.py + notifications.py, 1:1) --------------

  updateDraft(text: string): void {
    // ADR-003 stage 3.6: queueable - the single most textbook idempotent
    // last-write-wins case in the app; a keystroke autosave lost to a
    // dropped connection is the exact "vanishes silently" gap this stage
    // exists to close.
    this.transport.fireIntent("app-composer", "updateDraft", [text], undefined, true);
  }

  selectModel(modelId: string): void {
    // R8a: writes the chat-task model assignment. The backend routes this
    // through the same helper the Settings > Ollama page uses, so the two
    // surfaces cannot report different models.
    this.transport.fireIntent("app-composer", "selectModel", [modelId], undefined, true);
  }

  setReasoningLevel(level: string): void {
    this.transport.fireIntent("app-composer", "setReasoningLevel", [level], undefined, true);
  }

  attachFile(): void {
    // R8a: opens a NATIVE file dialog server-side (backend/native_dialogs.py,
    // same mechanism as Settings > Llama.cpp's GGUF picker) - there is no
    // browser-side file to pass, staging happens entirely on the backend.
    // ADR-003 stage 3.1 review-fix: the backend handler blocks on the user's
    // own pace in a native OS dialog, not on the network - the transport's
    // ordinary 10s default would report an ordinary slow picker as a failure.
    this.transport.fireIntent("app-composer", "attachFile", [], NATIVE_DIALOG_TIMEOUT_MS);
  }

  removeAttachment(attachmentId: string): void {
    this.transport.fireIntent("app-composer", "removeAttachment", [attachmentId]);
  }

  cancelChatRequest(requestId: string): void {
    this.transport.fireIntent("app-composer", "cancelChatRequest", [requestId]);
  }

  dismissNotification(): void {
    this.transport.fireIntent("notification", "dismiss", []);
  }
}
