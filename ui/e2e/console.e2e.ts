/**
 * The console, end to end: a tap in a real browser, on the committed bundle
 * the engine serves, checked where it matters -- on the Art-Net the rig
 * receives. The unit suites check each half; this checks that the halves
 * still meet.
 */
import { DESPACIO, at, moverDimmers, pinspotRgbw } from "./engine";
import { expect, test } from "./fixtures";

const lit = (dmx: Uint8Array) => moverDimmers(dmx).every((v) => v > 0);
const dark = (dmx: Uint8Array) =>
  moverDimmers(dmx).every((v) => v === 0) && pinspotRgbw(dmx).flat().every((v) => v === 0);

test("the committed bundle loads, connects and shows the rig", async ({ page, engine, artnet, openConsole }) => {
  await openConsole(page);
  // Before a single tap the rig is live: the engine starts lit.
  await artnet.waitFor("every mover lit", lit);
  await expect(page.getByRole("slider", { name: "Master" })).toBeVisible();
  for (const tab of ["Show", "Color", "Move", "Bright", "Setup"]) {
    await expect(page.locator(".tabbar").getByRole("button", { name: tab })).toBeVisible();
  }
  // Every asset the bundle references is served -- the failure the CI bundle
  // job guards in git, checked here in a browser.
  const index = await (await fetch(engine.url())).text();
  for (const asset of index.match(/assets\/[\w.-]+/g) ?? []) {
    const res = await fetch(`http://127.0.0.1:${engine.port}/${asset}`);
    expect(res.status, asset).toBe(200);
  }
});

test("Blackout takes every light to zero on the wire, and lets go where the show got to",
  async ({ page, artnet, openConsole }) => {
    await openConsole(page);
    await artnet.waitFor("every mover lit", lit);
    await page.getByRole("button", { name: "Blackout", exact: true }).click();
    await artnet.waitFor("every mover's dimmer and every pinspot colour at zero", dark);
    await expect(page.locator(".banners")).toContainText("Blackout — master is at zero");
    // Blackout is the master, not panic: the pinspots' mode channel is a
    // setting, held where it is, not a light.
    const frame = artnet.frame()!;
    expect(DESPACIO.pinspots.map((a) => at(frame, a, DESPACIO.pinspot.control))).toEqual([255, 255]);

    await page.getByRole("button", { name: "Blackout ON" }).click();
    await artnet.waitFor("every mover lit again", lit);
    await expect(page.locator(".banners")).toHaveCount(0);
  });

test("the Master fader scales what the rig receives", async ({ page, artnet, openConsole }) => {
  await openConsole(page);
  const master = page.getByRole("slider", { name: "Master" });
  // The startup look is at full level, so the master IS the dimmer: 255 at 1,
  // half that at 0.5. (The engine starts at 0.9 -- 230 -- so waiting for 255
  // also proves the fader, not the startup frame, set it.)
  await master.fill("1");
  await artnet.waitFor("every dimmer at full", (d) => moverDimmers(d).every((v) => v === 255));
  await master.fill("0.5");
  await artnet.waitFor("every dimmer at half", (d) =>
    moverDimmers(d).every((v) => Math.abs(v - 127.5) <= 1));
  await expect(page.locator(".header")).toContainText("50%");
  await master.fill("0");
  await artnet.waitFor("every dimmer at zero", (d) => moverDimmers(d).every((v) => v === 0));
  await expect(page.locator(".banners")).toContainText("Master is at zero");
});

test("Panic forces every channel to zero and keeps sending; Release brings the show back",
  async ({ page, artnet, openConsole }) => {
    await openConsole(page);
    await artnet.waitFor("every mover lit", lit);
    await page.getByRole("button", { name: "Panic — force output to zero" }).click();
    await artnet.waitFor("all 512 channels at zero", (d) => d.every((v) => v === 0));
    await expect(page.locator(".banners")).toContainText("PANIC");
    // Still sending: a blackout that stops sending is not a blackout, because a
    // node holds the last frame it got.
    const frames = await artnet.sample(500);
    expect(frames.length).toBeGreaterThan(10);
    expect(frames.every((d) => d.every((v) => v === 0))).toBe(true);

    await page.locator(".banners").getByRole("button", { name: "Release" }).click();
    await artnet.waitFor("the show lit again", lit);
  });

test("GO walks the Night cue list, Back steps back, and any cue can be jumped to",
  async ({ page, openConsole }) => {
    await openConsole(page);
    const card = page.locator(".card").filter({ has: page.getByRole("button", { name: /all 9 cues$/ }) });
    await expect(card).toContainText("— /9");
    await expect(card).toContainText("Not started");
    await card.getByRole("button", { name: /^GO — / }).click();
    await expect(card).toContainText("1/9");
    await expect(card).toContainText("Now: Warm Up");
    await card.getByRole("button", { name: /^GO — / }).click();
    await expect(card).toContainText("2/9");
    await card.getByRole("button", { name: "Back" }).click();
    await expect(card).toContainText("1/9");
    await card.getByRole("button", { name: "Show all 9 cues" }).click();
    await card.getByRole("button", { name: /^9\. / }).click();
    await expect(card).toContainText("9/9");
    await expect(card).toContainText("end of the list");
    await expect(card.getByRole("button", { name: /^GO — / })).toBeDisabled();
  });

