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
  use: {
    baseURL: "http://127.0.0.1:8799",
    trace: "on-first-retry",
  },
  webServer: {
    command: "python ../tests_e2e/run_backend.py",
    url: "http://127.0.0.1:8799/api/health",
    timeout: 30000,
    reuseExistingServer: !process.env.CI,
  },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
});
