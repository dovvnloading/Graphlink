import { expect, test, type Locator, type Page } from "@playwright/test";
import { gotoApp } from "./helpers";

async function openTable(page: Page) {
  await gotoApp(page);
  await page.getByRole("button", { name: "Add a table", exact: true }).click();
  const source = page.locator(".react-flow__node-note").filter({
    has: page.getByRole("region", { name: "Data table" }),
  });
  await expect(source).toBeVisible();
  return source;
}

async function editHours(source: Locator, hours: string) {
  await source.getByRole("button", { name: "Edit table", exact: true }).click();
  await source.getByRole("textbox", { name: "Hours, row 1", exact: true }).fill(hours);
  await source.getByRole("button", { name: "Save table", exact: true }).click();
  await expect(source.getByRole("table", { name: "Table data" }).getByRole("row").nth(1))
    .toContainText(hours);
}

async function reopenSavedGraph(page: Page) {
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const libraryButton = page.locator('[data-overlay-trigger="library"]');
  const library = page.getByRole("dialog", { name: "Chat Library" });
  await libraryButton.click();
  page.once("dialog", (dialog) => { void dialog.accept(); });
  await library.getByRole("button", { name: "New Chat", exact: true }).first().click();
  await expect(page.locator(".react-flow__node")).toHaveCount(0);
  await libraryButton.click();
  await library.locator(".library-row").first().getByRole("button", { name: /^Open chat/ }).click();
  await expect(library).toBeHidden();
  await page.getByRole("button", { name: "Fit All" }).click();
}

// Chart nodes load lazily. Their fixed-size placeholder can be replaced
// without a resize, leaving a visible port whose connection was never drawn.
async function chartConnectionIsPainted(page: Page) {
  return page.evaluate(() => {
    const source = document.querySelector('.react-flow__node-note [data-handleid="table-source"]');
    const target = document.querySelector('.react-flow__node-chart [data-handleid="table-target"]');
    const canvas = document.querySelector<HTMLCanvasElement>(".scene-connection-canvas");
    const context = canvas?.getContext("2d");
    if (!source || !target || !canvas || !context) return false;
    const start = source.getBoundingClientRect(), end = target.getBoundingClientRect();
    const bounds = canvas.getBoundingClientRect();
    const gap = end.left - start.right;
    if (gap <= 0 || !bounds.width || !bounds.height) return false;
    const scaleX = canvas.width / bounds.width, scaleY = canvas.height / bounds.height;
    const x = Math.floor((start.right + gap / 3 - bounds.left) * scaleX);
    const y = Math.floor((Math.min(start.top, end.top) - bounds.top - 4) * scaleY);
    const width = Math.max(1, Math.ceil(gap / 3 * scaleX));
    const height = Math.ceil((Math.max(start.bottom, end.bottom) - Math.min(start.top, end.top) + 8) * scaleY);
    const pixels = context.getImageData(x, y, width, height).data;
    return pixels.some((value, index) => index % 4 === 3 && value > 0);
  });
}

