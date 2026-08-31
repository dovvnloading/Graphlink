import { expect, test } from "@playwright/test";
import { gotoApp } from "./helpers";

/**
 * ADR-015 stage 15.6: a real save -> clear -> reload round trip through
 * chats.db (tests_e2e/run_backend.py's own isolated temp copy, never a real
 * user's), not just an in-memory state check.
 *
 * A bare `page.reload()` would NOT prove this: backend/app.py's create_app()
 * defaults `restrict_sessions=True`, which pins every WS connection to the
 * SAME DEFAULT_SESSION_ID (backend/events.py) and that session's in-memory
 * SceneDocument survives a reconnect for a 300s idle TTL - so a node would
 * still "be there" after a reload even if Save/Load were completely broken,
 * because nothing ever actually threw the in-memory state away. This spec
 * instead exercises the app's own explicit, real UI actions that DO discard
 * and reconstruct state:
 *
 * - AppBar's "Save" button -> `store.saveChat()` -> backend/chat_library.py's
 *   save_chat(), one real atomic write into chats.db.
 * - Chat Library's own "New Chat" button -> `newChat()` intent ->
 *   `canvas_document.clear_for_load()` - genuinely empties the live document
 *   (chat_library.py's own new_chat(), read directly).
 * - Chat Library's per-row "Open chat" button -> `loadChat(id)` ->
 *   backend/chat_library.py's load_chat(), which reads the row BACK OFF DISK
 *   (`load_chat_row`) and restores it into the document - the real,
 *   exercised "reload workspace" path of this app.
 *
 * The node saved/reloaded here is a System Prompt note (the "System Prompt"
 * plugin action, plugins/system_prompt/plugin.py), NOT a Conversation Node -
 * deliberately, and NOT the bare double-click placeholder alone either.
 * Empirically confirmed while building this suite (backend/session_load.py's
 * `_restore_node`, read directly):
 *
 * - A double-click's own default `kind="placeholder"` (backend/domain/
 *   model.py) is excluded from `_REGULAR_KINDS` entirely - session_save.py's
 *   `_build_chat_data` never serializes it, so a canvas holding ONLY a
 *   placeholder saves a real chats.db row whose own node list is empty.
 * - "conversation" (and html/pycoder/code_sandbox/web_research/artifact/
 *   gitlink) is in `_PARENT_NODE_INDEX_KINDS`, which `_restore_node`
 *   REQUIRES a resolvable parent index for - `if parent_new_id is None:
 *   return None, None`. A Conversation Node parented off a placeholder saves
 *   fine (its own `parent_node_index` is simply `null`) but is then SILENTLY
 *   DROPPED on load, because null is never a resolvable index. There is no
 *   real, LLM-free UI path to a first, persistable "conversation"-kind node
 *   on a genuinely empty canvas: bootstrapping one always means parenting it
 *   off the one parentless creation gesture there is (double-click), and
 *   that gesture's own node is exactly the unpersisted kind this drops.
 * - A Note (`kind="note"`, System Prompt's own handler) has NEITHER problem:
 *   notes are NOT part of `_REGULAR_KINDS`/the node-index/parent-resolution
 *   machinery above at all - they serialize into their own `notes_data` list
 *   (position-only, by backend/session_save.py's own notes-are-separate
 *   design) and restore via `_restore_notes`, which never resolves a parent
 *   index. The placeholder here is still needed as System Prompt's own
 *   required `parent_node_id` argument (branch-root lookup), but the NOTE it
 *   creates round-trips through save/load independent of that placeholder
 *   parent's own unpersisted fate - confirmed empirically before writing
 *   this spec's final assertions.
 */
test("saves a node, clears the canvas, then reloads it from the chat library", async ({ page }) => {
  await gotoApp(page);

  const canvas = page.getByTestId("scene-canvas");
  const box = await canvas.boundingBox();
  if (!box) throw new Error("scene-canvas has no layout box to click into");
  // A corner offset rather than the exact center - see create-node.spec.ts's
  // own comment. It used to be dodging the empty-state hint's "Load Sample
  // Workspace" button; that overlay is gone, and the offset is kept only
  // because the coordinates are arbitrary either way.
  await canvas.dblclick({ position: { x: box.width * 0.15, y: box.height * 0.15 } });
  await expect(page.locator(".react-flow__node")).toHaveCount(1);

  await page.locator(".react-flow__node").first().click();
  await page.locator('[data-overlay-trigger="plugins"]').click();
  await page.getByRole("option", { name: "System Prompt" }).click();
  await expect(page.locator(".react-flow__node-note")).toHaveCount(1);

  await page.getByRole("button", { name: "Save", exact: true }).click();

  await page.locator('[data-overlay-trigger="library"]').click();
  const libraryDialog = page.getByRole("dialog", { name: "Chat Library" });
  await expect(libraryDialog).toBeVisible();

  // Fully isolated chats.db (a fresh temp file per E2E run - see
  // tests_e2e/run_backend.py), so this Save produced exactly one row.
  const savedRow = libraryDialog.locator(".library-row");
  await expect(savedRow).toHaveCount(1);
  const openSavedChat = savedRow.getByRole("button", { name: /^Open chat/ });

  // Clears the live document server-side and closes the dialog (newChat()'s
  // own overlays.close() call, ChatLibraryDialog.tsx).
  await page.getByRole("button", { name: "New Chat", exact: true }).click();
  await expect(libraryDialog).toBeHidden();
  await expect(page.locator(".react-flow__node")).toHaveCount(0);

  await page.locator('[data-overlay-trigger="library"]').click();
  await expect(libraryDialog).toBeVisible();
  await openSavedChat.click();

  await expect(libraryDialog).toBeHidden();
  await expect(page.locator(".react-flow__node-note")).toHaveCount(1);
});
