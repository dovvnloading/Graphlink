import { SettingsState, initialSettingsState } from "./bridgeTypes";
import { isQWebChannelAvailable, connectQWebChannel } from "../../lib/bridge-core/transport";
import { validateSettingsState } from "../../lib/bridge-core/generated/settings-state";
import {
  BridgeRejection,
  RejectionListener,
  parseIslandState,
} from "../../lib/bridge-core/islandState";

export type { BridgeRejection, RejectionListener };

type StateListener = (state: SettingsState) => void;

interface QtSignal<T> {
  connect(listener: (value: T) => void): void;
  disconnect?: (listener: (value: T) => void) => void;
}

interface QtSettingsObject {
  stateChanged: QtSignal<string>;
  ready: () => void;
  setActiveSection: (section: string) => void;
}

export interface SettingsBridge {
  ready(): void;
  setActiveSection(section: string): void;
  dispose(): void;
}

/**
 * Shell-only for now: setActiveSection is the one intent this increment
 * builds. Each page's own intents (secrets, workers, pickers) arrive in
 * their own later increments, following the same createXBridge shape.
 */
function parseState(payload: string) {
  return parseIslandState(payload, validateSettingsState);
}

class MockSettingsBridge implements SettingsBridge {
  private state: SettingsState = structuredClone(initialSettingsState);
  private readonly listener: StateListener;

  constructor(listener: StateListener) {
    this.listener = listener;
  }

  ready(): void {
    this.listener(this.state);
  }

  setActiveSection(section: string): void {
    this.state = { ...this.state, activeSection: section, revision: this.state.revision + 1 };
    this.listener(this.state);
  }

  dispose(): void {}
}

export function createSettingsBridge(
  listener: StateListener,
  onRejection?: RejectionListener,
): SettingsBridge {
  const fallback = new MockSettingsBridge(listener);

  if (!isQWebChannelAvailable()) {
    return fallback;
  }

  let remote: QtSettingsObject | null = null;
  let connected = false;
  const stateListener = (payload: string) => {
    const outcome = parseState(payload);
    if (outcome.ok) {
      listener(outcome.state);
      onRejection?.(null);
      return;
    }
    console.error(
      `[settings bridge] rejected payload (${outcome.rejection.kind}): ${outcome.rejection.reason}`,
      outcome.rejection.details,
    );
    onRejection?.(outcome.rejection);
  };

  connectQWebChannel((objects) => {
    remote = objects.settingsBridge as QtSettingsObject;
    remote.stateChanged.connect(stateListener);
    connected = true;
    remote.ready();
  });

  const call = <K extends keyof QtSettingsObject>(
    method: K,
    ...args: QtSettingsObject[K] extends (...values: infer A) => void ? A : never
  ) => {
    if (connected && remote && typeof remote[method] === "function") {
      (remote[method] as (...values: unknown[]) => void)(...args);
    }
  };

  return {
    ready: () => call("ready"),
    setActiveSection: (section) => call("setActiveSection", section),
    dispose: () => {
      remote?.stateChanged.disconnect?.(stateListener);
      remote = null;
    },
  };
}
