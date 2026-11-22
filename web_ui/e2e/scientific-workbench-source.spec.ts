import { expect, test } from "@playwright/test";
import { gotoApp } from "./helpers";

test.use({ viewport: { width: 1440, height: 1200 } });

test("creates an upstream dataset from an empty table field, reviews its import and runs the grouped calculation", async ({ page }) => {
  await gotoApp(page);
  await page.locator('[data-overlay-trigger="settings"]').click();
  const settings = page.getByRole("dialog", { name: "Settings" });
  const priorSection = await settings.getByRole("region").getAttribute("aria-label");
  await settings.getByRole("button", { name: "Plugins", exact: true }).click();
  const grant = settings.getByRole("checkbox", { name: "Granted: Statistics", exact: true });
  const wasGranted = await grant.isChecked();
  if (!wasGranted) await grant.click();
  await expect(grant).toBeChecked();
  await page.keyboard.press("Escape");
  try {
    await page.locator('[data-overlay-trigger="plugins"]').click();
    const search = page.getByRole("searchbox", { name: "Search plugins" });
    await search.fill("Statistics");
    await page.getByRole("list", { name: "Plugin search results" }).getByText("Statistics", { exact: true }).click();
    const target = page.locator(".react-flow__node-scientific").first();
    await page.getByRole("button", { name: "Fit All" }).click();
    await target.getByLabel("Operation", { exact: true }).selectOption("grouped_table_summary");
    const recovery = target.getByRole("region", { name: "Connect a source table" });
    await recovery.getByRole("button", { name: "Create source and import" }).click();
    await expect(page.locator(".react-flow__node-scientific")).toHaveCount(2);
    await page.getByRole("button", { name: "Fit All" }).click();
    await recovery.getByRole("textbox", { name: "Pasted table text" }).fill("Group,Value\nA,10\nA,20\nB,25");
    await recovery.getByRole("button", { name: "Preview table", exact: true }).click();
    await expect(recovery.getByRole("table", { name: "Pending import preview" })).toContainText("25");
    await recovery.getByRole("button", { name: "Apply reviewed table" }).click();
    const sourceTable = target.getByRole("combobox", { name: "Import Typed table from a connected result" });
    await expect(sourceTable).toBeVisible();
    await sourceTable.selectOption({ index: 1 });
    await target.getByLabel("Numeric measurement column").selectOption({ label: "Value · numeric" });
    await target.getByLabel("Group column").selectOption({ index: 1 });
    await target.getByRole("button", { name: "Run calculation" }).click();
    const result = target.getByRole("region", { name: "Latest result" });
    await expect(result.locator(".science-result-highlight strong")).toHaveText("2");
    await expect(recovery).toHaveCount(0);
  } finally {
    await page.keyboard.press("Escape");
    await page.locator('[data-overlay-trigger="settings"]').click();
    await settings.getByRole("button", { name: "Plugins", exact: true }).click();
    if (!wasGranted && await grant.isChecked()) await grant.click();
    if (priorSection && priorSection !== "Plugins") await settings.getByRole("button", { name: priorSection, exact: true }).click();
    await page.keyboard.press("Escape");
  }
});
