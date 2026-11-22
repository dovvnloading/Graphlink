/**
 * Capture the current UI in both themes as a visual regression baseline.
 *
 *   npm --prefix web_ui run build
 *   cd web_ui && npm run capture:ui -- /absolute/path/outside/the/repo
 *
 * Uses the same deterministic seed, real backend and health wait as
 * capture-screenshots.mjs, but never writes to the checkout or ~/.graphlink.
 * Individual screenshot failures go into manifest.json and do not abort the
 * remainder of the run. The backend is always terminated, including SIGINT.
 */
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const PORT = 8802;
const BASE = `http://127.0.0.1:${PORT}`;
const VIEWPORT = { width: 1440, height: 900 };
const THEMES = ["dark", "light"];
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

// Check the resolved target as well as its spelling: a symlink outside the
// checkout can otherwise resolve to a directory *inside* the repository.
function resolvedDestination(destination) {
  let parent = destination;
  const missing = [];
  while (!existsSync(parent)) {
    missing.unshift(basename(parent));
    const next = dirname(parent);
    if (next === parent) throw new Error("Cannot resolve the output directory");
    parent = next;
  }
  return resolve(realpathSync(parent), ...missing);
}

function outputDirectory(argument) {
  if (!argument) throw new Error("Usage: node scripts/capture-ui.mjs <out-dir> (outside the repository)");
  const directory = resolvedDestination(resolve(argument));
  const root = realpathSync(REPO_ROOT);
  const below = relative(root, directory);
  if (below === "" || (below !== ".." && !below.startsWith(`..${sep}`) && !isAbsolute(below))) {
    throw new Error("The capture output directory must be outside the repository");
  }
  mkdirSync(directory, { recursive: true });
  return directory;
}

function run(cmd, args, env) {
  return new Promise((done, fail) => {
    const child = spawn(cmd, args, { cwd: REPO_ROOT, env, stdio: "inherit" });
    child.once("error", fail);
    child.once("exit", (code) => code === 0 ? done() : fail(new Error(`${cmd} exited ${code}`)));
  });
}

async function waitForHealth(backend, getSpawnError, timeoutMs = 90_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (getSpawnError()) throw getSpawnError();
    if (backend.exitCode !== null || backend.signalCode !== null) {
      throw new Error(`backend exited before its health check (${backend.exitCode ?? backend.signalCode})`);
    }
    try {
      const response = await fetch(`${BASE}/api/health`);
      if (response.ok) return;
    } catch {
      // A cold backend can take time to load its providers and matplotlib.
    }
    await sleep(400);
  }
  throw new Error(`backend did not answer on ${BASE} in time`);
}

function terminate(backend) {
  if (!backend?.pid || backend.exitCode !== null || backend.signalCode !== null) return Promise.resolve();
  if (process.platform === "win32") {
    // Never use image-name taskkill, which could terminate an unrelated Python.
    return new Promise((done) => {
      const killer = spawn("taskkill", ["/pid", String(backend.pid), "/T", "/F"], { stdio: "ignore" });
      killer.once("error", () => done());
      killer.once("exit", () => done());
    });
  }
  backend.kill("SIGTERM");
  return Promise.race([
    new Promise((done) => backend.once("exit", done)),
    sleep(5_000).then(() => { if (backend.exitCode === null) backend.kill("SIGKILL"); }),
  ]);
}

function slug(value) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function errorLine(error) {
  return (error instanceof Error ? error.message : String(error)).split(/\r?\n/, 1)[0];
}

async function dismiss(page) {
  await page.keyboard.press("Escape").catch(() => {});
  await sleep(100);
}

function toolbarButton(page, label) {
  return page.getByRole("toolbar", { name: "Application bar" })
    .getByRole("button", { name: label, exact: true });
}

async function fitAll(page) {
  await page.getByRole("button", { name: "Fit All", exact: true }).click();
  await sleep(350);
}

async function dismissToasts(page) {
  const closes = page.locator(".notification-dismiss");
  for (let i = 0; i < 10 && await closes.count() > 0; i += 1) {
    const first = closes.first();
    if (!await first.isVisible().catch(() => false)) break;
    await first.click();
  }
}

function clipFor(rect, margin = 12) {
  if (!rect) throw new Error("Target has no on-screen bounding box");
  const x = Math.max(0, Math.floor(rect.x - margin));
  const y = Math.max(0, Math.floor(rect.y - margin));
  const right = Math.min(VIEWPORT.width, Math.ceil(rect.x + rect.width + margin));
  const bottom = Math.min(VIEWPORT.height, Math.ceil(rect.y + rect.height + margin));
  if (right <= x || bottom <= y) throw new Error("Target is outside the viewport");
  return { x, y, width: right - x, height: bottom - y };
}

