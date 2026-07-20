import { NotificationState, initialNotificationState } from "./bridgeTypes";
import { isQWebChannelAvailable, connectQWebChannel } from "../../lib/bridge-core/transport";
import { validateNotificationState } from "../../lib/bridge-core/generated/notification-state";
import {
  BridgeRejection,
  RejectionListener,
  parseIslandState,
} from "../../lib/bridge-core/islandState";

export type { BridgeRejection, RejectionListener };

type StateListener = (state: NotificationState) => void;

interface QtSignal<T> {
  connect(listener: (value: T) => void): void;
  disconnect?: (listener: (value: T) => void) => void;
}

interface QtNotificationObject {
  stateChanged: QtSignal<string>;
  ready: () => void;
  copyDetails: () => void;
  dismiss: () => void;
  resize: (height: number) => void;
}

export interface NotificationBridge {
  ready(): void;
  copyDetails(): void;
  dismiss(): void;
  resize(height: number): void;
  dispose(): void;
}

/**
 * Event-push toast: Python drives visible/message/msgType, JS sends back
 * only one real intent (copyDetails) plus the shared resize() height-
 * negotiation call every negotiated-sizing island uses (see composer's
 * bridge.ts for the fuller rationale on that one).
 */
function parseState(payload: string) {
  return parseIslandState(payload, validateNotificationState);
}

class MockNotificationBridge implements NotificationBridge {
  private state: NotificationState = structuredClone(initialNotificationState);
  private readonly listener: StateListener;

  constructor(listener: StateListener) {
    this.listener = listener;
  }

  ready(): void {
    this.listener(this.state);
  }

  copyDetails(): void {}
  dismiss(): void {}
  resize(): void {}
  dispose(): void {}
}

export function createNotificationBridge(
  listener: StateListener,
  onRejection?: RejectionListener,
): NotificationBridge {
  const fallback = new MockNotificationBridge(listener);

  if (!isQWebChannelAvailable()) {
    return fallback;
  }

  let remote: QtNotificationObject | null = null;
  let connected = false;
  let pendingHeight: number | null = null;
  const stateListener = (payload: string) => {
    const outcome = parseState(payload);
    if (outcome.ok) {
      listener(outcome.state);
      onRejection?.(null);
      return;
    }
    console.error(
      `[notification bridge] rejected payload (${outcome.rejection.kind}): ${outcome.rejection.reason}`,
      outcome.rejection.details,
    );
    onRejection?.(outcome.rejection);
  };

  connectQWebChannel((objects) => {
    remote = objects.notificationBridge as QtNotificationObject;
    remote.stateChanged.connect(stateListener);
    connected = true;
    remote.ready();
    if (pendingHeight !== null) remote.resize(pendingHeight);
  });

  const call = <K extends keyof QtNotificationObject>(
    method: K,
    ...args: QtNotificationObject[K] extends (...values: infer A) => void ? A : never
  ) => {
    if (connected && remote && typeof remote[method] === "function") {
      (remote[method] as (...values: unknown[]) => void)(...args);
    }
  };

  return {
    ready: () => call("ready"),
    copyDetails: () => call("copyDetails"),
    dismiss: () => call("dismiss"),
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
