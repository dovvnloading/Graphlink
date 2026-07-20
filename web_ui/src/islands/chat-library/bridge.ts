import { ChatLibraryState, initialChatLibraryState } from "./bridgeTypes";
import { isQWebChannelAvailable, connectQWebChannel } from "../../lib/bridge-core/transport";
import { installTextFocusReporting } from "../../lib/bridge-core/textFocus";
import { validateChatLibraryState } from "../../lib/bridge-core/generated/chat-library-state";
import {
  BridgeRejection,
  RejectionListener,
  parseIslandState,
} from "../../lib/bridge-core/islandState";

export type { BridgeRejection, RejectionListener };

type StateListener = (state: ChatLibraryState) => void;

interface QtSignal<T> {
  connect(listener: (value: T) => void): void;
  disconnect?: (listener: (value: T) => void) => void;
}

interface QtChatLibraryObject {
  stateChanged: QtSignal<string>;
  ready: () => void;
  refresh: () => void;
  loadChat: (id: number) => void;
  deleteChat: (id: number) => void;
  renameChat: (id: number, title: string) => void;
  newChat: () => void;
  close: () => void;
}

export interface ChatLibraryBridge {
  ready(): void;
  refresh(): void;
  loadChat(id: number): void;
  deleteChat(id: number): void;
  renameChat(id: number, title: string): void;
  newChat(): void;
  close(): void;
  dispose(): void;
}

function parseState(payload: string) {
  return parseIslandState(payload, validateChatLibraryState);
}

class MockChatLibraryBridge implements ChatLibraryBridge {
  private readonly state: ChatLibraryState = structuredClone(initialChatLibraryState);
  private readonly listener: StateListener;

  constructor(listener: StateListener) {
    this.listener = listener;
  }

  ready(): void {
    this.listener(this.state);
  }

  refresh(): void {}
  loadChat(): void {}
  deleteChat(): void {}
  renameChat(): void {}
  newChat(): void {}
  close(): void {}
  dispose(): void {}
}

export function createChatLibraryBridge(
  listener: StateListener,
  onRejection?: RejectionListener,
): ChatLibraryBridge {
  const fallback = new MockChatLibraryBridge(listener);

  if (!isQWebChannelAvailable()) {
    return fallback;
  }

  let remote: QtChatLibraryObject | null = null;
  let connected = false;
  const stateListener = (payload: string) => {
    const outcome = parseState(payload);
    if (outcome.ok) {
      listener(outcome.state);
      onRejection?.(null);
      return;
    }
    console.error(
      `[chat-library bridge] rejected payload (${outcome.rejection.kind}): ${outcome.rejection.reason}`,
      outcome.rejection.details,
    );
    onRejection?.(outcome.rejection);
  };

  connectQWebChannel((objects) => {
    remote = objects.chatLibraryBridge as QtChatLibraryObject;
    remote.stateChanged.connect(stateListener);
    connected = true;
    remote.ready();
    // Real text inputs (search + inline rename) mean this island must
    // participate in the keyboard-arbitration protocol, like command-palette.
    installTextFocusReporting(objects);
  });

  const call = <K extends keyof QtChatLibraryObject>(
    method: K,
    ...args: QtChatLibraryObject[K] extends (...values: infer A) => void ? A : never
  ) => {
    if (connected && remote && typeof remote[method] === "function") {
      (remote[method] as (...values: unknown[]) => void)(...args);
    }
  };

  return {
    ready: () => call("ready"),
    refresh: () => call("refresh"),
    loadChat: (id) => call("loadChat", id),
    deleteChat: (id) => call("deleteChat", id),
    renameChat: (id, title) => call("renameChat", id, title),
    newChat: () => call("newChat"),
    close: () => call("close"),
    dispose: () => {
      remote?.stateChanged.disconnect?.(stateListener);
      remote = null;
    },
  };
}