// A pointer drag on the empty React Flow pane pans the whole scene (the app
// deliberately disables React Flow's stock panOnDrag and owns this gesture).
async function centerNode(page, node) {
  const canvas = await page.getByTestId("scene-canvas").boundingBox();
  if (!canvas) throw new Error("Canvas has no bounding box");
  const desired = { x: canvas.x + canvas.width / 2, y: canvas.y + canvas.height / 2 };
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const rect = await node.boundingBox();
    if (!rect) throw new Error("Node is no longer mounted in the viewport");
    const dx = desired.x - (rect.x + rect.width / 2);
    const dy = desired.y - (rect.y + rect.height / 2);
    if (Math.abs(dx) < 22 && Math.abs(dy) < 22) return;
    const points = [
      [canvas.x + 28, canvas.y + 28],
      [canvas.x + canvas.width - 28, canvas.y + 28],
      [canvas.x + 28, canvas.y + canvas.height - 28],
    ];
    let start = points[0];
    for (const point of points) {
      if (await page.evaluate(([x, y]) => document.elementFromPoint(x, y)?.matches(".react-flow__pane"), point)) {
        start = point;
        break;
      }
    }
    await page.mouse.move(start[0], start[1]);
    await page.mouse.down();
    await page.mouse.move(
      start[0] + Math.max(-250, Math.min(250, dx)),
      start[1] + Math.max(-200, Math.min(200, dy)),
      { steps: 4 },
    );
    await page.mouse.up();
    await sleep(80);
  }
  throw new Error("Could not center node on the canvas");
}

async function zoomTo100(page) {
  const scale = page.locator(".viewport-scale");
  const canvas = await page.getByTestId("scene-canvas").boundingBox();
  if (!canvas) throw new Error("Cannot zoom without a canvas");
  const readZoom = async () => Number.parseFloat((await scale.textContent()) ?? "0");
  // Wheel zoom is anchored at the canvas center, where the target was panned.
  await page.mouse.move(canvas.x + canvas.width / 2, canvas.y + canvas.height / 2);
  for (let i = 0; i < 30 && await readZoom() < 100; i += 1) {
    await page.mouse.wheel(0, -300);
    await sleep(110);
  }
  const zoom = await readZoom();
  if (zoom < 100) throw new Error(`Wheel zoom did not reach 100% (got ${zoom}%)`);
}