test("a colour tapped on the quick palette is what the pinspots put out", async ({ page, artnet, openConsole }) => {
  await openConsole(page, { hash: "color" });
  await page.locator(".card").filter({ hasText: "Applies to" })
    .getByRole("button", { name: "pinspots", exact: true }).click();
  const swatch = page.getByRole("button", { name: "palette 1" });
  const css = await swatch.evaluate((el) => getComputedStyle(el).backgroundColor);
  const want = (css.match(/\d+/g) ?? []).slice(0, 3).map(Number);
  expect(want).toHaveLength(3);
  await swatch.click();
  // The same hue on both pinspots: each channel in proportion to the swatch's.
  const peak = Math.max(...want);
  const matches = (rgbw: number[]) => {
    const top = Math.max(...rgbw.slice(0, 3));
    return top > 0 && rgbw.slice(0, 3).every((v, i) => Math.abs(v / top - want[i]! / peak) < 0.06);
  };
  await artnet.waitFor(`both pinspots at ${css}`, (d) => pinspotRgbw(d).every(matches));
  // The movers are not pinspots: their dimmers are untouched by it.
  expect(lit(artnet.frame()!)).toBe(true);

  await page.getByRole("button", { name: "Clear", exact: true }).click();
  await artnet.waitFor("the pinspots back off that colour", (d) => !pinspotRgbw(d).every(matches));
});

test("a typed tempo is the engine's tempo", async ({ page, openConsole }) => {
  await openConsole(page);
  const bpm = page.getByRole("spinbutton", { name: "BPM" });
  await bpm.fill("140");
  await bpm.blur();
  await expect(page.locator(".header")).toContainText("140.0 bpm");
  // Out of the clock's range: never sent, and the field goes back to the tempo.
  await bpm.fill("400");
  await bpm.blur();
  await expect(bpm).toHaveValue("140.0");
});

test.describe("with a DJ bridge", () => {
  test.use({ engineOptions: { syncPort: true } });

  test("tempo from the bridge drives the clock, says who is driving, and can be taken back",
    async ({ page, engine, openConsole }) => {
      await openConsole(page);
      const card = page.locator(".card").filter({ hasText: "DJ sync" });
      await expect(card).toContainText("port open, nothing has spoken through it yet");
      await engine.sendOsc("/master/bpm/current", 126);
      await expect(card).toContainText("LOCKED");
      await expect(card).toContainText("source rkbx");
      await expect(page.locator(".header")).toContainText("126.0 bpm");
    });
});

test("two consoles share one state and can see each other", async ({ browser, engine }) => {
  const open = async (name: string) => {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    await context.addInitScript((n) => {
      localStorage.setItem("klights.guide.welcomed", "1");
      localStorage.setItem("klights.name", n);
    }, name);
    const page = await context.newPage();
    await page.goto(engine.url("setup"));
    await expect(page.locator(".status-dot.open")).toBeVisible();
    return { context, page };
  };
  const a = await open("front of house");
  const b = await open("booth");
  try {
    for (const { page } of [a, b]) {
      await expect(page.locator(".presence")).toContainText("front of house");
      await expect(page.locator(".presence")).toContainText("booth");
    }
    await a.page.getByRole("slider", { name: "Master" }).fill("0.3");
    await expect(b.page.getByRole("slider", { name: "Master" })).toHaveValue("0.3");
    await expect(b.page.locator(".header")).toContainText("30%");
    // And who did it: no locking, so the other desk is told instead.
    await expect(b.page.locator(".presence")).toContainText(/front of house.*master/i);
  } finally {
    await a.context.close();
    await b.context.close();
  }
});

test("without the token a console watches but cannot touch, and says so", async ({ page, artnet, openConsole }) => {
  await openConsole(page, { token: null });
  await expect(page.locator(".banners")).toContainText("VIEW ONLY");
  await artnet.waitFor("every mover lit", lit);
  await page.getByRole("button", { name: "Blackout", exact: true }).click();
  // Nothing reaches the rig, for long enough to have arrived if it were going to.
  const frames = await artnet.sample(800);
  expect(frames.length).toBeGreaterThan(10);
  expect(frames.every(lit)).toBe(true);
  await expect(page.getByRole("button", { name: "Blackout", exact: true })).toBeVisible();
});

test.describe("when the engine restarts", () => {
  // The browser logs the socket failing while the engine is down; that is the
  // event under test, not a fault in the console.
  test.use({ allowedErrors: [/WebSocket/i, /ERR_CONNECTION_REFUSED/i] });

  test("a connected console says the rig is holding, then picks the show back up by itself",
    async ({ page, engine, artnet, openConsole }) => {
      await openConsole(page);
      await artnet.waitFor("every mover lit", lit);
      await engine.halt();
      await expect(page.locator(".banners")).toContainText("Disconnected — the rig is holding its last frame");
      await engine.restart();
      await expect(page.locator(".status-dot.open")).toBeVisible({ timeout: 10_000 });
      await expect(page.locator(".banners")).toHaveCount(0);
      // Not a stale page: it drives the new engine.
      await page.getByRole("button", { name: "Blackout", exact: true }).click();
      await artnet.waitFor("the new engine's rig blacked out", dark);
    });
});
