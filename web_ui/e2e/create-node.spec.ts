import { expect, test } from "@playwright/test";
import { gotoApp } from "./helpers";

/**
 * ADR-015 stage 15.6: creates two real nodes through two real UI entry
 * points, neither needing an LLM call or any network access:
 *
 * 1. Double-click on empty canvas - SceneCanvas.tsx's own "R1 create-node
 *    gesture" (its onDoubleClick handler), which fires the `addNode` scene
 *    intent (backend/api/intents_nodes.py) straight into
 *    SceneDocument.add_node - a pure in-memory mutation, no provider call
 *    anywhere in that path. This is also the only way to get a FIRST node
 *    onto an empty canvas: every plugin-picker entry (below) requires an
 *    already-selected parent node, so there is no other real entry point
 *    for node #1.
 * 2. The Plugins popover (AppBar.tsx's `[data-overlay-trigger="plugins"]`)
 *    -> "Conversation Node" - one of the 7 first-party plugins migrated
 *    onto ADR-014's `register_builtin_plugin` escape hatch
 *    (plugins/conversation_node/plugin.py). Picked over every other
 *    picker entry specifically because its handler
 *    (SceneDocument.add_conversation_node) does nothing but create and
 *    connect a node - unlike, say, Web Research or the Virtual Environment
 *    Runner, it never calls out to a provider or a subprocess, so this
 *    stays exactly as deterministic and offline as the double-click gesture above.
 *    Built-in plugins also bypass the install-time consent/grant gate
 *    entirely (see backend/plugins.py's own module docstring on
 *    `builtin_actions` vs `picker_entries`), so there is no extra Settings
 *    > Plugins step needed before this can run.
 */
test("creates a node by double-click, then a Conversation Node via the Plugins picker", async ({ page }) => {
  await gotoApp(page);

  const canvas = page.getByTestId("scene-canvas");
  const box = await canvas.boundingBox();
  if (!box) throw new Error("scene-canvas has no layout box to click into");
  // Deliberately NOT the canvas's exact center: SceneCanvas.tsx's own empty-
  // state hint (".scene-empty-hint", styles.css) is centered with `inset: 0`
  // + flexbox centering and stays mounted until the first node exists - a
  // dblclick at width/2,height/2 lands ON its real "Load Sample Workspace"
  // button instead of blank canvas. A corner offset stays clear of it.
  await canvas.dblclick({ position: { x: box.width * 0.15, y: box.height * 0.15 } });

  await expect(page.locator(".react-flow__node")).toHaveCount(1);

  // Select the freshly created node - Conversation Node's own handler
  // requires a valid parent_node_id (plugins/conversation_node/plugin.py).
  await page.locator(".react-flow__node").first().click();

  await page.locator('[data-overlay-trigger="plugins"]').click();
  // "Branch Foundations" (backend/plugins.py's _CATEGORY_META) is the
  // picker's own default active category, so no extra category click is
  // needed - Conversation Node is a member of it.
  await page.getByRole("option", { name: "Conversation Node" }).click();

  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  // React Flow itself stamps `react-flow__node-<type>` on every node
  // wrapper from node.type (NODE_TYPES' own "conversation" key,
  // SceneCanvas.tsx) - a selector that needs no source change to be
  // stable, and proves the SECOND node is genuinely the conversation kind
  // just created, not just "some node".
  await expect(page.locator(".react-flow__node-conversation")).toHaveCount(1);
});
