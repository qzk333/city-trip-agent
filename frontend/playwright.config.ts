import { defineConfig, devices } from "@playwright/test";
import { resolve } from "node:path";

const python =
  process.platform === "win32"
    ? "..\\backend\\.venv\\Scripts\\python.exe"
    : "../backend/.venv/bin/python";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  use: { baseURL: "http://127.0.0.1:5174", trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    {
      name: "mobile",
      use: { ...devices["iPhone 13"], defaultBrowserType: "chromium" },
    },
  ],
  webServer: [
    {
      command: `"${python}" -m uvicorn app.main:app --app-dir ../backend --host 127.0.0.1 --port 8001`,
      url: "http://127.0.0.1:8001/api/health",
      reuseExistingServer: false,
      env: {
        TOOL_PROVIDER: "demo",
        PLANNING_MODE: "demo",
        DATABASE_URL: "sqlite:///../data/e2e.sqlite3",
        LOCAL_API_CONFIG_PATH: resolve("../.local/e2e-api-config.json"),
      },
    },
    {
      command: "npm run dev -- --port 5174 --strictPort",
      url: "http://127.0.0.1:5174",
      reuseExistingServer: false,
      env: { TRAVEL_API_TARGET: "http://127.0.0.1:8001" },
    },
  ],
});
