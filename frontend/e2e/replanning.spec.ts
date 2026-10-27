import { test, expect, type APIRequestContext } from "@playwright/test";

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

async function savedShortTrip(request: APIRequestContext) {
  const trip = await (
    await request.post("/api/trips", {
      data: {
        start_date: "2026-10-24",
        days: 3,
        day_end: "13:00",
        lodging_mode: "route_first",
      },
    })
  ).json();
  const started = await (
    await request.post(`/api/trips/${trip.id}/plan-runs`)
  ).json();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/runs/${started.run_id}`)).json())
          .status,
    )
    .toBe("completed");
  const run = await (await request.get(`/api/runs/${started.run_id}`)).json();
  const applied = await request.post(
    `/api/trips/${trip.id}/proposals/${run.proposal.id}/apply`,
    { data: { base_version: 0, idempotency_key: `save-${run.proposal.id}` } },
  );
  expect(applied.ok()).toBeTruthy();
  return { trip, proposal: run.proposal };
}

test("an overcrowded third day triggers coordination and preserves the requested date", async ({
  page,
  request,
}) => {
  const { trip, proposal } = await savedShortTrip(request);
  await page.addInitScript(
    (id) => localStorage.setItem("city-trip:last-trip", id),
    trip.id,
  );
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: /版本记录.*v1/ }),
  ).toBeVisible();
  await page.getByLabel("允许重新挑选普通推荐地点", { exact: true }).check();
  await page
    .getByLabel("想怎样调整？", { exact: true })
    .fill("把浙博 · 孤山馆区移到第3天");
  await page.getByRole("button", { name: "生成行程调整", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "自然语言行程调整 · 尚未保存" }),
  ).toBeVisible();
  await expect(page.locator(".revision-review")).toContainText("演示规则");
  await expect(page.locator(".revision-change-reasons")).toContainText(
    "从第 3 天移到第 2 天",
  );
  await expect(
    page.getByRole("button", { name: "保存演示行程", exact: true }),
  ).toBeEnabled();
  const before = await (await request.get(`/api/trips/${trip.id}`)).json();
  expect(before.current_version).toBe(1);
  await page.getByRole("button", { name: "保存演示行程", exact: true }).click();
  await expect(
    page.getByRole("button", { name: /版本记录.*v2/ }),
  ).toBeVisible();
  const saved = await (await request.get(`/api/trips/${trip.id}`)).json();
  expect(saved.requirements.place_days.gushan_museum).toBe(3);
  expect(saved.itinerary.days[0]).toEqual(proposal.itinerary.days[0]);
  expect(
    saved.itinerary.days.flatMap(
      (day: { activities: unknown[] }) => day.activities,
    ),
  ).toHaveLength(6);
  await page.reload();
  await expect(
    page.getByRole("button", { name: /版本记录.*v2/ }),
  ).toBeVisible();
  await page.locator(".revision-review").screenshot({
    path: `../artifacts/replanning-reasons-${test.info().project.name}.png`,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("local-only adjustments explain the conflict; full coordination can resolve it", async ({
  page,
  request,
}) => {
  const { trip } = await savedShortTrip(request);
  await page.addInitScript(
    (id) => localStorage.setItem("city-trip:last-trip", id),
    trip.id,
  );
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: /版本记录.*v1/ }),
  ).toBeVisible();
  await page.getByLabel("协调范围", { exact: true }).selectOption("local");
  await page
    .getByLabel("想怎样调整？", { exact: true })
    .fill("把浙博 · 孤山馆区移到第3天");
  await page.getByRole("button", { name: "生成行程调整", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "保存演示行程", exact: true }),
  ).toBeDisabled();
  await expect(
    page
      .getByRole("region", { name: "行程预览", exact: true })
      .getByRole("alert"),
  ).toContainText("暂时无法保存");
  await page.getByRole("button", { name: "取消调整", exact: true }).click();
  await page.getByLabel("协调范围", { exact: true }).selectOption("all_days");
  await page.getByRole("button", { name: "生成行程调整", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "保存演示行程", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByLabel("允许重新挑选普通推荐地点", { exact: true }),
  ).not.toBeChecked();
});
