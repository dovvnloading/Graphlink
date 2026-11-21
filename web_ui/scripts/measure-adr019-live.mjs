/**
 * ADR-019 live budgets, report-only. Real built SPA + uvicorn + Chromium.
 *
 * Usage from repository root:
 *   node web_ui/scripts/measure-adr019-live.mjs --output perf-results/adr019-live.json
 * The companion Python module only seeds isolated SQLite rows, launches the
 * real app (with deterministic offline *model* responses), and reads OS RSS.
 */
import { spawn, execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readdirSync, rmSync, createWriteStream } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, dirname, join } from "node:path";
import { performance } from "node:perf_hooks";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";
import { setTimeout as sleep } from "node:timers/promises";
import { chromium } from "@playwright/test";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const PYTHON = process.env.GRAPHLINK_PERF_PYTHON || (process.platform === "win32" ? "python" : "python");
const MODULE = "backend.tests.perf.live_backend";
const MIB = 1024 * 1024;
const WORKLOADS = [100, 500];

function round(value) {
  return Math.round(value * 100) / 100;
}

function percentile(sorted, fraction) {
  return sorted[Math.max(0, Math.ceil(sorted.length * fraction) - 1)];
}

function frameStats(samples, name) {
  if (!Array.isArray(samples) || samples.length < 8) {
    throw new Error(name + ": too few animation frames (" + (samples?.length ?? 0) + ")");
  }
  const values = samples.filter((value) => Number.isFinite(value) && value > 0).sort((a, b) => a - b);
  if (values.length < 8) throw new Error(name + ": insufficient valid frame intervals");
  return {
    [name + "_frames"]: values.length,
    [name + "_frame_p50_ms"]: round(percentile(values, 0.50)),
    [name + "_frame_p95_ms"]: round(percentile(values, 0.95)),
    [name + "_frame_p99_ms"]: round(percentile(values, 0.99)),
    [name + "_frame_max_ms"]: round(values[values.length - 1]),
    [name + "_frames_over_33ms"]: values.filter((value) => value > 33.33).length,
  };
}

function python(args, env) {
  return execFileSync(PYTHON, ["-m", MODULE, ...args], {
    cwd: ROOT,
    env,
    encoding: "utf8",
    timeout: 180_000,
    maxBuffer: 16 * MIB,
    windowsHide: true,
  });
}

function isolatedEnvironment(profile) {
  const home = join(profile, "home");
  const appData = join(profile, "AppData", "Roaming");
  const localAppData = join(profile, "AppData", "Local");
  const cache = join(profile, "cache");
  for (const directory of [home, appData, localAppData, cache]) {
    mkdirSync(directory, { recursive: true });
  }
  return {
    ...process.env,
    HOME: home,
    USERPROFILE: home,
    APPDATA: appData,
    LOCALAPPDATA: localAppData,
    XDG_CONFIG_HOME: join(profile, "config"),
    XDG_CACHE_HOME: cache,
  };
}

