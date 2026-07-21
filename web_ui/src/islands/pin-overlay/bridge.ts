import { PinOverlayState, initialPinOverlayState } from "./bridgeTypes";
import { isQWebChannelAvailable, connectQWebChannel } from "../../lib/bridge-core/transport";
import { installTextFocusReporting } from "../../lib/bridge-core/textFocus";
import { validatePinOverlayState } from "../../lib/bridge-core/generated/pin-overlay-state";
import {
  BridgeRejection,
  RejectionListener,
  parseIslandState,
} from "../../lib/bridge-core/islandState";

export type { BridgeRejection, RejectionListener };

type StateListener = (state: PinOverlayState) => void;

interface QtSignal<T> {
  connect(listener: (value: T) => void): void;
  disconnect?: (listener: (value: T) => void) => void;
}

interface QtPinOverlayObject {
  stateChanged: QtSignal<string>;
  ready: () => void;
  selectPin: (id: string) => void;
  deletePin: (id: string) => void;
  createPin: () => void;
  editPin: (id: string) => void;
  commitDraft: (title: string, note: string) => void;
  discardDraft: () => void;
  resize: (height: number) => void;
  close: () => void;
}

export interface PinOverlayBridge {
  ready(): void;
  selectPin(id: string): void;
  deletePin(id: string): void;
  createPin(): void;
  editPin(id: string): void;
  commitDraft(title: string, note: string): void;
  discardDraft(): void;
  resize(height: number): void;
  close(): void;
  dispose(): void;
}

function parseState(payload: string) {
  return parseIslandState(payload, validatePinOverlayState);
}

class MockPinOverlayBridge implements PinOverlayBridge {
  private readonly state: PinOverlayState = structuredClone(initialPinOverlayState);
  private readonly listener: StateListener;

  constructor(listener: StateListener) {
    this.listener = listener;
  }

  ready(): void {
    this.listener(this.state);
  }

  selectPin(): void {}
  deletePin(): void {}
  createPin(): void {}
  editPin(): void {}
  commitDraft(): void {}
  discardDraft(): void {}
  resize(): void {}
  close(): void {}
  dispose(): void {}
}

export function createPinOverlayBridge(
  listener: StateListener,
  onRejection?: RejectionListener,
): PinOverlayBridge {
  const fallback = new MockPinOverlayBridge(listener);

  if (!isQWebChannelAvailable()) {
    return fallback;
  }

  let remote: QtPinOverlayObject | null = null;
  let connected = false;
  const stateListener = (payload: string) => {
    const outcome = parseState(payload);
    if (outcome.ok) {
      listener(outcome.state);
      onRejection?.(null);
      return;
    }
    console.error(
      `[pin-overlay bridge] rejected payload (${outcome.rejection.kind}): ${outcome.rejection.reason}`,
      outcome.rejection.details,
    );
    onRejection?.(outcome.rejection);
  };

  connectQWebChannel((objects) => {
    remote = objects.pinOverlayBridge as QtPinOverlayObject;
    remote.stateChanged.connect(stateListener);
    connected = true;
    remote.ready();
    // Real text inputs (search + inline actions) mean this island must
    // participate in the keyboard-arbitration protocol, like command-palette.
    installTextFocusReporting(objects);
  });

  const call = <K extends keyof QtPinOverlayObject>(
    method: K,
    ...args: QtPinOverlayObject[K] extends (...values: infer A) => void ? A : never
  ) => {
    if (connected && remote && typeof remote[method] === "function") {
      (remote[method] as (...values: unknown[]) => void)(...args);
    }
  };

  return {
    ready: () => call("ready"),
    selectPin: (id) => call("selectPin", id),
    deletePin: (id) => call("deletePin", id),
    createPin: () => call("createPin"),
    editPin: (id) => call("editPin", id),
    commitDraft: (title, note) => call("commitDraft", title, note),
    discardDraft: () => call("discardDraft"),
    resize: (height) => call("resize", height),
    close: () => call("close"),
    dispose: () => {
      remote?.stateChanged.disconnect?.(stateListener);
      remote = null;
    },
  };
}
