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

async function setGrants(page: Page, names: string[], enabled: boolean) {
  await page.locator('[data-overlay-trigger="settings"]').click();
  const settings = page.getByRole("dialog", { name: "Settings" });
  const previousSection = await settings.getByRole("region").getAttribute("aria-label");
  await settings.getByRole("button", { name: "Plugins", exact: true }).click();
  for (const name of names) {
    const grant = settings.getByRole("checkbox", { name: `Granted: ${name}`, exact: true });
    if ((await grant.isChecked()) !== enabled) await grant.click();
    await expect(grant).toBeChecked({ checked: enabled });
  }
  if (previousSection && previousSection !== "Plugins") {
    await settings.getByRole("button", { name: previousSection, exact: true }).click();
    await expect(settings.getByRole("region", { name: previousSection })).toBeVisible();
  }
  await page.keyboard.press("Escape");
  await expect(settings).toBeHidden();
}

test("imports measurements, analyzes a connected column, and reopens the reproducible result", async ({ page }, testInfo) => {
  await gotoApp(page);
  await setGrants(page, ["Scientific Core", "Statistics"], true);

  const source = await launchPack(page, "Scientific Core");
  await source.getByLabel("CSV measurements").fill("Station,Temperature (C)\n1,8\n2,10\n3,18");
  await source.getByRole("button", { name: "Run calculation" }).click();
  await expect(source.getByRole("table", { name: "Result table" })).toHaveCount(0);
  await source.getByText(/Tables · 3 rows/).click();
  await expect(source.getByRole("table", { name: "Result table" }).getByRole("row")).toHaveCount(4);
  await source.locator(".node-heading-row").click();
  const statistics = await launchPack(page, "Statistics");
  await statistics.getByRole("combobox", { name: "Import Observations from a connected result" })
    .selectOption({ label: "Scientific Core · Temperature (C)" });
  await expect(statistics.getByRole("textbox", { name: "Observations", exact: true })).toHaveValue("[8,10,18]");
  await statistics.getByRole("button", { name: "Run calculation" }).click();
  const latest = statistics.getByRole("region", { name: "Latest result" });
  const mean = latest.locator(".science-result-highlight strong");
  await expect(latest.locator(".science-result-highlight")).toContainText("Mean");
  await expect(mean).toHaveText("12");
  await expect(latest.locator("dl")).toHaveCount(0);
  await latest.getByText("Actions & exports").click();

  const downloadStarted = page.waitForEvent("download");
  await latest.getByRole("button", { name: "Export JSON" }).click();
  const download = await downloadStarted;
  expect(download.suggestedFilename()).toBe("statistics-describe.json");
  const exportPath = testInfo.outputPath(download.suggestedFilename());
  await download.saveAs(exportPath);
  const raw = await readFile(exportPath, "utf8");
  // Python's canonical float encoding is significant in the input hash.
  expect(raw).toMatch(/"observations"\s*:\s*\[\s*8\.0\s*,/);
  const result = JSON.parse(raw);
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

test("reviews concurrent edits in two views and enforces grant changes on a connected source", async ({ page, context }) => {
  await gotoApp(page);
  await setGrants(page, ["Scientific Core", "Statistics"], true);
  const source = await launchPack(page, "Scientific Core");
  await source.getByLabel("CSV measurements").fill("Reading\n1\n3");
  await source.getByRole("button", { name: "Run calculation" }).click();
  await source.getByText(/Tables · 2 rows/).click();
  await expect(source.getByRole("table")).toBeVisible();
  await source.locator(".node-heading-row").click();
  const stats = await launchPack(page, "Statistics");
  await stats.getByRole("combobox", { name: "Import Observations from a connected result" })
    .selectOption({ label: "Scientific Core · Reading" });
  await stats.getByRole("button", { name: "Run calculation" }).click();
  const mean = stats.locator(".science-result-highlight strong");
  await expect(mean).toHaveText("2");

  await setGrants(page, ["Scientific Core"], false);
  await expect(source.getByRole("status")).toContainText("Enable Scientific Core");
  await expect(stats.getByRole("button", { name: "Run calculation" })).toBeDisabled();
  await expect(mean).toHaveText("2");
  await setGrants(page, ["Scientific Core"], true);
  await expect(stats.getByRole("button", { name: "Run calculation" })).toBeEnabled();

  const input = stats.getByRole("textbox", { name: "Observations", exact: true });
  await input.fill("[null]");
  await stats.getByRole("button", { name: "Run calculation" }).click();
  await expect(input).toBeFocused();
  await expect(input).toHaveAttribute("aria-invalid", "true");
  await input.fill("[9007199254740993, 2]");
  await stats.getByRole("button", { name: "Run calculation" }).click();
  await expect(stats.getByRole("alert")).toContainText("cannot be represented exactly");
  await expect(mean).toHaveText("2");
  await input.fill("[1e-999,2]");
  await stats.getByRole("button", { name: "Run calculation" }).click();
  await expect(stats.getByRole("alert")).toContainText("too small");
  await input.fill("[40,60]");

  // Zooming below the level-of-detail threshold unmounts the actual editor.
  await page.emulateMedia({ reducedMotion: "reduce" });
  const zoomOut = page.getByRole("button", { name: "Zoom Out" });
  for (let i = 0; i < 8 && await input.count(); i++) {
    await zoomOut.click();
  }
  await expect(input).toHaveCount(0);
  await page.getByRole("button", { name: "Fit All" }).click();
  await expect(input).toHaveValue("[40,60]");

  const second = await context.newPage();
  await second.goto("/");
  await expect(second.locator(".app-shell")).toHaveAttribute("data-connection-status", "open");
  const onboarding = second.getByRole("dialog").filter({ has: second.getByText(/welcome to graphlink/i) });
  if (await onboarding.isVisible()) await second.keyboard.press("Escape");
  await second.keyboard.press("Escape");
  await second.getByRole("button", { name: "Fit All" }).click();
  const other = second.locator(".react-flow__node-scientific").filter({ has: second.locator(".node-heading-row").filter({ hasText: "Statistics" }) });
  await other.getByRole("textbox", { name: "Observations", exact: true }).fill("[4,6]");
  await other.getByRole("button", { name: "Run calculation" }).click();
  await expect(mean).toHaveText("5");
  await expect(input).toHaveValue("[40,60]");
  await expect(stats.getByRole("button", { name: "Run calculation" })).toBeDisabled();
  await stats.getByRole("button", { name: "Keep my inputs" }).click();
  await stats.getByRole("button", { name: "Run calculation" }).click();
  await expect(mean).toHaveText("50");
  await expect(other.getByRole("textbox", { name: "Observations", exact: true })).toHaveValue("[40,60]");
});
