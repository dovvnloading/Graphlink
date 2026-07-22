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
import type { DragSpeedState } from "../../lib/bridge-core/generated/drag-speed-state";
import type { FontControlState } from "../../lib/bridge-core/generated/font-control-state";
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
  fontFamily: "Segoe UI",
  fontSizePt: 9,
  fontColor: "#F0F0F0",
};

export const initialDragSpeedState: DragSpeedState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  percentPresets: [25, 50, 75, 100],
  percentMin: 5,
  percentMax: 100,
};

export const initialFontControlState: FontControlState = {
  schemaVersion: 1,
  minCompatibleSchemaVersion: 1,
  revision: 0,
  fontFamilies: ["Segoe UI"],
  colorPresets: [],
  sizeMin: 8,
  sizeMax: 16,
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
  private dragConfig: DragSpeedState = initialDragSpeedState;
  private fontConfig: FontControlState = initialFontControlState;
  private readonly listeners = new Set<Listener>();
  private readonly unsubscribers: Array<() => void> = [];

  constructor(private readonly transport: WsTransport) {}

  private bind<T>(topic: keyof typeof TOPIC_VALIDATORS, assign: (value: T) => void): () => void {
    return this.transport.subscribe(topic, (payload) => {
      const validated = TOPIC_VALIDATORS[topic](payload);
      if (validated.ok) {
        assign(validated.value as T);
        this.emit();
      } else {
        console.error(`[${topic}] rejected snapshot:`, validated.errors);
      }
    });
  }

  connect(): void {
    this.unsubscribers.push(
      this.bind<SceneState>("scene", (v) => (this.scene = v)),
      this.bind<GridControlState>("grid-control", (v) => (this.grid = v)),
      this.bind<DragSpeedState>("drag-speed", (v) => (this.dragConfig = v)),
      this.bind<FontControlState>("font-control", (v) => (this.fontConfig = v)),
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
  getDragConfig = (): DragSpeedState => this.dragConfig;
  getFontConfig = (): FontControlState => this.fontConfig;

  private emit(): void {
    for (const listener of [...this.listeners]) listener();
  }

  // -- intents (backend/canvas.py's registered surface, 1:1) ---------------

  addNode(x: number, y: number, title = ""): void {
    this.transport.intent("scene", "addNode", [x, y, title]);
  }

  // R3.1: real chat nodes - createChatNode/deleteChatNode/setChatCollapsed
  // mirror backend/canvas.py's intent names 1:1 (same convention as every
  // other scene intent above).
  addChatNode(x: number, y: number, content: string, isUser: boolean, parentId?: string): void {
    const args: unknown[] = [x, y, content, isUser];
    if (parentId !== undefined) args.push(parentId);
    this.transport.intent("scene", "addChatNode", args);
  }

  deleteChatNode(id: string): void {
    this.transport.intent("scene", "deleteChatNode", [id]);
  }

  // R3.5: real code nodes - deletion has no dedicated intent (code nodes are
  // never branch points/reparented, so the generic removeNodes intent below
  // already covers it).
  addCodeNode(x: number, y: number, code: string, language: string, parentId?: string): void {
    const args: unknown[] = [x, y, code, language];
    if (parentId !== undefined) args.push(parentId);
    this.transport.intent("scene", "addCodeNode", args);
  }

  setChatCollapsed(id: string, collapsed: boolean): void {
    this.transport.intent("scene", "setChatCollapsed", [id, collapsed]);
  }

  // R3.9/R3.10: real document nodes (attachments). Unlike chat/code,
  // parentId is REQUIRED - the backend's add_document_node signature has no
  // default for it (a document node can never exist without a parent chat
  // node in the legacy app). The five backend keyword-only fields
  // (file_path/mime_type/duration_seconds/byte_size/preview_label) are
  // bundled into one optional `options` object rather than five trailing
  // optional parameters: unlike addChatNode/addCodeNode's single optional
  // trailing parentId (conditionally omitted from the wire args so the
  // backend's own default kicks in), omitting only SOME of five trailing
  // positional slots while supplying a later one is not well-formed over
  // dispatch_intent's plain `handler(*args)` positional call - so this
  // always sends the full 11-arg positional list, filling any field the
  // caller didn't supply with the exact same default the backend method
  // itself uses. Same intent name reused for collapse - see setChatCollapsed
  // below this method's call sites in SceneCanvas.tsx for why.
  addDocumentNode(
    x: number,
    y: number,
    title: string,
    content: string,
    attachmentKind: string,
    parentId: string,
    options: {
      filePath?: string;
      mimeType?: string;
      durationSeconds?: number | null;
      byteSize?: number | null;
      previewLabel?: string;
    } = {},
  ): void {
    const {
      filePath = "",
      mimeType = "",
      durationSeconds = null,
      byteSize = null,
      previewLabel = "",
    } = options;
    this.transport.intent("scene", "addDocumentNode", [
      x,
      y,
      title,
      content,
      attachmentKind,
      parentId,
      filePath,
      mimeType,
      durationSeconds,
      byteSize,
      previewLabel,
    ]);
  }

  // R3.13/R3.14: real thinking nodes + generic docking. addThinkingNode has
  // no real UI creation trigger yet - same situation addCodeNode/
  // addDocumentNode were in when they landed; real creation is R4's agent
  // layer. setNodeDocked is intentionally generic (any node kind, either
  // direction) rather than a thinking-node-specific intent - it backs both
  // ThinkingNodeView's "Dock to Parent Node" and ChatNodeView's per-child
  // "Reveal Docked Items" undock action.
  addThinkingNode(x: number, y: number, thinkingText: string, parentId: string): void {
    this.transport.intent("scene", "addThinkingNode", [x, y, thinkingText, parentId]);
  }

  // R3.17/R3.18: real HTML view nodes. Same posture as addThinkingNode/
  // addDocumentNode - parentId is REQUIRED (the backend's add_html_node has
  // no default for it), and there is no real UI creation trigger yet (R4's
  // agent/plugin layer). The html source string rides the same `content`
  // field every other node kind's text lives in - no new wire field.
  addHtmlNode(x: number, y: number, htmlContent: string, parentId: string): void {
    this.transport.intent("scene", "addHtmlNode", [x, y, htmlContent, parentId]);
  }

  // R3.21/R3.22: real image nodes. Same posture as addThinkingNode/
  // addHtmlNode - no real UI creation trigger yet (R4's agent/plugin layer);
  // this method exists so the intent shape is testable now. The image bytes
  // themselves are never sent over this (or any) WS topic - only the small
  // imageAssetId reference string SceneNodeRow carries rides the wire; the
  // caller is responsible for having already uploaded/generated the bytes
  // and obtained imageBytesBase64 some other way (out of scope here).
  // mimeType defaults to "image/png" to match the backend's own default.
  addImageNode(
    x: number,
    y: number,
    imageBytesBase64: string,
    prompt: string,
    parentId: string,
    mimeType = "image/png",
  ): void {
    this.transport.intent("scene", "addImageNode", [x, y, imageBytesBase64, prompt, parentId, mimeType]);
  }

  setNodeDocked(id: string, docked: boolean): void {
    this.transport.intent("scene", "setNodeDocked", [id, docked]);
  }

  // R3.3: the Composer's real Send action - a real user ChatNode. The
  // assistant's reply is deferred to R4 (graphlink_config.py's Qt/non-Qt
  // split is a prerequisite for calling the real agent layer); the backend
  // surfaces that honestly via the existing notification topic, no fake
  // response synthesized here.
  sendMessage(text: string): void {
    this.transport.intent("scene", "sendMessage", [text]);
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

  updatePin(id: string, title: string, note: string): void {
    this.transport.intent("scene", "updatePin", [id, title, note]);
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

  organizeNodes(): void {
    this.transport.intent("scene", "organizeNodes", []);
  }

  // Grid intents ride the grid-control topic; font intents ride scene - both
  // keep the legacy bridges' @Slot names 1:1 (backend/canvas.py contract).
  setGridSize(size: number): void {
    this.transport.intent("grid-control", "setGridSize", [size]);
  }

  setGridOpacityPercent(percent: number): void {
    this.transport.intent("grid-control", "setGridOpacityPercent", [percent]);
  }

  setGridStyle(style: string): void {
    this.transport.intent("grid-control", "setGridStyle", [style]);
  }

  setGridColor(hex: string): void {
    this.transport.intent("grid-control", "setGridColor", [hex]);
  }

  setFontFamily(family: string): void {
    this.transport.intent("scene", "setFontFamily", [family]);
  }

  setFontSize(sizePt: number): void {
    this.transport.intent("scene", "setFontSize", [sizePt]);
  }

  setFontColor(hex: string): void {
    this.transport.intent("scene", "setFontColor", [hex]);
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
