/**
 * Re-shoots the README screenshots.
 *
 *     npm --prefix web_ui run build     # the app serves dist/app, not src
 *     npm --prefix web_ui run capture:screenshots
 *
 * WHY. assets/screenshots/ went stale because re-taking those images used to
 * be a manual afternoon: configure a provider, run a real build, hope it
 * produced something presentable, crop four screenshots by hand. So the
 * README kept showing a toolbar that no longer exists. This makes it one
 * command, and the content deterministic - the graph comes from
 * tools/seed_demo_graph.py, written by hand and assembled through the real
 * SceneDocument API, so no model has to be running and every capture is
 * identical to the last.
 *
 * RESOLUTION. 1920x1080 at deviceScaleFactor 2 gives 3840x2160, matching
 * what the previous screenshots were shot at. That is the whole reason this
 * drives a real Chromium rather than reusing the in-editor browser preview,
 * whose captures cap out around 800px wide - fine for checking a layout,
 * a visible downgrade in a README on any HiDPI display.
 *
 * ISOLATION. A fresh temp dir per run for settings and the chat database,
 * so this never touches ~/.graphlink. The backend is this repo's real
 * create_app(), the same one tests_e2e/run_backend.py boots.
 */

import { spawn } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

// Lives under web_ui/scripts/ (beside check-bundle-size.mjs) because that is
// where this repo's node tooling resolves its dependencies from - Playwright
// is a web_ui devDependency, and an ESM bare import resolves against the
// SCRIPT's directory, not the working directory.
const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const OUT_DIR = join(REPO_ROOT, "assets", "screenshots");
const PORT = 8801;
const BASE = `http://127.0.0.1:${PORT}`;
const VIEWPORT = { width: 1920, height: 1080 };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitForHealth(timeoutMs = 90_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${BASE}/api/health`);
      if (res.ok) return;
    } catch {
      // Not listening yet. The backend's import graph (matplotlib, the
      // provider SDKs) is genuinely slow on a cold filesystem cache.
    }
    await sleep(400);
  }
  throw new Error(`backend did not answer on ${BASE} in time`);
}

function run(cmd, args, opts = {}) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(cmd, args, { cwd: REPO_ROOT, stdio: "inherit", ...opts });
    child.on("error", reject);
    child.on("exit", (code) =>
      code === 0 ? resolvePromise() : reject(new Error(`${cmd} exited ${code}`)),
    );
  });
}

/** The map is canvas HUD, not part of a figure about two nodes - it is
 *  shown in the hero and folded away for the close-ups so it cannot sit in
 *  the corner of a diagram it has nothing to do with. */
async function setMinimapCollapsed(page, collapsed) {
  const toggle = page.locator(".scene-minimap-toggle");
  if (!(await toggle.count())) return;
  const label = await toggle.getAttribute("aria-label");
  const isCollapsed = label === "Expand minimap";
  if (isCollapsed !== collapsed) {
    await toggle.click();
    await sleep(250);
  }
}

/** Zoom the canvas in on a node by pointing at it and scrolling - the real
 *  gesture, so the framing cannot drift from what a user would see. */
async function zoomOnto(page, locator, steps) {
  const box = await locator.boundingBox();
  if (!box) throw new Error("node has no layout box to zoom onto");
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  for (let i = 0; i < steps; i += 1) {
    await page.mouse.wheel(0, -240);
    await sleep(120);
  }
  await sleep(500);
}

/** Fit All frames the graph in the whole canvas region, which the composer
 *  island overlays the bottom ~150px of - so the lowest row of a fitted
 *  graph sits behind it. Nudge the viewport up by dragging empty canvas at
 *  the far left, clear of every node at this zoom. */
async function panUp(page, dy) {
  await page.mouse.move(150, 320);
  await page.mouse.down();
  await page.mouse.move(150, 320 - dy, { steps: 12 });
  await page.mouse.up();
  await sleep(400);
}

async function fitAll(page) {
  await page.locator('[aria-label="Fit All"]').click();
  await sleep(900);
}

/**
 * A figure framed on the nodes it is about, rather than a screenshot of the
 * whole window with the subject somewhere in it. Full-viewport shots of a
 * zoomed-in canvas are mostly empty space, and the composer island sits over
 * the bottom of it - so the clip is clamped above the composer rather than
 * capturing chrome that has nothing to do with the figure.
 */
async function clipAround(page, locators, { pad = 48, bottomLimit = 1040 } = {}) {
  const boxes = [];
  for (const locator of locators) {
    const box = await locator.boundingBox();
    if (box) boxes.push(box);
  }
  if (boxes.length === 0) throw new Error("nothing to frame");
  const left = Math.max(0, Math.min(...boxes.map((b) => b.x)) - pad);
  const top = Math.max(0, Math.min(...boxes.map((b) => b.y)) - pad);
  const right = Math.min(VIEWPORT.width, Math.max(...boxes.map((b) => b.x + b.width)) + pad);
  const bottom = Math.min(bottomLimit, Math.max(...boxes.map((b) => b.y + b.height)) + pad);
  return { x: left, y: top, width: right - left, height: bottom - top };
}

async function main() {
  const dataDir = mkdtempSync(join(tmpdir(), "graphlink-capture-"));
  const dbPath = join(dataDir, "chats.db");

  console.log("seeding the demo graph...");
  await run("python", ["tools/seed_demo_graph.py", dbPath]);

  console.log("starting the backend...");
  const backend = spawn(
    "python",
    ["tools/capture_backend.py", String(PORT), dataDir],
    { cwd: REPO_ROOT, stdio: "inherit" },
  );

  let browser;
  try {
    await waitForHealth();

    browser = await chromium.launch();
    const page = await browser.newPage({
      viewport: VIEWPORT,
      deviceScaleFactor: 2,
      colorScheme: "dark",
    });

    await page.goto(BASE);
    await page.locator('.app-shell[data-connection-status="open"]').waitFor();

    // First run in a fresh settings dir, so onboarding opens. Escape is its
    // own documented dismissal path.
    const onboarding = page.getByRole("dialog", { name: "Welcome to Graphlink" });
    try {
      await onboarding.waitFor({ state: "visible", timeout: 6000 });
      await page.keyboard.press("Escape");
      await onboarding.waitFor({ state: "hidden" });
    } catch {
      // Never opened; nothing to dismiss.
    }

    console.log("loading the demo graph...");
    await page.locator('[data-overlay-trigger="library"]').click();
    const library = page.getByRole("dialog", { name: "Chat Library" });
    await library.waitFor({ state: "visible" });
    await library.getByRole("button", { name: /^Open chat/ }).first().click();
    await library.waitFor({ state: "hidden" });
    await page.locator(".react-flow__node").first().waitFor();
    // The load raises a notification, which lands over the canvas. Dismiss
    // it rather than shipping a screenshot of a toast.
    const toastClose = page.locator(".notification-dismiss").first();
    if (await toastClose.isVisible().catch(() => false)) await toastClose.click();
    await sleep(1200);

    // -- The hero: the whole graph, with the toolbar and the map. ---------
    await fitAll(page);
    await panUp(page, 95);
    await page.screenshot({ path: join(OUT_DIR, "canvas-branching.png") });
    console.log("captured canvas-branching.png");

    // -- A build, mid-run, beside the chart it produced. ------------------
    await setMinimapCollapsed(page, true);
    await fitAll(page);
    await zoomOnto(page, page.locator(".plan-node").first(), 1);
    await page.screenshot({
      path: join(OUT_DIR, "builder-run.png"),
      clip: await clipAround(page, [
        page.locator(".plan-node").first(),
        page.locator(".react-flow__node-chart").first(),
      ]),
    });
    console.log("captured builder-run.png");

    // -- Code and charts. -------------------------------------------------
    await fitAll(page);
    await zoomOnto(page, page.locator(".react-flow__node-code_sandbox").first(), 1);
    await page.screenshot({
      path: join(OUT_DIR, "code-and-charts.png"),
      clip: await clipAround(page, [
        page.locator(".react-flow__node-code_sandbox").first(),
        page.locator(".react-flow__node-chart").first(),
      ]),
    });
    console.log("captured code-and-charts.png");

    // -- The launcher, with a recipe's steps previewed. -------------------
    await setMinimapCollapsed(page, false);
    await fitAll(page);
    await page.locator('[data-overlay-trigger="builder-launch"]').click();
    const builder = page.getByRole("dialog", { name: "Builder" });
    await builder.waitFor({ state: "visible" });
    await builder.getByRole("button", { name: "Recipe" }).click();
    await page.getByText(/^Research and summarize/).first().click();
    await sleep(600);
    await page.screenshot({ path: join(OUT_DIR, "builder-launcher.png") });
    console.log("captured builder-launcher.png");
  } finally {
    if (browser) await browser.close();
    backend.kill();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
