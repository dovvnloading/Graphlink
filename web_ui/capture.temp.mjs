// Demo-video capture engine. Boots the staged app, choreographs real input
// (eased mouse drags, wheel-zoom camera moves, dialog interactions), and
// records every compositor frame via CDP Page.startScreencast with
// timestamps, for beat-aligned assembly later. Scratchpad only.
//
// Usage (from web_ui/, where playwright-core lives):
//   node capture.temp.mjs <shot> <outDir>
// Env: PORT (default 8797), BAR (seconds per bar, default 1.363636)
import { chromium } from "playwright-core";
import { mkdirSync, writeFileSync } from "fs";
import { join } from "path";

const SHOT = process.argv[2];
const OUT = process.argv[3];
const PORT = process.env.PORT || "8797";
const BAR = Number(process.env.BAR || "1.363636");

// ---------------------------------------------------------------- helpers
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const easeInOut = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const easeOut = (t) => 1 - Math.pow(1 - t, 3);

async function easedMove(page, x0, y0, x1, y1, ms, ease = easeInOut) {
  const steps = Math.max(8, Math.round(ms / 16));
  for (let i = 1; i <= steps; i++) {
    const t = ease(i / steps);
    await page.mouse.move(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t);
    await sleep(ms / steps);
  }
}

// Wheel-zoom burst at a fixed anchor; pacing eased so the zoom ramps.
async function wheelZoom(page, x, y, ticks, deltaPerTick, totalMs) {
  await page.mouse.move(x, y);
  for (let i = 0; i < ticks; i++) {
    await page.mouse.wheel(0, deltaPerTick);
    const t = i / ticks;
    const pace = 0.4 + 1.6 * Math.abs(t - 0.5); // slow-fast-slow spacing
    await sleep((totalMs / ticks) * pace);
  }
}

// Camera pan via the app's own pointer-drag pan (cursor overlay hidden).
async function panCamera(page, fromX, fromY, dx, dy, ms) {
  await page.evaluate(() => { window.__cursorHidden = true; });
  await page.mouse.move(fromX, fromY);
  await page.mouse.down();
  await easedMove(page, fromX, fromY, fromX + dx, fromY + dy, ms);
  await page.mouse.up();
  await page.evaluate(() => { window.__cursorHidden = false; });
}

async function nodeRect(page, selector) {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height, cx: r.x + r.width / 2, cy: r.y + r.height / 2 };
  }, selector);
}

// ---------------------------------------------------------------- setup
async function boot() {
  const browser = await chromium.launch({ headless: true, args: ["--force-device-scale-factor=1"] });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, colorScheme: "dark" });
  await page.goto(`http://127.0.0.1:${PORT}`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".app-shell[data-connection-status='open']", { timeout: 25000 });
  await page.evaluate(() => document.documentElement.setAttribute("data-theme", "dark"));
  try {
    const ob = page.getByRole("dialog", { name: "Welcome to Graphlink" });
    await ob.waitFor({ state: "visible", timeout: 4000 });
    await page.keyboard.press("Escape");
    await ob.waitFor({ state: "hidden" });
  } catch { /* not shown */ }
  const hint = page.getByRole("button", { name: "Load Sample Workspace" });
  if (await hint.count()) await hint.click();
  await page.waitForFunction(() => document.querySelectorAll(".react-flow__node").length >= 1, { timeout: 15000 });
  await page.waitForTimeout(1200);
  await injectCursor(page);
  return { browser, page };
}

