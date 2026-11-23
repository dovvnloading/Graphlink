import type { SceneStore } from "../canvas/sceneStore";
import type { ConfirmOptions } from "../ui/ConfirmDialog";

export type NewChatConfirm = (options: ConfirmOptions) => Promise<boolean>;

/**
 * Global command/keyboard callers cannot use hooks. The mounted composer
 * provides a kit confirm function through useKnowledgeFileImport; lazy dialogs
 * may also register while open. The most recently mounted provider wins, and
 * unmounting it restores an earlier provider. Without one we fail closed.
 */
const providers: NewChatConfirm[] = [];

export function registerNewChatConfirm(confirm: NewChatConfirm): () => void {
  providers.push(confirm);
  return () => {
    const index = providers.lastIndexOf(confirm);
    if (index >= 0) providers.splice(index, 1);
  };
}

export async function requestNewChat(store: SceneStore, confirmFn: NewChatConfirm | null = providers.at(-1) ?? null): Promise<void> {
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
