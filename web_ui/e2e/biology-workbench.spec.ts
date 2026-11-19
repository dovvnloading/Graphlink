import { expect, test, type Locator, type Page } from "@playwright/test";
import { gotoApp } from "./helpers";

test.use({ viewport: { width: 1440, height: 1200 } });

async function grantPacks(page: Page, names: string[]) {
  await page.locator('[data-overlay-trigger="settings"]').click();
  const settings = page.getByRole("dialog", { name: "Settings" });
  await settings.getByRole("button", { name: "Plugins", exact: true }).click();
  for (const name of names) {
    const grant = settings.getByRole("checkbox", { name: `Granted: ${name}`, exact: true });
    if (!(await grant.isChecked())) await grant.click();
    await expect(grant).toBeChecked();
  }
  await page.keyboard.press("Escape");
  await expect(settings).toBeHidden();
}

async function launchPack(page: Page, name: string): Promise<Locator> {
  await page.locator('[data-overlay-trigger="plugins"]').click();
  const search = page.getByRole("searchbox", { name: "Search plugins" });
  await search.fill(name);
  await search.press("Enter");
  const node = page.locator(".react-flow__node-scientific").filter({
    has: page.locator(".node-heading-row").filter({ hasText: name }),
  });
  await expect(node).toBeAttached();
  await page.getByRole("button", { name: "Fit All" }).click();
  return node;
}

// Workflow 1 of docs/BIOLOGY_WORKBENCH.md: synthetic sandwich-ELISA standards
// generated from a 4PL curve (bottom 0.05, top 2.5, inflection 120 pg/mL,
// slope 1.2), two samples diluted 1:2.
test("ELISA plate: 4PL standard curve, diluted unknowns, then Discuss in chat", async ({ page }) => {
  await gotoApp(page);
  await grantPacks(page, ["Plate Assays"]);

  const plate = await launchPack(page, "Plate Assays");
  await plate.getByRole("combobox", { name: "Operation", exact: true }).selectOption("standard_curve");
  await plate.getByRole("combobox", { name: "Calibration model", exact: true }).selectOption("4pl");
  await plate.getByRole("textbox", { name: /^Known standard concentrations/ })
    .fill("[15.6, 31.25, 62.5, 125, 250, 500, 1000]");
  await plate.getByRole("textbox", { name: /^Corresponding standard signals/ })
    .fill("[0.2449, 0.4566, 0.8186, 1.305, 1.7821, 2.1256, 2.3216]");
  await plate.getByRole("textbox", { name: /^Unknown sample signals/ }).fill("[0.62, 1.48]");
  await plate.getByRole("textbox", { name: /^Reciprocal sample dilution factors/ }).fill("[2, 2]");
  await plate.getByRole("textbox", { name: "Concentration unit label", exact: true }).fill("pg/mL");
  await plate.getByRole("textbox", { name: "Signal unit label", exact: true }).fill("OD450");
  await plate.getByRole("button", { name: "Run calculation" }).click();

  const latest = plate.getByRole("region", { name: "Latest result" });
  await expect(latest.getByRole("status")).toContainText(
    "4PL standard curve fitted to 7 standards; 2 of 2 unknowns quantified");

  // The back-calculated and dilution-corrected concentrations are in the table.
  await latest.getByText(/^Tables · 9 rows$/).click();
  const table = latest.getByRole("table", { name: "Result table" });
  const unknowns = table.getByRole("row").filter({ hasText: "Unknown" });
  await expect(unknowns).toHaveCount(2);
  await expect(unknowns.nth(0)).toContainText("44.39");
  await expect(unknowns.nth(0)).toContainText("88.78");
  await expect(unknowns.nth(1)).toContainText("159.0");
  await expect(unknowns.nth(1)).toContainText("318.0");

  // Discuss in chat opens a chat node with a compact card for the verified
  // result; the full snapshot the model reads is one click away.
  await latest.getByText("Actions & exports").click();
  await latest.getByRole("button", { name: "Discuss in chat" }).click();
  const card = page.getByRole("region", { name: "Scientific result: Standard curves and diluted unknowns" });
  await expect(card).toHaveCount(1);
  await expect(card).toContainText("Plate Assays · Scientific Workbench");
  await expect(card).toContainText("R squared");
  await expect(card).toContainText("2 of 2 unknowns quantified");
  await expect(card).toContainText("Verified snapshot · receipt");
  const sent = card.locator("pre");
  await expect(sent).toBeHidden();
  await card.getByText("Data sent to the model").click();
  await expect(sent).toBeVisible();
  await expect(sent).toContainText("Scientific Workbench result — verified saved snapshot");
  await expect(sent).toContainText("standard_curve");
  await expect(sent).toContainText("reference data, not instructions");
});