async function captureTheme(page, theme, capture, interrupted) {
  const shot = (surface, name, prepare = async () => ({})) => {
    if (interrupted()) throw new Error("Capture interrupted");
    return capture(page, theme, surface, name, prepare);
  };
  const onboarding = page.getByRole("dialog", { name: "Welcome to Graphlink" });
  await onboarding.waitFor({ state: "visible", timeout: 6_000 }).catch(() => {});
  if (!await onboarding.isVisible().catch(() => false)) {
    // The same isolated backend persists completion after the dark pass;
    // the Settings General page is the supported route back into the wizard.
    try {
      await toolbarButton(page, "Settings").click();
      const settings = page.getByRole("dialog", { name: "Settings" });
      await settings.locator(".settings-rail-button").filter({ hasText: /^General$/ }).click();
      await settings.getByRole("button", { name: "Show Onboarding" }).click();
      await onboarding.waitFor({ state: "visible" });
    } catch (error) {
      console.error(`${theme} / reopening onboarding: ${errorLine(error)}`);
    }
  }
  for (let step = 1; step <= 4; step += 1) {
    await shot(`Onboarding step ${step}`, `onboarding-${step}`, async () => {
      await onboarding.waitFor({ state: "visible" });
      if (step > 1) await onboarding.getByRole("button", { name: "Next", exact: true }).click();
      await onboarding.getByText(`Step ${step} of 4`).waitFor();
      return {};
    });
  }
  await onboarding.getByRole("button", { name: /^(Done|Finish)$/ }).click().catch(() => dismiss(page));
  await dismiss(page);

  await shot("Empty canvas", "empty-canvas");
  await shot("App bar", "app-bar", async () => ({ clip: { x: 0, y: 0, width: 1440, height: 56 } }));
  await shot("Composer", "composer", async () => {
    const composer = page.locator(".app-composer-layer");
    await composer.waitFor({ state: "visible" });
    return { clip: clipFor(await composer.boundingBox(), 0) };
  });

  const barSurfaces = [
    "Library", "View", "Plugins", "Pins", "Global Search", "Knowledge",
    "Builder", "Agent", "Organize", "Export methods",
  ];
  for (const name of barSurfaces) {
    await shot(`App bar: ${name}`, `appbar-${slug(name)}`, async () => {
      await dismiss(page);
      await toolbarButton(page, name).click();
      await sleep(180);
      return {};
    });
    await dismiss(page);
  }
  await shot("Help and diagnostics menu", "appbar-help-menu", async () => {
    await toolbarButton(page, "Help and diagnostics").click();
    await page.getByRole("dialog", { name: "Help and diagnostics" }).waitFor();
    return {};
  });
  await dismiss(page);
  for (const item of ["Help", "Diagnostics", "About"]) {
    await shot(`Help and diagnostics: ${item}`, `appbar-help-${slug(item)}`, async () => {
      await toolbarButton(page, "Help and diagnostics").click();
      await page.getByRole("dialog", { name: "Help and diagnostics" })
        .getByRole("button", { name: item, exact: true }).click();
      await page.getByRole("dialog", { name: item, exact: true }).waitFor();
      return {};
    });
    await dismiss(page);
  }

  const shortcuts = [
    { key: "Control+k", name: "Command palette", stem: "keyboard-palette" },
    { key: "Control+p", name: "Quick switcher", stem: "keyboard-switcher" },
    { key: "Control+f", name: "Canvas search", stem: "keyboard-search" },
  ];
  for (const shortcut of shortcuts) {
    await shot(shortcut.name, shortcut.stem, async () => {
      await dismiss(page);
      await page.getByTestId("scene-canvas").locator(".react-flow__pane").click({ position: { x: 20, y: 20 } });
      await page.keyboard.press(shortcut.key);
      if (shortcut.key === "Control+f") {
        await page.getByRole("textbox", { name: "Search the canvas" }).waitFor();
      } else {
        await page.getByRole("dialog", { name: shortcut.name }).waitFor();
      }
      return {};
    });
    if (shortcut.key === "Control+f") {
      await shot("Canvas search: index", "keyboard-search-index", async () => {
        await page.getByRole("textbox", { name: "Search the canvas" }).fill("index");
        return {};
      });
    }
    await dismiss(page);
  }

  for (const name of ["model", "reasoning"]) {
    await shot(`Composer ${name} popover`, `composer-${name}`, async () => {
      await page.locator(`[data-overlay-trigger="${name}"]`).click();
      await sleep(160);
      return {};
    });
    await dismiss(page);
  }

  await shot("Settings: open", "settings-open", async () => {
    await toolbarButton(page, "Settings").click();
    await page.getByRole("dialog", { name: "Settings" }).waitFor();
    return {};
  });
  const settings = page.getByRole("dialog", { name: "Settings" });
  const labels = await settings.locator(".settings-rail-button").allTextContents().catch(() => []);
  for (let index = 0; index < labels.length; index += 1) {
    const name = labels[index].trim();
    const stem = `settings-${slug(name)}`;
    await shot(`Settings: ${name}`, stem, async () => {
      if (!await settings.isVisible()) await toolbarButton(page, "Settings").click();
      await settings.locator(".settings-rail-button").nth(index).click();
      await settings.getByRole("region", { name, exact: true }).waitFor();
      await sleep(180);
      return {};
    });
    const pane = settings.locator(".settings-page");
    const overflow = await pane.evaluate((element) => element.scrollHeight > element.clientHeight + 2).catch(() => false);
    if (overflow) {
      await shot(`Settings: ${name} (bottom)`, `${stem}-bottom`, async () => {
        await pane.evaluate((element) => { element.scrollTop = element.scrollHeight; });
        await sleep(110);
        return {};
      });
    }
  }
  await dismiss(page);

  await shot("Seeded chat: Fit All", "seeded-canvas", async () => {
    await toolbarButton(page, "Library").click();
    const library = page.getByRole("dialog", { name: "Chat Library" });
    await library.waitFor({ state: "visible" });
    await library.getByRole("button", { name: /^Open chat/ }).first().click();
    await library.waitFor({ state: "hidden" });
    await page.locator(".react-flow__node").first().waitFor();
    await dismissToasts(page);
    await fitAll(page);
    return {};
  });

  // The seeded graph can grow new kinds over time; don't hardcode its list.
  const kinds = await page.locator(".react-flow__node").evaluateAll((elements) => [...new Set(
    elements.flatMap((element) => [...element.classList]
      .filter((cls) => cls.startsWith("react-flow__node-"))
      .map((cls) => cls.slice("react-flow__node-".length))),
  )]).catch(() => []);
  for (const kind of kinds) {
    await shot(`Node: ${kind} at 100%`, `node-${slug(kind)}`, async () => {
      await fitAll(page);
      const node = page.locator(`.react-flow__node-${kind}`).first();
      await node.waitFor({ state: "visible" });
      await centerNode(page, node);
      await zoomTo100(page);
      await centerNode(page, node);
      return { clip: clipFor(await node.boundingBox()) };
    });
  }
  await shot("Chat node context menu", "node-chat-context-menu", async () => {
    await fitAll(page);
    const node = page.locator(".react-flow__node-chat").first();
    await centerNode(page, node);
    await zoomTo100(page);
    await centerNode(page, node);
    await node.locator(".node-card-header").click({ button: "right" });
    await page.getByRole("menu", { name: "Chat message actions" }).waitFor();
    return {};
  });
  await dismiss(page);
  await shot("Composer with selected chat node", "composer-node-selected", async () => {
    await fitAll(page);
    const node = page.locator(".react-flow__node-chat").first();
    await node.waitFor({ state: "visible" });
    await node.locator(".node-card-header").click();
    await page.locator(".app-composer-layer").waitFor();
    return {};
  });

  await shot("Gallery", "gallery", async () => {
    await page.goto(`${BASE}/#gallery`);
    await page.getByRole("heading", { name: "UI gallery" }).waitFor();
    // The app's stored theme may have been explicit; gallery offers a direct
    // visual-review switch, independent of backend settings.
    await page.getByRole("button", { name: theme === "dark" ? "Dark" : "Light" }).click();
    return { fullPage: true };
  });
}

