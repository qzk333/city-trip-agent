import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page, request }) => {
  await page.route(/^https:\/\/fonts\.(googleapis|gstatic)\.com\//, (route) =>
    route.abort(),
  );
  await request.post("/api/settings/local/clear", { data: {} });
});

test("one theme field, confirmed-only history, reopen saved version and delete", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "生成我的行程" }),
  ).toBeEnabled();
  await expect(
    page.getByRole("group", { name: "感兴趣的体验", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "夜景", exact: true }).click();
  const createdRequest = page.waitForRequest(
    (r) => r.url().endsWith("/api/trips") && r.method() === "POST",
  );
  await page.getByRole("button", { name: "生成我的行程" }).click();
  const body = (await createdRequest).postDataJSON();
  expect(body.profile.themes).toContain("night");
  expect(body).not.toHaveProperty("interests");
  await page.getByRole("button", { name: "保存演示行程" }).click();
  await expect(
    page.getByRole("button", { name: /版本记录.*v1/ }),
  ).toBeVisible();
  const id = await page.evaluate(() =>
    localStorage.getItem("city-trip:last-trip"),
  );
  expect(id).toBeTruthy();
  await page
    .getByLabel("想怎样调整？", { exact: true })
    .fill("第1天晚出发10分钟");
  await page.getByRole("button", { name: "生成行程调整" }).click();
  await expect(
    page.getByRole("region", { name: "行程预览", exact: true }),
  ).toBeVisible();
  const draft = await (
    await request.post("/api/trips", { data: { start_date: "2026-10-26" } })
  ).json();
  await page.getByRole("button", { name: "新行程", exact: true }).click();
  await page.getByRole("button", { name: "历史行程", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "历史行程", exact: true });
  await expect(
    dialog.getByText("只展示你已确认保存的行程，未保存的草案不会出现在这里。"),
  ).toBeVisible();
  await expect(dialog.locator(`[data-trip-id="${draft.id}"]`)).toHaveCount(0);
  const card = dialog.locator(`[data-trip-id="${id}"]`);
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "打开行程", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(
    page.getByRole("region", { name: "行程预览", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /版本记录.*v1/ }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("button", { name: /版本记录.*v1/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "行程预览", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "历史行程", exact: true }).click();
  await dialog
    .locator(`[data-trip-id="${id}"]`)
    .getByRole("button", { name: "删除杭州行程" })
    .click();
  await expect(
    dialog.getByRole("group", { name: "确认删除行程" }),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "保留行程", exact: true }).click();
  expect((await request.get(`/api/trips/${id}`)).ok()).toBeTruthy();
  await dialog
    .locator(`[data-trip-id="${id}"]`)
    .getByRole("button", { name: "删除杭州行程" })
    .click();
  await dialog
    .getByRole("button", { name: "确认永久删除", exact: true })
    .click();
  await expect(dialog.locator(`[data-trip-id="${id}"]`)).toHaveCount(0);
  expect((await request.get(`/api/trips/${id}`)).status()).toBe(404);
  await page.getByRole("button", { name: "关闭历史行程" }).click();
  await expect(page.getByRole("button", { name: /版本记录/ })).toBeDisabled();
  expect(
    await page.evaluate(() => localStorage.getItem("city-trip:last-trip")),
  ).toBeNull();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});

test("history search, empty result and keyboard dismissal", async ({
  page,
}) => {
  await page.goto("/");
  const trigger = page.getByRole("button", { name: "历史行程", exact: true });
  await expect(trigger).toBeEnabled();
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "历史行程", exact: true });
  await dialog.getByLabel("按城市查找历史行程").fill("不存在的评测城市");
  await expect(
    dialog.getByRole("button", { name: "查找", exact: true }),
  ).toBeEnabled();
  await dialog.getByRole("button", { name: "查找", exact: true }).click();
  await expect(
    dialog.getByText("没有找到这个城市的已保存行程。"),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
});
