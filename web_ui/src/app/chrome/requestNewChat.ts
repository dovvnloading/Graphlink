import type { SceneStore } from "../canvas/sceneStore";
import type { ConfirmOptions } from "../ui/ConfirmDialog";

export type NewChatConfirm = (options: ConfirmOptions) => Promise<boolean>;

/**
 * The global shortcut and command palette are synchronous callers. They share
 * the app-level confirmation provided by the eagerly mounted quick switcher.
 * An absent provider fails closed for a nonempty/saved scene, never silently
 * discarding work. Tests may inject a confirm explicitly.
 */
let appConfirm: NewChatConfirm | null = null;

export function registerNewChatConfirm(confirm: NewChatConfirm): () => void {
  appConfirm = confirm;
  return () => {
    if (appConfirm === confirm) appConfirm = null;
  };
}

export async function requestNewChat(store: SceneStore, confirmFn: NewChatConfirm | null = appConfirm): Promise<void> {
  const scene = store.getScene();
  const nothingToLose = scene.nodes.length === 0 && !scene.hasSavedChat;
  if (!nothingToLose) {
    if (!confirmFn || !(await confirmFn({
      title: "Start a new chat?",
      body: "Your current edits will be saved before switching.",
      confirmLabel: "Start new chat",
    }))) return;
  }
  store.newChat();
}
