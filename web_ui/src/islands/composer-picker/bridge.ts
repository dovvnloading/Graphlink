import { ComposerPickerState, initialComposerPickerState } from "./bridgeTypes";
import { isQWebChannelAvailable, connectQWebChannel } from "../../lib/bridge-core/transport";
import { installTextFocusReporting } from "../../lib/bridge-core/textFocus";
import { validateComposerPickerState } from "../../lib/bridge-core/generated/composer-picker-state";
import {
  BridgeRejection,
  RejectionListener,
  parseIslandState,
} from "../../lib/bridge-core/islandState";

export type { BridgeRejection, RejectionListener };

type StateListener = (state: ComposerPickerState) => void;

interface QtSignal<T> {
  connect(listener: (value: T) => void): void;
  disconnect?: (listener: (value: T) => void) => void;
}

interface QtComposerPickerObject {
  stateChanged: QtSignal<string>;
  ready: () => void;
  selectOption: (id: string) => void;
  requestSettings: () => void;
  resize: (height: number) => void;
  close: () => void;
}

export interface ComposerPickerBridge {
  ready(): void;
  selectOption(id: string): void;
  requestSettings(): void;
  resize(height: number): void;
  close(): void;
  dispose(): void;
}

function parseState(payload: string) {
  return parseIslandState(payload, validateComposerPickerState);
}

class MockComposerPickerBridge implements ComposerPickerBridge {
  private readonly state: ComposerPickerState = structuredClone(initialComposerPickerState);
  private readonly listener: StateListener;

  constructor(listener: StateListener) {
    this.listener = listener;
  }

  ready(): void {
    this.listener(this.state);
  }

  selectOption(): void {}
  requestSettings(): void {}
  resize(): void {}
  close(): void {}
  dispose(): void {}
}

export function createComposerPickerBridge(
  listener: StateListener,
  onRejection?: RejectionListener,
): ComposerPickerBridge {
  const fallback = new MockComposerPickerBridge(listener);

  if (!isQWebChannelAvailable()) {
    return fallback;
  }

  let remote: QtComposerPickerObject | null = null;
  let connected = false;
  const stateListener = (payload: string) => {
    const outcome = parseState(payload);
    if (outcome.ok) {
      listener(outcome.state);
      onRejection?.(null);
      return;
    }
    console.error(
      `[composer-picker bridge] rejected payload (${outcome.rejection.kind}): ${outcome.rejection.reason}`,
      outcome.rejection.details,
    );
    onRejection?.(outcome.rejection);
  };

  connectQWebChannel((objects) => {
    remote = objects.composerPickerBridge as QtComposerPickerObject;
    remote.stateChanged.connect(stateListener);
    connected = true;
    remote.ready();
    // Real search input (model kind) means this island must participate in
    // the keyboard-arbitration protocol, like command-palette/pin-overlay.
    installTextFocusReporting(objects);
  });

  const call = <K extends keyof QtComposerPickerObject>(
    method: K,
    ...args: QtComposerPickerObject[K] extends (...values: infer A) => void ? A : never
  ) => {
    if (connected && remote && typeof remote[method] === "function") {
      (remote[method] as (...values: unknown[]) => void)(...args);
    }
  };

  return {
    ready: () => call("ready"),
    selectOption: (id) => call("selectOption", id),
    requestSettings: () => call("requestSettings"),
    resize: (height) => call("resize", height),
    close: () => call("close"),
    dispose: () => {
      remote?.stateChanged.disconnect?.(stateListener);
      remote = null;
    },
  };
}
