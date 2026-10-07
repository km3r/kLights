import { defineConfig, devices } from "@playwright/test";

/**
 * The browser end-to-end suite: the real engine, serving the committed
 * ui/dist, driven by Chromium, checked on the Art-Net it puts on the wire.
 * See e2e/engine.ts.
 *
 *     npm run e2e             (from ui/; needs Python and a Chromium)
 *
 * No retries. A test here that fails once has found something -- a race in
 * the console or the engine is exactly what this suite exists to catch, and a
 * retry would turn it into a pass.
 */
export default defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.e2e\.ts$/,
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: process.env.CI ? 2 : undefined,
  timeout: 30_000,
  expect: { timeout: 5_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    // A laptop at the desk. Phone layouts are set per file with test.use, so
    // the journeys run once rather than once per device.
    { name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 } } },
  ],
});
