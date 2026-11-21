/**
 * Records the README's UI-flow GIFs.
 *
 *     npm --prefix web_ui run build     # the app serves dist/app, not src
 *     npm --prefix web_ui run capture:gifs
 *
 * Same isolation and content as capture-screenshots.mjs: a fresh temp
 * profile, the hand-built incident graph from tools/seed_demo_graph.py, and
 * the real backend through tools/capture_backend.py. Nothing touches
 * ~/.graphlink and no model is needed.
 *
 * FRAMES. Playwright's own video recorder is lossy VP8, which smears small
 * UI text. This records lossless PNG frames with the Chromium screencast
 * instead, keeps each frame's real timestamp, and lets ffmpeg 3.0 or newer
 * (on PATH, or set FFMPEG to its path) build one palette-optimised GIF per
 * flow under assets/gifs/.
 *
 * POINTER. Headless frames have no mouse pointer, so the page gets a small
 * cursor overlay that follows real mouse events, and every action glides the
 * mouse to its target before clicking. The flows are paced for a reader,
 * not for speed.
 *
 * PYTHON. Set GRAPHLINK_PYTHON (for example "py -3.12") when the default
 * `python` on PATH is not the project's interpreter.
 *
 * BROWSER. Playwright's bundled Chromium by default (`npx playwright install
 * chromium`). Set GRAPHLINK_CAPTURE_CHANNEL=msedge or chrome to drive an
 * installed browser instead; any Chromium build has the screencast API.
 *
 * SUBSET. Set GRAPHLINK_CAPTURE_ONLY to a comma-separated list of GIF names
 * (canvas-navigation, branch-edit-rerun, table-chart-report, graph-runner,
 * scientific-workbench, biology-workbench) to re-record only those.
 */

import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const OUT_DIR = join(REPO_ROOT, "assets", "gifs");
const PORT = 8802;
const BASE = `http://127.0.0.1:${PORT}`;
const VIEWPORT = { width: 1280, height: 800 };
const GIF_FPS = 15;
const FFMPEG = process.env.FFMPEG ?? "ffmpeg";
const PYTHON = (process.env.GRAPHLINK_PYTHON ?? "python").split(" ").filter(Boolean);
const ONLY = new Set((process.env.GRAPHLINK_CAPTURE_ONLY ?? "").split(",").map((name) => name.trim()).filter(Boolean));
const wanted = (name) => ONLY.size === 0 || ONLY.has(name);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function run(cmd, args, opts = {}) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(cmd, args, { cwd: REPO_ROOT, stdio: "inherit", ...opts });
    child.on("error", reject);
    child.on("exit", (code) =>
      code === 0 ? resolvePromise() : reject(new Error(`${cmd} exited ${code}`)),
    );
  });
}

async function waitForHealth(timeoutMs = 90_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      if ((await fetch(`${BASE}/api/health`)).ok) return;
    } catch {
      // Not listening yet.
    }
    await sleep(400);
  }
  throw new Error(`backend did not answer on ${BASE} in time`);
}

// -- pointer ----------------------------------------------------------------

const CURSOR_SCRIPT = `
  addEventListener("DOMContentLoaded", () => {
    const dot = document.createElement("div");
    dot.id = "__capture_cursor";
    dot.innerHTML = '<svg width="22" height="22" viewBox="0 0 22 22"><path d="M3 2 L3 18 L7.5 13.8 L10.6 20.4 L13.4 19.1 L10.3 12.6 L16.4 12.4 Z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    Object.assign(dot.style, {
      position: "fixed", left: "0px", top: "0px", width: "22px", height: "22px",
      zIndex: "2147483647", pointerEvents: "none", transformOrigin: "3px 2px",
      transition: "transform 90ms ease-out", filter: "drop-shadow(0 1px 2px rgba(0,0,0,.45))",
    });
    document.body.appendChild(dot);
    const at = (e) => { dot.style.left = (e.clientX - 3) + "px"; dot.style.top = (e.clientY - 2) + "px"; };
    addEventListener("mousemove", at, { capture: true, passive: true });
    addEventListener("mousedown", (e) => { at(e); dot.style.transform = "scale(0.82)"; }, { capture: true });
    addEventListener("mouseup", () => { dot.style.transform = "scale(1)"; }, { capture: true });
  });
`;

