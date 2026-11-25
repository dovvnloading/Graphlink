import { expect, test, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
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
    // Wait for the async history load and verify the keyboard's active option.
    // The failing Chromium trace showed the active descendant change under a
    // stationary pointer between Home and Enter; keep the final assertion too.
    await expect(scope).toBeEnabled();
    await scope.press("Home");
    const scopeOptions = page.getByRole("listbox", { name: "Run scope" });
    await expect(scopeOptions).toBeFocused();
    const wholeGraph = scopeOptions.getByRole("option", { name: "Whole graph", exact: true });
    await expect(scopeOptions).toHaveAttribute("aria-activedescendant", (await wholeGraph.getAttribute("id"))!);
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
  const reads: string[] = [];
  page.on("request", request => {
    const url = new URL(request.url());
    if (request.method() === "GET" && /^\/api\/workflows\/\d+\/(runs|presets)(\/|$)/.test(url.pathname)) {
      reads.push(url.pathname + url.search);
    }
  });
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

  // Observe a second real poll while the receipt is still awaiting approval.
  // The two-second cadence must never request a full list or another receipt.
  const activeDetail = reads.find(path => /\/runs\/[^/]+$/.test(path));
  expect(activeDetail).toBeDefined();
  const detailReads = () => reads.filter(path => path === activeDetail).length;
  const beforePoll = detailReads();
  await expect.poll(detailReads, { timeout: 8000 }).toBeGreaterThan(beforePoll);
  expect(reads.some(path => /\/runs\?summary=true$/.test(path))).toBe(true);
  expect(reads.some(path => /\/(runs|presets)$/.test(path))).toBe(false);

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

// Use real persisted receipts and approved, manual, pure presets. The browser
// must never download the full history just to render badges or list rows.
test("large workflow history uses bounded summaries and on-demand receipts", async ({ page }, testInfo) => {
  test.setTimeout(180_000);
  await openTable(page);
  const workspaces = await page.request.get("/api/workflows/workspaces");
  expect(workspaces.ok()).toBe(true);
  const workspace = (await workspaces.json())[0].id as number;
  const base = "/api/workflows/" + workspace;
  const ports = [{ name: "first", kind: "text", required: true }, { name: "second", kind: "text", required: true }] as const;
  const specFor = (index: number): WorkflowSpec => ({
    schema_version: 1, workflow_id: "operator-history-bench-" + index, revision: "v1",
    name: "History benchmark " + index, inputs: [...ports], steps: [],
    outputs: ports.map(port => ({ port, source: { kind: "input", port: port.name } })),
  });
  const inline = "x".repeat(32 * 1024);
  const parameters = { first: { kind: "text", text: inline }, second: { kind: "text", text: inline } };
  const ids: string[] = [];
  for (let i = 0; i < 50; i++) {
    const started = await page.request.post(base + "/runs", { data: { spec: specFor(i), parameters } });
    expect(started.status(), await started.text()).toBe(202);
    const { run_id } = await started.json();
    ids.push(run_id);
    await started.dispose();
    await expect.poll(async () => {
      const response = await page.request.get(base + "/runs/" + run_id);
      const status = response.ok() ? (await response.json()).status : response.status();
      await response.dispose();
      return status;
    }).toBe("completed");
  }
  const presetRequest = { spec: specFor(50), parameters: {
    ...parameters, first: { kind: "text", text: "p".repeat(900_000) },
  } };
  const preview = await page.request.post(base + "/presets/preview", { data: presetRequest });
  expect(preview.ok(), await preview.text()).toBe(true);
  const { fingerprint } = await preview.json();
  await preview.dispose();
  const presetIds: string[] = [];
  for (let i = 0; i < 20; i++) {
    const saved = await page.request.post(base + "/presets", { data: {
      request: presetRequest, name: "History benchmark preset " + i, allowed_effects: ["pure"],
      cost_usd: 0, trigger: null, fingerprint, approved: true,
    } });
    expect(saved.status(), await saved.text()).toBe(201);
    presetIds.push((await saved.json()).preset_id);
    await saved.dispose();
  }

  try {
    const sizes: Record<string, { bytes: number; milliseconds: number }> = {};
    for (const path of ["/runs", "/runs?summary=true", "/presets", "/presets?summary=true"]) {
      const start = performance.now();
      const response = await page.request.get(base + path);
      expect(response.ok()).toBe(true);
      sizes[path] = { bytes: (await response.body()).byteLength, milliseconds: performance.now() - start };
      await response.dispose();
    }
    expect(sizes["/runs"].bytes).toBeGreaterThan(50 * 64 * 1024);
    expect(sizes["/presets"].bytes).toBeGreaterThan(20 * 900_000);
    expect(sizes["/runs?summary=true"].bytes).toBeLessThan(50_000);
    expect(sizes["/presets?summary=true"].bytes).toBeLessThan(20_000);

    const requests: string[] = [];
    page.on("request", request => {
      const url = new URL(request.url());
      if (request.method() === "GET" && url.pathname.startsWith(base + "/")) {
        requests.push(url.pathname.slice(base.length) + url.search);
      }
    });
    const count = (path: string) => requests.filter(item => item === path).length;
    const library = page.getByRole("dialog", { name: "Workflow library" });
    const timings: Record<string, number> = {};
    const start = performance.now();
    await page.locator('[data-overlay-trigger="workflow-library"]').press("Enter");
    await expect(library.getByRole("tab", { name: "Runs (50)", exact: true })).toBeVisible();
    await expect(library.getByRole("tab", { name: "Presets (20)", exact: true })).toBeVisible();
    timings.workflows = performance.now() - start;
    const idleCounts = [count("/runs?summary=true"), count("/presets?summary=true")];
    // Observe a complete former 2-second polling interval on an inactive tab.
    await page.waitForTimeout(2500);
    expect([count("/runs?summary=true"), count("/presets?summary=true")]).toEqual(idleCounts);

    const runsStart = performance.now();
    await library.getByRole("tab", { name: "Runs (50)", exact: true }).click();
    const newest = library.getByRole("button", { name: /^History benchmark 49,/ });
    await expect(newest).toBeVisible();
    timings.runs = performance.now() - runsStart;
    expect(requests.filter(path => /^\/runs\/[^/]+$/.test(path))).toEqual([]);
    const detailStart = performance.now();
    await newest.click();
    await expect(library.getByRole("heading", { name: "History benchmark 49", exact: true })).toBeVisible();
    timings.detail = performance.now() - detailStart;
    const detailPath = "/runs/" + ids[49];
    expect(count(detailPath)).toBe(1);
    const runReads = count("/runs?summary=true");
    await page.waitForTimeout(2500);
    expect(count(detailPath)).toBe(1);
    expect(count("/runs?summary=true")).toBe(runReads);

    const presetsStart = performance.now();
    await library.getByRole("tab", { name: "Presets (20)", exact: true }).click();
    await expect(library.getByText("History benchmark preset 19", { exact: true })).toBeVisible();
    timings.presets = performance.now() - presetsStart;
    await page.waitForTimeout(2500);
    expect(count(detailPath)).toBe(1);
    expect(count("/runs")).toBe(0);
    expect(count("/presets")).toBe(0);
    expect(requests.filter(path => /^\/runs\/[^/]+$/.test(path))).toEqual([detailPath]);
    const measurements = JSON.stringify({ sizes, timings, requests }, null, 2);
    await testInfo.attach("Large history HTTP sizes and browser interaction timings", {
      body: measurements, contentType: "application/json",
    });
    // Successful browser runs retain their Actions log even when a reporter
    // does not persist non-image attachments.
    console.info("Large workflow history benchmark:\n" + measurements);
    await page.keyboard.press("Escape");
    await expect(library).toBeHidden();
  } finally {
    for (const id of presetIds) {
      const deleted = await page.request.delete(base + "/presets/" + id);
      await deleted.dispose();
    }
  }
});

const inspectorValues = [
  { name: "inline text", payload: { kind: "text", text: "x".repeat(65_536) }, external: false },
  { name: "external text", payload: { kind: "text", text: "x".repeat(65_537) }, external: true },
  { name: "external JSON", payload: { kind: "json", value: { text: "x".repeat(65_537) } }, external: true },
  { name: "external table", payload: { kind: "table", columns: ["id", "data"], rows: [["001", "x".repeat(65_537)]] }, external: true },
] as const;

for (const { name, payload, external } of inspectorValues) {
  test(`detached run inspector preserves ${name} through the real backend`, async ({ page }) => {
    await openTable(page);
    let assetRequests = 0;
    page.on("request", request => { if (/\/api\/workflows\/\d+\/runs\/[^/]+\/assets\//.test(request.url())) assetRequests++; });
    await page.locator('[data-overlay-trigger="workflow-library"]').press("Enter");
    const library = page.getByRole("dialog", { name: "Workflow library" });
    await library.getByRole("tab", { name: /^Runs/ }).press("Enter");
    await library.getByRole("button", { name: "Run from JSON" }).press("Enter");
    const port = { name: "value", kind: payload.kind, required: true } as const;
    const spec: WorkflowSpec = {
      schema_version: 1, workflow_id: "inspector-artifact-audit", revision: "v1", name: "Inspector " + name,
      inputs: [port],
      steps: [{ step_id: "review", adapter: {
        adapter_id: "control.approval", adapter_version: "1", side_effect_class: "pure",
        ports: { inputs: [port], outputs: [port] },
      }, config_json: JSON.stringify({ kind: payload.kind, message: "Check the synthetic input" }),
      bindings: [{ port: "value", source: { kind: "input", port: "value" } }] }],
      outputs: [{ port, source: { kind: "step", step_id: "review", port: "value" } }],
    };
    await library.getByRole("textbox", { name: "Workflow request JSON (spec and parameters)" })
      .fill(JSON.stringify({ spec, parameters: { value: payload } }));
    await library.getByRole("button", { name: "Preview workflow", exact: true }).press("Enter");
    await expect(library.getByRole("region", { name: "Workflow preview", exact: true })).toContainText("Check the synthetic input");
    await library.getByRole("button", { name: /^(Start reviewed workflow|Approve preview and start)$/ }).press("Enter");
    const inspector = library.getByRole("region", { name: "Run detail" });
    await inspector.getByRole("button", { name: "Approve step", exact: true }).press("Enter");
    await expect(library.getByRole("status").filter({ hasText: "Inspector " + name + ": Completed" })).toBeAttached();
    const result = inspector.getByRole("region", { name: "Result" });
    await expect(inspector.getByRole("button", { name: "Save result as graph" })).toBeEnabled();
    if (external) {
      await expect(result.getByText(/Stored separately · 64\./)).toBeVisible();
      await inspector.getByRole("button", { name: "Inputs (1)" }).press("Enter");
      await inspector.getByRole("button", { name: "Outputs (1)" }).press("Enter");
      await expect(inspector.getByRole("button", { name: "Download value (JSON)" })).toHaveCount(3);
      expect(assetRequests).toBe(0);
      const download = page.waitForEvent("download");
      await result.getByRole("button", { name: "Download value (JSON)" }).press("Enter");
      const artifact = await download;
      expect(JSON.parse(await readFile(await artifact.path(), "utf8"))).toEqual(payload);
      expect(assetRequests).toBe(1);
    } else {
      await expect(result.getByRole("region", { name: "Value" })).toHaveText("x".repeat(65_536));
      await expect(result.getByRole("button", { name: /Download/ })).toHaveCount(0);
      expect(assetRequests).toBe(0);
    }
  });
}
