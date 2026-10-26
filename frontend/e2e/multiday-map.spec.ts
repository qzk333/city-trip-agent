import { test, expect } from "@playwright/test";

test.beforeEach(async ({ request }) => {
  await request.post("/api/settings/local/clear", { data: {} });
});
test.afterEach(async ({ request }) => {
  await request.post("/api/settings/local/clear", { data: {} });
});

test("three requested days have activities and transport is suggested per leg", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "生成我的行程", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "公共交通", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "全程步行", exact: true }),
  ).toHaveCount(0);
  await page.getByLabel("旅行天数", { exact: true }).selectOption("3");
  await page.getByRole("button", { name: "生成我的行程", exact: true }).click();
  await expect(page.getByRole("region", { name: "行程预览" })).toBeVisible();
  await expect(page.locator(".day-tabs button")).toHaveCount(3);
  for (let i = 0; i < 3; i++) {
    await page.locator(".day-tabs button").nth(i).click();
    expect(await page.locator(".activity-card").count()).toBeGreaterThan(0);
    await expect(page.locator(".empty-small")).toHaveCount(0);
    await expect(page.locator(".leg-recommendation").first()).toBeVisible();
  }
  await expect(
    page.getByRole("button", { name: "保存演示行程", exact: true }),
  ).toBeEnabled();
});

test("map expands into a larger viewer and closes with button or Escape", async ({
  page,
}) => {
  await page.goto("/");
  const original = await page.locator(".map-frame").boundingBox();
  await page.getByRole("button", { name: "放大地图", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: /地图放大查看/ });
  await expect(dialog).toBeVisible();
  const expanded = await dialog.locator(".map-frame").boundingBox();
  expect(expanded!.height).toBeGreaterThan(original!.height * 1.6);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await page.getByRole("button", { name: "放大地图", exact: true }).click();
  await page.getByRole("button", { name: "关闭放大地图", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});
