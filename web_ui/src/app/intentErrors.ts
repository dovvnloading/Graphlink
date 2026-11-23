import type { WsTransport } from "../lib/ws/transport";
import { toast } from "./ui/Toast";

/** The server deliberately uses this exact text for non-domain failures. */
export const GENERIC_INTENT_ERROR = "Something went wrong while handling this request.";

/** Messages appear in the toast title; backend reason stays verbatim in details. */
export const INTENT_ACTIONS: Readonly<Record<string, string>> = {
  "app-chat-library/saveChat": "save the chat",
  "app-chat-library/newChat": "start a new chat",
  "app-chat-library/loadChat": "open the chat",
  "app-chat-library/deleteChat": "delete the chat",
  "app-chat-library/renameChat": "rename the chat",
  "app-chat-library/createWorkspace": "create the workspace",
  "app-chat-library/exportWorkspace": "export the workspace",
  "app-chat-library/setGraphFavorite": "update the favorite",
  "app-chat-library/setGraphArchived": "archive the chat",
  "app-chat-library/setGraphTags": "update the chat tags",
  "app-chat-library/setWorkspaceDefaultModel": "change the default model",
  "app-settings/setActiveSection": "open the settings section",
  "scene/exportScientificWorkspaceMethods": "export the methods",
  "scene/undo": "undo the change",
  "scene/redo": "redo the change",
  "scene/organizeNodes": "organize the canvas",
  "scene/applyOrganize": "organize the canvas",
  "scene/addPin": "add a pin",
  "scene/updatePin": "update the pin",
  "scene/removePin": "remove the pin",
  "scene/setSnapToGrid": "change the grid settings",
  "scene/setFadeConnections": "change the connection display",
  "scene/setOrthogonalConnections": "change the connection routing",
  "scene/setSmartGuides": "change the guides",
  "scene/setDragFactor": "change the drag behavior",
  "scene/addChatNode": "add the chat node",
  "scene/deleteChatNode": "delete the chat node",
  "scene/addNode": "add a node",
  "scene/removeNodes": "delete the nodes",
  "scene/connectNodes": "connect the nodes",
  "scene/removeEdges": "remove the connections",
  "scene/moveNodes": "move the nodes",
  "scene/sendMessage": "send the message",
  "scene/setChatUseKnowledge": "change the knowledge setting",
  "diagnostics/openLogFolder": "open the log folder",
};

/**
 * These request() flows already render their own errors (or, for workspace
 * import, a detailed server notification). Toasting them again duplicates UI.
 * Fire-and-forget mutation failures are intentionally NOT skipped.
 */
export const SKIP_INTENT_ERROR_TOASTS = new Set([
  "knowledge/openCitation",           // Document View replaces its content with an error.
  "diagnostics/exportDiagnosticBundle", // Diagnostics shows an inline export error.
  "app-chat-library/importWorkspace",  // Import publishes a detailed server notification.
  "scene/previewOrganize",             // Organization dialog shows a preview error.
  "scene/previewGraphRun",             // Graph runner handles request failures in its dialog.
  "scene/executeGraphRun",             // Graph runner shows a run error in its dialog.
  "scene/previewChatRerun",            // Chat rerun dialog displays request errors.
  "scene/startChatRerun",              // Chat rerun dialog displays request errors.
]);

export function showIntentError(
  transport: Pick<WsTransport, "fireIntent">,
  topic: string,
  intent: string,
  message: string,
): void {
  const key = topic + "/" + intent;
  if (SKIP_INTENT_ERROR_TOASTS.has(key)) return;
  const action = INTENT_ACTIONS[key] ?? "complete that action";
  const generic = message === GENERIC_INTENT_ERROR;
  toast.show({
    variant: "error",
    title: "Couldn't " + action + ".",
    details: generic ? "Details were written to the log." : message,
    ...(generic ? {
      action: {
        label: "Open log folder",
        onClick: () => transport.fireIntent("diagnostics", "openLogFolder", []),
      },
    } : {}),
  });
}

/** One shell-level registration; safe to tear down on StrictMode remount. */
export function registerIntentErrors(transport: WsTransport): () => void {
  return transport.onIntentError((topic, intent, message) => {
    showIntentError(transport, topic, intent, message);
  });
}
