import { randomBytes } from "node:crypto";
import { defineConfig } from "@playwright/test";

// One ephemeral capability shared only by the isolated server and test workers.
// Never use a live account credential. Production auth and origin checks stay on.
const token = process.env.GRAPHLINK_SAFETY_E2E_TOKEN ?? randomBytes(32).toString("hex");
process.env.GRAPHLINK_SAFETY_E2E_TOKEN = token;
const executablePath = process.env.GRAPHLINK_E2E_CHROMIUM_EXECUTABLE;

export default defineConfig({
  testDir: "./e2e-safety",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 20_000 },
  outputDir: "./test-results/safety",
  reporter: [["list"], ["json", { outputFile: "./test-results/safety-results.json" }]],
  use: {
    baseURL: "http://127.0.0.1:8801",
    extraHTTPHeaders: { Authorization: `Bearer ${token}` },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 1200 },
    launchOptions: executablePath ? { executablePath } : {},
  },
  webServer: {
    command: "python ../tests_e2e/run_safety_backend.py",
    // Public SPA readiness avoids placing the ephemeral token in logs or URLs.
    url: "http://127.0.0.1:8801/",
    env: { GRAPHLINK_SAFETY_E2E_TOKEN: token },
    reuseExistingServer: false,
    timeout: 180_000,
    stdout: "pipe",
    stderr: "pipe",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
