/**
 * Scene-topic client store (Qt-removal plan R1).
 *
 * Binds the WS transport's "scene" topic to a validated, subscribable local
 * snapshot, and exposes the intent surface backend/canvas.py registers.
 * Deliberately framework-free (plain listeners, no React import) so the
 * store logic is unit-testable without rendering; React consumes it through
 * useSyncExternalStore in SceneCanvas.
 */

import { TOPIC_VALIDATORS } from "../../lib/api-contract/topics";
import type { SceneState } from "../../lib/bridge-core/generated/scene-state";
import type { GridControlState } from "../../lib/bridge-core/generated/grid-control-state";
import type { WsTransport } from "../../lib/ws/transport";

export const initialSceneState: SceneState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  nodes: [],
  edges: [],
  pins: [],
  snapToGrid: false,
  dragFactor: 1,
};

export const initialGridState: GridControlState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  gridSize: 10,
  gridOpacityPercent: 30,
  gridStyle: "Dots",
  gridColor: "#555555",
  sizePresets: [10, 20, 50, 100],
  stylePresets: ["Dots", "Lines", "Cross"],
  colorPresets: [],
};

type Listener = () => void;

export class SceneStore {
  private scene: SceneState = initialSceneState;
  private grid: GridControlState = initialGridState;
  private readonly listeners = new Set<Listener>();
  private readonly unsubscribers: Array<() => void> = [];

  constructor(private readonly transport: WsTransport) {}

  connect(): void {
    this.unsubscribers.push(
      this.transport.subscribe("scene", (payload) => {
        const validated = TOPIC_VALIDATORS["scene"](payload);
        if (validated.ok) {
          this.scene = validated.value;
          this.emit();
        } else {
          console.error("[scene] rejected snapshot:", validated.errors);
        }
      }),
      this.transport.subscribe("grid-control", (payload) => {
        const validated = TOPIC_VALIDATORS["grid-control"](payload);
        if (validated.ok) {
          this.grid = validated.value;
          this.emit();
        } else {
          console.error("[grid-control] rejected snapshot:", validated.errors);
        }
      }),
    );
  }

  dispose(): void {
    for (const unsubscribe of this.unsubscribers) unsubscribe();
    this.unsubscribers.length = 0;
  }

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getScene = (): SceneState => this.scene;
  getGrid = (): GridControlState => this.grid;

  private emit(): void {
    for (const listener of [...this.listeners]) listener();
  }

  // -- intents (backend/canvas.py's registered surface, 1:1) ---------------

  addNode(x: number, y: number, title = ""): void {
    this.transport.intent("scene", "addNode", [x, y, title]);
  }

  moveNode(id: string, x: number, y: number): void {
    this.transport.intent("scene", "moveNode", [id, x, y]);
  }

  removeNodes(ids: string[]): void {
    if (ids.length > 0) this.transport.intent("scene", "removeNodes", [ids]);
  }

  connectNodes(source: string, target: string): void {
    this.transport.intent("scene", "connectNodes", [source, target]);
  }

  removeEdges(ids: string[]): void {
    if (ids.length > 0) this.transport.intent("scene", "removeEdges", [ids]);
  }

  addPin(title: string, x: number, y: number, note = ""): void {
    this.transport.intent("scene", "addPin", [title, x, y, note]);
  }

  removePin(id: string): void {
    this.transport.intent("scene", "removePin", [id]);
  }

  setSnapToGrid(enabled: boolean): void {
    this.transport.intent("scene", "setSnapToGrid", [enabled]);
  }

  setDragFactor(factor: number): void {
    this.transport.intent("scene", "setDragFactor", [factor]);
  }
}

/** start + (proposed - start) * factor: the drag-speed contract carried over
 * from the Qt canvas (ChatView's drag factor scaled item motion the same
 * way). Exported standalone for direct unit testing. */
export function scaleDragPosition(
  start: { x: number; y: number },
  proposed: { x: number; y: number },
  factor: number,
): { x: number; y: number } {
  return {
    x: start.x + (proposed.x - start.x) * factor,
    y: start.y + (proposed.y - start.y) * factor,
  };
}
