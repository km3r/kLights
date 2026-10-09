/**
 * Studio against a real engine and a real show folder (a copy of
 * shared/show-example): what the unit suite proves against a mock socket --
 * drafts checked by the engine, saves that quote the rev they read -- proved
 * here with the engine doing the checking and the file on disk as the answer.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

test.use({
  engineOptions: { showDir: true },
  // The example track has no waveform and no audio, Studio asks for both,
  // and 404 is the engine's documented "not in the show folder" -- which
  // Studio shows as "No waveform" and an offer to open a file.
  allowedFailures: /\/api\/(waveforms|audio)\/synth-128\?/,
});

async function openTimeline(page: Page, url: string): Promise<void> {
  await page.goto(url);
  // Studio's own first-visit offer, answered so it does not cover the editor.
  await page.getByRole("button", { name: "Not now" }).click();
  await page.getByRole("link", { name: "Open timeline" }).click();
  await expect(page.getByRole("region", { name: "lanes" })).toBeVisible();
  // The engine's verdict on the timeline as loaded: only a connected page
  // gets one. Loading the editor can hold a slow machine's page for over a
  // second, and the engine used to drop a page that far behind its snapshots
  // as one that stopped reading. The page reconnected by itself, but a Save
  // or a Drive the rig pressed in that gap was refused as "not connected to
  // the engine" -- which is what a test that pressed on at once ran into on
  // CI. A page that is only behind is no longer dropped (SEND_DEADLINE_S in
  // engine/server.py); the wait stays, so each test starts from a timeline
  // the engine has answered for.
  await expect(page.getByRole("button", { name: "valid" })).toBeVisible({ timeout: 15_000 });
}

test("opens on a library of the show folder, read through the engine", async ({ page, engine }) => {
  await page.goto(engine.url("studio"));
  await expect(page.getByRole("link", { name: /^Tracks\s*1$/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /^Routines\s*4$/ })).toBeVisible();
  await expect(page.getByRole("link", { name: /^Template sets\s*1$/ })).toBeVisible();
  await expect(page.getByText("synthetic 128").first()).toBeVisible();
  // And the reasons it is not yet ready for a night, as the engine sees them.
  await expect(page.getByText("No CDJ signature")).toBeVisible();
});

test("a look made and changed in Studio is written to the event, and a console's picker has it at once",
  async ({ page, browser, engine }) => {
    const file = join(engine.eventDir, "parametric_looks.json");
    const authored = JSON.parse(readFileSync(file, "utf8")).looks.length;
    await page.goto(engine.url("studio/looks"));
    await page.getByRole("button", { name: "Not now" }).click();
    const looks = page.getByRole("region", { name: "looks" });
    await expect(looks.getByRole("button", { name: "Lazy Orbit" })).toBeVisible();

    // A new look: one block, written to the event as it is made.
    await looks.getByRole("button", { name: "New look…" }).click();
    const dialog = page.getByRole("dialog", { name: "New look" });
    await dialog.getByLabel("name").fill("E2E Swing");
    await dialog.getByLabel("block").selectOption("pendulum");
    await dialog.getByRole("button", { name: "Make it" }).click();
    await expect(page.getByText(/Made E2E Swing/)).toBeVisible();
    let saved = JSON.parse(readFileSync(file, "utf8"));
    expect(saved.looks).toHaveLength(authored + 1);
    expect(saved.looks.at(-1)).toMatchObject({ name: "E2E Swing", block: "pendulum" });
    // The file keeps what was in it by hand: its comments and its schema line.
    expect(saved._comment.length).toBeGreaterThan(10);
    expect(saved.$schema).toContain("parametric_looks.schema.json");

    // Changed in its panel, saved quoting the rev the engine just answered with.
    const panel = page.getByLabel("selected look");
    await panel.getByLabel("width").fill("45");
    await panel.getByRole("button", { name: "Save the look" }).click();
    await expect(page.getByText(/Saved E2E Swing/)).toBeVisible();
    saved = JSON.parse(readFileSync(file, "utf8"));
    expect(saved.looks.at(-1).args.width).toBe(45);

    // No restart: another console lists it, and can play it.
    const phone = await browser.newContext();
    await phone.addInitScript(() => localStorage.setItem("klights.guide.welcomed", "1"));
    const console_ = await phone.newPage();
    try {
      await console_.goto(engine.url("move"));
      await expect(console_.getByRole("button", { name: "E2E Swing" }).first()).toBeVisible();
    } finally {
      await phone.close();
    }

    // Renamed, with the cue that names it: Heads - Cathedral took over a
    // stored look, so the stored look comes back, hidden for the new name.
    await looks.getByLabel("filter looks").fill("cathedral");
    await looks.getByRole("button", { name: "Heads - Cathedral" }).click();
    await panel.getByLabel("look name").fill("Cathedral");
    await panel.getByRole("button", { name: "Save and rename" }).click();
    await expect(page.getByText(/Renamed Heads - Cathedral to Cathedral, and moved what named it: 1 cue/)).toBeVisible();
    const cues = JSON.parse(readFileSync(join(engine.eventDir, "cues.json"), "utf8")).cues;
    expect(cues.find((c: { name: string }) => c.name === "Cathedral").movement)
      .toEqual({ "corner movers": "Cathedral" });
    saved = JSON.parse(readFileSync(file, "utf8"));
    expect(saved.retired.find((r: { name: string }) => r.name === "Heads - Cathedral").replaced_by)
      .toBe("Cathedral");

    // And an edit made OUTSIDE Studio -- by hand, by the MCP server -- is read
    // by the engine itself: no button, no restart.
    saved.looks.push({ name: "By Hand", block: "pulse", groups: ["pinspots"], args: { depth: 0.5 } });
    writeFileSync(file, JSON.stringify(saved, null, 2));
    await looks.getByLabel("filter looks").fill("by hand");
    await expect(looks.getByRole("button", { name: "By Hand" })).toBeVisible({ timeout: 10_000 });
  });

test("a clip edited in the inspector is checked by the engine and saved to the file",
  async ({ page, engine }) => {
    const file = join(engine.showDir!, "timelines", "synth-128.json");
    const before = readFileSync(file, "utf8");
    await openTimeline(page, engine.url("studio"));
    const lanes = page.getByRole("region", { name: "lanes" });
    await lanes.getByLabel("fan-drop at bar 41.1").locator("rect").first().click();
    const inspector = page.getByRole("contentinfo", { name: "inspector" });
    await expect(inspector).toContainText("fan-drop");
    await inspector.getByRole("button", { name: "tight" }).click();
    await expect(lanes.getByText(/tight · color @primary/)).toBeVisible();

    // Save is offered as soon as there is a change, unless the engine's last
    // verdict had errors; the engine checks the file again as it saves.
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(page.getByRole("button", { name: "Saved" })).toBeDisabled();

    const saved = JSON.parse(readFileSync(file, "utf8"));
    const chorus = saved.rows.find((r: { id: string }) => r.id === "scene")
      .items.find((i: { id: string }) => i.id === "chorus1");
    expect(chorus.variation).toBe("tight");
    expect(readFileSync(file, "utf8")).not.toBe(before);

    // A reload reads it back from the engine, not from this page's memory.
    await page.reload();
    await expect(page.getByRole("region", { name: "lanes" }).getByText(/tight · color @primary/))
      .toBeVisible();
  });

test("the rig Studio drives plays the timeline on its screen, not the file as saved",
  async ({ page, browser, engine }) => {
    const sent: string[] = [];
    page.on("websocket", (ws) => ws.on("framesent", (f) => sent.push(String(f.payload))));
    const drafts = () => sent.filter((f) => f.includes('"type":"timeline_draft"')
      && f.includes('"variation":"tight"')).length;
    await openTimeline(page, engine.url("studio"));
    const lanes = page.getByRole("region", { name: "lanes" });
    await lanes.getByLabel("fan-drop at bar 41.1").locator("rect").first().click();
    await page.getByRole("contentinfo", { name: "inspector" })
      .getByRole("button", { name: "tight" }).click();
    // The edit's own check has gone to the engine before the page has the
    // rig, so it put nothing on stage and no other is on its way.
    await expect.poll(drafts).toBeGreaterThan(0);
    await page.getByRole("button", { name: "Drive the rig" }).click();

    const phone = await browser.newContext();
    await phone.addInitScript(() => localStorage.setItem("klights.guide.welcomed", "1"));
    const console_ = await phone.newPage();
    try {
      await console_.goto(engine.url());
      await expect(console_.locator(".banners"))
        .toContainText("is driving the rig on synth-128 (unsaved draft)");
    } finally {
      await phone.close();
    }
  });

test("Studio can drive the rig, every console is told, and a console can take it back",
  async ({ page, browser, engine }) => {
    await openTimeline(page, engine.url("studio"));
    await page.getByRole("button", { name: "Drive the rig" }).click();

    const phone = await browser.newContext();
    await phone.addInitScript(() => localStorage.setItem("klights.guide.welcomed", "1"));
    const console_ = await phone.newPage();
    try {
      await console_.goto(engine.url());
      const banner = console_.locator(".banners");
      await expect(banner).toContainText("is driving the rig on");
      await expect(banner).toContainText("synth-128");
      await banner.getByRole("button", { name: "Release" }).click();
      await expect(console_.getByText("is driving the rig")).toHaveCount(0);
      // And Studio hears it let go.
      await expect(page.getByRole("button", { name: "Drive the rig" })).toBeVisible();
    } finally {
      await phone.close();
    }
  });

test("Space plays and pauses wherever the focus is, and types in a text field",
  async ({ page, engine }) => {
    await openTimeline(page, engine.url("studio"));
    const play = page.getByRole("button", { name: "Play", exact: true });
    const pause = page.getByRole("button", { name: "Pause", exact: true });
    const owning = page.getByRole("button", { name: "owns track" });

    // A button really clicked keeps the focus, and a browser presses the
    // focused button on Space. Here it must not: the lane keeps its mode.
    await page.getByRole("button", { name: "fills gaps" }).first().click();
    const flipped = await owning.count();
    await page.keyboard.press("Space");
    await expect(pause).toBeVisible();
    await expect(owning).toHaveCount(flipped);

    // A menu does not open on it either.
    await page.getByLabel("zoom").focus();
    await page.keyboard.press("Space");
    await expect(play).toBeVisible();

    // In a field that takes text, it is a space.
    const address = page.getByLabel("vj-opacity address");
    await address.focus();
    await page.keyboard.press("End");
    await page.keyboard.press("Space");
    await expect(address).toHaveValue("/composition/layers/1/video/opacity ");
    await expect(play).toBeVisible();
  });
