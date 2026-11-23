import { expect, test } from "@playwright/test";
import { gotoApp } from "./helpers";

test.use({ viewport: { width: 1440, height: 1200 } });

test("creates an upstream dataset from an empty table field, reviews its import and runs the grouped calculation", async ({ page }) => {
  await gotoApp(page);
  await page.locator('[data-overlay-trigger="settings"]').click();
  const settings = page.getByRole("dialog", { name: "Settings" });
  const priorSection = await settings.locator(".settings-page").getAttribute("aria-label");
  await settings.getByRole("button", { name: "Plugins", exact: true }).click();
  const grant = settings.getByRole("switch", { name: "Granted: Statistics", exact: true });
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
    const operation = target.getByRole("button", { name: "Operation", exact: true });
    await operation.click();
    await page.getByRole("listbox", { name: "Operation", exact: true })
      .getByRole("option", { name: "Summarize a typed table by group", exact: true }).click();
    await expect(operation).toContainText("Summarize a typed table by group");
    const recovery = target.getByRole("region", { name: "Connect a source table" });
    await recovery.getByRole("button", { name: "Create source and import" }).click();
    await expect(page.locator(".react-flow__node-scientific")).toHaveCount(2);
    await page.getByRole("button", { name: "Fit All" }).click();
    await recovery.getByRole("textbox", { name: "Pasted table text" }).fill("Group,Value\nA,10\nA,20\nB,25");
    await recovery.getByRole("button", { name: "Preview table", exact: true }).click();
    await expect(recovery.getByRole("table", { name: "Pending import preview" })).toContainText("25");
    await recovery.getByRole("button", { name: "Apply reviewed table" }).click();
    const sourceTable = target.getByRole("button", { name: "Import Typed table from a connected result" });
    await expect(sourceTable).toBeVisible();
    await sourceTable.click();
    await page.getByRole("listbox", { name: "Import Typed table from a connected result", exact: true })
      .getByRole("option", { name: "Statistics · Whole typed table (3 rows, 2 columns)", exact: true }).click();
    const measurement = target.getByRole("button", { name: "Numeric measurement column", exact: true });
    await measurement.click();
    await page.getByRole("listbox", { name: "Numeric measurement column", exact: true })
      .getByRole("option", { name: "Value · numeric", exact: true }).click();
    await expect(measurement).toContainText("Value · numeric");
    const group = target.getByRole("button", { name: "Group column", exact: true });
    await group.click();
    await page.getByRole("listbox", { name: "Group column", exact: true })
      .getByRole("option", { name: "Group · categorical", exact: true }).click();
    await expect(group).toContainText("Group · categorical");
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
