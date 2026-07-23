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
    reasoning: { level: "quick", label: "Quick Mode (No CoT)", options: [] },
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
      this.bind<AppComposerState>("app-composer", (v) => (this.composer = v)),
      this.bind<TokenCounterState>("token-counter", (v) => (this.tokenCounter = v)),
      this.bind<NotificationState>("notification", (v) => (this.notification = v)),
    );
  }

  dispose(): void {
    for (const unsubscribe of this.unsubscribers) unsubscribe();
    this.unsubscribers.length = 0;
  }

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getComposer = (): AppComposerState => this.composer;
  getTokenCounter = (): TokenCounterState => this.tokenCounter;
  getNotification = (): NotificationState => this.notification;

  private emit(): void {
    for (const listener of [...this.listeners]) listener();
  }

  // -- intents (backend/composer.py + notifications.py, 1:1) --------------

  updateDraft(text: string): void {
    this.transport.intent("app-composer", "updateDraft", [text]);
  }

  setReasoningLevel(level: string): void {
    this.transport.intent("app-composer", "setReasoningLevel", [level]);
  }

  cancelChatRequest(requestId: string): void {
    this.transport.intent("app-composer", "cancelChatRequest", [requestId]);
  }

  dismissNotification(): void {
    this.transport.intent("notification", "dismiss", []);
  }
}
