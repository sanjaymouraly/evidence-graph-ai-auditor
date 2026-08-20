import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests run against a real server: the FastAPI app serving the exported UI,
 * seeded with the synthetic workspace. They assert on rendered state, not on mocks.
 *
 *   # terminal 1
 *   PYTHONPATH=backend .venv/bin/python -c "from app.seed import seed_demo; seed_demo()"
 *   PYTHONPATH=backend .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
 *   # terminal 2
 *   cd frontend && npm run build && npx playwright test
 */
const baseURL = process.env.EGA_BASE_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    colorScheme: "dark",
    viewport: { width: 1440, height: 900 },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