async function unusedLoopbackPort() {
  const server = createServer();
  await new Promise((ok, fail) => {
    server.once("error", fail);
    server.listen(0, "127.0.0.1", ok);
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("No TCP port assigned");
  await new Promise((ok) => server.close(ok));
  return address.port;
}

async function waitForBackend(url, processHandle) {
  const deadline = performance.now() + 180_000;
  while (performance.now() < deadline) {
    if (processHandle.exitCode !== null || processHandle.signalCode !== null) {
      throw new Error("Backend exited before its health endpoint responded");
    }
    try {
      const response = await fetch(url + "/api/health", { signal: AbortSignal.timeout(2500) });
      if (response.ok && (await response.json()).status === "ok") return;
    } catch {
      // Cold import of the real provider/backend graph can take tens of seconds.
    }
    await sleep(250);
  }
  throw new Error("Timed out waiting for the real backend at " + url);
}

async function twoPaints(page) {
  await page.evaluate(() => new Promise((ok) => {
    globalThis.requestAnimationFrame(() => globalThis.requestAnimationFrame(ok));
  }));
}

async function reachInteractiveCanvas(page, url) {
  await page.goto(url + "/", { waitUntil: "domcontentloaded", timeout: 60_000 });
  await page.locator('.app-shell[data-connection-status="open"]').waitFor({
    state: "attached", timeout: 60_000,
  });
  await page.locator('[data-testid="scene-canvas"] .react-flow__pane').waitFor({
    state: "visible", timeout: 60_000,
  });
  await twoPaints(page);
  // A fresh, isolated settings profile opens onboarding. Its Escape path is
  // the same one used in the existing E2E helper, not a test-only hook.
  const onboarding = page.getByRole("dialog", { name: "Welcome to Graphlink" });
  try {
    await onboarding.waitFor({ state: "visible", timeout: 2_000 });
    await page.keyboard.press("Escape");
    await onboarding.waitFor({ state: "hidden", timeout: 10_000 });
  } catch {
    if (await onboarding.isVisible()) throw new Error("Could not close onboarding");
  }
  await twoPaints(page);
}

async function waitForGraph(page, count) {
  const overview = page.getByRole("group", { name: "Canvas overview, " + count + " nodes" });
  await overview.waitFor({ state: "visible", timeout: 90_000 });
  // The visible node views are code-split. Include their mounting work,
  // rather than stopping the timer at the first WS state message.
  await page.locator(".scene-node-loading").first().waitFor({ state: "hidden", timeout: 90_000 });
  await twoPaints(page);
}

async function openSavedChat(page, title, count) {
  await page.locator('[data-overlay-trigger="library"]').click();
  const library = page.getByRole("dialog", { name: "Chat Library" });
  await library.waitFor({ state: "visible", timeout: 30_000 });
  const row = library.locator(".library-row").filter({ hasText: title });
  await row.waitFor({ state: "visible", timeout: 30_000 });
  const begin = performance.now();
  await row.getByRole("button", { name: /^Open chat/ }).click();
  await library.waitFor({ state: "hidden", timeout: 90_000 });
  await waitForGraph(page, count);
  return round(performance.now() - begin);
}

async function beginFrameSampling(page) {
  await page.evaluate(() => {
    const intervals = [];
    let previous = null;
    let active = true;
    function tick(timestamp) {
      if (!active) return;
      if (previous !== null) intervals.push(timestamp - previous);
      previous = timestamp;
      globalThis.requestAnimationFrame(tick);
    }
    globalThis.__adr019StopFrames = () => {
      active = false;
      return intervals;
    };
    globalThis.requestAnimationFrame(tick);
  });
}

async function endFrameSampling(page) {
  return page.evaluate(() => {
    const stop = globalThis.__adr019StopFrames;
    if (!stop) throw new Error("frame sampler not installed");
    delete globalThis.__adr019StopFrames;
    return stop();
  });
}

async function panAndZoom(page) {
  const pane = page.locator(".react-flow__pane");
  const box = await pane.boundingBox();
  if (!box) throw new Error("React Flow pane has no screen bounds");
  const startX = box.x + 25;
  const startY = box.y + 25;
  const before = await page.locator(".react-flow__viewport").getAttribute("transform");
  await beginFrameSampling(page);
  await page.mouse.move(startX, startY);
  await page.mouse.down();
  for (let i = 0; i < 26; i += 1) {
    await page.mouse.move(startX + (i + 1) * 10, startY + (i + 1) * 4);
    await sleep(12);
  }
  await page.mouse.up();
  await page.getByRole("button", { name: "Zoom Out" }).click();
  await page.getByRole("button", { name: "Zoom In" }).click();
  await sleep(150);
  const samples = await endFrameSampling(page);
  const after = await page.locator(".react-flow__viewport").getAttribute("transform");
  if (before === after) throw new Error("Pan/zoom did not alter the real canvas viewport");
  return frameStats(samples, "pan_zoom");
}

async function installIntentClient(page) {
  await page.evaluate(async () => {
    const socket = new globalThis.WebSocket("ws://" + globalThis.location.host + "/ws?session=default");
    const pending = new Map();
    let nextId = 0;
    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      const item = pending.get(message.id);
      if (!item) return;
      if (message.kind !== "result" && message.kind !== "error" && message.kind !== "state") return;
      pending.delete(message.id);
      if (message.kind === "error") item.reject(new Error(message.error));
      else item.resolve(message.kind === "result" ? message.value : message.payload);
    });
    socket.addEventListener("close", () => {
      for (const item of pending.values()) item.reject(new Error("Perf intent socket closed"));
      pending.clear();
    });
    await new Promise((ok, fail) => {
      socket.addEventListener("open", ok, { once: true });
      socket.addEventListener("error", () => fail(new Error("Perf intent WebSocket failed")), { once: true });
    });
    globalThis.__adr019IntentClient = {
      request: (fields) => new Promise((resolveRequest, rejectRequest) => {
        const id = ++nextId;
        pending.set(id, { resolve: resolveRequest, reject: rejectRequest });
        socket.send(JSON.stringify({ ...fields, id }));
      }),
      close: () => socket.close(),
    };
  });
}

