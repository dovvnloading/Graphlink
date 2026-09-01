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

/** Fit All frames the graph in the whole canvas region, which the composer
 *  island overlays the bottom ~150px of. Nudge the viewport up by dragging
 *  empty canvas at the far left, clear of every node at fit zoom. */
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

    // Every capture is the full canvas at Fit All - no zoomed or clipped
    // figures. A canvas app's screenshots should show the canvas.
    await fitAll(page);
    await panUp(page, 30);
    await page.screenshot({ path: join(OUT_DIR, "canvas-branching.png") });
    console.log("captured canvas-branching.png");

    // -- The launcher, with a recipe's steps previewed. -------------------
    await page.locator('[data-overlay-trigger="builder-launch"]').click();
    const builder = page.getByRole("dialog", { name: "Builder" });
    await builder.waitFor({ state: "visible" });
    await builder.getByRole("button", { name: "Recipe" }).click();
    await page.getByText(/^Research and summarize/).first().click();
    await sleep(600);
    await page.screenshot({ path: join(OUT_DIR, "builder-launcher.png") });
    console.log("captured builder-launcher.png");
    await page.keyboard.press("Escape");
    await sleep(400);

    // NOTE: no light-theme capture. The node font color is scene state with
    // a near-white default chosen for the dark theme, so flipping the
    // emulated color scheme currently produces white-on-white node text - a
    // real product bug, tracked separately. Re-add a light capture once the
    // default adapts to the theme.

  } finally {
    if (browser) await browser.close();
    backend.kill();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
