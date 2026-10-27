import { test, expect, type Page } from "@playwright/test";

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

async function enterBoundary(
  page: Page,
  label: "抵达" | "返程",
  name: string,
  at: string,
) {
  await page.getByLabel(`填写${label}地点与时间`, { exact: true }).check();
  const editor = page
    .locator(".boundary-editor")
    .filter({ has: page.getByLabel(`${label}时间`, { exact: true }) });
  await editor.getByText("手动确认名称与位置坐标", { exact: true }).click();
  await page.getByLabel(`${label}地点名称`, { exact: true }).fill(name);
  await page.getByLabel(`${label}时间`, { exact: true }).fill(at);
}

test("arrival and departure constrain the route; natural edits preview, save and reload", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "生成我的行程", exact: true }),
  ).toBeEnabled();
  await page.getByLabel("旅行天数", { exact: true }).selectOption("3");
  await enterBoundary(page, "抵达", "我的抵达车站", "11:00");
  await enterBoundary(page, "返程", "我的返程机场", "16:00");
  await page.getByRole("button", { name: "生成我的行程", exact: true }).click();
  await expect(page.locator(".timeline .hotel-marker").first()).toContainText(
    "我的抵达车站",
  );
  await expect(
    page.getByRole("button", { name: "保存演示行程", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "保存演示行程", exact: true }).click();
  await page
    .getByLabel("想怎样调整？", { exact: true })
    .fill("第2天晚出发20分钟");
  await page.getByRole("button", { name: "生成行程调整", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "自然语言行程调整 · 尚未保存" }),
  ).toBeVisible();
  await expect(page.locator(".revision-review")).toContainText(
    "第2天晚出发20分钟",
  );
  await page.getByRole("button", { name: "保存演示行程", exact: true }).click();
  await page.reload();
  await expect(
    page.getByRole("button", { name: /版本记录.*v2/ }),
  ).toBeVisible();
  await expect(page.getByLabel("抵达地点名称", { exact: true })).toHaveValue(
    "我的抵达车站",
  );
  await page.getByRole("button", { name: /DAY 03/ }).click();
  await expect(page.locator(".timeline")).toContainText("我的返程机场");
  await expect(page.locator(".timeline")).toContainText("16:00");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: `../artifacts/boundaries-revisions-${test.info().project.name}.png`,
    fullPage: true,
  });
  await page.locator(".timeline").screenshot({
    path: `../artifacts/boundary-timeline-${test.info().project.name}.png`,
  });
});

test("late arrival and early flight use transfer-only days and hotel anchors", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "生成我的行程", exact: true }),
  ).toBeEnabled();
  await page.getByLabel("旅行天数", { exact: true }).selectOption("3");
  await enterBoundary(page, "抵达", "晚到车站", "20:30");
  await enterBoundary(page, "返程", "早班机场", "08:00");
  await page.getByRole("button", { name: "生成我的行程", exact: true }).click();
  await expect(page.locator(".boundary-day")).toContainText("抵离接驳");
  await page.getByRole("button", { name: "保存演示行程", exact: true }).click();
  const hotels = page.getByRole("region", { name: "住宿候选", exact: true });
  await expect(hotels).toContainText("晚到车站");
  await hotels
    .getByRole("button", { name: /^选择第1晚/ })
    .first()
    .click();
  await expect(page.locator(".revision-review")).toContainText("演示模式");
  await page.getByRole("button", { name: "保存演示行程", exact: true }).click();
  await hotels.getByRole("tab", { name: /^第 2 晚/ }).click();
  await expect(hotels).toContainText("早班机场");
  await hotels
    .getByRole("button", { name: /^选择第2晚/ })
    .first()
    .click();
  await page.getByRole("button", { name: "保存演示行程", exact: true }).click();
  await page.getByRole("button", { name: /DAY 03/ }).click();
  await expect(page.locator(".boundary-day")).toBeVisible();
  await expect(page.locator(".timeline")).toContainText("早班机场");
  await expect(page.locator(".timeline .route-note")).toHaveCount(1);
  await page.screenshot({
    path: `../artifacts/boundary-transfers-${test.info().project.name}.png`,
    fullPage: true,
  });
  await page.locator(".timeline").screenshot({
    path: `../artifacts/transfer-timeline-${test.info().project.name}.png`,
  });
});