async function intent(page, method, args) {
  return page.evaluate(({ method, args }) =>
    globalThis.__adr019IntentClient.request({
      kind: "intent", topic: "scene", intent: method, args,
    }), { method, args });
}

async function runRerunBatch(page, count) {
  // Use a second ordinary WebSocket only to read the scene's real node IDs
  // (restoration remints them) and invoke the same public intents as the UI.
  // The visible SPA owns the canvas, receives publishes and renders them.
  await installIntentClient(page);
  const scene = await page.evaluate(() => globalThis.__adr019IntentClient.request({
    kind: "subscribe", topics: ["scene"],
  }));
  const marker = "ADR019_RERUN_ROOT_" + count;
  const root = scene.nodes.find((node) =>
    node.kind === "chat" && typeof node.content === "string" && node.content.includes(marker));
  if (!root || root.versionCount < 2) throw new Error("Edited factory root not restored");
  const scope = "all_branches_below";
  const preview = await intent(page, "previewChatRerun", [root.id, scope]);
  if (!preview.count || !preview.previewToken) throw new Error("Re-run preview contains no eligible replies");
  await beginFrameSampling(page);
  const begin = performance.now();
  const run = await intent(page, "startChatRerun", [
    root.id, scope, preview.previewToken, Boolean(preview.requiresConfirmation),
  ]);
  let status;
  const deadline = performance.now() + 120_000;
  do {
    if (performance.now() >= deadline) throw new Error("Re-run batch timed out");
    await sleep(75);
    status = await intent(page, "getChatRerunStatus", [run.jobId]);
  } while (status.status === "running");
  await twoPaints(page);
  const durationMs = round(performance.now() - begin);
  const samples = await endFrameSampling(page);
  globalThis.console.log("ADR-019", count, "re-run result", status.status, status.completed, "of", preview.count);
  if (status.status !== "completed" || status.completed !== preview.count) {
    throw new Error("Real re-run failed: " + JSON.stringify(status));
  }
  await page.evaluate(() => globalThis.__adr019IntentClient.close());
  return {
    ...frameStats(samples, "rerun"),
    rerun_batch_ms: durationMs,
    rerun_replies: status.completed,
  };
}

async function memorySnapshot(browser, context, page, backendPid, env) {
  const browserSession = await browser.newBrowserCDPSession();
  const processInfo = await browserSession.send("SystemInfo.getProcessInfo");
  await browserSession.detach();
  const rendererPids = [...new Set(processInfo.processInfo
    .filter((item) => item.type === "renderer").map((item) => item.id))];
  if (!rendererPids.length) throw new Error("Chromium returned no renderer PIDs");
  const rss = JSON.parse(python(["rss", "--pids", String(backendPid), ...rendererPids.map(String)], env));
  const session = await context.newCDPSession(page);
  await session.send("Performance.enable");
  const metrics = await session.send("Performance.getMetrics");
  await session.detach();
  const used = metrics.metrics.find((item) => item.name === "JSHeapUsedSize")?.value;
  if (!Number.isFinite(used)) throw new Error("Renderer JSHeapUsedSize not available from CDP");
  const rendererBytes = rendererPids.reduce((sum, pid) => {
    if (!Number.isFinite(rss[String(pid)])) throw new Error("Missing renderer RSS " + pid);
    return sum + rss[String(pid)];
  }, 0);
  return {
    backend_rss_mib: round(rss[String(backendPid)] / MIB),
    renderer_rss_mib: round(rendererBytes / MIB),
    renderer_js_heap_mib: round(used / MIB),
    renderer_processes: rendererPids.length,
  };
}

