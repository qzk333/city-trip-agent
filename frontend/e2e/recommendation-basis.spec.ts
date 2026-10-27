import {
  test,
  expect,
  type APIRequestContext,
  type Page,
} from "@playwright/test";

test.beforeEach(async ({ page, request }) => {
  await page.route(/^https:\/\/fonts\.(googleapis|gstatic)\.com\//, (route) =>
    route.abort(),
  );
  await request.post("/api/settings/local/clear", { data: {} });
});
test.afterEach(async ({ request }) => {
  await request.post("/api/settings/local/clear", { data: {} });
});

async function savedTrip(request: APIRequestContext) {
  const trip = await (
    await request.post("/api/trips", {
      data: { start_date: "2026-10-26", lodging_mode: "route_first" },
    })
  ).json();
  const run = await (
    await request.post(`/api/trips/${trip.id}/plan-runs`, { data: {} })
  ).json();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/api/runs/${run.run_id}`)).json()).status,
    )
    .toBe("completed");
  const result = await (await request.get(`/api/runs/${run.run_id}`)).json();
  expect(
    (
      await request.post(
        `/api/trips/${trip.id}/proposals/${result.proposal.id}/apply`,
        {
          data: {
            base_version: 0,
            idempotency_key: `basis-${result.proposal.id}`,
          },
        },
      )
    ).ok(),
  ).toBeTruthy();
  return (await request.get(`/api/trips/${trip.id}`)).json();
}

async function openSaved(page: Page, id: string) {
  await page.addInitScript(
    (value) => localStorage.setItem("city-trip:last-trip", value),
    id,
  );
  await page.goto("/");
}

test("timed model planning shows evening slots, meals and dated weather rationale", async ({
  page,
  request,
}) => {
  const trip = await savedTrip(request);
  await page.route(`**/api/trips/${trip.id}`, async (route) => {
    const body = await (await route.fetch()).json();
    const day = body.itinerary.days[0];
    day.activities[day.activities.length - 1].start = "19:00";
    day.activities[day.activities.length - 1].end = "20:00";
    day.weather_note =
      "当日白天预报小雨，文化活动优先；晚间多云，保留夜景时段，开放情况待核实。";
    day.breaks = [
      { start: "12:00", end: "13:00", kind: "meal", reason: "同区午餐与休息" },
    ];
    body.itinerary.weather.data.days = [
      {
        date: day.date,
        dayweather: "小雨",
        nightweather: "多云",
        daytemp: "25",
        nighttemp: "20",
        reporttime: "2026-10-26 18:00:00",
      },
    ];
    body.itinerary.planning_decision = {
      source: "model_schedule",
      model_calls: 4,
      review_rounds: 1,
      weather_provided: true,
      notes: ["模型已收到逐日天气，并在交通检查后复核一次具体时间表。"],
      recommendations: [],
    };
    await route.fulfill({ json: body });
  });
  await openSaved(page, trip.id);
  await expect(
    page.getByText("本次由模型安排地点与具体时间", { exact: true }),
  ).toBeVisible();
  const arrangements = page.getByRole("region", {
    name: "当日天气与休息安排",
    exact: true,
  });
  await expect(
    arrangements.getByText("天气安排依据", { exact: true }),
  ).toBeVisible();
  await expect(arrangements).toContainText("当日白天预报小雨");
  await expect(arrangements).toContainText("12:00 — 13:00");
  await expect(arrangements).toContainText("同区午餐与休息");
  await expect(page.getByText("19:00 — 20:00", { exact: true })).toBeVisible();
  await expect(
    page.getByText("白天小雨 · 夜间多云", { exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(arrangements).toContainText("同区午餐与休息");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
});

test("itinerary shows specific model rationale and distinguishes model decisions from map evidence", async ({
  page,
  request,
}) => {
  const trip = await savedTrip(request);
  const pid = trip.itinerary.days[0].activities[0].place_id;
  const name = trip.places.find((p: { id: string }) => p.id === pid).name;
  await page.route(`**/api/trips/${trip.id}`, async (route) => {
    const body = await (await route.fetch()).json();
    body.itinerary.planning_decision = {
      source: "model_days",
      model_calls: 2,
      notes: ["模型负责取舍与分天；程序核算交通、时间和硬条件。"],
      recommendations: [
        {
          place_id: pid,
          role: "landmark",
          reason: "以自然景观作为本次城市体验的开场，与后续文化活动形成区别。",
          source_ids: [],
          tradeoff: "夜间开放与内部步行尚未核实。",
          basis: "model",
        },
      ],
    };
    const scheduled = new Set(
      body.itinerary.days.flatMap((d: { activities: { place_id: string }[] }) =>
        d.activities.map((a) => a.place_id),
      ),
    );
    let alternative = body.places.find(
      (p: { id: string }) => !scheduled.has(p.id),
    );
    if (!alternative) {
      alternative = {
        ...body.places[0],
        id: "demo-basis-alternative",
        name: "未排入的演示备选",
      };
      body.places.push(alternative);
    }
    body.itinerary.planning_decision.recommendations.push({
      place_id: alternative.id,
      role: "alternative",
      reason: "因与当天路线偏离，保留为备选。",
      source_ids: [],
      tradeoff: "可在调整偏好后再比较。",
      basis: "model",
    });
    await route.fulfill({ json: body });
  });
  await openSaved(page, trip.id);
  await expect(
    page.getByText("本次由模型选点与分天", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("主规划模型请求 2 次", { exact: true }),
  ).toBeVisible();
  const basis = page.getByRole("region", {
    name: `${name}推荐依据`,
    exact: true,
  });
  await expect(basis).toContainText("以自然景观作为本次城市体验的开场");
  await expect(basis).toContainText("模型判断");
  await expect(basis).toContainText("内部步行尚未核实");
  await page.getByText("未排入的候选与取舍（1）", { exact: true }).click();
  await expect(
    page.getByText("因与当天路线偏离，保留为备选。", { exact: true }),
  ).toBeVisible();

  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("legacy plans do not invent model execution or explanations", async ({
  page,
  request,
}) => {
  const trip = await savedTrip(request);
  await page.route(`**/api/trips/${trip.id}`, async (route) => {
    const body = await (await route.fetch()).json();
    delete body.itinerary.planning_decision;
    for (const place of body.places) delete place.candidate_match;
    await route.fulfill({ json: body });
  });
  await openSaved(page, trip.id);
  await expect(
    page
      .getByText("旧版本未记录具体推荐理由，重新生成预览后可查看。", {
        exact: true,
      })
      .first(),
  ).toBeVisible();
  await expect(
    page.getByText("本次由模型选点与分天", { exact: true }),
  ).toHaveCount(0);
});

test("afternoon night claim is qualified; model environment inference and grouped checks remain clear", async ({
  page,
  request,
}) => {
  const trip = await savedTrip(request);
  await page.route(`**/api/trips/${trip.id}`, async (route) => {
    const body = await (await route.fetch()).json();
    const activity = body.itinerary.days[0].activities[0];
    activity.start = "16:18";
    activity.end = "17:33";
    activity.reason = "作为傍晚入夜收尾，落实夜景兴趣";
    const place = body.places.find(
      (p: { id: string }) => p.id === activity.place_id,
    );
    place.fixture = false;
    place.traits = { ...(place.traits || {}), indoor_outdoor: "unknown" };
    body.itinerary.status = "NO_SOLUTION_WITHIN_LIMIT";
    body.itinerary.planning_decision = {
      source: "model_schedule",
      model_calls: 4,
      notes: [],
      recommendations: [
        {
          place_id: activity.place_id,
          reason: "适合在傍晚至入夜看海景，落实夜间观景需求。",
          role: "interest",
          source_ids: [],
          tradeoff: "夜间开放未知",
          basis: "model",
        },
      ],
      place_assessments: [
        {
          place_id: activity.place_id,
          indoor_outdoor: "outdoor",
          confidence: "high",
          reason: "地图分类和地点名称表明主要为露天步道",
          basis: "model",
        },
      ],
      quality_review: {
        status: "changes_requested",
        summary: "审核发现夜景推荐理由与实际时段不一致。",
        days: [
          {
            day: activity.day,
            evening: "missing",
            pace: "appropriate",
            explanation: "时段不符合这次夜景目标。",
          },
        ],
        issues: [
          {
            category: "rationale",
            day: activity.day,
            place_ids: [activity.place_id],
            alternative_ids: [],
            evidence: "夜景表述与实际时段不一致。",
            suggestion: "整体比较时间与推荐理由。",
          },
        ],
      },
      review_failure: {
        code: "timeout",
        message: "模型响应超时",
        retryable: true,
      },
    };
    body.itinerary.checks = [
      {
        code: "break_overlap",
        severity: "error",
        message: "第 1 天用餐与参观重叠 60 分钟。",
        activity_id: activity.id,
      },
      {
        code: "schedule_night_missing",
        severity: "warning",
        message: "已预留晚间但具体时间表没有晚间体验。",
        activity_id: null,
      },
      {
        code: "night_preference_unverified",
        severity: "warning",
        message: "重复的夜景提示不应显示",
        activity_id: null,
      },
      {
        code: "activity_time_adjusted",
        severity: "info",
        message: "调整记录默认折叠测试",
        activity_id: null,
      },
      {
        code: "schedule_review_unavailable",
        severity: "warning",
        message: "复核未完成：模型响应超时。",
        activity_id: null,
      },
    ];
    await route.fulfill({ json: body });
  });
  await openSaved(page, trip.id);
  await expect(
    page.getByText("室外为主 · 模型判断", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".basis-timing-warning").first()).toContainText(
    "尚未落实晚间观景",
  );
  await expect(
    page.getByText("适合在傍晚至入夜看海景，落实夜间观景需求。", {
      exact: true,
    }),
  ).toBeHidden();
  await expect(
    page.getByText("查看模型原始推荐理由（时段未落实）", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("当前时段尚未落实夜景目标，需调整时间或重新取舍。", {
      exact: true,
    }),
  ).toBeVisible();
  const checks = page.getByRole("region", { name: "行程检查", exact: true });
  await expect(
    checks.getByRole("heading", { name: "待解决冲突（1）" }),
  ).toBeVisible();
  await expect(
    checks.getByText("重复的夜景提示不应显示", { exact: true }),
  ).toHaveCount(0);
  await expect(
    checks.getByText("调整记录默认折叠测试", { exact: true }),
  ).toBeHidden();
  await checks.getByText("查看程序调整记录（1）", { exact: true }).click();
  await expect(
    checks.getByText("调整记录默认折叠测试", { exact: true }),
  ).toBeVisible();
  await expect(checks).toContainText("复核结果：模型响应超时");
  await page.reload();
  await expect(
    page.getByText("室外为主 · 模型判断", { exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("evening activity can show fulfilled rationale and indoor model judgment", async ({
  page,
  request,
}) => {
  const trip = await savedTrip(request);
  await page.route(`**/api/trips/${trip.id}`, async (route) => {
    const body = await (await route.fetch()).json();
    const activity = body.itinerary.days[0].activities[0];
    activity.start = "19:00";
    activity.end = "20:00";
    activity.time_intent = "night";
    activity.reason = "安排晚间室内文化体验";
    const place = body.places.find(
      (p: { id: string }) => p.id === activity.place_id,
    );
    place.fixture = false;
    place.traits = { ...(place.traits || {}), indoor_outdoor: "unknown" };
    body.itinerary.planning_decision = {
      source: "model_schedule",
      model_calls: 2,
      notes: [],
      recommendations: [
        {
          place_id: activity.place_id,
          reason: "以晚间文化体验衔接夜景主题，开放时间待确认。",
          role: "interest",
          source_ids: [],
          tradeoff: "开放时间待确认",
          basis: "model",
        },
      ],
      place_assessments: [
        {
          place_id: activity.place_id,
          indoor_outdoor: "indoor",
          confidence: "medium",
          reason: "主要参观对象为室内展厅，附属室外空间另计",
          basis: "model",
        },
      ],
    };
    await route.fulfill({ json: body });
  });
  await openSaved(page, trip.id);
  await expect(
    page.getByText("室内为主 · 模型判断", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".venue-assessment").first()).toContainText(
    "置信程度中等",
  );
  await expect(page.locator(".venue-assessment").first()).toContainText(
    "未实地核验",
  );
  await expect(
    page.getByText("以晚间文化体验衔接夜景主题，开放时间待确认。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(page.locator(".basis-timing-warning")).toHaveCount(0);
});

test("guide-discovered candidates are visible without being forced into itinerary", async ({
  page,
  request,
}) => {
  const trip = await savedTrip(request);
  await page.route(`**/api/trips/${trip.id}`, async (route) => {
    const body = await (await route.fetch()).json();
    body.places.push({
      ...body.places[0],
      id: "amap-guide-new",
      name: "南头古城",
      fixture: false,
    });
    body.itinerary.guides = {
      ...body.itinerary.guides,
      discovered_place_ids: ["amap-guide-new"],
      discovery_note:
        "攻略提出 2 个地点线索，地图补充 1 个候选；供主 Agent 比较，不自动安排。",
    };
    await route.fulfill({ json: body });
  });
  await openSaved(page, trip.id);
  const discoveries = page.getByRole("note", {
    name: "攻略补充候选",
    exact: true,
  });
  await expect(discoveries).toContainText("地图补充 1 个候选");
  await expect(discoveries).toContainText("南头古城");
  await expect(
    page.locator(".activity-card").filter({
      has: page.getByRole("heading", { name: "南头古城", exact: true }),
    }),
  ).toHaveCount(0);
  await page.reload();
  await expect(discoveries).toContainText("南头古城");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("independent reviewer shows daily reasoning and unresolved evening advice after reload", async ({
  page,
  request,
}) => {
  const trip = await savedTrip(request);
  await page.route(`**/api/trips/${trip.id}`, async (route) => {
    const body = await (await route.fetch()).json();
    body.itinerary.planning_decision.quality_review = {
      status: "changes_requested",
      summary: "逐日审核后，第一天晚间仍需补充具体安排。",
      days: [
        {
          day: 1,
          evening: "missing",
          pace: "too_sparse",
          explanation: "多探索且23:00结束，18:00以后没有具体安排。",
        },
      ],
      issues: [
        {
          category: "evening",
          day: 1,
          place_ids: [],
          alternative_ids: [],
          evidence: "18:00—23:00留白。",
          suggestion: "比较同区街区体验与晚餐。",
        },
      ],
    };
    body.itinerary.checks.push({
      code: "reviewer_evening",
      severity: "warning",
      message:
        "审核 Agent · 第 1 天：18:00—23:00留白。建议比较同区街区体验与晚餐。",
    });
    await route.fulfill({ json: body });
  });
  await openSaved(page, trip.id);
  const report = page.getByLabel("审核 Agent 报告", { exact: true });
  await expect(report).toContainText("仍有改进建议");
  await report.getByText("查看逐日审核依据", { exact: true }).click();
  await expect(report).toContainText("晚间待补充");
  await expect(report).toContainText("多探索且23:00结束");
  const checks = page.getByRole("region", { name: "行程检查", exact: true });
  await expect(checks).toContainText("建议比较同区街区体验与晚餐");
  await page.reload();
  await expect(report).toContainText("第一天晚间仍需补充具体安排");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("reviewer pass cannot hide hard conflicts and unavailable review is explicit", async ({
  page,
  request,
}) => {
  const trip = await savedTrip(request);
  let unavailable = false;
  await page.route(`**/api/trips/${trip.id}`, async (route) => {
    const body = await (await route.fetch()).json();
    body.itinerary.planning_decision.quality_review = {
      status: unavailable ? "unavailable" : "passed",
      summary: unavailable
        ? "审核模型超时，未完成独立体验审核。"
        : "体验取舍合理，保留必要休息。",
      days: [],
      issues: [],
    };
    body.itinerary.checks.push({
      code: "late_arrival",
      severity: "error",
      message: "交通无法赶上当前活动。",
    });
    if (unavailable)
      body.itinerary.checks.push({
        code: "quality_review_unavailable",
        severity: "warning",
        message: "审核模型超时，程序提示保留。",
      });
    await route.fulfill({ json: body });
  });
  await openSaved(page, trip.id);
  const checks = page.getByRole("region", { name: "行程检查", exact: true });
  await expect(
    page.getByLabel("审核 Agent 报告", { exact: true }),
  ).toContainText("未发现需调整的体验问题");
  await expect(checks).toContainText("草案仍有冲突，暂不能保存");
  await expect(checks).toContainText("交通无法赶上当前活动");
  unavailable = true;
  await page.reload();
  await expect(
    page.getByLabel("审核 Agent 报告", { exact: true }),
  ).toContainText("暂未完成");
  await expect(checks).toContainText("审核模型超时，程序提示保留");
});

test("failed main revision shows fallback and the concrete duplicate instead of implying an applied change", async ({
  page,
  request,
}) => {
  const trip = await savedTrip(request);
  await page.route(`**/api/trips/${trip.id}`, async (route) => {
    const body = await (await route.fetch()).json();
    body.itinerary.planning_decision.review_outcome = "failed";
    body.itinerary.planning_decision.selected_attempt = 1;
    body.itinerary.planning_decision.review_failure = {
      code: "invalid_schedule",
      message: "模型时间表格式或内容未通过校验",
      retryable: false,
      details: [
        {
          code: "duplicate_place",
          field: "daily_schedule.1.activities.0.place_id",
          message:
            "华强北博物馆重复安排于第1天第4项、第2天第3项、第2天第5项；只保留一次。",
        },
      ],
    };
    body.itinerary.checks.push({
      code: "schedule_review_unavailable",
      severity: "warning",
      message: "修改结果未通过校验，保留原草案。",
    });
    await route.fulfill({ json: body });
  });
  await openSaved(page, trip.id);
  const outcome = page.getByRole("status", {
    name: "审核后调整结果",
    exact: true,
  });
  await expect(outcome).toContainText("当前展示调整前草案");
  const checks = page.getByRole("region", { name: "行程检查", exact: true });
  await checks.getByText("查看具体失败原因", { exact: true }).click();
  await expect(checks).toContainText("华强北博物馆重复安排");
  await page.reload();
  await expect(outcome).toContainText("修改结果未通过校验");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("accepted main revision clearly says the changed timetable was adopted", async ({
  page,
  request,
}) => {
  const trip = await savedTrip(request);
  await page.route(`**/api/trips/${trip.id}`, async (route) => {
    const body = await (await route.fetch()).json();
    body.itinerary.planning_decision.review_outcome = "applied";
    body.itinerary.planning_decision.selected_attempt = 2;
    await route.fulfill({ json: body });
  });
  await openSaved(page, trip.id);
  await expect(
    page.getByRole("status", { name: "审核后调整结果", exact: true }),
  ).toContainText("已采纳调整后的时间表");
});

test("an evening intent before nineteen is not mechanically labeled as a failed night experience", async ({
  page,
  request,
}) => {
  const trip = await savedTrip(request);
  await page.route(`**/api/trips/${trip.id}`, async (route) => {
    const body = await (await route.fetch()).json();
    const activity = body.itinerary.days[0].activities[0];
    activity.start = "18:15";
    activity.end = "18:45";
    activity.time_intent = "night";
    body.itinerary.planning_decision.quality_review = {
      status: "passed",
      summary: "整体时段取舍合理，日落和开放条件仍待确认。",
      days: [],
      issues: [],
      overall_score: 80,
    };
    await route.fulfill({ json: body });
  });
  await openSaved(page, trip.id);
  await expect(page.locator(".basis-timing-warning")).toHaveCount(0);
  await expect(
    page.getByLabel("审核 Agent 报告", { exact: true }),
  ).toContainText("整体时段取舍合理");
});
