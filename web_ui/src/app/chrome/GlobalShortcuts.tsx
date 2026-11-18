// Register keyboard shortcuts after the static shell can paint. The listener
// has no DOM, and the costly navigation/grouping helpers are only parsed here.
import { useReactFlow } from "@xyflow/react";
import { useEffect } from "react";
import { isTextEditable } from "../../lib/bridge-core/textFocus";
import { applyCompareBranches, applySynthesizeBranches } from "../canvas/branchActions";
import { measuredNodeSize } from "../canvas/measuredNodeSize";
import { resolveTreeNavigationTarget, type TreeNavigationDirection } from "../canvas/treeNavigation";
import type { SceneStore } from "../canvas/sceneStore";
import { useOverlays } from "../overlays/overlays";
import { requestNewChat } from "./requestNewChat";
import { isGatedWhileTyping, resolveShortcut, type ShortcutId } from "./shortcuts";

export function GlobalShortcuts({ store }: { store: SceneStore }) {
  const overlays = useOverlays();
  const reactFlow = useReactFlow();

  useEffect(() => {
    function navigate(direction: TreeNavigationDirection) {
      const scene = store.getScene();
      // store.getSelectedNodeId() mirrors React Flow's own selection, but
      // only ever tracks ONE id - so re-derive from React Flow to honor
      // legacy's "exactly one item selected" precondition, which a
      // multi-selection must fail.
      const selected = reactFlow.getNodes().filter((n) => n.selected);
      const currentId = selected.length === 1 ? selected[0].id : null;
      const targetId = resolveTreeNavigationTarget(scene, currentId, direction);
      if (!targetId) return; // every legacy boundary is a pure no-op

      // Legacy clears the whole selection, selects the target, and centers
      // the view on it (instant, zoom unchanged). Selecting through
      // setNodes also flows out via onSelectionChange -> the store's
      // selected-node mirror, so the composer's context anchor follows.
      reactFlow.setNodes((nodes) => nodes.map((n) => ({ ...n, selected: n.id === targetId })));
      const target = scene.nodes.find((n) => n.id === targetId);
      if (!target) return;
      // Legacy centerOn() takes the item's bounding-rect CENTER, so the node
      // half-size matters. It has to come from measuredNodeSize: React Flow's
      // internal `measured` is empty here far more often than not (every
      // scene snapshot rebuilds the node objects it is derived from - see
      // that helper's own comment), and a naive `?? 0` would silently degrade
      // to centering on the top-left corner, throwing the target half off
      // screen at high zoom. Only a node absent from the DOM lands on the
      // zero fallback, which is the legacy no-size behavior anyway.
      const size = measuredNodeSize(reactFlow, targetId) ?? { width: 0, height: 0 };
      reactFlow.setCenter(target.x + size.width / 2, target.y + size.height / 2, {
        zoom: reactFlow.getZoom(),
      });
    }

    function dispatch(id: ShortcutId) {
      switch (id) {
        case "new-chat":
          return requestNewChat(store);
        case "toggle-library":
          // Legacy's Ctrl+L is overlay_manager.toggle("library"), not open.
          return overlays.toggle("library", "dialog");
        case "save-chat":
          return store.saveChat();
        case "create-frame":
          // Legacy createFrame/createContainer need only ONE eligible node
          // (graphlink_scene.py:689-730) and silently no-op on zero - the
          // shortcuts match that exactly. The palette's own Create Frame
          // entry keeps its stricter 2+ gate; see the ledger note.
          return applyGrouping("frame");
        case "create-container":
          return applyGrouping("container");
        case "compare-branches":
          return applyCompareBranches(store, reactFlow);
        case "synthesize-branches":
          return applySynthesizeBranches(store, reactFlow);
        case "toggle-palette":
          return overlays.toggle("palette", "dialog");
        case "toggle-search":
          return overlays.toggle("search", "popover");
        case "toggle-quick-switcher":
          return overlays.toggle("quick-switcher", "dialog");
        // ADR-010 stage 10.2: the backend owns the stack, so these are a
        // straight forward - the frontend never decides WHAT gets undone.
        // A refusal (nothing to undo, or a node still generating) comes
        // back as a notification, not a silent no-op.
        case "undo":
          return store.undo();
        case "redo":
          return store.redo();
        case "navigate-up":
          return navigate("up");
        case "navigate-down":
          return navigate("down");
        case "navigate-left":
          return navigate("left");
        case "navigate-right":
          return navigate("right");
      }
    }

    function applyGrouping(kind: "frame" | "container") {
      const ids = reactFlow.getNodes().filter((n) => n.selected).map((n) => n.id);
      if (ids.length === 0) return; // legacy: bare return, no message
      if (kind === "frame") store.createFrame(ids);
      else store.createContainer(ids);
    }

    function onKeyDown(event: KeyboardEvent) {
      const id = resolveShortcut(event);
      if (!id) return;
      // The AcceleratorForwardingFilter port: while a text input has focus,
      // the gated shortcuts are left entirely alone so the keystroke reaches
      // the field, and only the exempt ones (Save, palette) still fire.
      if (isGatedWhileTyping(id) && isTextEditable(document.activeElement)) return;
      event.preventDefault();
      dispatch(id);
    }

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [overlays, reactFlow, store]);

  return null;
}