async function measureOne(nodeCount, keepProfile) {
  const profile = mkdtempSync(join(tmpdir(), "graphlink-adr019-"));
  const env = isolatedEnvironment(profile);
  let backend = null;
  let browser = null;
  try {
    const seeded = python(["seed", "--profile", profile, "--nodes", String(nodeCount)], env);
    const fixture = JSON.parse(seeded.trim().split(/\r?\n/).at(-1));
    const port = await unusedLoopbackPort();
    const url = "http://127.0.0.1:" + port;
    const stdout = createWriteStream(join(profile, "backend.stdout.log"));
    const stderr = createWriteStream(join(profile, "backend.stderr.log"));
    const started = performance.now();
    backend = spawn(PYTHON, ["-m", MODULE, "serve", "--profile", profile, "--port", String(port)], {
      cwd: ROOT, env, stdio: ["ignore", stdout, stderr], windowsHide: true,
    });
    stdout.end();
    stderr.end();
    await waitForBackend(url, backend);
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      deviceScaleFactor: 1,
      reducedMotion: "reduce",
    });
    const page = await context.newPage();
    await reachInteractiveCanvas(page, url);
    const coldStartMs = round(performance.now() - started);

    const openMs = await openSavedChat(page, "ADR019 " + nodeCount + " nodes load", nodeCount);
    const memoryLoaded = await memorySnapshot(browser, context, page, backend.pid, env);
    const panMetrics = await panAndZoom(page);

    await openSavedChat(page, "ADR019 " + nodeCount + " nodes rerun", nodeCount);
    const rerunMetrics = await runRerunBatch(page, nodeCount);
    const memoryRerun = await memorySnapshot(browser, context, page, backend.pid, env);
    return {
      name: fixture.name,
      node_count: fixture.node_count,
      edge_count: fixture.edge_count,
      cold_start_to_interactive_ms: coldStartMs,
      saved_chat_open_ms: openMs,
      ...panMetrics,
      ...rerunMetrics,
      backend_rss_mib: memoryLoaded.backend_rss_mib,
      renderer_rss_mib: memoryLoaded.renderer_rss_mib,
      renderer_js_heap_mib: memoryLoaded.renderer_js_heap_mib,
      renderer_processes: memoryLoaded.renderer_processes,
      post_rerun_backend_rss_mib: memoryRerun.backend_rss_mib,
      post_rerun_renderer_rss_mib: memoryRerun.renderer_rss_mib,
      post_rerun_renderer_js_heap_mib: memoryRerun.renderer_js_heap_mib,
    };
  } finally {
    if (browser) await browser.close();
    if (backend) {
      backend.kill("SIGTERM");
      // Windows cannot unlink the SQLite profile while uvicorn still owns it.
      for (let i = 0; i < 50 && backend.exitCode === null && backend.signalCode === null; i += 1) {
        await sleep(100);
      }
    }
    if (keepProfile) {
      globalThis.console.log("Kept isolated profile for diagnostics:", profile);
    } else {
      for (let i = 0; i < 10; i += 1) {
        try {
          rmSync(profile, { recursive: true, force: true });
          break;
        } catch (error) {
          if (i === 9) globalThis.console.warn("Could not remove isolated profile", profile, error);
          else await sleep(300);
        }
      }
    }
  }
}

async function main() {
  const args = process.argv.slice(2);
  const outputIndex = args.indexOf("--output");
  if (outputIndex >= 0 && !args[outputIndex + 1]) throw new Error("--output needs a file path");
  const output = resolve(ROOT, outputIndex >= 0 ? args[outputIndex + 1] : "perf-results/adr019-live.json");
  const keepProfile = args.includes("--keep-profile");
  const spa = join(ROOT, "web_ui", "dist", "app");
  if (!existsSync(join(spa, "index.html")) ||
      !existsSync(join(spa, "assets")) || readdirSync(join(spa, "assets")).length === 0) {
    throw new Error("Built SPA missing: run npm --prefix web_ui run build first");
  }
  const report = {
    _comment: "ADR-019 live budgets; report-only. NOT a baseline.json regression gate. Browser animation-frame intervals, real SQLite chat loads, offline scripted model completions.",
    measured_at: new Date().toISOString(),
    measured_on: process.platform + " " + process.arch + ", Node " + process.version +
      ", Playwright Chromium; 1440x900, deviceScaleFactor=1, reducedMotion=reduce",
    workloads: [],
  };
  try {
    for (const count of WORKLOADS) {
      globalThis.console.log("Measuring live ADR-019 workload", count, "nodes");
      report.workloads.push(await measureOne(count, keepProfile));
    }
  } catch (error) {
    report.error = error instanceof Error ? error.stack || error.message : String(error);
    throw error;
  } finally {
    const { writeFileSync } = await import("node:fs");
    mkdirSync(dirname(output), { recursive: true });
    writeFileSync(output, JSON.stringify(report, null, 2) + "\n", "utf8");
    globalThis.console.log("ADR-019 live report:", output);
    globalThis.console.log(JSON.stringify(report, null, 2));
  }
}

main().catch((error) => {
  globalThis.console.error(error);
  process.exitCode = 1;
});
