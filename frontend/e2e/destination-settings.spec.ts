import { test, expect } from "@playwright/test";

test("destination cascading clears old places and persists the selected city", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByLabel("省份 / 州 / 行政区", { exact: true }),
  ).toBeEnabled();
  await page
    .getByLabel("省份 / 州 / 行政区", { exact: true })
    .selectOption("CN-SH");
  await expect(page.getByLabel("城市", { exact: true })).toBeEnabled();
  await page
    .getByLabel("城市", { exact: true })
    .selectOption({ label: "上海" });
  await expect(page.getByLabel("住处 / 每日出发点")).toHaveValue(
    "参考起点 · 上海",
  );
  await expect(page.getByText("西湖 · 湖滨", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "生成我的行程" }).click();
  await expect(page.getByRole("region", { name: "行程预览" })).toBeVisible();
  await expect(
    page.getByText("上海 · 城市轻旅行", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "保存演示行程" }).click();
  await expect(
    page.getByText("行程已保存，刷新页面也可以继续查看。"),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByText("上海 · 城市轻旅行", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByLabel("省份 / 州 / 行政区", { exact: true }),
  ).toHaveValue("CN-SH");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});

test("country and province cascade supports a foreign city without reusing Hangzhou", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.getByLabel("国家 / 地区", { exact: true })).toBeEnabled();
  await page.getByLabel("国家 / 地区", { exact: true }).selectOption("JP");
  await expect(
    page.getByLabel("省份 / 州 / 行政区", { exact: true }),
  ).toBeEnabled();
  await page
    .getByLabel("省份 / 州 / 行政区", { exact: true })
    .selectOption("JP-13");
  await expect(page.getByLabel("城市", { exact: true })).toBeEnabled();
  await page
    .getByLabel("城市", { exact: true })
    .selectOption({ label: "东京" });
  await expect(page.getByLabel("住处 / 每日出发点")).toHaveValue(
    "参考起点 · 东京",
  );
  await page.getByRole("button", { name: "生成我的行程" }).click();
  await expect(
    page.getByText("东京 · 城市轻旅行", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "行程预览" })).toBeVisible();
});

test("BYOK settings survive reload in one tab, remain masked, and can be cleared", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "API 设置", exact: true }).click();
  await page.getByLabel("规划模式", { exact: true }).selectOption("llm");
  await page
    .getByLabel("模型服务地址", { exact: true })
    .fill("https://public.example/v1");
  await page.getByLabel("模型名称", { exact: true }).fill("example-model");
  await page
    .getByLabel("模型 API Key", { exact: true })
    .fill("browser-only-fixture-key");
  await page
    .getByLabel("高德 Web 服务 Key", { exact: true })
    .fill("browser-only-map-fixture");
  await expect(
    page.getByLabel("模型 API Key", { exact: true }),
  ).toHaveAttribute("type", "password");
  await page
    .getByRole("button", { name: "保存 API 设置", exact: true })
    .click();
  await expect(page.getByRole("dialog", { name: "我的 API 设置" })).toHaveCount(
    0,
  );
  await page.reload();
  await page.getByRole("button", { name: "API 设置", exact: true }).click();
  await expect(page.getByLabel("模型 API Key", { exact: true })).toHaveValue(
    "browser-only-fixture-key",
  );
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain(
    "browser-only-fixture-key",
  );
  await expect(
    page.getByLabel("高德 Web 服务 Key", { exact: true }),
  ).toHaveValue("browser-only-map-fixture");
  await page
    .getByRole("button", { name: "清除本标签页配置", exact: true })
    .click();
  await expect(page.getByRole("dialog", { name: "我的 API 设置" })).toHaveCount(
    0,
  );
  await page.getByRole("button", { name: "API 设置", exact: true }).click();
  await expect(page.getByLabel("模型 API Key", { exact: true })).toHaveValue(
    "",
  );
  await expect(page.getByLabel("规划模式", { exact: true })).toHaveValue(
    "demo",
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});
