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

  // Settings navigation is session-local on the backend; earlier specs can
  // leave another page active even after the canvas is reset.
  const general = rail.getByRole("button", { name: "General", exact: true });
  await general.click();
  await expect(general).toHaveAttribute("aria-current", "page");
  await expect(dialog.getByRole("region", { name: "General" })).toBeVisible();
});

// The policy kit Select is an independent nested overlay, not a second dialog.
test("Escape closes only the nested listbox before closing Settings", async ({ page }) => {
  await gotoApp(page);
  const opener = page.locator('[data-overlay-trigger="settings"]');
  await opener.click();
  const dialog = page.getByRole("dialog", { name: "Settings" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Close Settings" })).not.toBeFocused();
  await dialog.getByRole("navigation", { name: "Settings sections" })
    .getByRole("button", { name: "General", exact: true }).click();

  const policy = dialog.getByRole("button", { name: "Model selection policy" });
  await policy.click();
  const listbox = page.getByRole("listbox", { name: "Model selection policy" });
  await expect(listbox).toBeVisible();
  await expect(listbox.getByRole("option", { name: "Best quality" })).toBeVisible();
  await expect(listbox).toBeFocused();

  await page.keyboard.press("Escape");
  await expect(listbox).toBeHidden();
  await expect(dialog).toBeVisible();
  await expect(policy).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(opener).toBeFocused();
});
