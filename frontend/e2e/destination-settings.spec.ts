import { test, expect } from "@playwright/test";
import type { Page } from "@playwright/test";

test.beforeEach(async ({ request }) => {
  const result = await request.post("/api/settings/local/clear", { data: {} });
  expect(result.ok()).toBeTruthy();
});
test.afterEach(async ({ request }) => {
  await request.post("/api/settings/local/clear", { data: {} });
});

async function openModelTestSettings(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "API 设置", exact: true }).click();
  await page.getByLabel("规划模式", { exact: true }).selectOption("llm");
  await page
    .getByLabel("模型服务地址", { exact: true })
    .fill("https://model.example/v1");
  await page.getByLabel("模型名称", { exact: true }).fill("example-model");
  await page
    .getByLabel("模型 API Key", { exact: true })
    .fill("test-fixture-key");
}

test("Guangdong lists 21 Chinese cities and selecting Foshan updates the destination", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByLabel("省份 / 州 / 行政区", { exact: true }),
  ).toBeEnabled();
  await page
    .getByLabel("省份 / 州 / 行政区", { exact: true })
    .selectOption("CN-GD");
  const city = page.getByLabel("城市", { exact: true });
  await expect(city).toBeEnabled();
  await expect(city.locator("option")).toHaveCount(22);
  const labels = await city.locator("option").allTextContents();
  expect(labels).toEqual(
    expect.arrayContaining([
      "广州",
      "深圳",
      "佛山",
      "珠海",
      "汕头",
      "东莞",
      "中山",
      "潮州",
    ]),
  );
  expect(labels.join(" ")).not.toMatch(/[a-z]/i);
  await city.selectOption({ label: "佛山" });
  await page.getByLabel("住宿安排", { exact: true }).selectOption("fixed");
  await expect(page.getByLabel("住处 / 每日出发点")).toHaveValue(
    "参考起点 · 佛山",
  );
});

test("model test gives inline validation, progress, success and retry failure feedback", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "API 设置", exact: true }).click();
  await page.getByRole("button", { name: "测试模型连接", exact: true }).click();
  await expect(page.locator("#model-test-result")).toContainText(
    "请先将规划模式切换",
  );
  await page.getByLabel("规划模式", { exact: true }).selectOption("llm");
  await page.getByRole("button", { name: "测试模型连接", exact: true }).click();
  await expect(page.locator("#model-test-result")).toContainText(
    "请填写模型服务地址",
  );
  await page
    .getByLabel("模型服务地址", { exact: true })
    .fill("https://model.example/v1");
  await page.getByLabel("模型名称", { exact: true }).fill("example-model");
  await page
    .getByLabel("模型 API Key", { exact: true })
    .fill("test-fixture-key");
  // An unfinished map configuration must not block a model-only connection test.
  await page.getByLabel("地点与交通数据", { exact: true }).selectOption("amap");
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  let attempts = 0;
  await page.route("**/api/settings/test", async (route) => {
    const config = JSON.parse(
      decodeURIComponent(route.request().headers()["x-travel-config"]),
    );
    expect(config.tool_provider).toBe("demo");
    attempts++;
    if (attempts === 1) {
      await pending;
      await route.fulfill({
        json: { ok: true, message: "模型已响应；连接成功。" },
      });
    } else {
      await route.fulfill({
        status: 400,
        json: { detail: "模型认证失败，请检查自己的 Key 和访问权限" },
      });
    }
  });
  await page.getByRole("button", { name: "测试模型连接", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "正在测试模型…", exact: true }),
  ).toBeDisabled();
  await expect(page.locator("#model-test-result")).toContainText(
    "正在连接服务",
  );
  release();
  await expect(page.locator("#model-test-result")).toContainText("模型已响应");
  await expect(page.locator("#model-test-result")).toBeInViewport();
  await page.getByRole("button", { name: "测试模型连接", exact: true }).click();
  await expect(page.locator("#model-test-result")).toHaveAttribute(
    "role",
    "alert",
  );
  await expect(page.locator("#model-test-result")).toContainText(
    "模型认证失败",
  );
  await expect(
    page.getByRole("button", { name: "测试模型连接", exact: true }),
  ).toBeEnabled();
});

