import { expect, type Locator, type Page } from "@playwright/test";
import { gotoApp } from "./helpers";

export async function openTable(page: Page) {
  await gotoApp(page);
  await page.getByRole("button", { name: "Add a table", exact: true }).click();
  const source = page.locator(".react-flow__node-note").filter({
    has: page.getByRole("region", { name: "Data table" }),
  });
  await expect(source).toBeVisible();
  return source;
}

export async function editHours(source: Locator, hours: string) {
  await source.getByRole("button", { name: "Edit table", exact: true }).click();
  await source.getByRole("textbox", { name: "Hours, row 1", exact: true }).fill(hours);
  await source.getByRole("button", { name: "Save table", exact: true }).click();
  await expect(source.getByRole("table", { name: "Table data" }).getByRole("row").nth(1))
    .toContainText(hours);
}

export async function reopenSavedGraph(page: Page) {
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const libraryButton = page.locator('[data-overlay-trigger="library"]');
  const library = page.getByRole("dialog", { name: "Chat library" });
  await libraryButton.click();
  page.once("dialog", (dialog) => { void dialog.accept(); });
  await library.getByRole("button", { name: "New chat", exact: true }).first().click();
  await expect(page.locator(".react-flow__node")).toHaveCount(0);
  await libraryButton.click();
  await library.locator(".library-row").first().getByRole("button", { name: /^Open chat/ }).click();
  await expect(library).toBeHidden();
  await page.getByRole("button", { name: "Fit All" }).click();
}
