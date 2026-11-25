import { expect, test, type Page } from "@playwright/test";
import { openTable, editHours } from "./table-workflow-helpers";
import type { WorkflowSpec } from "../src/lib/workflow-contract/generated/workflow";
import { runWaiting, type DetachedRun, type RunSummary } from "../src/app/chrome/workflowApi";

test.use({ viewport: { width: 1440, height: 1000 }, contextOptions: { reducedMotion: "reduce" } });

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
    await page.getByRole("option", { name: value, exact: true }).click();
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
    const hide = preview.getByRole("button", { name: "Hide full" });
    await expect(hide).toHaveAttribute("aria-expanded", "true");
    await expect(hide).toBeFocused();
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
    if (await dialog.isVisible()) { await page.keyboard.press("Escape"); await expect(dialog).toBeHidden(); }
  }
});

test("library revision selection and keyboard dismissal in both themes", async ({ page }, testInfo) => {
  await openTable(page);
  for (const theme of ["Dark", "Light"] as const) {
    await setTheme(page, theme);
    const trigger = page.locator('[data-overlay-trigger="workflow-library"]');
    await trigger.press("Enter");
    const library = page.getByRole("dialog", { name: "Workflow library" });
    const starters = library.getByRole("region", { name: "Built-in starter workflows" });
    await starters.getByRole("button", { name: /^Add .+ to the library$/ }).first().press("Enter");
    const version = library.getByRole("button", { name: "Version history" });
    await expect(version).toContainText("Version 1");
    await version.press("Enter");
    await expect(page.getByRole("listbox", { name: "Version history" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(version).toBeFocused();
    await expect(library).toBeVisible();
    await expect(library.getByRole("region", { name: "Run this workflow" }).getByRole("button", { name: "Preview workflow" })).toBeEnabled();
    const image = testInfo.outputPath(`library-${theme.toLowerCase()}.png`);
    await page.screenshot({ path: image, animations: "disabled" });
    await testInfo.attach(`Workflow library (${theme})`, { path: image, contentType: "image/png" });
    await library.getByRole("button", { name: "All workflows" }).press("Enter");
    await library.getByRole("searchbox", { name: "Search workflows" }).fill("no-audit-matches-8ca970");
    await expect(library.getByRole("status").filter({ hasText: "No workflows match your search." })).toBeAttached();
    await page.keyboard.press("Escape");
    await expect(library).toBeHidden();
    await expect(trigger).toBeFocused();
  }
});

test("a reviewed human approval gate waits until explicit keyboard approval", async ({ page }) => {
  await openTable(page);
  await page.locator('[data-overlay-trigger="workflow-library"]').press("Enter");
  const history = page.getByRole("dialog", { name: "Workflow library" });
  await history.getByRole("tab", { name: /^Runs/ }).press("Enter");
  await history.getByRole("button", { name: "Run from JSON" }).press("Enter");
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
  const inspector = history.getByRole("region", { name: "Run detail" });
  await expect(inspector.getByRole("button", { name: "Approve step", exact: true })).toBeEnabled();
  await expect(history.getByRole("status").filter({ hasText: "Waiting for approval: Review" })).toBeAttached();

  // A notification must navigate even while the existing library dialog is open
  // on an unsaved JSON launcher. Reviewing is not an approval or submission.
  const notification = history.getByRole("region", { name: "Notifications" }).getByRole("status")
    .filter({ hasText: "Keyboard approval audit is waiting for you" });
  const review = notification.getByRole("button", { name: "Review run", exact: true });
  await expect(review).toBeVisible();
  await history.getByRole("button", { name: "All runs" }).press("Enter");
  await history.getByRole("button", { name: "Run from JSON" }).press("Enter");
  await history.getByRole("textbox", { name: "Workflow request JSON (spec and parameters)" }).fill("unsaved draft");
  await review.click();
  await expect(history.getByRole("textbox", { name: "Workflow request JSON (spec and parameters)" })).toHaveCount(0);
  await expect(inspector.getByRole("heading", { name: "Keyboard approval audit" })).toBeVisible();
  await expect(history.getByRole("heading", { name: "Selected run" })).toBeFocused();
  await expect(inspector.getByRole("button", { name: "Approve step", exact: true })).toBeEnabled();
  await expect(history.getByRole("status").filter({ hasText: "Waiting for approval: Review" })).toBeAttached();
  await inspector.getByRole("button", { name: "Approve step", exact: true }).press("Enter");
  await expect(history.getByRole("status").filter({ hasText: "Keyboard approval audit: Completed" })).toBeAttached();
  await expect(inspector.getByRole("button", { name: "Approve step", exact: true })).toHaveCount(0);
});

/** Deterministic browser coverage of navigation races: only workflow HTTP
 * receipts and active-approval reads are stubbed; the SPA and library use
 * the real backend. The test above separately exercises a real approval. */
for (const theme of ["Dark", "Light"] as const) {
  test("approval notifications navigate from presets and other workspaces in " + theme, async ({ page }, testInfo) => {
    const port = { name: "value", kind: "text", required: true } as const;
    const spec: WorkflowSpec = {
      schema_version: 1, workflow_id: "notification-browser", revision: "r1", name: "Notification target workflow",
      inputs: [port],
      steps: [{ step_id: "review", adapter: {
        adapter_id: "control.approval", adapter_version: "1", side_effect_class: "pure",
        ports: { inputs: [port], outputs: [port] },
      }, config_json: JSON.stringify({ kind: "text", message: "Check it" }),
      bindings: [{ port: "value", source: { kind: "input", port: "value" } }] }],
      outputs: [{ port, source: { kind: "step", step_id: "review", port: "value" } }],
    };
    const waiting: DetachedRun = {
      run_id: "target", status: "running", error: null, cost_usd: 0, created_at: "2026-11-24T10:00:00Z", spec,
      inputs: { value: { kind: "text", text: "input" } }, outputs: {},
      steps: { review: { step_id: "review", status: "running", error: null, approval: { status: "pending", message: "Check it" } } },
      preview: { fingerprint: "fixture", estimated_cost_usd: 0, requires_approval: false, steps: [], control_flow: {} },
    };
    const other: DetachedRun = { ...waiting, run_id: "other", status: "completed", steps: {},
      spec: { ...spec, name: "Other workflow" } };
    const stale: DetachedRun = { ...other, run_id: "target", spec: { ...spec, name: "Stale workspace receipt" } };
    function summary(record: DetachedRun): RunSummary {
      const { run_id, preset_id, status, created_at, started_at, finished_at, cost_usd, spec } = record;
      return { run_id, preset_id, status, created_at, started_at, finished_at, cost_usd,
        spec: { name: spec.name }, waiting_for_approval: runWaiting(record) };
    }
    let showNotice = false;
    let missing = false;
    let releaseTarget!: () => void;
    let targetReady = new Promise<void>((resolve) => { releaseTarget = resolve; });
    const mutations: string[] = [];
    await page.route("**/api/workflows/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (route.request().method() !== "GET") {
        mutations.push(route.request().method() + " " + path);
        await route.fulfill({ status: 400, json: { detail: "Unexpected mutation during navigation" } });
        return;
      }
      if (path === "/api/workflows/active-approvals") {
        await route.fulfill({ json: showNotice ? [{ workspace_id: 4, run_id: "target", workflow: spec.name,
          step_id: "review", message: "Check it" }] : [] });
      } else if (path === "/api/workflows/workspaces") {
        await route.fulfill({ json: [{ id: 3, name: "First" }, { id: 4, name: "Second" }] });
      } else if (path === "/api/workflows/3/runs") {
        await route.fulfill({ json: [summary(stale)] });
      } else if (path === "/api/workflows/4/runs") {
        await targetReady;
        await route.fulfill({ json: missing ? [] : [waiting, other].map(summary) });
      } else if (path === "/api/workflows/3/runs/target") {
        await route.fulfill({ json: stale });
      } else if (path === "/api/workflows/4/runs/target") {
        await targetReady;
        await route.fulfill(missing ? { status: 404, json: { detail: "Workflow run not found." } } : { json: waiting });
      } else if (path === "/api/workflows/4/runs/other") {
        await route.fulfill({ json: other });
      } else if (path.endsWith("/presets")) {
        await route.fulfill({ json: [] });
      } else {
        await route.fallback();
      }
    });

    await openTable(page);
    await setTheme(page, theme);
    await page.locator('[data-overlay-trigger="workflow-library"]').press("Enter");
    const library = page.getByRole("dialog", { name: "Workflow library" });
    await library.getByRole("region", { name: "Built-in starter workflows" })
      .getByRole("button", { name: /^Add .+ to the library$/ }).first().press("Enter");
    await expect(library.getByRole("button", { name: "Version history" })).toBeVisible();
    await library.getByRole("tab", { name: /^Presets/ }).press("Enter");
    await library.getByRole("button", { name: "New preset" }).press("Enter");
    await expect(library.getByRole("button", { name: "Preview preset" })).toBeEnabled();
    const draft = library.getByRole("textbox", { name: "Preset name" });
    await draft.fill("unsaved preset draft");
    showNotice = true;
    const review = library.getByRole("region", { name: "Notifications" }).getByRole("button", { name: "Review run", exact: true });
    const target = library.getByRole("heading", { name: spec.name, exact: true });
    const selected = library.getByRole("heading", { name: "Selected run" });
    const approve = library.getByRole("button", { name: "Approve step", exact: true });
    await expect(review).toBeVisible();
    await expect(page.getByRole("button", { name: "Review run", exact: true })).toHaveCount(1);
    await review.press("Enter");
    try {
      await expect(library.getByText("Loading the run…")).toBeVisible();
      await expect(draft).toHaveCount(0);
      await expect(approve).toHaveCount(0);
    } finally { releaseTarget(); }
    await expect(target).toBeVisible();
    await expect(selected).toBeFocused();
    await expect(approve).toBeEnabled();
    await library.getByRole("tab", { name: /^Presets/ }).press("Enter");
    await expect(draft).toHaveCount(0);
    await expect(library.getByText("No presets yet")).toBeVisible();
    await library.getByRole("tab", { name: /^Runs/ }).press("Enter");
    await library.getByRole("button", { name: "All runs" }).press("Enter");
    await library.getByRole("button", { name: /^Other workflow,/ }).press("Enter");
    await expect(library.getByRole("heading", { name: "Other workflow" })).toBeVisible();
    await review.click();
    await expect(target).toBeVisible();
    await expect(selected).toBeFocused();

    await library.getByRole("button", { name: "All runs" }).press("Enter");
    await library.getByRole("button", { name: "Workspace", exact: true }).press("Enter");
    await page.getByRole("option", { name: "First", exact: true }).click();
    await library.getByRole("button", { name: /^Stale workspace receipt,/ }).press("Enter");
    await expect(library.getByRole("heading", { name: "Stale workspace receipt" })).toBeVisible();
    targetReady = new Promise<void>((resolve) => { releaseTarget = resolve; });
    await review.click();
    try {
      await expect(library.getByText("Loading the run…")).toBeVisible();
      await expect(library.getByRole("heading", { name: "Stale workspace receipt" })).toHaveCount(0);
      await expect(approve).toHaveCount(0);
    } finally { releaseTarget(); }
    await expect(target).toBeVisible();
    await expect(selected).toBeFocused();
    await expect(approve).toBeEnabled();
    const screenshot = testInfo.outputPath("workflow-notification-" + theme.toLowerCase() + ".png");
    await page.screenshot({ path: screenshot, animations: "disabled" });
    await testInfo.attach("Approval navigation (" + theme + ")", { path: screenshot, contentType: "image/png" });

    missing = true;
    await review.click();
    await expect(library.getByText(/This run is no longer available in this workspace/)).toBeVisible();
    await expect(approve).toHaveCount(0);
    await library.getByRole("button", { name: "All runs" }).press("Enter");
    await expect(library.getByText("No runs yet")).toBeVisible();
    expect(mutations).toEqual([]);
  });
}