let pointer = { x: VIEWPORT.width / 2, y: VIEWPORT.height / 2 };

async function glide(page, x, y, ms = 520) {
  const steps = Math.max(10, Math.round(ms / 22));
  const from = pointer;
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    const ease = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2;
    await page.mouse.move(from.x + (x - from.x) * ease, from.y + (y - from.y) * ease);
    await sleep(ms / steps);
  }
  pointer = { x, y };
}

async function centreOf(locator) {
  await locator.waitFor({ state: "visible" });
  const box = await locator.boundingBox();
  if (!box) throw new Error("target has no box");
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

async function click(page, locator, pause = 450) {
  const { x, y } = await centreOf(locator);
  // A target under the composer or clipped by a scrolling card would take the
  // click somewhere else and record a silent no-op. Fail instead.
  const hit = await locator.evaluate((el, [px, py]) => {
    const top = globalThis.document.elementFromPoint(px, py);
    return !!top && (el === top || el.contains(top));
  }, [x, y]);
  if (!hit) throw new Error(`click target is covered at ${Math.round(x)},${Math.round(y)}`);
  await glide(page, x, y);
  await sleep(140);
  await page.mouse.down();
  await sleep(80);
  await page.mouse.up();
  await sleep(pause);
}

async function choose(page, locator, value, pause = 500) {
  const { x, y } = await centreOf(locator);
  await glide(page, x, y);
  await sleep(200);
  await locator.selectOption(value);
  await sleep(pause);
}

async function type(page, locator, text, pause = 400) {
  await click(page, locator, 150);
  await locator.fill("");
  await locator.pressSequentially(text, { delay: 55 });
  await sleep(pause);
}

/** Drag on empty canvas to pan, so the motion itself is visible. */
async function pan(page, from, to, ms = 900) {
  await glide(page, from.x, from.y);
  await page.mouse.down();
  await glide(page, to.x, to.y, ms);
  await page.mouse.up();
  // A drag that starts near a card can leave its text selected; never record that.
  await page.evaluate(() => globalThis.getSelection()?.removeAllRanges());
  await sleep(300);
}

// -- recording --------------------------------------------------------------

async function record(page, name, flow) {
  const dir = mkdtempSync(join(tmpdir(), `graphlink-gif-${name}-`));
  const cdp = await page.context().newCDPSession(page);
  const frames = [];
  cdp.on("Page.screencastFrame", ({ data, metadata, sessionId }) => {
    const file = join(dir, `${String(frames.length).padStart(5, "0")}.png`);
    writeFileSync(file, Buffer.from(data, "base64"));
    frames.push({ file, t: metadata.timestamp });
    cdp.send("Page.screencastFrameAck", { sessionId }).catch(() => {});
  });
  await cdp.send("Page.startScreencast", {
    format: "png", maxWidth: VIEWPORT.width, maxHeight: VIEWPORT.height, everyNthFrame: 1,
  });
  // Nudge the pointer so the first frame arrives with the cursor drawn.
  await page.mouse.move(pointer.x + 1, pointer.y);
  await page.mouse.move(pointer.x, pointer.y);
  await sleep(500);
  try {
    await flow();
    await sleep(1600); // Hold the final state before the loop restarts.
  } finally {
    const end = Date.now() / 1000;
    await cdp.send("Page.stopScreencast");
    await cdp.detach();
    await encode(name, frames, end, dir);
    rmSync(dir, { recursive: true, force: true });
  }
}

async function encode(name, frames, end, dir) {
  if (frames.length < 2) throw new Error(`${name}: the screencast produced no frames`);
  // The concat demuxer replays each frame for its real on-screen duration.
  const lines = [];
  frames.forEach((frame, i) => {
    const next = i + 1 < frames.length ? frames[i + 1].t : end;
    lines.push(`file '${frame.file.replace(/\\/g, "/")}'`, `duration ${Math.max(0.001, next - frame.t).toFixed(3)}`);
  });
  lines.push(`file '${frames.at(-1).file.replace(/\\/g, "/")}'`);
  const list = join(dir, "frames.txt");
  writeFileSync(list, lines.join("\n"));
  const out = join(OUT_DIR, `${name}.gif`);
  // Two passes: a one-pass split/palettegen graph buffers every decoded
  // frame until the palette is ready, which runs out of memory on long flows.
  const palette = join(dir, "palette.png");
  const input = ["-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", list];
  await run(FFMPEG, [...input, "-vf", `fps=${GIF_FPS},palettegen=max_colors=256:stats_mode=diff`, palette]);
  await run(FFMPEG, [...input, "-i", palette, "-lavfi",
    `fps=${GIF_FPS}[v];[v][1:v]paletteuse=dither=none:diff_mode=rectangle`, "-loop", "0", out]);
  const seconds = (end - frames[0].t).toFixed(1);
  console.log(`captured ${name}.gif (${seconds} s, ${(statSync(out).size / 1e6).toFixed(1)} MB)`);
}

// -- app helpers ------------------------------------------------------------

const node = (page, text) => page.locator(".react-flow__node").filter({ hasText: text }).first();

async function fitAll(page, pause = 900) {
  await click(page, page.getByRole("button", { name: "Fit All" }), pause);
}

async function dismissToasts(page) {
  const close = page.locator(".notification-dismiss").first();
  if (await close.isVisible().catch(() => false)) await close.click();
}

async function openPlugin(page, search, row) {
  await click(page, page.getByRole("button", { name: "Plugins", exact: true }), 500);
  await type(page, page.getByRole("searchbox", { name: "Search plugins" }), search, 500);
  await click(page, page.locator(".plugin-picker-row").filter({ hasText: row }).first(), 900);
}

async function newCanvas(page) {
  // Clear any draft and branch target the previous flow left in the composer.
  const cancelBranch = page.getByRole("button", { name: "Cancel branching from this message" });
  if (await cancelBranch.isVisible().catch(() => false)) await cancelBranch.click();
  await page.getByRole("textbox", { name: "Message composer" }).fill("");
  await page.locator('[data-overlay-trigger="library"]').click();
  const library = page.getByRole("dialog", { name: "Chat Library" });
  // New Chat confirms before discarding the unsaved canvas.
  // On an already-empty canvas no confirm appears and this handler lingers
  // to the next call, so tolerate a dialog that another handler accepted.
  page.once("dialog", (dialog) => dialog.accept().catch(() => {}));
  // The empty-library state repeats New Chat; either one starts a canvas.
  await library.getByRole("button", { name: "New Chat", exact: true }).first().click();
  await library.waitFor({ state: "hidden" }).catch(() => page.keyboard.press("Escape"));
  await page.getByRole("button", { name: "Add a table" }).waitFor();
  await dismissToasts(page);
  await sleep(500);
}

/** Open the seeded incident graph fitted to the window, discarding any edits. */
async function openSeededGraph(page) {
  const cancelBranch = page.getByRole("button", { name: "Cancel branching from this message" });
  if (await cancelBranch.isVisible().catch(() => false)) await cancelBranch.click();
  await page.getByRole("textbox", { name: "Message composer" }).fill("");
  await page.locator('[data-overlay-trigger="library"]').click();
  const library = page.getByRole("dialog", { name: "Chat Library" });
  page.once("dialog", (dialog) => dialog.accept().catch(() => {}));
  await library.getByRole("button", { name: /^Open chat/ }).first().click();
  await library.waitFor({ state: "hidden" });
  await page.locator(".react-flow__node").first().waitFor();
  await dismissToasts(page);
  await page.getByRole("button", { name: "Fit All" }).click();
  await sleep(1200);
}

async function grantPacks(page, packs) {
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const settings = page.getByRole("dialog", { name: "Settings" });
  await settings.getByRole("button", { name: "Plugins", exact: true }).click();
  for (const pack of packs) {
    const box = settings.getByRole("checkbox", { name: `Granted: ${pack}` });
    // The checkbox is controlled: it flips once the backend confirms the grant.
    if (!(await box.isChecked())) await box.click();
    const deadline = Date.now() + 10_000;
    while (!(await box.isChecked())) {
      if (Date.now() > deadline) throw new Error(`could not grant ${pack}`);
      await sleep(150);
    }
  }
  await page.keyboard.press("Escape");
  await settings.waitFor({ state: "hidden" });
}

// -- flows ------------------------------------------------------------------

/** The seeded incident graph: overview, pan, jump to a reply, branch from it. */
async function canvasFlow(page) {
  await sleep(700);
  await pan(page, { x: 640, y: 170 }, { x: 520, y: 260 });
  await page.keyboard.press("Control+f");
  const search = page.getByRole("textbox", { name: "Search the canvas" });
  await type(page, search, "Start at the database", 300);
  await search.press("Enter");
  await sleep(1300);
  await search.fill("");
  await search.press("Escape");
  const reply = node(page, "Start at the database, because");
  // Hovering the card reveals its actions; Branch from here targets the composer.
  const { x, y } = await centreOf(reply.locator(".node-card-header").first());
  await glide(page, x, y);
  await sleep(500);
  await click(page, reply.getByRole("button", { name: "Branch from here" }), 900);
  await type(page, page.getByRole("textbox", { name: "Message composer" }),
    "How would I test whether a database query regressed?", 1800);
}

/** Edit the root question in place, flip between its versions, then preview
 * Re-run from here. The preview sends nothing to a model, so none is needed. */
async function rerunFlow(page) {
  await sleep(600);
  await page.keyboard.press("Control+f");
  const search = page.getByRole("textbox", { name: "Search the canvas" });
  await type(page, search, "jumped from 180ms", 300);
  await search.press("Enter");
  await sleep(1300);
  await search.fill("");
  await search.press("Escape");
  const question = node(page, "jumped from 180ms");
  const { x, y } = await centreOf(question.locator(".node-card-header").first());
  await glide(page, x, y);
  await sleep(400);
  await click(page, question.getByRole("button", { name: "Node actions" }), 600);
  await click(page, page.getByRole("menuitem", { name: "Edit", exact: true }), 600);
  const editor = question.getByRole("textbox", { name: "Edit message text" });
  await editor.press("Control+End");
  await editor.pressSequentially(" The deploy also rewrote the events repository queries.", { delay: 45 });
  await sleep(500);
  await click(page, question.getByRole("button", { name: "Save changes" }), 1200);
  await click(page, question.getByRole("button", { name: "Previous version" }), 1100);
  await click(page, question.getByRole("button", { name: "Next version" }), 1000);
  await click(page, question.getByRole("button", { name: "Re-run from here" }), 1600);
  const dialog = page.getByRole("dialog", { name: "Re-run from here" });
  await click(page, dialog.getByRole("radio", { name: "All branches below" }), 2200);
  const skipped = dialog.getByRole("region", { name: "Skipped nodes" });
  if (await skipped.isVisible().catch(() => false)) {
    await skipped.scrollIntoViewIfNeeded();
    await sleep(1800);
  }
}

/** A table drives a chart and a report; editing the table marks both stale. */
async function tableFlow(page) {
  await click(page, page.getByRole("button", { name: "Add a table" }), 900);
  // Anchor on the card itself: its "Summarize by group" controls hide while editing.
  const table = page.locator(".react-flow__node")
    .filter({ has: page.getByRole("region", { name: "Data table" }) }).first();
  // A new node spawns with its corner at the viewport centre; pull it up and
  // left so the chart and report it creates have room on screen.
  const box = await table.boundingBox();
  const empty = { x: box.x + box.width + 60, y: Math.max(80, box.y - 120) };
  await pan(page, empty, { x: empty.x - (box.x - 160), y: empty.y - (box.y - 110) }, 1000);
  await choose(page, table.getByLabel("Group by"), "Team");
  await choose(page, table.getByLabel("Sum column"), "Hours");
  await click(page, table.getByRole("button", { name: "Create chart" }), 900);
  await click(page, table.getByRole("button", { name: "Create report" }), 900);
  await fitAll(page, 1200);
  await click(page, table.getByRole("button", { name: "Edit table" }), 500);
  await type(page, table.getByRole("textbox", { name: "Hours, row 1" }), "7", 400);
  await click(page, table.getByRole("button", { name: "Save table" }), 1400);
}

/** Graph Runner previews the dependency order, then reruns the stale outputs. */
async function runnerFlow(page) {
  await openPlugin(page, "graph runner", "Graph Runner");
  const dialog = page.getByRole("dialog", { name: "Graph Runner" });
  await choose(page, dialog.getByLabel("Run scope"), "all");
  await click(page, dialog.getByRole("button", { name: "Preview run" }), 1600);
  await click(page, dialog.getByRole("button", { name: "Run graph" }), 2200);
  await page.keyboard.press("Escape");
  await sleep(500);
  await fitAll(page, 1400);
}

/** Scroll a Workbench card's own body until `target` is fully inside it. */
async function revealInCard(page, card, target) {
  const body = card.locator(".science-body");
  await glide(page, ...Object.values(await centreOf(body)));
  for (let i = 0; i < 24; i++) {
    const [area, box] = await Promise.all([body.boundingBox(), target.boundingBox()]);
    if (box && box.y >= area.y + 4 && box.y + box.height <= area.y + area.height - 6) break;
    await page.mouse.wheel(0, box && box.y < area.y ? -200 : 200);
    await sleep(90);
  }
  await sleep(300);
}

/** Open a Workbench pack from the picker and pull its new card into view. */
async function openWorkbench(page, category, pack) {
  await click(page, page.getByRole("button", { name: "Plugins", exact: true }), 500);
  const picker = page.locator(".plugin-picker-shell");
  await click(page, picker.getByRole("button", { name: category }), 700);
  await click(page, picker.locator(".plugin-picker-row").filter({ hasText: pack }), 1100);
  const card = node(page, pack);
  // The card spawns with its corner at the viewport centre; pull it up into view.
  const b = await card.boundingBox();
  const grab = { x: b.x - 220, y: Math.min(b.y + 280, VIEWPORT.height - 120) };
  await pan(page, grab, { x: grab.x - (b.x - 360), y: grab.y - (b.y - 70) }, 1000);
  return card;
}

/** A Chemistry node converts a transmission scan, then charts it. */
async function scienceFlow(page) {
  const card = await openWorkbench(page, "Scientific Workbench", "Chemistry");
  await choose(page, card.getByRole("combobox", { name: "Operation", exact: true }), "transmission_spectrum", 700);
  await click(page, card.getByRole("button", { name: "Load example" }), 600);
  await click(page, card.getByRole("button", { name: "Run calculation" }), 1300);
  const latest = card.getByRole("region", { name: "Latest result" });
  await latest.waitFor();
  // Results are compact: the chart action sits under Actions & exports.
  const actions = latest.getByText("Actions & exports", { exact: true });
  await revealInCard(page, card, actions);
  await click(page, actions, 700);
  const chart = card.getByRole("button", { name: "Chart this" });
  await revealInCard(page, card, chart);
  await click(page, chart, 1400);
  await fitAll(page, 1400);
}

/** A Plate Assays node fits an ELISA 4PL standard curve to labelled
 * synthetic standards, reads two diluted samples, then discusses the
 * verified result in chat (workflow 1 of docs/BIOLOGY_WORKBENCH.md). */
async function biologyFlow(page) {
  const card = await openWorkbench(page, "Biology Workbench", "Plate Assays");
  await choose(page, card.getByRole("combobox", { name: "Operation", exact: true }), "standard_curve", 700);
  await choose(page, card.getByRole("combobox", { name: "Calibration model", exact: true }), "4pl", 600);
  const fields = [
    [/^Known standard concentrations/, "[15.6, 31.25, 62.5, 125, 250, 500, 1000]"],
    [/^Corresponding standard signals/, "[0.2449, 0.4566, 0.8186, 1.305, 1.7821, 2.1256, 2.3216]"],
    [/^Unknown sample signals/, "[0.62, 1.48]"],
    [/^Reciprocal sample dilution factors/, "[2, 2]"],
  ];
  for (const [name, value] of fields) {
    const box = card.getByRole("textbox", { name });
    await revealInCard(page, card, box);
    await click(page, box, 150);
    await box.fill(value);
    await sleep(350);
  }
  for (const [name, value] of [["Concentration unit label", "pg/mL"], ["Signal unit label", "OD450"]]) {
    const box = card.getByRole("textbox", { name, exact: true });
    await revealInCard(page, card, box);
    await type(page, box, value, 300);
  }
  const run = card.getByRole("button", { name: "Run calculation" });
  await revealInCard(page, card, run);
  await click(page, run, 1300);
  const latest = card.getByRole("region", { name: "Latest result" });
  await latest.waitFor();
  const tables = latest.getByText(/^Tables · \d+ rows$/);
  await revealInCard(page, card, tables);
  await click(page, tables, 700);
  const unknown = latest.getByRole("row").filter({ hasText: "Unknown" }).last();
  await revealInCard(page, card, unknown);
  await sleep(1200);
  const actions = latest.getByText("Actions & exports", { exact: true });
  await revealInCard(page, card, actions);
  await click(page, actions, 700);
  const discuss = card.getByRole("button", { name: "Discuss in chat" });
  await revealInCard(page, card, discuss);
  await click(page, discuss, 600);
  await page.getByRole("region", { name: /^Scientific result: / }).waitFor();
  await fitAll(page, 2000);
}

// -- main -------------------------------------------------------------------

async function main() {
  mkdirSync(OUT_DIR, { recursive: true });
  const dataDir = mkdtempSync(join(tmpdir(), "graphlink-gifs-"));
  console.log("seeding the demo graph...");
  await run(PYTHON[0], [...PYTHON.slice(1), "tools/seed_demo_graph.py", join(dataDir, "chats.db")]);

  console.log("starting the backend...");
  const backend = spawn(PYTHON[0], [...PYTHON.slice(1), "tools/capture_backend.py", String(PORT), dataDir],
    { cwd: REPO_ROOT, stdio: "inherit" });

  let browser;
  try {
    await waitForHealth();
    browser = await chromium.launch({ channel: process.env.GRAPHLINK_CAPTURE_CHANNEL || undefined });
    const context = await browser.newContext({
      viewport: VIEWPORT, deviceScaleFactor: 1, colorScheme: "dark",
    });
    await context.addInitScript(CURSOR_SCRIPT);
    const page = await context.newPage();
    page.setDefaultTimeout(15_000);
    await page.goto(BASE);
    await page.locator('.app-shell[data-connection-status="open"]').waitFor();

    const onboarding = page.getByRole("dialog", { name: "Welcome to Graphlink" });
    try {
      await onboarding.waitFor({ state: "visible", timeout: 6000 });
      await page.keyboard.press("Escape");
      await onboarding.waitFor({ state: "hidden" });
    } catch {
      // Never opened.
    }
    await grantPacks(page, ["Scientific Core", "Chemistry", "Plate Assays"]);

    if (wanted("canvas-navigation")) {
      await openSeededGraph(page);
      await record(page, "canvas-navigation", () => canvasFlow(page));
    }

    if (wanted("branch-edit-rerun")) {
      await openSeededGraph(page);
      await record(page, "branch-edit-rerun", () => rerunFlow(page));
      await page.keyboard.press("Escape");
    }

    // Graph Runner replays the graph the table flow builds, so they go together.
    if (wanted("table-chart-report") || wanted("graph-runner")) {
      await newCanvas(page);
      await record(page, "table-chart-report", () => tableFlow(page));
      await record(page, "graph-runner", () => runnerFlow(page));
    }

    if (wanted("scientific-workbench")) {
      await newCanvas(page);
      await record(page, "scientific-workbench", () => scienceFlow(page));
    }

    if (wanted("biology-workbench")) {
      await newCanvas(page);
      await record(page, "biology-workbench", () => biologyFlow(page));
    }
  } finally {
    if (browser) await browser.close();
    backend.kill();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
