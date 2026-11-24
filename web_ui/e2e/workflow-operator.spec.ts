import { expect, test, type Page } from "@playwright/test";
import { openTable, editHours } from "./table-workflow-helpers";
import type { WorkflowSpec } from "../src/lib/workflow-contract/generated/workflow";

test.use({ viewport: { width: 1440, height: 1000 }, reducedMotion: "reduce" });

async function runner(page: Page) {
  await page.locator('[data-overlay-trigger="plugins"]').press("Enter");
  const search = page.getByRole("searchbox", { name: /plugin/i });
  await search.fill("Graph Runner");
  await search.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Graph runner" });
  await expect(dialog).toBeVisible();
  return dialog;
}

async function setTheme(page: Page, name: "Dark" | "Light") {
  await page.locator('[data-overlay-trigger="settings"]').press("Enter");
  const settings = page.getByRole("dialog", { name: "Settings" });
  await settings.getByRole("button", { name: "General", exact: true }).press("Enter");
  await settings.getByRole("radiogroup", { name: "Theme", exact: true })
    .getByRole("radio", { name, exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", name.toLowerCase());
  await page.keyboard.press("Escape");
  await expect(settings).toBeHidden();
}

test("keyboard preview, execute, inspect and compare durable runs in both themes", async ({ page }, testInfo) => {
  const source = await openTable(page);
  const sourceId = await source.getAttribute("data-id");
  const table = page.locator(`.react-flow__node-note[data-id="${sourceId}"]`);
  for (const [label, value] of [["Group by", "Team"], ["Sum column", "Hours"]]) {
    const select = table.getByRole("button", { name: label, exact: true });
    await select.press("Enter");
    await page.getByRole("option", { name: value, exact: true }).press("Enter");
    await expect(select).toContainText(value);
  }
  await table.getByRole("button", { name: "Create grouped table" }).press("Enter");
  await expect(page.getByRole("region", { name: "Table output" })).toBeAttached();
  await page.getByRole("button", { name: "Fit All" }).click();

  for (const [theme, hours] of [["Dark", "7"], ["Light", "9"]] as const) {
    await setTheme(page, theme);
    await editHours(table, hours);
    const dialog = await runner(page);
    const scope = dialog.getByRole("button", { name: "Run scope" });
    await scope.press("Home");
    await page.keyboard.press("Enter");
    await expect(scope).toContainText("Whole graph");
    await dialog.getByRole("button", { name: "Preview run" }).press("Enter");
    const preview = dialog.getByRole("region", { name: "Run preview" });
    await expect(preview).toContainText("1 ready");
    await expect(preview.getByRole("heading", { name: "Run preview" })).toBeFocused();
    const show = preview.getByRole("button", { name: "Show full" });
    await expect(show).toHaveAttribute("aria-expanded", "false");
    await expect(preview.getByRole("region", { name: "Full input" })).toHaveCount(0);
    await show.press("Enter");
    await expect(show).toHaveAttribute("aria-expanded", "true");
    await expect(show).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(preview.getByRole("region", { name: "Full input" })).toBeFocused();
    await preview.getByRole("button", { name: "Hide full" }).press("Enter");
    await preview.getByRole("button", { name: "Jump to run confirmation" }).press("Enter");
    const run = preview.getByRole("button", { name: "Run graph" });
    await expect(run).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(dialog.getByRole("region", { name: "Latest run" }).getByRole("status")).toContainText("Completed");
    await dialog.getByRole("button", { name: "Run history", exact: true }).press("Enter");
    const history = page.getByRole("dialog", { name: "Run history", exact: true });
    const runs = history.getByRole("button", { name: /^View run / });
    await expect(runs).toHaveCount(theme === "Dark" ? 1 : 2);
    await runs.first().press("Enter");
    await expect(history.getByRole("heading", { name: "Selected run" })).toBeFocused();
    const detail = history.getByRole("region", { name: "Run detail" });
    await expect(detail).toBeVisible();
    await expect(detail.getByRole("region", { name: "Full input" })).toHaveCount(0);
    await detail.getByRole("button", { name: "Show full" }).first().press("Enter");
    await expect(detail.getByRole("region", { name: "Full input" }).first()).toBeVisible();
    await detail.getByRole("button", { name: "Hide full" }).first().press("Enter");
    if (theme === "Light") {
      await history.getByRole("button", { name: "Compare with another run" }).press("Enter");
      await page.keyboard.press("End");
      await page.keyboard.press("Enter");
      await expect(history.getByRole("region", { name: "Step comparison" })).toBeVisible();
    }
    const image = testInfo.outputPath(`history-${theme.toLowerCase()}.png`);
    await page.screenshot({ path: image, animations: "disabled" });
    await testInfo.attach(`Run history (${theme})`, { path: image, contentType: "image/png" });
    await history.getByRole("button", { name: "Back to run history" }).press("Enter");
    await expect(history.getByRole("heading", { name: "Saved runs" })).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(history).toBeHidden();
  }
});

test("library revision selection and keyboard dismissal in both themes", async ({ page }, testInfo) => {
  await openTable(page);
  for (const theme of ["Dark", "Light"] as const) {
    await setTheme(page, theme);
    const trigger = page.locator('[data-overlay-trigger="workflow-library"]');
    await trigger.press("Enter");
    const library = page.getByRole("dialog", { name: "Workflow Library" });
    const starters = library.getByRole("region", { name: "Built-in starter workflows" });
    await starters.getByRole("button", { name: /^Import / }).first().press("Enter");
    const version = library.getByRole("button", { name: "Version history" });
    await expect(version).toContainText("Version 1");
    await version.press("Enter");
    await expect(page.getByRole("listbox", { name: "Version history" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(version).toBeFocused();
    await expect(library).toBeVisible();
    await library.getByRole("searchbox", { name: "Search workflows" }).fill("no-audit-matches-8ca970");
    await expect(library.getByRole("status").filter({ hasText: "No workflows match your search." })).toBeVisible();
    const image = testInfo.outputPath(`library-${theme.toLowerCase()}.png`);
    await page.screenshot({ path: image, animations: "disabled" });
    await testInfo.attach(`Workflow Library (${theme})`, { path: image, contentType: "image/png" });
    await page.keyboard.press("Escape");
    await expect(library).toBeHidden();
    await expect(trigger).toBeFocused();
  }
});

test("a reviewed human approval gate waits until explicit keyboard approval", async ({ page }) => {
  await openTable(page);
  const dialog = await runner(page);
  await dialog.getByRole("button", { name: "Run history", exact: true }).press("Enter");
  const history = page.getByRole("dialog", { name: "Run history", exact: true });
  await history.getByRole("button", { name: "Detached workflow runs" }).press("Enter");
  await history.getByRole("button", { name: "Preview and start a typed workflow" }).press("Enter");
  const port = { name: "value", kind: "text", required: true } as const;
  const spec: WorkflowSpec = {
    schema_version: 1, workflow_id: "operator-audit", revision: "v1", name: "Keyboard approval audit",
    inputs: [port],
    steps: [{ step_id: "review", adapter: {
      adapter_id: "control.approval", adapter_version: "1", side_effect_class: "pure",
      ports: { inputs: [port], outputs: [port] },
    }, config_json: JSON.stringify({ kind: "text", message: "Check the synthetic input" }),
    bindings: [{ port: "value", source: { kind: "input", port: "value" } }] }],
    outputs: [{ port, source: { kind: "step", step_id: "review", port: "value" } }],
  };
  await history.getByRole("textbox", { name: "Workflow request JSON (spec and parameters)" })
    .fill(JSON.stringify({ spec, parameters: { value: { kind: "text", text: "synthetic input" } } }));
  await history.getByRole("button", { name: "Preview workflow", exact: true }).press("Enter");
  await expect(history.getByRole("region", { name: "Workflow preview", exact: true })).toContainText("Check the synthetic input");
  await history.getByRole("button", { name: /^(Start reviewed workflow|Approve preview and start)$/ }).press("Enter");
  const inspector = history.getByRole("region", { name: "Detached workflow inspector" });
  await expect(inspector.getByRole("button", { name: "Approve step", exact: true })).toBeEnabled();
  await expect(history.getByRole("status").filter({ hasText: "Waiting for approval: review" })).toBeVisible();
  await inspector.getByRole("button", { name: "Approve step", exact: true }).press("Enter");
  await expect(history.getByRole("status").filter({ hasText: "Keyboard approval audit: completed" })).toBeVisible();
  await expect(inspector.getByRole("button", { name: "Approve step", exact: true })).toHaveCount(0);
});
