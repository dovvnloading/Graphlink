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
import { COMPOSER_DRAFT_OFFLINE_COALESCE_KEY } from "../../lib/ws/transport";
import { announce } from "../announcer";

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
  /** Local draft writes whose request has not reached a terminal result.
   * Draft text stays optimistic until all writes settle and an explicitly
   * requested snapshot confirms current server authority. Generations, not
   * text equality, disambiguate valid A -> B -> A edit sequences. */
  private readonly pendingDraftGenerations = new Set<number>();
  private latestOptimisticDraft: { generation: number; text: string } | null = null;
  private nextDraftGeneration = 1;
  /** Generation fenced by the one explicit snapshot request in flight. */
  private draftResyncGeneration: number | null = null;
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
        const prevState = this.composer.request.state;
        let next = v;
        const optimistic = this.latestOptimisticDraft?.text;
        if (optimistic !== undefined) {
          // Accept unrelated server-owned fields while holding the newest
          // local text over stale/intermediate broadcasts. A value match is
          // not an acknowledgement: the user may legitimately type A-B-A.
          next = { ...v, draft: { ...v.draft, text: optimistic } };
        }
        this.composer = next;
        this.syncStream(prevId, next.request.id);
        this.announceRequestStateChange(prevState, next.request.state);
        this.requestDraftResync();
      }),
      this.bind<TokenCounterState>("token-counter", (v) => (this.tokenCounter = v)),
      this.bind<NotificationState>("notification", (v) => (this.notification = v)),
    );
  }

  private onDraftSettled(generation: number): void {
    this.pendingDraftGenerations.delete(generation);
    this.requestDraftResync();
  }

  private requestDraftResync(): void {
    if (
      this.draftResyncGeneration !== null ||
      this.pendingDraftGenerations.size > 0 ||
      !this.latestOptimisticDraft
    ) {
      return;
    }

    const fenceGeneration = this.latestOptimisticDraft.generation;
    this.draftResyncGeneration = fenceGeneration;
    const sent = this.transport.resubscribe("app-composer", (payload) => {
      this.draftResyncGeneration = null;
      // REVIEW-FIX (resubscribe-callback-dropped-on-version-rejection): null
      // means the correlated reply failed schema-version negotiation - there
      // is no snapshot to resync from and no useful retry to attempt right
      // here (the version skew, not a stale read, is the actual problem).
      // Clearing the fence above is still the important part: without it,
      // this callback previously never ran at all on a rejection, leaving
      // draftResyncGeneration stuck non-null and every future resync request
      // a permanent no-op for the rest of this store's life.
      if (payload === null) return;
      const validated = TOPIC_VALIDATORS["app-composer"](payload);
      if (!validated.ok) return;
      if (
        this.pendingDraftGenerations.size > 0 ||
        this.latestOptimisticDraft?.generation !== fenceGeneration
      ) {
        this.requestDraftResync();
        return;
      }
      // WsTransport invokes this one-shot callback before ordinary topic
      // listeners, so the same requested snapshot becomes authoritative.
      this.latestOptimisticDraft = null;
    });
    if (!sent) this.draftResyncGeneration = null;
  }

  private onDraftCoalesced(generation: number): void {
    this.pendingDraftGenerations.delete(generation);
    this.requestDraftResync();
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

  /** ADR-012 stage 12.3: screen-reader announcement for the main assistant
   * reply's own lifecycle - "generating" is the one state a sighted user
   * sees as a visible spinner/stream preview (Composer.tsx's own
   * composer-stream-preview) with no non-visual equivalent before this.
   * Deliberately keyed off `request.state`, not `request.id` (syncStream's
   * own key) - an id can flip to a new value while staying "generating"
   * (regenerate-in-place), which must NOT re-announce "responding" as if a
   * fresh request had started. */
  private announceRequestStateChange(
    prevState: AppComposerState["request"]["state"] | undefined,
    nextState: AppComposerState["request"]["state"],
  ): void {
    if (nextState === prevState) return;
    if (nextState === "generating") {
      announce("Assistant is responding");
    } else if (prevState === "generating" || prevState === "finalizing") {
      if (nextState === "succeeded") announce("Response complete");
      else if (nextState === "failed") announce("Response failed");
      else if (nextState === "canceled") announce("Response canceled");
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
    const generation = this.nextDraftGeneration++;
    this.pendingDraftGenerations.add(generation);
    this.latestOptimisticDraft = { generation, text };
    if (this.composer.draft.text !== text) {
      this.composer = { ...this.composer, draft: { ...this.composer.draft, text } };
      this.emit();
    }
    // ADR-003 stage 3.6: queueable - the single most textbook idempotent
    // last-write-wins case in the app; a keystroke autosave lost to a
    // dropped connection is the exact "vanishes silently" gap this stage
    // exists to close.
    this.transport.fireIntent(
      "app-composer",
      "updateDraft",
      [text],
      undefined,
      true,
      COMPOSER_DRAFT_OFFLINE_COALESCE_KEY,
      () => this.onDraftSettled(generation),
      () => this.onDraftCoalesced(generation),
    );
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
