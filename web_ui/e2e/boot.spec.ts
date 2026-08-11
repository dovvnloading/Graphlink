import { expect, test } from "@playwright/test";
import { gotoApp } from "./helpers";

/**
 * ADR-015 stage 15.6: the boot-smoke test. Proves the full real stack -
 * backend/app.py's create_app() under uvicorn (tests_e2e/run_backend.py),
 * the real built SPA it serves at "/", and a real WS connection between
 * them - comes up cleanly, with no LLM/network dependency anywhere in the
 * path (see SceneCanvas.tsx/AppBar.tsx, both read directly, for the exact
 * DOM this asserts against).
 */
test("boots against the real backend and renders the canvas shell", async ({ page }) => {
  await gotoApp(page);

  // App.tsx's header: the real app bar, not a placeholder.
  await expect(page.getByRole("toolbar", { name: "Application bar" })).toBeVisible();

  // SceneCanvas.tsx's own wrapper (data-testid added alongside this suite -
  // see that file's own comment) plus React Flow's real viewport element,
  // which only exists once ReactFlow has actually mounted and measured its
  // container.
  await expect(page.getByTestId("scene-canvas")).toBeVisible();
  await expect(page.locator(".react-flow__viewport")).toBeVisible();

  // The connection badge (connectionBadge.ts) reporting the real WS
  // handshake's outcome, not just "the page loaded".
  await expect(page.locator(".app-conn-open")).toHaveText("connected");

  // A fresh session has zero nodes - SceneCanvas.tsx's own empty-state hint
  // is the honest "nothing broken, genuinely nothing here yet" signal for
  // that case (see its own comment on why this exists at all).
  await expect(page.locator(".scene-empty-hint")).toBeVisible();
});
