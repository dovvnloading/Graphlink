import { expect, test } from "@playwright/test";
import { gotoApp } from "./helpers";

/**
 * ADR-015 stage 15.6: opens Settings via the real AppBar affordance and
 * asserts the real section tabs render - SettingsDialog.tsx's own SECTIONS
 * list, read directly rather than guessed. That list actually has 8
 * entries today (General, Ollama (Local), Llama.cpp (Local), API Endpoint,
 * Integrations, MCP Servers, Plugins, Resource Limits); this asserts the
 * 5 this repo's own architecture doc names explicitly, not the full set,
 * so this spec doesn't need updating every time a new settings page is
 * added.
 */
test("opens Settings and renders the real provider/general section tabs", async ({ page }) => {
  await gotoApp(page);

  await page.locator('[data-overlay-trigger="settings"]').click();

  const dialog = page.getByRole("dialog", { name: "Settings" });
  await expect(dialog).toBeVisible();

  const rail = dialog.getByRole("navigation", { name: "Settings sections" });
  for (const section of ["General", "Ollama (Local)", "Llama.cpp (Local)", "API Endpoint", "Integrations"]) {
    await expect(rail.getByRole("button", { name: section, exact: true })).toBeVisible();
  }

  // General is the default active section (SettingsDialog.tsx's initial
  // state) - its own page should already be rendered without an extra click.
  await expect(dialog.getByRole("region", { name: "General" })).toBeVisible();
});
