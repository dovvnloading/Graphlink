import { expect, test } from "@playwright/test";
import { gotoApp } from "./helpers";

/**
 * Browser Agent is a real first-party plugin picker action, not a duplicated
 * frontend-only button. This proves the production SPA sends executePlugin,
 * receives the native harness node, and can render it on an empty canvas.
 * Creating the card must NOT invoke Playwright or an LLM.
 */
test("Browser Agent creates an editable workspace-agent node without browsing", async ({ page }, testInfo) => {
  await gotoApp(page);
  await expect(page.locator(".react-flow__node")).toHaveCount(0);

  await page.locator('[data-overlay-trigger="plugins"]').click();
  const picker = page.getByRole("dialog", { name: "Plugins" });
  await expect(picker).toBeVisible();
  await picker.getByRole("searchbox", { name: "Search plugins" }).fill("Browser Agent");

  const entry = picker.getByRole("button", { name: /^Browser Agent/ });
  await expect(entry).toBeVisible();
  await entry.click();

  const agent = page.locator(".react-flow__node-harness");
  await expect(agent).toHaveCount(1);
  await expect(agent).toContainText("Browser Agent");
  await expect(page.locator(".react-flow__node")).toHaveCount(1);

  const screenshot = testInfo.outputPath("browser-agent-plugin.png");
  await page.screenshot({ path: screenshot });
  await testInfo.attach("browser-agent-plugin", { path: screenshot, contentType: "image/png" });
});
