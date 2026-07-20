import { CommandPaletteState, initialCommandPaletteState } from "./bridgeTypes";
import { isQWebChannelAvailable, connectQWebChannel } from "../../lib/bridge-core/transport";
import { installTextFocusReporting } from "../../lib/bridge-core/textFocus";
import { validateCommandPaletteState } from "../../lib/bridge-core/generated/command-palette-state";
import {
  BridgeRejection,
  RejectionListener,
  parseIslandState,
} from "../../lib/bridge-core/islandState";

export type { BridgeRejection, RejectionListener };

type StateListener = (state: CommandPaletteState) => void;

interface QtSignal<T> {
  connect(listener: (value: T) => void): void;
  disconnect?: (listener: (value: T) => void) => void;
}

interface QtCommandPaletteObject {
  stateChanged: QtSignal<string>;
  ready: () => void;
  executeCommand: (id: string) => void;
  dismiss: () => void;
}

export interface CommandPaletteBridge {
  ready(): void;
  executeCommand(id: string): void;
  dismiss(): void;
  dispose(): void;
}

/**
 * Snapshot-and-execute shape: Python snapshots availability once on open()
 * (Python-only - there is no JS-callable "open"; the palette only ever opens
 * because show_command_palette() made the host visible) and pushes the full
 * command list; JS filters/navigates that snapshot entirely client-side and
 * sends back exactly one real intent, executeCommand(id), which Python
 * re-validates against LIVE state before ever invoking a callback. See
 * graphlink_command_palette_bridge.py's module docstring for the fuller
 * rationale.
 */
function parseState(payload: string) {
  return parseIslandState(payload, validateCommandPaletteState);
}

class MockCommandPaletteBridge implements CommandPaletteBridge {
  private state: CommandPaletteState = structuredClone(initialCommandPaletteState);
  private readonly listener: StateListener;

  constructor(listener: StateListener) {
    this.listener = listener;
  }

  ready(): void {
    this.listener(this.state);
  }

  executeCommand(): void {}
  dismiss(): void {}
  dispose(): void {}
}

export function createCommandPaletteBridge(
  listener: StateListener,
  onRejection?: RejectionListener,
): CommandPaletteBridge {
  const fallback = new MockCommandPaletteBridge(listener);

  if (!isQWebChannelAvailable()) {
    return fallback;
  }

  let remote: QtCommandPaletteObject | null = null;
  let connected = false;
  const stateListener = (payload: string) => {
    const outcome = parseState(payload);
    if (outcome.ok) {
      listener(outcome.state);
      onRejection?.(null);
      return;
    }
    console.error(
      `[command-palette bridge] rejected payload (${outcome.rejection.kind}): ${outcome.rejection.reason}`,
      outcome.rejection.details,
    );
    onRejection?.(outcome.rejection);
  };

  connectQWebChannel((objects) => {
    remote = objects.commandPaletteBridge as QtCommandPaletteObject;
    remote.stateChanged.connect(stateListener);
    connected = true;
    remote.ready();
    // The palette has a real text search input (unlike notification), so it
    // must participate in the keyboard-arbitration protocol the same way
    // composer does - see AcceleratorForwardingFilter.
    installTextFocusReporting(objects);
  });

  const call = <K extends keyof QtCommandPaletteObject>(
    method: K,
    ...args: QtCommandPaletteObject[K] extends (...values: infer A) => void ? A : never
  ) => {
    if (connected && remote && typeof remote[method] === "function") {
      (remote[method] as (...values: unknown[]) => void)(...args);
    }
  };

  return {
    ready: () => call("ready"),
    executeCommand: (id) => call("executeCommand", id),
    dismiss: () => call("dismiss"),
    dispose: () => {
      remote?.stateChanged.disconnect?.(stateListener);
      remote = null;
    },
  };
}
