/**
 * Captures documentation screenshots from the running application.
 *
 * Prerequisites: the FastAPI app is serving the exported frontend (default http://localhost:8000)
 * and the synthetic workspace is seeded. Screenshots are real renders of live pages with real API
 * data — nothing is mocked or drawn by hand.
 *
 *   node scripts/capture_screenshots.mjs            # writes docs/images/*.png
 *   BASE_URL=http://localhost:8000 node scripts/capture_screenshots.mjs
 */
import { createRequire } from "node:module";
import fs from "node:fs/promises";
import path from "node:path";

// Playwright is installed with the frontend workspace, not at the repository root. Resolve it
// from there so the documented command works from a clean checkout.
const require = createRequire(new URL("../frontend/package.json", import.meta.url));
const { chromium } = require("@playwright/test");

const BASE_URL = process.env.BASE_URL ?? "http://localhost:8000";
const OUT_DIR = path.resolve(process.cwd(), "docs", "images");
const EMAIL = process.env.EGA_DEMO_EMAIL ?? "admin@northwind.example";
const PASSWORD = process.env.EGA_DEMO_PASSWORD ?? "DemoPass!2026";

const api = async (route, token, options = {}) => {
  const response = await fetch(`${BASE_URL}${route}`, {
    ...options,
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", ...(options.headers ?? {}) },
  });
  if (!response.ok) throw new Error(`${route} -> ${response.status}`);
  return response.json();
};

async function main() {
  await fs.mkdir(OUT_DIR, { recursive: true });

  const loginResponse = await fetch(`${BASE_URL}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: EMAIL, password: PASSWORD }),
  });
  if (!loginResponse.ok) throw new Error(`Login failed: ${loginResponse.status}`);
  const { access_token: token, user } = await loginResponse.json();

  const decisions = await api("/api/decisions?page_size=50", token);
  const byTitle = (needle) => decisions.items.find((item) => item.title.toLowerCase().includes(needle));
  const approved = byTitle("cascade") ?? decisions.items[0];
  const contradicted = byTitle("apex") ?? decisions.items[0];
  const documents = await api("/api/documents?page_size=50", token);
  const contractDocument = documents.items.find((item) => item.doc_key === "apex-contract") ?? documents.items[0];

  // Headless Chromium does not ship the PDF viewer. Use `HEADED=1 xvfb-run -a node ...` when
  // capturing the original-document preview so the PDF itself is rendered, not a blank frame.
  const browser = await chromium.launch({ headless: process.env.HEADED !== "1" });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    colorScheme: "dark",
    reducedMotion: "reduce",
  });
  const page = await context.newPage();
  page.on("console", (message) => {
    if (message.type() === "error") console.log("[console]", message.text());
  });

  await page.goto(`${BASE_URL}/login/`, { waitUntil: "networkidle" });
  await page.evaluate(([tokenValue, userValue]) => {
    window.localStorage.setItem("ega.token", tokenValue);
    window.localStorage.setItem("ega.user", JSON.stringify(userValue));
  }, [token, user]);

  const shots = [
    ["dashboard", "/"],
    ["ingestion", "/ingestion/"],
    ["documents", "/documents/"],
    ["source-viewer", `/documents/view/?id=${contractDocument.id}`],
    ["claims", "/claims/"],
    ["graph", `/graph/`],
    ["contradictions", "/contradictions/"],
    ["decisions", "/decisions/"],
    ["decision-builder", "/decisions/new/"],
    ["decision-checklist", `/decisions/view/?id=${contradicted.id}`],
    ["decision-approved", `/decisions/view/?id=${approved.id}`],
    ["reviews", "/reviews/"],
    ["ask", "/ask/"],
    ["benchmarks", "/benchmarks/"],
    ["reports", "/reports/"],
    ["audit", "/audit/"],
    ["settings", "/settings/"],
  ];

  for (const [name, route] of shots) {
    await page.goto(`${BASE_URL}${route}`, { waitUntil: "networkidle" });
    await page.waitForTimeout(700);
    await page.screenshot({ path: path.join(OUT_DIR, `${name}.png`), fullPage: false });
    console.log(`captured ${name} <- ${route}`);
  }

  // Ask a real question so the screenshot shows an actual grounded answer with citations,
  // not the empty state. This goes through the live API, so nothing is mocked.
  await page.goto(`${BASE_URL}/ask/`, { waitUntil: "networkidle" });
  const askInput = page.getByPlaceholder(/Ask about price/i);
  await askInput.fill("What is the warranty for Cascade Pumps?");
  await page.getByRole("button", { name: "Send" }).click();
  await page.waitForTimeout(2500);
  await page.screenshot({ path: path.join(OUT_DIR, "ask.png") });
  console.log("captured ask <- answered question");

  // Interaction states: checklist tab and alternatives tab of the approved case, contradiction detail.
  await page.goto(`${BASE_URL}/decisions/view/?id=${contradicted.id}`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Sufficiency checklist" }).click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(OUT_DIR, "decision-sufficiency-gate.png") });
  console.log("captured decision-sufficiency-gate <- checklist tab");

  await page.goto(`${BASE_URL}/decisions/view/?id=${approved.id}`, { waitUntil: "networkidle" });
  await page.getByRole("button", { name: "Alternatives" }).click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(OUT_DIR, "decision-alternatives.png") });
  console.log("captured decision-alternatives <- alternatives tab");

  await page.getByRole("button", { name: "AI analysis" }).click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: path.join(OUT_DIR, "decision-analysis.png") });
  console.log("captured decision-analysis <- analysis tab");

  // Original-document preview: the file is fetched with the session token and rendered from a
  // blob URL, so this screenshot proves the stored PDF itself is viewable, not only its spans.
  await page.goto(`${BASE_URL}/documents/view/?id=${contractDocument.id}`, { waitUntil: "networkidle" });
  const originalToggle = page.getByRole("button", { name: "Original", exact: true });
  if (await originalToggle.count()) {
    await originalToggle.click();
    await page.waitForSelector('iframe[title^="Original PDF"]');
    await page.waitForTimeout(1800);
    await page.screenshot({ path: path.join(OUT_DIR, "source-viewer-original.png") });
    console.log("captured source-viewer-original <- authenticated PDF preview");
  }

  await browser.close();
  console.log(`\nScreenshots written to ${OUT_DIR}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
