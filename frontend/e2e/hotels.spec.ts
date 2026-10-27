import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page, request }) => {
  // Browser regression must not depend on external font downloads.
  await page.route(/^https:\/\/fonts\.(googleapis|gstatic)\.com\//, (route) =>
    route.abort(),
  );
  await request.post("/api/settings/local/clear", { data: {} });
});
test.afterEach(async ({ request }) => {
  await request.post("/api/settings/local/clear", { data: {} });
});

test("route-first hotel candidates support user selection, preview, persistence and cancellation", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "生成我的行程", exact: true }),
  ).toBeEnabled();
  await expect(page.getByLabel("住宿安排", { exact: true })).toHaveValue(
    "route_first",
  );
  await expect(
    page.getByLabel("住处 / 每日出发点", { exact: true }),
  ).toHaveCount(0);
  await page.getByLabel("旅行天数", { exact: true }).selectOption("3");
  await page.getByRole("button", { name: "生成我的行程", exact: true }).click();
  const hotels = page.getByRole("region", { name: "住宿候选", exact: true });
  await expect(hotels).toBeVisible();
  await expect(hotels.getByRole("tab")).toHaveCount(2);
  await expect(hotels.locator(".hotel-price").first()).toContainText(
    "位置参考",
  );
  await expect(hotels.locator(".hotel-source").first()).toContainText(
    "非真实酒店",
  );
  await expect(
    hotels.getByRole("button", { name: /^选择第1晚/ }).first(),
  ).toBeDisabled();
  await page.getByRole("button", { name: "保存演示行程", exact: true }).click();
  await expect(
    page.getByRole("button", { name: /版本记录.*v1/ }),
  ).toBeVisible();
  await hotels
    .getByRole("button", { name: /^选择第1晚/ })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "第 1 晚住宿调整 · 尚未保存" }),
  ).toBeVisible();
  await expect(hotels.locator(".hotel-option.chosen")).toHaveCount(1);
  await page.getByRole("button", { name: "保存演示行程", exact: true }).click();
  await expect(
    page.getByRole("button", { name: /版本记录.*v2/ }),
  ).toBeVisible();
  await page.reload();
  await expect(hotels.locator(".hotel-option.chosen")).toHaveCount(1);
  await hotels.getByRole("tab", { name: /^第 2 晚/ }).click();
  await hotels
    .getByRole("button", { name: /^选择第2晚/ })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "第 2 晚住宿调整 · 尚未保存" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "取消调整", exact: true }).click();
  await hotels.getByRole("tab", { name: /^第 1 晚/ }).click();
  await expect(hotels.locator(".hotel-option.chosen")).toHaveCount(1);
  await page.reload();
  await expect(hotels.locator(".hotel-option.chosen")).toHaveCount(1);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: `../artifacts/hotels-${test.info().project.name}.png`,
    fullPage: true,
  });
  await hotels.screenshot({
    path: `../artifacts/hotel-panel-${test.info().project.name}.png`,
  });
});

test("user can enter a previously booked hotel and preview the connecting routes", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "生成我的行程", exact: true }),
  ).toBeEnabled();
  await page.getByLabel("旅行天数", { exact: true }).selectOption("2");
  await page.getByRole("button", { name: "生成我的行程", exact: true }).click();
  await page.getByRole("button", { name: "保存演示行程", exact: true }).click();
  const hotels = page.getByRole("region", { name: "住宿候选", exact: true });
  await hotels
    .getByText("已经订好酒店？填写酒店名称和位置", { exact: true })
    .click();
  await page
    .getByLabel("第1晚已订酒店名称", { exact: true })
    .fill("我已经订好的酒店");
  await page.getByLabel("第1晚酒店经度", { exact: true }).fill("120.16");
  await page.getByLabel("第1晚酒店纬度", { exact: true }).fill("30.25");
  await page
    .getByRole("button", { name: "使用第 1 晚已订酒店", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "第 1 晚住宿调整 · 尚未保存" }),
  ).toBeVisible();
  await expect(hotels.locator(".hotel-option.chosen h4")).toHaveText(
    "我已经订好的酒店",
  );
  await expect(
    page.getByRole("button", { name: "保存演示行程", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "保存演示行程", exact: true }).click();
  await page.reload();
  await expect(hotels.locator(".hotel-option.chosen h4")).toHaveText(
    "我已经订好的酒店",
  );
});
