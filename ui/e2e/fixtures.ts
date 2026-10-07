/**
 * The e2e suite's fixtures: a fresh engine and a wire for every test, and a
 * page that fails the test if the console logs an error.
 *
 * A fresh engine per test costs a fifth of a second and buys tests that cannot
 * leak into each other -- a blackout, a panic or a cue taken in one never
 * decides another's result.
 */
import { test as base, expect, type Page } from "@playwright/test";
import { ArtNet, Engine, type EngineOptions } from "./engine";

type Fixtures = {
  /** How this test's engine runs; set with `test.use({ engineOptions })`. */
  engineOptions: EngineOptions;
  /** Console errors this test expects (a reconnect test sees the socket fail).
   *  One RegExp, alternatives joined with |, never an array: Playwright reads
   *  `[regexA, regexB]` in test.use as its [value, options] tuple, because the
   *  second element is an object, and the option silently became regexA. */
  allowedErrors: RegExp | null;
  /** URLs this test expects to fail (Studio asks for a waveform a track may
   *  not have). Any other 4xx or 5xx fails the test, named by its URL. */
  allowedFailures: RegExp | null;
  /** Whether the first-visit welcome card has already been answered. */
  welcomed: boolean;
  artnet: ArtNet;
  engine: Engine;
  /** Open the console and wait until it is connected and showing state. */
  openConsole: (page: Page, opts?: { hash?: string; token?: string | null }) => Promise<void>;
};

export const test = base.extend<Fixtures>({
  engineOptions: [{}, { option: true }],
  allowedErrors: [null, { option: true }],
  allowedFailures: [null, { option: true }],
  welcomed: [true, { option: true }],

  artnet: async ({}, use) => {
    const artnet = await ArtNet.open();
    await use(artnet);
    artnet.close();
  },

  engine: async ({ engineOptions, artnet }, use, testInfo) => {
    const engine = await Engine.start({ ...engineOptions, artnetPort: artnet.port });
    try {
      await use(engine);
    } finally {
      await engine.stop();
      if (testInfo.status !== testInfo.expectedStatus) {
        await testInfo.attach("engine.log", { body: engine.log, contentType: "text/plain" });
      }
    }
  },

  context: async ({ context, welcomed }, use) => {
    if (welcomed) {
      await context.addInitScript(() => {
        try { localStorage.setItem("klights.guide.welcomed", "1"); } catch { /* opaque origin */ }
      });
    }
    await use(context);
  },

  // Depends on `engine` so that it is torn down FIRST: with the engine stopped
  // under an open page, the console's reconnect logs a refused socket after
  // the test is over, and that is teardown, not a fault.
  page: async ({ page, allowedErrors, allowedFailures, engine }, use) => {
    void engine;
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
    page.on("console", (m) => {
      const text = m.text();
      // The browser's own line for a failed request does not name the URL;
      // the response listener below does, so it reports those instead.
      if (m.type() !== "error" || text.startsWith("Failed to load resource")) return;
      if (!allowedErrors?.test(text)) errors.push(`console.error: ${text}`);
    });
    page.on("response", (r) => {
      if (r.status() >= 400 && !allowedFailures?.test(r.url())) {
        errors.push(`HTTP ${r.status()} ${r.url()}`);
      }
    });
    await use(page);
    expect(errors, "the console logged errors or a request failed").toEqual([]);
  },

  openConsole: async ({ engine }, use) => {
    await use(async (page, { hash = "", token } = {}) => {
      await page.goto(engine.url(hash, token === undefined ? {} : { token }));
      await expect(page.locator(".status-dot.open")).toBeVisible();
      await expect(page.locator(".header")).toContainText("despacio");
    });
  },
});

export { expect };
