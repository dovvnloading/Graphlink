import { ToolbarState, initialToolbarState } from "./bridgeTypes";
import { isQWebChannelAvailable, connectQWebChannel } from "../../lib/bridge-core/transport";
import { validateToolbarState } from "../../lib/bridge-core/generated/toolbar-state";
import {
  BridgeRejection,
  RejectionListener,
  parseIslandState,
} from "../../lib/bridge-core/islandState";

export type { BridgeRejection, RejectionListener };

type StateListener = (state: ToolbarState) => void;

interface QtSignal<T> {
  connect(listener: (value: T) => void): void;
  disconnect?: (listener: (value: T) => void) => void;
}

interface QtToolbarObject {
  stateChanged: QtSignal<string>;
  ready: () => void;
  reportAnchorRect: (name: string, x: number, y: number, width: number, height: number) => void;
  openLibrary: () => void;
  saveChat: () => void;
  togglePins: () => void;
  organizeNodes: () => void;
  zoomIn: () => void;
  zoomOut: () => void;
  resetZoom: () => void;
  fitAll: () => void;
  toggleControls: (visible: boolean) => void;
  togglePlugins: () => void;
  selectMode: (mode: string) => void;
  openSettings: () => void;
  openAbout: () => void;
  openHelp: () => void;
}

export interface ToolbarBridge {
  ready(): void;
  reportAnchorRect(name: string, x: number, y: number, width: number, height: number): void;
  openLibrary(): void;
  saveChat(): void;
  togglePins(): void;
  organizeNodes(): void;
  zoomIn(): void;
  zoomOut(): void;
  resetZoom(): void;
  fitAll(): void;
  toggleControls(visible: boolean): void;
  togglePlugins(): void;
  selectMode(mode: string): void;
  openSettings(): void;
  openAbout(): void;
  openHelp(): void;
  dispose(): void;
}

function parseState(payload: string) {
  return parseIslandState(payload, validateToolbarState);
}

class MockToolbarBridge implements ToolbarBridge {
  private readonly state: ToolbarState = structuredClone(initialToolbarState);
  private readonly listener: StateListener;

  constructor(listener: StateListener) {
    this.listener = listener;
  }

  ready(): void {
    this.listener(this.state);
  }

  reportAnchorRect(): void {}
  openLibrary(): void {}
  saveChat(): void {}
  togglePins(): void {}
  organizeNodes(): void {}
  zoomIn(): void {}
  zoomOut(): void {}
  resetZoom(): void {}
  fitAll(): void {}
  toggleControls(): void {}
  togglePlugins(): void {}
  selectMode(): void {}
  openSettings(): void {}
  openAbout(): void {}
  openHelp(): void {}
  dispose(): void {}
}

export function createToolbarBridge(
  listener: StateListener,
  onRejection?: RejectionListener,
): ToolbarBridge {
  const fallback = new MockToolbarBridge(listener);

  if (!isQWebChannelAvailable()) {
    return fallback;
  }

  let remote: QtToolbarObject | null = null;
  let connected = false;
  const stateListener = (payload: string) => {
    const outcome = parseState(payload);
    if (outcome.ok) {
      listener(outcome.state);
      onRejection?.(null);
      return;
    }
    console.error(
      `[toolbar bridge] rejected payload (${outcome.rejection.kind}): ${outcome.rejection.reason}`,
      outcome.rejection.details,
    );
    onRejection?.(outcome.rejection);
  };

  connectQWebChannel((objects) => {
    remote = objects.toolbarBridge as QtToolbarObject;
    remote.stateChanged.connect(stateListener);
    connected = true;
    remote.ready();
  });

  const call = <K extends keyof QtToolbarObject>(
    method: K,
    ...args: QtToolbarObject[K] extends (...values: infer A) => void ? A : never
  ) => {
    if (connected && remote && typeof remote[method] === "function") {
      (remote[method] as (...values: unknown[]) => void)(...args);
    }
  };

  return {
    ready: () => call("ready"),
    reportAnchorRect: (name, x, y, width, height) => call("reportAnchorRect", name, x, y, width, height),
    openLibrary: () => call("openLibrary"),
    saveChat: () => call("saveChat"),
    togglePins: () => call("togglePins"),
    organizeNodes: () => call("organizeNodes"),
    zoomIn: () => call("zoomIn"),
    zoomOut: () => call("zoomOut"),
    resetZoom: () => call("resetZoom"),
    fitAll: () => call("fitAll"),
    toggleControls: (visible) => call("toggleControls", visible),
    togglePlugins: () => call("togglePlugins"),
    selectMode: (mode) => call("selectMode", mode),
    openSettings: () => call("openSettings"),
    openAbout: () => call("openAbout"),
    openHelp: () => call("openHelp"),
    dispose: () => {
      remote?.stateChanged.disconnect?.(stateListener);
      remote = null;
    },
  };
}
