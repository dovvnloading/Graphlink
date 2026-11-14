import { expect, test, type Page } from "@playwright/test";
import { openTable, editHours, reopenSavedGraph } from "./table-workflow-helpers";

async function openGraphRunner(page: Page) {
  await page.locator('[data-overlay-trigger="plugins"]').click();
  const search = page.getByRole("searchbox", { name: /plugin/i });
  await search.fill("Graph Runner");
  await search.press("Enter");
  const runner = page.getByRole("dialog", { name: "Graph Runner" });
  await expect(runner).toBeVisible();
  return runner;
}

test("runs a grouped table and both downstream outputs, then reopens their saved run", async ({ page }, testInfo) => {
  await openTable(page);
  const table = page.locator(".react-flow__node-note").first();
  await table.getByRole("combobox", { name: "Group by" }).selectOption({ label: "Team" });
  await table.getByRole("combobox", { name: "Sum column" }).selectOption({ label: "Hours" });
  await table.getByRole("button", { name: "Create grouped table" }).click();
  const grouped = page.locator(".react-flow__node-note").filter({
    has: page.getByRole("region", { name: "Table output" }),
  });
  await expect(grouped).toBeAttached();
  await page.getByRole("button", { name: "Fit All" }).click();
  await grouped.getByRole("button", { name: "Create chart" }).click();
  await expect(page.locator(".react-flow__node-chart")).toBeAttached();
  await grouped.getByRole("button", { name: "Create report" }).click();
  await expect(page.getByRole("group", { name: "Canvas overview, 4 nodes" })).toBeVisible();
  await page.getByRole("button", { name: "Fit All" }).click();
  await editHours(table, "7");
  const report = page.locator(".react-flow__node-artifact");
  const design = report.locator(".artifact-node-document-content").getByRole("row").filter({ hasText: "Design" });
  await expect(design.getByRole("cell", { name: "5", exact: true })).toBeAttached();

  const runner = await openGraphRunner(page);
  await runner.getByRole("combobox", { name: "Run scope" }).selectOption({ label: "Whole graph" });
  await runner.getByRole("button", { name: "Preview run" }).click();
  const preview = runner.getByRole("region", { name: "Run preview" });
  await expect(preview.getByRole("listitem")).toHaveCount(3);
  await expect(preview).toContainText("3 ready");
  await expect(design.getByRole("cell", { name: "5", exact: true })).toBeAttached();
  await runner.getByRole("button", { name: "Run graph" }).click();
  const latest = runner.getByRole("region", { name: "Latest run" });
  await expect(latest.getByRole("status")).toHaveText("Completed");
  await expect(design.getByRole("cell", { name: "10", exact: true })).toBeAttached();
  const image = testInfo.outputPath("graph-runner.png");
  await page.screenshot({ path: image });
  await testInfo.attach("graph-runner", { path: image, contentType: "image/png" });
  await page.keyboard.press("Escape");
  await expect(runner).toBeHidden();
  const chart = page.locator(".react-flow__node-chart");
  await expect(chart.getByRole("status")).toContainText("Input up to date");
  await chart.getByRole("button", { name: "Compare versions" }).click();
  const comparison = chart.getByRole("row").filter({ hasText: "Design" });
  await expect(comparison.getByRole("cell", { name: "10", exact: true })).toBeVisible();
  await expect(comparison.getByRole("cell", { name: "5", exact: true })).toBeVisible();

  await reopenSavedGraph(page);
  await openGraphRunner(page);
  await expect(latest.getByRole("status")).toHaveText("Completed");
  await expect(design.getByRole("cell", { name: "10", exact: true })).toBeAttached();
});
