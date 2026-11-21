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

  // The real WS handshake's outcome (App.tsx's data-connection-status),
  // not just "the page loaded". gotoApp already waited on this exact
  // attribute to reach "open" before returning, so this is a direct
  // re-assertion of that same state rather than a race - the app bar and
  // canvas checks above ran after that wait, and a WS drop between then and
  // now is exactly the kind of regression this line exists to catch.
  // connectionBadge.ts's own label text is NOT asserted here any more: it
  // now renders only for a degraded connection (App.tsx), so "connected"
  // has no visible text to check on the happy path this test exercises.
  await expect(page.locator('.app-shell[data-connection-status="open"]')).toBeAttached();

  // A fresh session has zero nodes, and that is asserted directly now. It
  // used to be asserted through SceneCanvas.tsx's empty-canvas hint - a
  // full-canvas overlay that has since been removed for painting itself
  // over every popover anchored on the canvas. An empty canvas is the state
  // this line was always about; the overlay was only ever a proxy for it.
  await expect(page.locator(".react-flow__node")).toHaveCount(0);
});

test("knowledge and Agent mode buttons keep their labels inside separate hit targets", async ({ page }) => {
  await gotoApp(page);
  const knowledge = page.getByRole("button", { name: "Use knowledge" });
  await expect(knowledge).not.toHaveAttribute("title", "Checking workspace knowledge…");

  const boxes = await page.evaluate(() => {
    const knowledgeButton = document.querySelector<HTMLButtonElement>(".composer-knowledge-toggle");
    const agentButton = document.querySelector<HTMLButtonElement>(
      ".composer-agent-toggle:not(.composer-knowledge-toggle)",
    );
    const knowledgeLabel = knowledgeButton?.querySelector("span");
    const agentLabel = agentButton?.querySelector("span");
    if (!knowledgeButton || !agentButton || !knowledgeLabel || !agentLabel) return null;
    const kb = knowledgeButton.getBoundingClientRect();
    const ab = agentButton.getBoundingClientRect();
    const kl = knowledgeLabel.getBoundingClientRect();
    const al = agentLabel.getBoundingClientRect();
    return {
      knowledge: { left: kb.left, right: kb.right, width: kb.width },
      agent: { left: ab.left, right: ab.right },
      knowledgeText: { left: kl.left, right: kl.right },
      agentText: { left: al.left, right: al.right },
    };
  });
  expect(boxes).not.toBeNull();
  if (!boxes) return;
  expect(boxes.knowledge.width).toBeGreaterThan(70); // not the rail's 30px icon width
  expect(boxes.knowledgeText.left).toBeGreaterThanOrEqual(boxes.knowledge.left);
  expect(boxes.knowledgeText.right).toBeLessThanOrEqual(boxes.knowledge.right);
  expect(boxes.knowledge.right).toBeLessThanOrEqual(boxes.agent.left);
  expect(boxes.agentText.left).toBeGreaterThanOrEqual(boxes.agent.left);
  expect(boxes.agentText.right).toBeLessThanOrEqual(boxes.agent.right);
});
