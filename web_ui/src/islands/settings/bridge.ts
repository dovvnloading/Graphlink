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
  setTheme: (theme: string) => void;
  setShowTokenCounter: (enabled: boolean) => void;
  setEnableSystemPrompt: (enabled: boolean) => void;
  setNotificationPreference: (notificationType: string, enabled: boolean) => void;
  setUpdateNotificationsEnabled: (enabled: boolean) => void;
  setGithubToken: (token: string) => void;
  clearGithubToken: () => void;
}

export interface SettingsBridge {
  ready(): void;
  setActiveSection(section: string): void;
  setTheme(theme: string): void;
  setShowTokenCounter(enabled: boolean): void;
  setEnableSystemPrompt(enabled: boolean): void;
  setNotificationPreference(notificationType: string, enabled: boolean): void;
  setUpdateNotificationsEnabled(enabled: boolean): void;
  setGithubToken(token: string): void;
  clearGithubToken(): void;
  dispose(): void;
}

/**
 * Each intent applies and republishes immediately - see
 * graphlink_settings_bridge.py's module docstring for why this departs
 * from the original AppearanceSettingsWidget's single batched "Apply"
 * button.
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

  private publish(next: Partial<SettingsState>) {
    this.state = { ...this.state, ...next, revision: this.state.revision + 1 };
    this.listener(this.state);
  }

  ready(): void {
    this.listener(this.state);
  }

  setActiveSection(section: string): void {
    this.publish({ activeSection: section });
  }

  setTheme(theme: string): void {
    this.publish({ theme });
  }

  setShowTokenCounter(enabled: boolean): void {
    this.publish({ showTokenCounter: enabled });
  }

  setEnableSystemPrompt(enabled: boolean): void {
    this.publish({ enableSystemPrompt: enabled });
  }

  setNotificationPreference(notificationType: string, enabled: boolean): void {
    this.publish({
      notificationPreferences: { ...this.state.notificationPreferences, [notificationType]: enabled },
    });
  }

  setUpdateNotificationsEnabled(enabled: boolean): void {
    this.publish({ updateNotificationsEnabled: enabled });
  }

  setGithubToken(token: string): void {
    // Mirrors the real bridge's write-only contract even in the mock: the
    // token value itself is never retained in state, only whether one was
    // set - so a dev-mode/test consumer can't come to rely on getting it
    // back, a behavior the real bridge structurally cannot provide.
    this.publish({ githubTokenConfigured: token.trim().length > 0 });
  }

  clearGithubToken(): void {
    this.publish({ githubTokenConfigured: false });
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
    setTheme: (theme) => call("setTheme", theme),
    setShowTokenCounter: (enabled) => call("setShowTokenCounter", enabled),
    setEnableSystemPrompt: (enabled) => call("setEnableSystemPrompt", enabled),
    setNotificationPreference: (notificationType, enabled) =>
      call("setNotificationPreference", notificationType, enabled),
    setUpdateNotificationsEnabled: (enabled) => call("setUpdateNotificationsEnabled", enabled),
    setGithubToken: (token) => call("setGithubToken", token),
    clearGithubToken: () => call("clearGithubToken"),
    dispose: () => {
      remote?.stateChanged.disconnect?.(stateListener);
      remote = null;
    },
  };
}
