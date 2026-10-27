import { test, expect, type Page } from "@playwright/test";

test.beforeEach(async ({ page, request }) => {
  // Browser regression must not depend on external font downloads.
  await page.route(/^https:\/\/fonts\.(googleapis|gstatic)\.com\//, (route) =>
    route.abort(),
  );
  await request.post("/api/settings/local/clear", { data: {} });
  await request.post("/api/settings/local", {
    data: {
      tool_provider: "amap",
      planning_mode: "demo",
      amap_api_key: "e2e-never-sent-to-provider",
    },
  });
});
test.afterEach(async ({ request }) => {
  await request.post("/api/settings/local/clear", { data: {} });
});

async function offlineCandidates(page: Page) {
  const payloads: Record<string, unknown>[] = [];
  await page.route("**/api/places/recommendations", async (route) => {
    payloads.push(route.request().postDataJSON());
    const response = await route.fetch({
      headers: {
        ...route.request().headers(),
        "x-travel-config": encodeURIComponent(
          JSON.stringify({ tool_provider: "demo", planning_mode: "demo" }),
        ),
      },
    });
    const body = await response.json();
    body.places = body.places.map((p: Record<string, unknown>) => ({
      ...p,
      fixture: false,
      traits: {
        themes: [],
        indoor_outdoor: "unknown",
        duration_min: 45,
        duration_max: 120,
        audience_notes: [],
        unknowns: ["内部步行", "营业与预约"],
        evidence: [],
      },
    }));
    await route.fulfill({ json: body });
  });
  return payloads;
}

test("preferences drive discovery, refreshing preserves choices and stale candidates block generation", async ({
  page,
}) => {
  const payloads = await offlineCandidates(page);
  await page.goto("/");
  await page.getByLabel("和谁出行", { exact: true }).selectOption("parents");
  await page.getByLabel("步行偏好", { exact: true }).selectOption("less");
  await page.getByRole("button", { name: "历史文化", exact: true }).click();
  await page.getByLabel("偏好补充", { exact: true }).fill("喜欢历史，少走路");
  await page
    .getByRole("button", { name: "查询城市真实地点", exact: true })
    .click();
  await expect(
    page.getByText(
      "已按旅行需求更新候选，保留已选择的同城地点。可查看推荐理由与待核实信息。",
      { exact: true },
    ),
  ).toBeVisible();
  expect(payloads[0].profile).toMatchObject({
    companions: "parents",
    walking: "less",
    themes: ["history"],
  });
  const choice = page.locator(".place-options label:not(.checked)").first();
  const name = await choice.locator("strong").textContent();
  await choice.getByRole("checkbox").check();
  const keptCount = await page.locator(".place-options label.checked").count();
  await expect(choice).toContainText("估算");
  await expect(choice).toContainText("待核实");
  await page.getByRole("button", { name: "夜景", exact: true }).click();
  await expect(
    page.getByText("旅行需求已变化，请更新候选；已选必去地点会保留。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "生成我的行程", exact: true }),
  ).toBeDisabled();
  await page
    .getByRole("button", { name: "按新需求更新候选", exact: true })
    .click();
  await expect(
    page
      .locator(".place-options label")
      .filter({ hasText: name! })
      .getByRole("checkbox"),
  ).toBeChecked();
  await expect(
    page.getByRole("button", { name: "生成我的行程", exact: true }),
  ).toBeEnabled();
  expect(payloads[1].keep_ids).toHaveLength(keptCount);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});

test("a late discovery result cannot overwrite a newer trip brief", async ({
  page,
}) => {
  let release!: () => void;
  const pending = new Promise<void>((resolve) => {
    release = resolve;
  });
  let started!: () => void;
  const requestStarted = new Promise<void>((resolve) => {
    started = resolve;
  });
  await page.route("**/api/places/recommendations", async (route) => {
    started();
    await pending;
    await route.fulfill({
      json: { catalog_id: "stale-catalog", places: [], note: "stale-result" },
    });
  });
  await page.goto("/");
  await page
    .getByRole("button", { name: "查询城市真实地点", exact: true })
    .click();
  await requestStarted;
  await page.getByLabel("旅行天数", { exact: true }).selectOption("2");
  release();
  await expect(
    page.getByRole("button", { name: "查询城市真实地点", exact: true }),
  ).toBeEnabled();
  await expect(page.getByText("stale-result", { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "按新需求更新候选", exact: true }),
  ).toHaveCount(0);
});