async function main() {
  let out;
  try {
    out = outputDirectory(process.argv[2]);
  } catch (error) {
    console.error(errorLine(error));
    process.exitCode = 2;
    return;
  }
  const manifest = [];
  const capture = async (page, theme, surface, name, prepare) => {
    const file = `${theme}-${name}.png`;
    try {
      const options = await prepare();
      await page.screenshot({ path: join(out, file), timeout: 30_000, animations: "disabled", ...options });
      manifest.push({ file, surface, theme, status: "ok", ok: true });
    } catch (error) {
      const line = errorLine(error);
      console.error(`${theme} / ${surface}: ${line}`);
      manifest.push({ file, surface, theme, status: "failed", ok: false, error: line });
    }
  };

  const dataDir = mkdtempSync(join(tmpdir(), "graphlink-ui-capture-"));
  const env = { ...process.env, HOME: dataDir, USERPROFILE: dataDir };
  let backend;
  let browser;
  let backendStarted = false;
  let spawnError = null;
  let interrupted = false;
  let stopping;
  const stop = () => backend ? (stopping ??= terminate(backend)) : Promise.resolve();
  const onSigint = () => {
    interrupted = true;
    void browser?.close().catch(() => {});
    void stop();
  };
  process.on("SIGINT", onSigint);

  try {
    console.log("seeding the demo graph...");
    await run("python", ["tools/seed_demo_graph.py", join(dataDir, "chats.db")], env);
    if (interrupted) throw new Error("Capture interrupted");
    console.log("starting the isolated backend...");
    backend = spawn("python", ["tools/capture_backend.py", String(PORT), dataDir], {
      cwd: REPO_ROOT, env, stdio: "inherit",
    });
    backend.once("error", (error) => { spawnError = error; });
    await waitForHealth(backend, () => spawnError);
    backendStarted = true;

    browser = await chromium.launch();
    for (const theme of THEMES) {
      if (interrupted) break;
      const page = await browser.newPage({
        viewport: VIEWPORT, deviceScaleFactor: 1, colorScheme: theme, reducedMotion: "reduce",
        acceptDownloads: true,
      });
      page.setDefaultTimeout(8_000);
      try {
        await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
        await page.goto(BASE);
        await page.locator('.app-shell[data-connection-status="open"]').waitFor({ timeout: 30_000 });
        await captureTheme(page, theme, capture, () => interrupted);
      } catch (error) {
        const file = `${theme}-boot.png`;
        const line = errorLine(error);
        console.error(`${theme} / app boot: ${line}`);
        manifest.push({ file, surface: "App boot", theme, status: "failed", ok: false, error: line });
      } finally {
        await page.close().catch(() => {});
      }
    }
  } catch (error) {
    console.error(errorLine(error));
  } finally {
    process.off("SIGINT", onSigint);
    await browser?.close().catch(() => {});
    await stop();
    rmSync(dataDir, { recursive: true, force: true });
    writeFileSync(join(out, "manifest.json"), JSON.stringify({ viewport: VIEWPORT, deviceScaleFactor: 1, captures: manifest }, null, 2) + "\n");
    const ok = manifest.filter((item) => item.ok).length;
    console.log(`${ok} ok, ${manifest.length - ok} failed`);
    // A failed *capture* should never suppress the remaining surfaces or
    // block visual review. A backend that never started is a real run failure.
    if (!backendStarted) process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(errorLine(error));
  process.exitCode = 1;
});