test("a stalled connection test times out visibly and enables retry", async ({
  page,
}) => {
  test.setTimeout(35_000);
  await openModelTestSettings(page);
  await page.route("**/api/settings/test", () => {
    /* Leave the request pending to exercise the real browser timeout. */
  });
  await page.getByRole("button", { name: "测试模型连接", exact: true }).click();
  await expect(page.locator("#model-test-result")).toContainText(
    "连接测试超时（20 秒）",
    { timeout: 25_000 },
  );
  await expect(
    page.getByRole("button", { name: "测试模型连接", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "关闭 API 设置", exact: true }),
  ).toBeEnabled();
});

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
  await page.getByLabel("住宿安排", { exact: true }).selectOption("fixed");
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
  await page.getByLabel("住宿安排", { exact: true }).selectOption("fixed");
  await expect(page.getByLabel("住处 / 每日出发点")).toHaveValue(
    "参考起点 · 东京",
  );
  await page.getByRole("button", { name: "生成我的行程" }).click();
  await expect(
    page.getByText("东京 · 城市轻旅行", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("region", { name: "行程预览" })).toBeVisible();
});

test("project file settings survive reload, remain masked, and can be cleared", async ({
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
  expect(
    await page.evaluate(() => JSON.stringify([localStorage, sessionStorage])),
  ).not.toContain("browser-only-fixture-key");
  await expect(
    page.getByLabel("高德 Web 服务 Key", { exact: true }),
  ).toHaveValue("browser-only-map-fixture");
  await page
    .getByRole("button", { name: "清除已保存配置", exact: true })
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

test("project config restores in a clean browser without browser credential storage", async ({
  page,
  browser,
}) => {
  await openModelTestSettings(page);
  await page
    .getByRole("button", { name: "保存 API 设置", exact: true })
    .click();
  await expect(page.getByRole("dialog", { name: "我的 API 设置" })).toHaveCount(
    0,
  );
  const fresh = await browser.newPage();
  try {
    await fresh.goto(page.url());
    await fresh.getByRole("button", { name: "API 设置", exact: true }).click();
    await expect(fresh.getByLabel("模型 API Key", { exact: true })).toHaveValue(
      "test-fixture-key",
    );
    await expect(
      fresh.getByLabel("模型 API Key", { exact: true }),
    ).toHaveAttribute("type", "password");
    expect(
      await fresh.evaluate(() => [
        localStorage.getItem("city-trip:user-api-config:v1"),
        sessionStorage.getItem("city-trip:user-api-config:v1"),
      ]),
    ).toEqual([null, null]);
    await fresh
      .getByRole("button", { name: "清除已保存配置", exact: true })
      .click();
    await expect(
      fresh.getByRole("dialog", { name: "我的 API 设置" }),
    ).toHaveCount(0);
    await page.reload();
    await page.getByRole("button", { name: "API 设置", exact: true }).click();
    await expect(page.getByLabel("模型 API Key", { exact: true })).toHaveValue(
      "",
    );
  } finally {
    await fresh.close();
  }
});

test("legacy browser keys migrate to the project file and are then removed from browser storage", async ({
  page,
  request,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "API 设置", exact: true }),
  ).toBeEnabled();
  await page.evaluate(() =>
    sessionStorage.setItem(
      "city-trip:user-api-config:v1",
      JSON.stringify({
        planning_mode: "llm",
        tool_provider: "demo",
        llm_base_url: "https://model.example/v1",
        llm_model: "example-model",
        llm_api_key: "migration-fixture-key",
      }),
    ),
  );
  await page.reload();
  await page.getByRole("button", { name: "API 设置", exact: true }).click();
  await expect(page.getByLabel("模型 API Key", { exact: true })).toHaveValue(
    "migration-fixture-key",
  );
  expect(
    await page.evaluate(() => [
      localStorage.getItem("city-trip:user-api-config:v1"),
      sessionStorage.getItem("city-trip:user-api-config:v1"),
    ]),
  ).toEqual([null, null]);
  const saved = await request.get("/api/settings/local");
  expect((await saved.json()).config.llm_api_key).toBe("migration-fixture-key");
});

test("failed legacy migration retains the original browser key for retry", async ({
  page,
}) => {
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "API 设置", exact: true }),
  ).toBeEnabled();
  await page.evaluate(() =>
    localStorage.setItem(
      "city-trip:user-api-config:v1",
      JSON.stringify({ llm_api_key: "retained-fixture-key" }),
    ),
  );
  await page.route("**/api/settings/local", async (route) => {
    if (route.request().method() === "POST")
      await route.fulfill({
        status: 500,
        json: { detail: "配置写入失败，请重试" },
      });
    else await route.continue();
  });
  await page.reload();
  await expect(
    page.getByText("配置写入失败，请重试", { exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(() =>
      localStorage.getItem("city-trip:user-api-config:v1"),
    ),
  ).toContain("retained-fixture-key");
  await page.getByRole("button", { name: "API 设置", exact: true }).click();
  await expect(page.getByLabel("模型 API Key", { exact: true })).toHaveValue(
    "retained-fixture-key",
  );
});
