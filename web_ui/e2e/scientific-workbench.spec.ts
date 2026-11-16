import { readFile } from "node:fs/promises";
import { expect, test, type Page } from "@playwright/test";
import { gotoApp } from "./helpers";
import { reopenSavedGraph } from "./table-workflow-helpers";

test.use({ viewport: { width: 1440, height: 1200 } });

async function launchPack(page: Page, name: string) {
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

test("imports measurements, analyzes a connected column, and reopens the reproducible result", async ({ page }, testInfo) => {
  await gotoApp(page);
  await page.locator('[data-overlay-trigger="settings"]').click();
  const settings = page.getByRole("dialog", { name: "Settings" });
  await settings.getByRole("button", { name: "Plugins", exact: true }).click();
  for (const pack of ["Scientific Core", "Statistics"]) {
    const grant = settings.getByRole("checkbox", { name: `Granted: ${pack}`, exact: true });
    if (!await grant.isChecked()) await grant.click();
    await expect(grant).toBeChecked();
  }
  await page.keyboard.press("Escape");
  await expect(settings).toBeHidden();

  const source = await launchPack(page, "Scientific Core");
  await source.getByLabel("CSV measurements").fill("Station,Temperature (C)\n1,8\n2,10\n3,18");
  await source.getByRole("button", { name: "Run calculation" }).click();
  await expect(source.getByRole("table", { name: "Result table" }).getByRole("row")).toHaveCount(4);
  await source.locator(".node-heading-row").click();
  const statistics = await launchPack(page, "Statistics");
  await statistics.getByRole("combobox", { name: "Import Observations from a connected result" })
    .selectOption({ label: "Scientific Core · Temperature (C)" });
  await expect(statistics.getByRole("textbox", { name: "Observations", exact: true })).toHaveValue("[8,10,18]");
  await statistics.getByRole("button", { name: "Run calculation" }).click();
  const latest = statistics.getByRole("region", { name: "Latest result" });
  const mean = latest.locator("dl > div").filter({ has: page.getByText("mean", { exact: true }) }).locator("dd");
  await expect(mean).toHaveText("12");

  const downloadStarted = page.waitForEvent("download");
  await latest.getByRole("button", { name: "Export JSON" }).click();
  const download = await downloadStarted;
  expect(download.suggestedFilename()).toBe("statistics-describe.json");
  const exportPath = testInfo.outputPath(download.suggestedFilename());
  await download.saveAs(exportPath);
  const result = JSON.parse(await readFile(exportPath, "utf8"));
  expect(result).toMatchObject({
    inputs: { observations: [8, 10, 18] }, values: { count: 3, mean: 12 },
    provenance: { packId: "statistics", sources: {
      observations: { packId: "core", operationId: "numeric_csv", selector: ["column", 1],
        resultHash: expect.stringMatching(/^[a-f0-9]{64}$/) },
    } },
  });

  await statistics.locator(".node-heading-row").click();
  await page.keyboard.press("Control+z");
  await expect(latest).toHaveCount(0);
  await page.keyboard.press("Control+Shift+z");
  await expect(mean).toHaveText("12");

  await reopenSavedGraph(page);
  await expect(mean).toHaveText("12");
  await latest.getByText("Inputs and provenance", { exact: true }).click();
  await expect(latest.locator("pre")).toContainText(result.provenance.resultHash);
  await expect(latest.locator("pre")).toContainText(result.provenance.sources.observations.resultHash);
});
