import { expect, type APIRequestContext, type Page, type TestInfo } from "@playwright/test";

export interface FixtureState {
  node_id: string;
  files: Record<string, { sha256: string; hex: string }>;
  initial_files: Record<string, { sha256: string; hex: string }>;
  writes: { path: string; sha256: string; bytes: number }[];
  busy: boolean;
  active_runs: number;
  fingerprint: string | null;
  error: string;
  change_state: string;
  approval_id: string;
  request_id: string;
  awaiting_approval: boolean;
  pending_approval_resolved: boolean | null;
  harness_status: string;
  activity: { tool: string; summary: string; outcome: string }[];
  source: string;
  different_source: string;
}

export interface WireIntent {
  kind: "intent";
  id: string;
  topic: string;
  intent: string;
  // The record variant is deliberate malformed wire input in validation tests.
  args: unknown[] | Record<string, unknown>;
}

export async function state(request: APIRequestContext): Promise<FixtureState> {
  const response = await request.get("/api/e2e/state");
  expect(response.ok()).toBe(true);
  return response.json() as Promise<FixtureState>;
}

export async function prepare(request: APIRequestContext, kind = "gitlink"): Promise<FixtureState> {
  const response = await request.post(`/api/e2e/prepare/${kind}`);
  expect(response.ok()).toBe(true);
  return response.json() as Promise<FixtureState>;
}

export async function action(request: APIRequestContext, name: string): Promise<FixtureState> {
  const response = await request.post(`/api/e2e/action/${name}`);
  expect(response.ok()).toBe(true);
  return response.json() as Promise<FixtureState>;
}

export async function openApp(page: Page): Promise<void> {
  await page.goto(`/#token=${process.env.GRAPHLINK_SAFETY_E2E_TOKEN}`);
  await expect(page.locator('.app-shell[data-connection-status="open"]')).toBeAttached();
  await expect(page.locator(".react-flow__node")).toHaveCount(1);
}

// Observe the REAL SPA socket; this does not replace WebSocket or WsTransport.
export function collectIntents(page: Page): WireIntent[] {
  const intents: WireIntent[] = [];
  page.on("websocket", (socket) => {
    socket.on("framesent", ({ payload }) => {
      const message = JSON.parse(String(payload)) as Partial<WireIntent>;
      if (message.kind === "intent") intents.push(message as WireIntent);
    });
  });
  return intents;
}

// A second native browser WebSocket sends adversarial/replayed envelopes
// through the real authenticated route and validation. The SPA stays connected.
export async function wireIntent(page: Page, message: WireIntent): Promise<Record<string, unknown>> {
  return page.evaluate((packet) => new Promise<Record<string, unknown>>((resolve, reject) => {
    const token = new URLSearchParams(location.hash.slice(1)).get("token");
    const socket = new WebSocket(`ws://${location.host}/ws?session=default&token=${encodeURIComponent(token ?? "")}`);
    const timer = setTimeout(() => { socket.close(); reject(new Error("Wire response timed out")); }, 15_000);
    socket.addEventListener("open", () => socket.send(JSON.stringify(packet)));
    socket.addEventListener("error", () => { clearTimeout(timer); reject(new Error("Wire failed")); });
    socket.addEventListener("message", (event) => {
      const reply = JSON.parse(String(event.data)) as Record<string, unknown>;
      if (reply.id === packet.id && (reply.kind === "result" || reply.kind === "error")) {
        clearTimeout(timer);
        socket.close();
        resolve(reply);
      }
    });
  }), message);
}

export function packet(topic: string, intent: string, args: unknown[]): WireIntent {
  return { kind: "intent", id: `e2e-${crypto.randomUUID()}`, topic, intent, args };
}

export async function evidence(
  request: APIRequestContext, page: Page, testInfo: TestInfo,
  before: FixtureState, after: FixtureState, expected: string, extra: object = {},
): Promise<void> {
  const metadata = await (await request.get("/api/e2e/info")).json() as object;
  await testInfo.attach("safety-evidence.json", {
    body: JSON.stringify({ ...metadata, test: testInfo.title, expected, actual: "passed assertions",
      browser: page.context().browser()?.version(), node: process.version,
      reducedMotion: await page.evaluate(() => matchMedia("(prefers-reduced-motion: reduce)").matches),
      before, after, ...extra }, null, 2),
    contentType: "application/json",
  });
  await testInfo.attach("review-ui.png", { body: await page.screenshot(), contentType: "image/png" });
}

export async function openProposal(page: Page): Promise<void> {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.getByRole("tab", { name: "Proposal", exact: true }).click();
  await expect(page.locator(".gitlink-node-proposal-tab")).toBeVisible();
  // The Proposal tab expands the initially framed Setup card. Its bottom
  // must clear the floating composer before any review/Apply interaction:
  // scrolling an occluded button into view can scroll the React Flow wrapper
  // temporarily and leave a click aimed at coordinates it immediately resets.
  // Use ordinary UI navigation and actual hit-tests, never a forced click.
  await page.getByRole("button", { name: "Fit All", exact: true }).click();
  await expect.poll(async () => page.locator(".gitlink-node-content").evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return rect.top >= 0 && rect.bottom <= innerHeight && rect.left >= 0 && rect.right <= innerWidth
      && [rect.top + 16, rect.bottom - 16].every((y) =>
        [rect.left + 16, rect.right - 16].every((x) => element.contains(document.elementFromPoint(x, y))));
  })).toBe(true);
}

export async function confirmApply(page: Page): Promise<void> {
  await page.getByRole("button", { name: /^Apply selected \(/ }).click();
  const confirmation = page.getByRole("dialog", { name: "Apply Selected Changes?" });
  await expect(confirmation).toBeVisible();
  await confirmation.getByRole("button", { name: "Yes", exact: true }).click();
}

export async function startPython(page: Page): Promise<void> {
  await page.getByRole("textbox", { name: "Follow-up message" }).fill("Run the fixed harmless Python fixture.");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByRole("group", { name: "Agent tool approval" })).toBeVisible();
}
