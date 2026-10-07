/**
 * The same console on a phone and on a laptop: where it starts, what each
 * shows, and that neither scrolls sideways. The defaults are the point --
 * getting them right is what stops the Perform/Design toggle being something
 * every operator has to find (ui/src/mode.tsx).
 */
import { devices, type Page } from "@playwright/test";
import { expect, test } from "./fixtures";

async function noSidewaysScroll(page: Page): Promise<void> {
  const [scroll, width] = await page.evaluate(
    () => [document.documentElement.scrollWidth, window.innerWidth]);
  expect(scroll, "the page scrolls sideways").toBeLessThanOrEqual(width);
}

// A phone's screen, pointer and agent, in the configured Chromium (a device's
// own browser type cannot be switched inside a describe).
const { viewport, userAgent, deviceScaleFactor, isMobile, hasTouch } = devices["Pixel 7"];

test.describe("on a phone", () => {
  test.use({ viewport, userAgent, deviceScaleFactor, isMobile, hasTouch });

  test("starts in Perform: the show tabs, no Setup, no Studio", async ({ page, openConsole }) => {
    await openConsole(page);
    const bar = page.locator(".tabbar");
    for (const tab of ["Show", "Color", "Move", "Bright"]) {
      await expect(bar.getByRole("button", { name: tab })).toBeVisible();
    }
    await expect(bar.getByRole("button", { name: "Setup" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: /Studio/ })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Perform" })).toBeVisible();
    await noSidewaysScroll(page);
  });

  test("Design is one tap away, and the phone remembers it", async ({ page, openConsole }) => {
    await openConsole(page);
    await page.getByRole("button", { name: "Perform" }).click();
    await expect(page.locator(".tabbar").getByRole("button", { name: "Setup" })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("button", { name: "Design" })).toBeVisible();
    await expect(page.locator(".tabbar").getByRole("button", { name: "Setup" })).toBeVisible();
  });

  test("every tab renders without sideways scroll", async ({ page, openConsole }) => {
    await openConsole(page);
    for (const tab of ["Show", "Color", "Move", "Bright"]) {
      await page.locator(".tabbar").getByRole("button", { name: tab }).click();
      await expect(page.locator(".tabbar").getByRole("button", { name: tab })).toHaveClass(/on/);
      await noSidewaysScroll(page);
    }
  });
});

test.describe("on a laptop", () => {
  test("starts in Design, with Setup and the Studio link", async ({ page, openConsole }) => {
    await openConsole(page);
    await expect(page.locator(".tabbar").getByRole("button", { name: "Setup" })).toBeVisible();
    await expect(page.getByRole("link", { name: /Studio/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "Design" })).toBeVisible();
    await page.locator(".tabbar").getByRole("button", { name: "Setup" }).click();
    await expect(page.locator(".presence")).toBeVisible();
    await noSidewaysScroll(page);
  });

  test("a tab survives a reload, because it lives in the address", async ({ page, openConsole }) => {
    await openConsole(page);
    await page.locator(".tabbar").getByRole("button", { name: "Bright" }).click();
    await expect(page).toHaveURL(/#bright$/);
    await page.reload();
    await expect(page.locator(".tabbar").getByRole("button", { name: "Bright" })).toHaveClass(/on/);
  });
});

test.describe("on a first visit", () => {
  test.use({ welcomed: false });

  test("the console offers a tour once, and the guide explains the tab you are on",
    async ({ page, openConsole }) => {
      await openConsole(page);
      await expect(page.getByText("New here?")).toBeVisible();
      await page.getByRole("button", { name: "Not now" }).click();
      await expect(page.getByText("New here?")).toHaveCount(0);
      await page.reload();
      await expect(page.locator(".status-dot.open")).toBeVisible();
      await expect(page.getByText("New here?")).toHaveCount(0);

      await page.getByRole("button", { name: "Guide" }).click();
      await expect(page).toHaveURL(/#guide\/show$/);
      await page.getByRole("button", { name: "Guide" }).click();
      await expect(page).toHaveURL(/#show$/);
    });
});
