import { DocumentViewerState, initialDocumentViewerState } from "./bridgeTypes";
import { isQWebChannelAvailable, connectQWebChannel } from "../../lib/bridge-core/transport";
import { validateDocumentViewerState } from "../../lib/bridge-core/generated/document-viewer-state";
import {
  BridgeRejection,
  RejectionListener,
  parseIslandState,
} from "../../lib/bridge-core/islandState";

export type { BridgeRejection, RejectionListener };

type StateListener = (state: DocumentViewerState) => void;

interface QtSignal<T> {
  connect(listener: (value: T) => void): void;
  disconnect?: (listener: (value: T) => void) => void;
}

interface QtDocumentViewerObject {
  stateChanged: QtSignal<string>;
  ready: () => void;
  close: () => void;
}

export interface DocumentViewerBridge {
  ready(): void;
  close(): void;
  dispose(): void;
}

function parseState(payload: string) {
  return parseIslandState(payload, validateDocumentViewerState);
}

class MockDocumentViewerBridge implements DocumentViewerBridge {
  private readonly state: DocumentViewerState = structuredClone(initialDocumentViewerState);
  private readonly listener: StateListener;

  constructor(listener: StateListener) {
    this.listener = listener;
  }

  ready(): void {
    this.listener(this.state);
  }

  close(): void {
    // No real embedded host to hide in the mock/test environment.
  }

  dispose(): void {}
}

export function createDocumentViewerBridge(
  listener: StateListener,
  onRejection?: RejectionListener,
): DocumentViewerBridge {
  const fallback = new MockDocumentViewerBridge(listener);

  if (!isQWebChannelAvailable()) {
    return fallback;
  }

  let remote: QtDocumentViewerObject | null = null;
  let connected = false;
  const stateListener = (payload: string) => {
    const outcome = parseState(payload);
    if (outcome.ok) {
      listener(outcome.state);
      onRejection?.(null);
      return;
    }
    console.error(
      `[document-viewer bridge] rejected payload (${outcome.rejection.kind}): ${outcome.rejection.reason}`,
      outcome.rejection.details,
    );
    onRejection?.(outcome.rejection);
  };

  connectQWebChannel((objects) => {
    remote = objects.documentViewerBridge as QtDocumentViewerObject;
    remote.stateChanged.connect(stateListener);
    connected = true;
    remote.ready();
  });

  const call = <K extends keyof QtDocumentViewerObject>(
    method: K,
    ...args: QtDocumentViewerObject[K] extends (...values: infer A) => void ? A : never
  ) => {
    if (connected && remote && typeof remote[method] === "function") {
      (remote[method] as (...values: unknown[]) => void)(...args);
    }
  };

  return {
    ready: () => call("ready"),
    close: () => call("close"),
    dispose: () => {
      remote?.stateChanged.disconnect?.(stateListener);
      remote = null;
    },
  };
}
