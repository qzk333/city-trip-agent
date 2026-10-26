import { test, expect } from "@playwright/test";

test.beforeEach(async ({ request }) => {
  const result = await request.post("/api/settings/local/clear", { data: {} });
  expect(result.ok()).toBeTruthy();
});
test.afterEach(async ({ request }) => {
  await request.post("/api/settings/local/clear", { data: {} });
});

test("generate, confirm, replan, reload and restore a fixed appointment", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "生成我的行程" }),
  ).toBeEnabled();
  await page.getByLabel("保留一项固定预约").check();
  await page.getByRole("button", { name: "生成我的行程" }).click();
  await expect(page.getByRole("region", { name: "行程预览" })).toBeVisible();
  await expect(page.getByText("14:00 — 15:30", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "保存演示行程" }).click();
  await expect(
    page.getByText("行程已保存，刷新页面也可以继续查看。"),
  ).toBeVisible();
  await page
    .getByLabel("想怎样调整？", { exact: true })
    .fill("第1天晚出发40分钟");
  await page.getByRole("button", { name: "生成行程调整" }).click();
  await expect(
    page.getByRole("heading", { name: "自然语言行程调整 · 尚未保存" }),
  ).toBeVisible();
  await expect(page.getByText("09:40 出发", { exact: true })).toBeVisible();
  await expect(page.getByText("14:00 — 15:30", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "保存演示行程" }).click();
  await expect(
    page.getByRole("button", { name: /版本记录.*v2/ }),
  ).toBeVisible();
  await page.reload();
  await expect(page.getByText("14:00 — 15:30", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: /版本记录.*v2/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: /版本记录/ }).click();
  await page.getByRole("button", { name: "恢复", exact: true }).click();
  await expect(page.getByText("已将历史行程恢复为新版本。")).toBeVisible();
  await expect(
    page.getByRole("button", { name: /版本记录.*v3/ }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});

test("an impossible time window cannot be saved as a valid itinerary", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "生成我的行程" }),
  ).toBeEnabled();
  await page.getByLabel("当天结束", { exact: true }).fill("09:10");
  await page.getByRole("button", { name: "生成我的行程" }).click();
  await expect(
    page.getByText("暂未找到满足条件的方案", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "保存演示行程" }),
  ).toBeDisabled();
  const preview = page.getByRole("region", { name: "行程预览", exact: true });
  await expect(preview.getByRole("alert")).toContainText("暂时无法保存");
  await expect(preview.getByRole("alert")).toContainText("未找到可用安排");
  await preview
    .getByRole("button", { name: "查看完整行程检查 ↓", exact: true })
    .click();
  await expect(page.locator("#itinerary-checks")).toBeInViewport();
});

test("cancelled changes stay cancelled after reload", async ({ page }) => {
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "生成我的行程" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "生成我的行程" }).click();
  await page.getByRole("button", { name: "保存演示行程" }).click();
  await expect(page.getByLabel("想怎样调整？", { exact: true })).toBeEnabled();
  await page
    .getByLabel("想怎样调整？", { exact: true })
    .fill("第1天晚出发40分钟");
  await page.getByRole("button", { name: "生成行程调整" }).click();
  await page.getByRole("button", { name: "取消调整" }).click();
  await expect(page.getByRole("region", { name: "行程预览" })).toHaveCount(0);
  await page.reload();
  await expect(page.getByLabel("想怎样调整？", { exact: true })).toBeEnabled();
  await expect(page.getByRole("region", { name: "行程预览" })).toHaveCount(0);
});
