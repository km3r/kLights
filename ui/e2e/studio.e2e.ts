/**
 * Studio against a real engine and a real show folder (a copy of
 * shared/show-example): what the unit suite proves against a mock socket --
 * drafts checked by the engine, saves that quote the rev they read -- proved
 * here with the engine doing the checking and the file on disk as the answer.
 */
import { readFileSync } from "node:fs";
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

    // Save is gated on the engine's verdict on the draft, so it enabling at
    // all means the real engine has checked this timeline.
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
