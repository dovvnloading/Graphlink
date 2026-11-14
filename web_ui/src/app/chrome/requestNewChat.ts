import type { SceneStore } from "../canvas/sceneStore";

/**
 * R7.5c: New Chat, with legacy's confirm step restored.
 * graphlink_window.py's new_chat (1427-1442) always showed a blocking
 * QMessageBox ("Start a new chat? Your current edits will be saved before switching.",
 * default No) before clearing the canvas, and only skipped it when there was
 * genuinely nothing to lose. R7.5a shipped the palette command without that
 * guard; both the palette and R7.5c's Ctrl+T now route through here so the
 * confirm can never apply to one surface and not the other.
 *
 * Legacy's skip condition is BOTH halves of "scene is empty AND there is no
 * current chat id" (graphlink_window.py:1429). The first draft of this used
 * only the empty-canvas half, which quietly inverted the guard in the unsafe
 * direction: emptying a loaded chat and pressing Ctrl+T skipped the confirm,
 * and newChat() drops current_chat_id, so the next Save would INSERT a new
 * row rather than update the loaded one - a silent detach from the chat the
 * user thought they were in. hasSavedChat (R7.5c) is the wire-side derivation
 * of current_chat_id added for exactly this predicate; the id itself stays
 * server-side.
 *
 * confirmFn is injectable purely so tests never touch a real modal; the
 * default is the browser's own blocking confirm, which matches legacy's
 * modal semantics (synchronous, blocks until answered) - the command
 * registry's run() is sync, so an async dialog could not be awaited here.
 */
export function requestNewChat(
  store: SceneStore,
  confirmFn: (message: string) => boolean = (message) => window.confirm(message),
): void {
  const scene = store.getScene();
  const nothingToLose = scene.nodes.length === 0 && !scene.hasSavedChat;
  if (!nothingToLose && !confirmFn("Start a new chat? Your current edits will be saved before switching.")) return;
  store.newChat();
}
