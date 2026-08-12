import { defineConfig } from "@playwright/test";

/**
 * ADR-015 stage 15.6: Playwright config for the boot-smoke E2E suite.
 *
 * Chromium only, deliberately - see tests_e2e/run_backend.py's own module
 * docstring for the full "why Chromium is a faithful proxy for pywebview's
 * WebView2" reasoning. There is no product reason to also run Firefox/
 * WebKit here: this app never ships in either engine, so a failure unique
 * to one of them would not describe a real user's experience, only cost
 * extra CI minutes (see .github/workflows/ci.yml's own Actions-minutes-
 * economy note for why that budget is watched closely in this repo).
 *
 * `webServer` owns the real backend's whole lifecycle: it runs
 * tests_e2e/run_backend.py (this repo's real backend/app.py factory under
 * uvicorn, not a mock), polls the given `url` until it answers, then tears
 * the process down after the run - so no spec file ever starts or stops a
 * server itself. `reuseExistingServer: !process.env.CI` means a developer
 * who already has `python tests_e2e/run_backend.py` running locally (e.g.
 * to poke at http://127.0.0.1:8799 directly while iterating on a spec) gets
 * that instance reused rather than a second one colliding on the same
 * port; CI always starts a fresh one.
 */
export default defineConfig({
  testDir: "./e2e",
  // Sequential, single worker - a REAL constraint of the backend under
  // test, not an arbitrary choice: create_app() defaults `restrict_
  // sessions=True` (backend/app.py, never overridden by
  // tests_e2e/run_backend.py - see that script's own docstring on why it
  // stays at the real, shipped default), which pins every WS connection to
  // the SAME session id/live SceneDocument, by design (ADR-004 stage 4.3).
  // Two specs running concurrently would therefore be mutating one shared
  // canvas at the same time - not a Playwright flakiness problem to work
  // around, but this app's real multi-tab behavior, which each spec's own
  // helpers.gotoApp() already resets to a blank canvas at the start of
  // every test specifically so specs stay independent of each other
  // despite sharing that one session.
  fullyParallel: false,
  workers: 1,
  // Timeouts sized for a COLD shared CI runner, not a warm dev machine.
  // The first CI run of this suite failed all 5 specs on the very first
  // assertion (the `.app-conn-open` WS badge) against Playwright's 5s
  // default, while the identical suite passed repeatedly on a local
  // machine - the gap is a 2-core runner doing a cold uvicorn boot (which
  // imports matplotlib/anthropic/openai), a cold browser launch, and a
  // first parse of the ~800 KiB SPA bundle before the WS handshake even
  // starts. These are ceilings for a hang, not expected durations: a
  // healthy run still finishes in ~30s total.
  timeout: 90_000,
  expect: { timeout: 30_000 },
  // CI only. Locally a retry would mask a spec that is genuinely racy and
  // wants fixing; in CI it absorbs one-off runner stalls without turning
  // the whole PR red.
  retries: process.env.CI ? 2 : 0,
  use: {
    baseURL: "http://127.0.0.1:8799",
    trace: "on-first-retry",
    actionTimeout: 30_000,
    navigationTimeout: 60_000,
  },
  webServer: {
    command: "python ../tests_e2e/run_backend.py",
    url: "http://127.0.0.1:8799/api/health",
    // Generous for the same cold-start reason: this backend's import graph
    // (matplotlib, the provider SDKs) is genuinely slow to load the first
    // time on a runner with a cold filesystem cache.
    timeout: 180_000,
    reuseExistingServer: !process.env.CI,
    stdout: "pipe",
    stderr: "pipe",
  },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
});
