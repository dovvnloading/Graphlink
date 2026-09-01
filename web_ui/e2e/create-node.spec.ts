import { expect, test } from "@playwright/test";
import { gotoApp } from "./helpers";

/**
 * ADR-015 stage 15.6: creates two real nodes through two real UI entry
 * points, neither needing an LLM call or any network access:
 *
 * 1. The Plugins popover with NOTHING selected -> "System Prompt". This is
 *    the empty-canvas entry point. It did not exist until plugins could be
 *    created without a parent: every picker entry required an
 *    already-selected node, so the only way to get node #1 onto an empty
 *    canvas was a double-click gesture that produced an untitled
 *    placeholder, and this spec used to bootstrap through it. System Prompt
 *    is registered requires_parent=False and its handler creates the note
 *    at the picker's reported spawn point (plugins/system_prompt/plugin.py).
 * 2. The Plugins popover again, WITH that node selected -> "Conversation
 *    Node" - one of the 7 first-party plugins migrated onto ADR-014's
 *    `register_builtin_plugin` escape hatch. Picked over every other picker
 *    entry specifically because its handler
 *    (SceneDocument.add_conversation_node) does nothing but create and
 *    connect a node - unlike, say, Web Research or the Virtual Environment
 *    Runner, it never calls out to a provider or a subprocess, so this stays
 *    fully deterministic and offline. Built-in plugins also bypass the
 *    install-time consent/grant gate entirely (see backend/plugins.py's own
 *    module docstring on `builtin_actions` vs `picker_entries`), so no
 *    Settings > Plugins step is needed first.
 *
 * Together they pin both halves of the creation contract: a plugin that
 * needs no parent works on a blank canvas, and one that does still branches
 * off the selection.
 */
test("creates a node from an empty canvas, then a Conversation Node via the Plugins picker", async ({ page }) => {
  await gotoApp(page);

  await expect(page.locator(".react-flow__node")).toHaveCount(0);

  // Nothing selected: the picker itself is the creation path now.
  await page.locator('[data-overlay-trigger="plugins"]').click();
  // "Branch Foundations" (backend/plugins.py's _CATEGORY_META) is the
  // picker's own default active category, and both entries below belong to
  // it, so no category click is needed.
  await page.getByRole("option", { name: "System Prompt" }).click();

  await expect(page.locator(".react-flow__node")).toHaveCount(1);
  await expect(page.locator(".react-flow__node-note")).toHaveCount(1);

  // Select it - Conversation Node's own handler DOES require a valid
  // parent_node_id (plugins/conversation_node/plugin.py).
  await page.locator(".react-flow__node").first().click();

  await page.locator('[data-overlay-trigger="plugins"]').click();
  await page.getByRole("option", { name: "Conversation Node" }).click();

  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  // React Flow itself stamps `react-flow__node-<type>` on every node
  // wrapper from node.type (NODE_TYPES' own "conversation" key,
  // SceneCanvas.tsx) - a selector that needs no source change to be
  // stable, and proves the SECOND node is genuinely the conversation kind
  // just created, not just "some node".
  await expect(page.locator(".react-flow__node-conversation")).toHaveCount(1);
});
