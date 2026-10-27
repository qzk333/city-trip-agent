import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { mkdirSync, mkdtempSync, openSync, closeSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { setTimeout as delay } from "node:timers/promises";

// Capture the actual application using disposable demo data and no user credentials.
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(join(root, "frontend/package.json"));
const { chromium, devices, expect } = require("@playwright/test");
const temporary = mkdtempSync(join(tmpdir(), "city-trip-readme-"));
const output = join(root, "docs/images");
mkdirSync(output, { recursive: true });
const apiPort = 8012;
const webPort = 5182;
const baseURL = `http://127.0.0.1:${webPort}`;
const apiURL = `http://127.0.0.1:${apiPort}`;
const children = [];
const descriptors = [];
let browser;

function start(name, command, args, cwd, env) {
  const log = openSync(join(temporary, `${name}.log`), "w");
  descriptors.push(log);
  const child = spawn(command, args, {
    cwd,
    env: { ...process.env, ...env },
    stdio: ["ignore", log, log],
    windowsHide: true,
  });
  child.on("error", (error) => console.error(`${name}: ${error.message}`));
  children.push(child);
  return child;
}

async function ready(url, child) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (child.exitCode !== null)
      throw new Error(`Service exited; logs: ${temporary}`);
    try {
      if ((await fetch(url, { signal: AbortSignal.timeout(1000) })).ok) return;
    } catch {
      // Startup includes migrations for a new temporary database.
    }
    await delay(300);
  }
  throw new Error(`Service not ready: ${url}; logs: ${temporary}`);
}

async function screenshot(page, name) {
  await page.screenshot({ path: join(output, name), animations: "disabled" });
  console.log(`Captured docs/images/${name}`);
}

try {
  // Fail instead of accidentally using an unrelated process already on these ports.
  for (const url of [apiURL, baseURL]) {
    let response;
    try {
      response = await fetch(url, { signal: AbortSignal.timeout(700) });
    } catch {
      continue;
    }
    if (response) throw new Error(`Screenshot port already in use: ${url}`);
  }
  const backend = start(
    "backend",
    join(
      root,
      process.platform === "win32"
        ? "backend/.venv/Scripts/python.exe"
        : "backend/.venv/bin/python",
    ),
    [
      "-m",
      "uvicorn",
      "app.main:app",
      "--app-dir",
      "backend",
      "--host",
      "127.0.0.1",
      "--port",
      String(apiPort),
    ],
    root,
    {
      DATABASE_URL: `sqlite:///${join(temporary, "demo.sqlite3").replaceAll("\\", "/")}`,
      LOCAL_API_CONFIG_PATH: join(temporary, "api-config.json"),
      TOOL_PROVIDER: "demo",
      PLANNING_MODE: "demo",
      AMAP_API_KEY: "",
      LLM_API_KEY: "",
      LLM_BASE_URL: "",
      LLM_MODEL: "",
    },
  );
  const frontend = start(
    "frontend",
    process.execPath,
    [
      join(root, "frontend/node_modules/vite/bin/vite.js"),
      "--host",
      "127.0.0.1",
      "--port",
      String(webPort),
      "--strictPort",
    ],
    join(root, "frontend"),
    { TRAVEL_API_TARGET: apiURL },
  );
  await Promise.all([
    ready(`${apiURL}/api/health`, backend),
    ready(baseURL, frontend),
  ]);
  browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1600, height: 1080 },
    deviceScaleFactor: 1,
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
  });
  await context.route(
    /^https:\/\/fonts\.(googleapis|gstatic)\.com\//,
    (route) => route.abort(),
  );
  const page = await context.newPage();
  await page.goto(baseURL);
  const generate = page.getByRole("button", {
    name: "生成我的行程",
    exact: true,
  });
  await expect(generate).toBeEnabled();
  await page.getByLabel("出发日期", { exact: true }).fill("2026-10-30");
  await page.getByLabel("旅行天数", { exact: true }).selectOption("3");
  await page.getByLabel("保留一项固定预约").check();
  await generate.click();
  await expect(
    page.getByRole("region", { name: "行程预览", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".activity-card").first()).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await screenshot(page, "workbench-desktop.png");

  await page.getByRole("button", { name: "保存演示行程", exact: true }).click();
  await expect(
    page.getByRole("button", { name: /版本记录.*v1/ }),
  ).toBeVisible();
  const hotels = page.getByRole("region", { name: "住宿候选", exact: true });
  await expect(hotels).toBeVisible();
  await hotels.scrollIntoViewIfNeeded();
  await hotels.screenshot({
    path: join(output, "hotels-desktop.png"),
    animations: "disabled",
  });
  console.log("Captured docs/images/hotels-desktop.png");

  await page
    .getByLabel("想怎样调整？", { exact: true })
    .fill("第1天晚出发5分钟");
  await page.getByRole("button", { name: "生成行程调整", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "自然语言行程调整 · 尚未保存" }),
  ).toBeVisible();
  await page.locator(".revision-review").scrollIntoViewIfNeeded();
  await page
    .locator(".revision-review")
    .screenshot({
      path: join(output, "replanning-desktop.png"),
      animations: "disabled",
    });
  console.log("Captured docs/images/replanning-desktop.png");
  await page.getByRole("button", { name: "保存演示行程", exact: true }).click();
  await page.getByRole("button", { name: /版本记录.*v2/ }).click();
  await page
    .locator(".history-modal")
    .screenshot({
      path: join(output, "versions-desktop.png"),
      animations: "disabled",
    });
  console.log("Captured docs/images/versions-desktop.png");
  await page.getByRole("button", { name: "关闭版本记录", exact: true }).click();

  const tripId = await page.evaluate(() =>
    localStorage.getItem("city-trip:last-trip"),
  );
  const mobile = await browser.newContext({
    ...devices["iPhone 13"],
    deviceScaleFactor: 1,
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
  });
  await mobile.route(/^https:\/\/fonts\.(googleapis|gstatic)\.com\//, (route) =>
    route.abort(),
  );
  await mobile.addInitScript(
    (id) => localStorage.setItem("city-trip:last-trip", id),
    tripId,
  );
  const mobilePage = await mobile.newPage();
  await mobilePage.goto(baseURL);
  await expect(
    mobilePage.getByRole("button", { name: /版本记录.*v2/ }),
  ).toBeVisible();
  if (
    await mobilePage
      .getByRole("button", { name: "收起条件", exact: true })
      .isVisible()
  ) {
    await mobilePage
      .getByRole("button", { name: "收起条件", exact: true })
      .click();
  }
  await mobilePage
    .locator(".day-tabs")
    .evaluate((element) => element.scrollIntoView({ block: "start" }));
  expect(
    await mobilePage.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await screenshot(mobilePage, "workbench-mobile.png");
  console.log(
    "Screenshot data: isolated demo fixtures; no live model/map calls.",
  );
} finally {
  await browser?.close();
  for (const child of children.reverse()) {
    if (child.exitCode === null) child.kill();
  }
  for (const descriptor of descriptors) closeSync(descriptor);
}