test("a chart and a Drafter keep independent versions of a grouped table after reopening", async ({ page }) => {
  const source = await openTable(page);
  const chart = page.locator(".react-flow__node-chart");
  const report = page.locator(".react-flow__node-artifact");
  await source.getByRole("combobox", { name: "Group by", exact: true }).selectOption({ label: "Team" });
  await source.getByRole("combobox", { name: "Sum column", exact: true }).selectOption({ label: "Hours" });
  await source.getByRole("button", { name: "Create chart", exact: true }).click();
  await expect(page.getByRole("group", { name: "Canvas overview, 2 nodes" })).toBeVisible();
  await page.getByRole("button", { name: "Fit All" }).click();
  await expect.poll(() => chartConnectionIsPainted(page)).toBe(true);
  await source.getByRole("button", { name: "Create report", exact: true }).click();
  await expect(page.getByRole("group", { name: "Canvas overview, 3 nodes" })).toBeVisible();
  await page.getByRole("button", { name: "Fit All" }).click();
  await expect(report).toBeAttached();

  const designRow = report.locator(".artifact-node-document-content").getByRole("row").filter({ hasText: "Design" });
  await expect(designRow.getByRole("cell", { name: "5", exact: true })).toBeVisible();
  await editHours(source, "7");
  await expect(chart.getByRole("status")).toContainText("Source changed");
  await expect(report.getByRole("status")).toContainText("Source changed");
  await expect(report.getByRole("button", { name: "Inspect source rows" })).toBeDisabled();
  await chart.getByRole("button", { name: "Rerun from table" }).click();
  await expect(chart.getByRole("status")).toContainText("Input up to date");
  await chart.getByRole("button", { name: "Inspect source rows" }).click();
  const contributors = chart.getByRole("table", { name: "Source rows for Design" });
  await expect(contributors.getByRole("row").nth(1).getByRole("cell")).toHaveText(["1", "7"]);
  await expect(contributors.getByRole("row").nth(2).getByRole("cell")).toHaveText(["3", "3"]);
  await chart.getByRole("button", { name: "Inspect source rows" }).click();
  await chart.getByRole("button", { name: "Compare versions" }).click();
  const changedGroup = chart.getByRole("row").filter({ hasText: "Design" });
  await expect(changedGroup.getByRole("cell", { name: "5", exact: true })).toBeVisible();
  await expect(changedGroup.getByRole("cell", { name: "10", exact: true })).toBeVisible();
  await chart.getByRole("button", { name: "Close comparison" }).click();
  await expect(designRow.getByRole("cell", { name: "5", exact: true })).toBeVisible();

  await reopenSavedGraph(page);
  await expect(chart.getByRole("status")).toContainText("Input up to date");
  await expect(report.getByRole("status")).toContainText("Source changed");
  await report.getByRole("button", { name: "Rerun from table" }).click();
  await expect(designRow.getByRole("cell", { name: "10", exact: true })).toBeVisible();
  await report.getByRole("button", { name: "Compare versions" }).click();
  const restore = report.getByRole("button", { name: "Restore previous version" });
  await expect(restore).toBeEnabled();
  await restore.click();
  await expect(designRow.getByRole("cell", { name: "5", exact: true })).toBeVisible();
  await expect(report.getByRole("status")).toContainText("Source changed");
  await expect(chart.getByRole("status")).toContainText("Input up to date");
});

test("refreshes only outputs that use the edited column and preserves authored report text", async ({ page }) => {
  const source = await openTable(page);
  const csv = 'Team,Hours,Cost\n"Design, UX",2,12\n"Design, UX",3,8\nEngineering,4,40';
  await source.getByRole("button", { name: "Edit CSV", exact: true }).click();
  await source.getByLabel("Import CSV file").setInputFiles({
    name: "project-hours.csv", mimeType: "text/csv", buffer: Buffer.from(csv),
  });
  await expect(source.getByRole("textbox", { name: "CSV data" })).toHaveValue(csv);
  await source.getByRole("button", { name: "Save table", exact: true }).click();
  await expect(source.getByRole("cell", { name: "Design, UX", exact: true })).toHaveCount(2);
  const sum = source.getByRole("combobox", { name: "Sum column", exact: true });
  await sum.selectOption({ label: "Hours" });
  await source.getByRole("button", { name: "Create chart", exact: true }).click();
  await sum.selectOption({ label: "Cost" });
  await source.getByRole("button", { name: "Create report", exact: true }).click();
  const chart = page.locator(".react-flow__node-chart");
  const report = page.locator(".react-flow__node-artifact");
  await expect(page.getByRole("group", { name: "Canvas overview, 3 nodes" })).toBeVisible();
  await page.getByRole("button", { name: "Fit All" }).click();
  await expect(report).toBeAttached();

  await editHours(source, "11");
  await expect(chart.getByRole("status")).toContainText("Source changed");
  await expect(report.getByRole("status")).toContainText("Input up to date");
  const markdown = report.locator(".artifact-node-document-content");
  await expect(markdown.getByRole("row").filter({ hasText: "Design, UX" })).toContainText("20");
  await report.getByRole("button", { name: "Inspect source rows" }).click();
  const contributors = report.getByRole("table", { name: "Source rows for Design, UX" });
  await expect(contributors.getByRole("row").nth(1).getByRole("cell")).toHaveText(["1", "12"]);
  await report.getByRole("combobox", { name: "Inspect group" }).selectOption({ label: "Engineering" });
  await expect(report.getByRole("table", { name: "Source rows for Engineering" }).getByRole("row").nth(1).getByRole("cell"))
    .toHaveText(["3", "40"]);

  await report.getByRole("button", { name: "Edit Markdown", exact: true }).click();
  await report.getByRole("textbox", { name: "Edit document Markdown" }).fill("Keep this editorial note.");
  await report.getByRole("button", { name: "Save changes", exact: true }).click();
  await expect(markdown).toHaveText("Keep this editorial note.");
  await expect(report.getByRole("button", { name: "Rerun from table" })).toBeDisabled();
  await reopenSavedGraph(page);
  await expect(markdown).toHaveText("Keep this editorial note.");
  await expect(report.getByRole("button", { name: "Rerun from table" })).toBeDisabled();
});