// A software cursor that follows real pointer events, with a click pulse.
async function injectCursor(page) {
  await page.evaluate(() => {
    const c = document.createElement("div");
    c.id = "demo-cursor";
    c.innerHTML = `<svg width="26" height="30" viewBox="0 0 26 30">
      <path d="M2 2 L2 24 L8 19 L12 28 L16 26 L12 17 L20 16 Z"
        fill="#fff" stroke="#1a1a1a" stroke-width="1.6" stroke-linejoin="round"/></svg>`;
    Object.assign(c.style, {
      position: "fixed", left: "0", top: "0", zIndex: "999999",
      pointerEvents: "none", filter: "drop-shadow(0 2px 5px rgba(0,0,0,.55))",
      display: "none",
    });
    document.body.appendChild(c);
    window.__cursorHidden = true;
    document.addEventListener("mousemove", (e) => {
      c.style.display = window.__cursorHidden ? "none" : "block";
      c.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`;
    }, true);
    document.addEventListener("mousedown", (e) => {
      if (window.__cursorHidden) return;
      const p = document.createElement("div");
      Object.assign(p.style, {
        position: "fixed", left: e.clientX - 17 + "px", top: e.clientY - 17 + "px",
        width: "34px", height: "34px", borderRadius: "50%",
        border: "2px solid rgba(255,255,255,.85)", zIndex: "999998",
        pointerEvents: "none", animation: "demoPulse .45s ease-out forwards",
      });
      document.body.appendChild(p);
      setTimeout(() => p.remove(), 500);
    }, true);
    const st = document.createElement("style");
    st.textContent = "@keyframes demoPulse{from{transform:scale(.4);opacity:.9}to{transform:scale(1.5);opacity:0}}";
    document.head.appendChild(st);
  });
}

const showCursor = (page) => page.evaluate(() => { window.__cursorHidden = false; });
const hideCursor = (page) => page.evaluate(() => { window.__cursorHidden = true; });

// ---------------------------------------------------------------- recorder
async function record(page, fn) {
  mkdirSync(OUT, { recursive: true });
  const cdp = await page.context().newCDPSession(page);
  const meta = [];
  let n = 0;
  cdp.on("Page.screencastFrame", async (ev) => {
    n += 1;
    const name = `f_${String(n).padStart(6, "0")}.jpg`;
    writeFileSync(join(OUT, name), Buffer.from(ev.data, "base64"));
    meta.push({ f: name, t: ev.metadata.timestamp });
    try { await cdp.send("Page.screencastFrameAck", { sessionId: ev.sessionId }); } catch { /* ended */ }
  });
  await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, everyNthFrame: 1 });
  const t0 = Date.now() / 1000;
  await fn();
  await sleep(350); // trailing frames
  await cdp.send("Page.stopScreencast");
  await sleep(250);
  writeFileSync(join(OUT, "meta.json"), JSON.stringify({ t0, frames: meta }, null, 0));
  console.log(JSON.stringify({ shot: SHOT, frames: n, seconds: meta.length ? +(meta[meta.length - 1].t - meta[0].t).toFixed(2) : 0 }));
}


// App-styled caption chip: uppercase kicker (the plan node's BUILD-badge
// language) + one line of body text on an inset surface. Rendered in-page
// so typography/borders are literally the app's own, and animated with the
// same quick-ease the app uses. Fades out shortly before `totalMs`.
async function caption(page, kicker, text, totalMs) {
  await page.evaluate(([k, t, ms]) => {
    document.getElementById("demo-caption")?.remove();
    const el = document.createElement("div");
    el.id = "demo-caption";
    el.innerHTML =
      `<span style="font-size:11px;font-weight:700;text-transform:uppercase;` +
      `letter-spacing:.09em;color:#7fb0e0;border:1px solid rgba(127,176,224,.55);` +
      `padding:3px 8px;border-radius:4px;flex-shrink:0">${k}</span>` +
      `<span style="font-size:16px;font-weight:600;color:#f0f0f0;letter-spacing:.01em">${t}</span>`;
    Object.assign(el.style, {
      position: "fixed", left: "50%", top: "84px", transform: "translateX(-50%)",
      display: "flex", alignItems: "center", gap: "12px",
      padding: "11px 18px", background: "rgba(30,30,30,.93)",
      border: "1px solid #3a3a3a", borderRadius: "8px",
      boxShadow: "0 6px 24px rgba(0,0,0,.45)", zIndex: "999997",
      pointerEvents: "none", opacity: "0",
      fontFamily: '"Segoe UI", system-ui, sans-serif', whiteSpace: "nowrap",
    });
    document.body.appendChild(el);
    el.animate(
      [{ opacity: 0, transform: "translateX(-50%) translateY(-14px)" },
       { opacity: 1, transform: "translateX(-50%) translateY(0)" }],
      { duration: 450, easing: "cubic-bezier(.2,.8,.3,1)", fill: "forwards" });
    setTimeout(() => {
      el.animate([{ opacity: 1 }, { opacity: 0 }],
        { duration: 350, easing: "ease-in", fill: "forwards" });
      setTimeout(() => el.remove(), 400);
    }, Math.max(600, ms - 420));
  }, [kicker, text, totalMs]);
}

const fitAll = async (page) => { await page.getByRole("button", { name: "Fit All" }).click(); await sleep(900); };

// ---------------------------------------------------------------- shots
const shots = {
  // Diagnostic only: verifies every interaction the real shots depend on.
  async probe(page) {
    const out = {};
    await fitAll(page);
    // 1. wheel zoom changes the viewport transform?
    const tf = () => page.evaluate(() => document.querySelector(".react-flow__viewport").style.transform);
    const before = await tf();
    await wheelZoom(page, 960, 500, 6, -80, 400);
    out.wheelZoom = (await tf()) !== before;
    await fitAll(page);
    // 2. node drag: grab a chat node header, move it 120px, position changes?
    const header = await nodeRect(page, ".react-flow__node-chat .scene-node-title");
    if (header) {
      const posBefore = await page.evaluate(() => document.querySelectorAll(".react-flow__node")[1]?.style.transform);
      await page.mouse.move(header.cx, header.cy);
      await page.mouse.down();
      await easedMove(page, header.cx, header.cy, header.cx + 120, header.cy + 60, 500);
      await page.mouse.up();
      const posAfter = await page.evaluate(() => document.querySelectorAll(".react-flow__node")[1]?.style.transform);
      out.nodeDrag = posBefore !== posAfter;
    }
    // 3. handle-drag connect: does RF even render an in-progress connection?
    const handles = await page.evaluate(() => document.querySelectorAll(".react-flow__handle").length);
    out.handleCount = handles;
    const src = await nodeRect(page, ".react-flow__node-note .react-flow__handle.source");
    if (src) {
      const edgesBefore = await page.evaluate(() => fetch("/demo/step").catch(() => null)).then(() => null).catch(() => null);
      await page.mouse.move(src.cx, src.cy);
      await page.mouse.down();
      await easedMove(page, src.cx, src.cy, src.cx + 150, src.cy + 120, 400);
      const inProgress = await page.evaluate(() =>
        !!document.querySelector(".react-flow__connection, .react-flow__connectionline"));
      await page.mouse.up();
      out.connectPreview = inProgress;
    }
    // 4. View popover opens + segmented buttons present?
    await page.getByRole("button", { name: "View", exact: true }).click();
    await sleep(500);
    out.viewPopover = (await page.getByRole("button", { name: "Lines" }).count()) > 0;
    await page.keyboard.press("Escape");
    // 5. command palette?
    await page.keyboard.press("Control+k");
    await sleep(500);
    out.palette = await page.evaluate(() => !!document.querySelector("[class*='palette'], [class*='command']"));
    await page.keyboard.press("Escape");
    console.log("PROBE " + JSON.stringify(out));
  },

  // (8 bars) The graph builds itself: question tight on the drop, branches
  // land per bar, camera eases out to track the growth, Fit All settle.
  async grow(page) {
    await fitAll(page);
    const q = await nodeRect(page, ".react-flow__node-chat");
    await wheelZoom(page, q.cx, q.cy, 10, -90, 600); // tight on the question
    await sleep(600);
    await record(page, async () => {
      await caption(page, "Canvas", "Every message is a node — branch from any point.", 9800);
      await sleep(BAR * 1600);                        // 1.6 bars: question alone
      for (let i = 0; i < 4; i++) {
        await page.evaluate(() => fetch("/demo/step", { method: "POST" }));
        // ease out a touch after each pop so the frame absorbs the new node
        const c = await nodeRect(page, ".react-flow__node-chat");
        await wheelZoom(page, c.cx, c.cy, 4, 46, BAR * 520);
        await sleep(BAR * 340);
      }
      await fitAll(page);                             // settle wide
      await sleep(BAR * 1200);
    });
  },

  // (6 bars) Document View: hover the dense brief, open it as a document,
  // widen the panel, bump the reading size.
  async docviewer(page) {
    await fitAll(page);
    await sleep(300);
    // Push in on the brief node (the tall assistant reply).
    const brief = await page.evaluate(() => {
      const nodes = [...document.querySelectorAll(".react-flow__node-chat")];
      const el = nodes.reduce((a, b) =>
        a.getBoundingClientRect().height >= b.getBoundingClientRect().height ? a : b);
      const r = el.getBoundingClientRect();
      return { cx: r.x + r.width / 2, cy: r.y + r.height / 2 };
    });
    await wheelZoom(page, brief.cx, brief.cy, 5, -70, 400);
    await sleep(400);
    await page.mouse.move(760, 900);
    await showCursor(page);
    await page.mouse.move(761, 899);
    await record(page, async () => {
      await caption(page, "Document View", "Read any branch like a document.", 11500);
      // Hover the node header - quick actions reveal - then click the doc icon.
      const btn = async () => {
        const el = await page.evaluate(() => {
          const nodes = [...document.querySelectorAll(".react-flow__node-chat")];
          const node = nodes.reduce((a, b) =>
            a.getBoundingClientRect().height >= b.getBoundingClientRect().height ? a : b);
          const q = node.querySelector('[aria-label="Open Document View"]');
          if (!q) return null;
          const r = q.getBoundingClientRect();
          return { cx: r.x + r.width / 2, cy: r.y + r.height / 2 };
        });
        return el;
      };
      const nodeHead = await page.evaluate(() => {
        const nodes = [...document.querySelectorAll(".react-flow__node-chat")];
        const node = nodes.reduce((a, b) =>
          a.getBoundingClientRect().height >= b.getBoundingClientRect().height ? a : b);
        const r = node.querySelector(".scene-node-title").getBoundingClientRect();
        return { cx: r.x + r.width / 2, cy: r.y + r.height / 2 };
      });
      await easedMove(page, 761, 899, nodeHead.cx, nodeHead.cy, 900);   // hover reveals actions
      await sleep(350);
      const b = await btn();
      await easedMove(page, nodeHead.cx, nodeHead.cy, b.cx, b.cy, 500);
      await page.mouse.down(); await sleep(80); await page.mouse.up();  // panel opens
      await sleep(1100);
      // Widen the panel by dragging its resize separator.
      const sep = await page.evaluate(() => {
        const el = document.querySelector('[role="separator"]');
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { cx: r.x + r.width / 2, cy: r.y + r.height / 2 };
      });
      if (sep) {
        await easedMove(page, b.cx, b.cy, sep.cx, sep.cy, 700);
        await page.mouse.down();
        await easedMove(page, sep.cx, sep.cy, sep.cx + 240, sep.cy, 900);
        await page.mouse.up();
        // The drag sweeps across the panel's text - clear the accidental
        // selection so the reading layout is clean.
        await page.evaluate(() => window.getSelection()?.removeAllRanges());
        await sleep(500);
      }
      // Bump the reading size twice.
      const plus = await page.getByRole("button", { name: "Increase text size" }).boundingBox();
      await easedMove(page, sep ? sep.cx + 240 : 900, sep ? sep.cy : 500,
        plus.x + plus.width / 2, plus.y + plus.height / 2, 600);
      await page.mouse.down(); await sleep(70); await page.mouse.up();
      await sleep(350);
      await page.mouse.down(); await sleep(70); await page.mouse.up();
      await sleep(1400);
    });
    await hideCursor(page);
  },

  // (2 bars) Snap drag: cursor already on the node, quick arc, release.
  async dragfast(page) {
    await fitAll(page);
    await wheelZoom(page, 1150, 420, 6, -70, 400);
    await sleep(400);
    const headers = await page.evaluate(() =>
      [...document.querySelectorAll(".react-flow__node-chat .scene-node-title")].map((el) => {
        const r = el.getBoundingClientRect();
        return { cx: r.x + r.width / 2, cy: r.y + r.height / 2 };
      }));
    const h = headers[headers.length - 1];
    await page.mouse.move(h.cx - 120, h.cy + 160);
    await showCursor(page);
    await page.mouse.move(h.cx - 119, h.cy + 159); // paint cursor at start
    await record(page, async () => {
      await caption(page, "Direct", "Connections keep up.", 3100);
      await easedMove(page, h.cx - 119, h.cy + 159, h.cx, h.cy, 260, easeOut);
      await page.mouse.down();
      await sleep(70);
      await easedMove(page, h.cx, h.cy, h.cx + 170, h.cy + 120, 420);
      await easedMove(page, h.cx + 170, h.cy + 120, h.cx + 60, h.cy + 190, 380);
      await page.mouse.up();
      await sleep(500);
    });
    await hideCursor(page);
  },

  // (2 bars) Quick connection: grab the research handle, pull to the note.
  async connectfast(page) {
    await fitAll(page);
    await wheelZoom(page, 1250, 500, 6, -70, 400);
    await sleep(400);
    const pick = (kind, handle) => page.evaluate(([k, h]) => {
      const nodes = [...document.querySelectorAll(`.react-flow__node-${k}`)];
      if (!nodes.length) return null;
      const el = nodes.reduce((a, b) =>
        a.getBoundingClientRect().x >= b.getBoundingClientRect().x ? a : b);
      const hd = el.querySelector(`.react-flow__handle.${h}`);
      if (!hd) return null;
      const r = hd.getBoundingClientRect();
      return { cx: r.x + r.width / 2, cy: r.y + r.height / 2 };
    }, [kind, handle]);
    const src = await pick("web_research", "source");
    const dst = await pick("note", "target");
    await page.mouse.move(src.cx - 60, src.cy + 80);
    await showCursor(page);
    await page.mouse.move(src.cx - 59, src.cy + 79);
    await record(page, async () => {
      await caption(page, "Connect", "Wire branches together.", 2900);
      await easedMove(page, src.cx - 59, src.cy + 79, src.cx, src.cy, 240, easeOut);
      await page.mouse.down();
      await sleep(70);
      await easedMove(page, src.cx, src.cy, dst.cx, dst.cy, 640);
      await sleep(80);
      await page.mouse.up();
      await sleep(700);
    });
    await hideCursor(page);
  },

  // S2 (6 bars): start tight on the question node, ease out to the whole graph.
  async reveal(page) {
    await fitAll(page);
    const q = await nodeRect(page, ".react-flow__node-chat");
    // pre-position: zoom IN at the question node (not recorded yet handled by caller order)
    await wheelZoom(page, q.cx, q.cy, 14, -90, 700);
    await sleep(600);
    await record(page, async () => {
      await sleep(BAR * 1000);                       // hold 1 bar, tight
      const q2 = await nodeRect(page, ".react-flow__node-chat");
      await wheelZoom(page, q2.cx, q2.cy, 22, 62, BAR * 3200); // ease out ~3.2 bars
      await fitAll(page);                            // settle to canonical frame
      await sleep(BAR * 800);
    });
  },

  // S3 (6 bars): grab the Qdrant reply and take it for a walk - connections track.
  async drag(page) {
    await fitAll(page);
    await sleep(300);
    // mild push-in so the drag fills the frame
    await wheelZoom(page, 1150, 420, 6, -70, 400);
    await sleep(500);
    await showCursor(page);
    const headers = await page.evaluate(() => {
      return [...document.querySelectorAll(".react-flow__node-chat .scene-node-title")].map((el) => {
        const r = el.getBoundingClientRect();
        return { cx: r.x + r.width / 2, cy: r.y + r.height / 2 };
      });
    });
    const h = headers[headers.length - 1]; // right-most assistant reply
    await record(page, async () => {
      await easedMove(page, 960, 800, h.cx, h.cy, BAR * 900);  // cursor flies in
      await sleep(180);
      await page.mouse.down();
      await sleep(140);
      // arc: right-down, sweep left, settle back right - one continuous eased path
      const path = [
        [h.cx + 210, h.cy + 120, BAR * 1100],
        [h.cx - 120, h.cy + 230, BAR * 1200],
        [h.cx + 90, h.cy + 40, BAR * 1100],
      ];
      let px = h.cx, py = h.cy;
      for (const [x, y, ms] of path) {
        await easedMove(page, px, py, x, y, ms);
        px = x; py = y;
      }
      await page.mouse.up();
      await sleep(BAR * 700);
    });
    await hideCursor(page);
  },

  // S4 (4 bars): drag a new connection from research node to the decision note.
  async connect(page) {
    await fitAll(page);
    await wheelZoom(page, 1250, 500, 6, -70, 400);
    await sleep(500);
    await showCursor(page);
    // Pick the RIGHT-side research node and the decision note by position -
    // React Flow renders all nodes as sibling divs, so :last-of-type lies.
    const pick = (kind, handle) => page.evaluate(([k, h]) => {
      const nodes = [...document.querySelectorAll(`.react-flow__node-${k}`)];
      if (!nodes.length) return null;
      const el = nodes.reduce((a, b) =>
        a.getBoundingClientRect().x >= b.getBoundingClientRect().x ? a : b);
      const hd = el.querySelector(`.react-flow__handle.${h}`);
      if (!hd) return null;
      const r = hd.getBoundingClientRect();
      return { cx: r.x + r.width / 2, cy: r.y + r.height / 2 };
    }, [kind, handle]);
    const src = await pick("web_research", "source");
    const dst = await pick("note", "target");
    await record(page, async () => {
      await easedMove(page, 960, 850, src.cx, src.cy, BAR * 800);
      await sleep(160);
      await page.mouse.down();
      await sleep(120);
      await easedMove(page, src.cx, src.cy, dst.cx, dst.cy, BAR * 1600, easeInOut);
      await sleep(120);
      await page.mouse.up();
      await sleep(BAR * 900);
    });
    await hideCursor(page);
  },

  // S5 (4 bars): open the Builder dialog, pick the recipe (steps preview), autopilot.
  async builderdialog(page) {
    await fitAll(page);
    await showCursor(page);
    const btn = await nodeRect(page, "[data-overlay-trigger='builder-launch'], .appbar");
    await record(page, async () => {
      await caption(page, "The Builder", "Give it a goal. Set budgets. Choose oversight.", 9800);
      // fly to the Builder appbar icon and click
      const b = await page.getByRole("button", { name: "Builder" }).boundingBox();
      await easedMove(page, 960, 700, b.x + b.width / 2, b.y + b.height / 2, BAR * 700);
      await page.mouse.down(); await sleep(90); await page.mouse.up();
      await sleep(BAR * 500);
      const recipe = await page.getByRole("button", { name: "Recipe" }).boundingBox();
      await easedMove(page, b.x, b.y + 20, recipe.x + recipe.width / 2, recipe.y + recipe.height / 2, BAR * 600);
      await page.mouse.down(); await sleep(80); await page.mouse.up();
      await sleep(400);
      const opt = await page.getByRole("button", { name: /^Research and summarize/ }).boundingBox();
      await easedMove(page, recipe.x + 60, recipe.y, opt.x + opt.width / 2, opt.y + opt.height / 2, BAR * 500);
      await page.mouse.down(); await sleep(80); await page.mouse.up();
      await sleep(BAR * 600); // steps preview lands
      const auto = await page.getByRole("radio", { name: /autopilot/i }).boundingBox();
      await easedMove(page, opt.x, opt.y, auto.x + 8, auto.y + 8, BAR * 500);
      await page.mouse.down(); await sleep(80); await page.mouse.up();
      await sleep(BAR * 900); // disclosure visible, hold
    });
    await hideCursor(page);
  },

  // S6 (8 bars): the Builder works - tick per bar, camera drifts to follow.
  async builderlive(page) {
    await fitAll(page);
    // frame on the plan node, roomy right side for what will appear
    const plan = await nodeRect(page, ".react-flow__node-plan");
    await wheelZoom(page, plan.cx, plan.cy, 4, -80, 400);
    await panCamera(page, 1500, 700, -150, 40, 500);
    await sleep(400);
    await record(page, async () => {
      await caption(page, "Autopilot", "It builds on your canvas — every step logged.", 14600);
      // open the activity disclosure so the log grows on camera
      await page.evaluate(() => { const d = document.querySelector(".plan-node-activity"); if (d) d.open = true; });
      for (let i = 0; i < 6; i++) {
        await page.evaluate(() => fetch("/demo/step", { method: "POST" }));
        await page.evaluate(() => { const d = document.querySelector(".plan-node-activity"); if (d) d.open = true; });
        await sleep(BAR * 1000);
        if (i === 2) await panCamera(page, 1500, 650, -190, 0, BAR * 700);
        if (i === 4) await panCamera(page, 1500, 650, -170, 0, BAR * 700);
      }
      await fitAll(page); // reveal the whole built graph
      await sleep(BAR * 1000);
    });
  },

  // S7 (6 bars): View popover - live canvas tuning, one hit per beat-pair.
  async viewtune(page) {
    await fitAll(page);
    await showCursor(page);
    await record(page, async () => {
      await caption(page, "Your Canvas", "Grid, routing, connections — tuned live.", 11800);
      const v = await page.getByRole("button", { name: "View", exact: true }).boundingBox();
      await easedMove(page, 960, 760, v.x + v.width / 2, v.y + v.height / 2, BAR * 700);
      await page.mouse.down(); await sleep(80); await page.mouse.up();
      await sleep(BAR * 500);
      for (const name of ["Lines", "Dots"]) {
        const el = await page.getByRole("button", { name }).boundingBox();
        await easedMove(page, v.x, v.y, el.x + el.width / 2, el.y + el.height / 2, BAR * 450);
        await page.mouse.down(); await sleep(70); await page.mouse.up();
        await sleep(BAR * 550);
      }
      for (const label of ["Orthogonal Routing", "Fade Connections"]) {
        const el = await page.getByLabel(label).boundingBox();
        await easedMove(page, 900, 500, el.x + 8, el.y + 8, BAR * 450);
        await page.mouse.down(); await sleep(70); await page.mouse.up();
        await sleep(BAR * 550);
      }
      await page.keyboard.press("Escape");
      await sleep(BAR * 600);
    });
    await hideCursor(page);
  },

  // S8 (2 bars): command palette - open, type, run Fit All.
  async palette(page) {
    await fitAll(page);
    await wheelZoom(page, 960, 500, 5, -70, 350);
    await sleep(400);
    await record(page, async () => {
      await caption(page, "Ctrl + K", "Everything is a command.", 5600);
      await sleep(500);
      await page.keyboard.press("Control+k");
      await sleep(BAR * 700);              // let the palette register
      await page.keyboard.type("fit", { delay: 170 });
      await sleep(BAR * 700);              // read the filtered result
      await page.keyboard.press("Enter");
      await sleep(BAR * 1400);             // watch the canvas animate
    });
  },

  // S9 (4 bars): finale - from the chart, pull all the way out.
  async finale(page) {
    await fitAll(page);
    const chart = await nodeRect(page, ".react-flow__node-chart");
    await wheelZoom(page, chart.cx, chart.cy, 13, -90, 600);
    await sleep(500);
    await record(page, async () => {
      await caption(page, "Local-First", "Your models. Your machine. Your graph.", 6900);
      await sleep(BAR * 600);
      const c2 = await nodeRect(page, ".react-flow__node-chart");
      await wheelZoom(page, c2 ? c2.cx : 960, c2 ? c2.cy : 500, 20, 60, BAR * 2200);
      await fitAll(page);
      await sleep(BAR * 1100);
    });
  },
};

// ---------------------------------------------------------------- main
const { browser, page } = await boot();
try {
  if (!shots[SHOT]) throw new Error("unknown shot: " + SHOT);
  await shots[SHOT](page);
} finally {
  await browser.close();
}
process.exit(0);
