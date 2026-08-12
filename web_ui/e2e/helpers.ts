import { expect, type Page } from "@playwright/test";

/**
 * ADR-015 stage 15.6: shared navigation entry point for every spec in this
 * suite.
 *
 * Two things every spec would otherwise have to repeat:
 *
 * 1. Wait for the REAL WS round-trip to complete (App.tsx's connection
 *    badge, `.app-conn-<status>`) before touching anything - a fresh
 *    `page.goto("/")` returns as soon as the SPA shell's static HTML/JS
 *    loads, well before the WsTransport handshake against the real backend
 *    (tests_e2e/run_backend.py) has actually completed.
 * 2. Dismiss the first-run onboarding wizard (chrome/OnboardingDialog.tsx).
 *    Every E2E run boots against a BRAND NEW settings_state_file (see that
 *    script's own docstring on why - full isolation from a real user's
 *    ~/.graphlink/), so `has_completed_onboarding` is always false and the
 *    dialog auto-opens once per page load, exactly as it would for a real
 *    user's very first launch. Escape is that component's own documented
 *    "closed by ANY means" dismissal path (see its module docstring), so
 *    this needs no knowledge of which of its 4 steps happened to be
 *    showing.
 * 3. Guarantee a BLANK canvas before the spec's own steps run. This is
 *    load-bearing, not cosmetic: create_app() defaults `restrict_sessions=
 *    True` (backend/app.py, and tests_e2e/run_backend.py never overrides
 *    it - see that script's own docstring), which pins every WS connection
 *    from every spec to the SAME session id, i.e. the SAME live
 *    SceneDocument on the backend (ADR-004 stage 4.3's real, shipped
 *    policy - this is not a test-only artifact). Without an explicit reset
 *    here, a node left on the canvas by an earlier spec (or an earlier
 *    `npx playwright test` invocation reusing the same backend process
 *    locally - see playwright.config.ts's own `reuseExistingServer`) would
 *    silently leak into the next spec's assertions. Chat Library's own
 *    "New Chat" button (ChatLibraryDialog.tsx's `newChat()`) is the real,
 *    exercised, confirm-free reset action - `.first()` because BOTH its
 *    "already have chats" header copy and its "no chats yet" empty-state
 *    copy can render at once and are functionally identical (same handler).
 */
export async function gotoApp(page: Page): Promise<void> {
  await page.goto("/");
  await expect(page.locator(".app-conn-open")).toBeVisible();

  // waitFor (not isVisible(), which resolves immediately either way) is
  // deliberate: the app-settings snapshot that decides whether onboarding
  // auto-opens arrives asynchronously over the SAME WS connection
  // app-conn-open just confirmed, so it can genuinely still be in flight at
  // this exact line. A bare isVisible() check here would race it - "not
  // visible yet" and "never opening" look identical at a single instant,
  // and picking the wrong one would leave the dialog to pop up mid-test
  // instead of being dismissed up front.
  const onboarding = page.getByRole("dialog", { name: "Welcome to Graphlink" });
  try {
    await onboarding.waitFor({ state: "visible", timeout: 5000 });
    await page.keyboard.press("Escape");
    await onboarding.waitFor({ state: "hidden" });
  } catch {
    // Genuinely never opened within the window - nothing to dismiss.
  }

  await page.locator('[data-overlay-trigger="library"]').click();
  const libraryDialog = page.getByRole("dialog", { name: "Chat Library" });
  await expect(libraryDialog).toBeVisible();
  await libraryDialog.getByRole("button", { name: "New Chat", exact: true }).first().click();
  await expect(libraryDialog).toBeHidden();
  await expect(page.locator(".react-flow__node")).toHaveCount(0);
}
