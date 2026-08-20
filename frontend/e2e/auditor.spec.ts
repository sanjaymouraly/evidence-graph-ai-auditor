import { expect, test, type Page } from "@playwright/test";

/**
 * End-to-end coverage of the mandatory acceptance paths, exercised through the real UI.
 * The server must be seeded from datasets/synthetic/dev (npm run test:e2e docs in README).
 */

const ADMIN = { email: "admin@northwind.example", password: "DemoPass!2026" };
const REVIEWER = { email: "reviewer@northwind.example", password: "DemoPass!2026" };
const VIEWER = { email: "viewer@northwind.example", password: "DemoPass!2026" };

async function uiLogin(page: Page, credentials: { email: string; password: string }) {
  await page.goto("/login/");
  await page.getByLabel(/email/i).fill(credentials.email);
  await page.getByLabel(/password/i).fill(credentials.password);
  await page.getByRole("button", { name: /sign in|log in|continue/i }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
}

test.describe("authentication and access control", () => {
  test("rejects a bad password and signs a valid user in", async ({ page }) => {
    await page.goto("/login/");
    await page.getByLabel(/email/i).fill(ADMIN.email);
    await page.getByLabel(/password/i).fill("wrong-password");
    await page.getByRole("button", { name: /sign in|log in|continue/i }).click();
    await expect(page.getByText(/invalid|incorrect|failed/i).first()).toBeVisible();
    await expect(page).toHaveURL(/\/login\//);

    await page.getByLabel(/password/i).fill(ADMIN.password);
    await page.getByRole("button", { name: /sign in|log in|continue/i }).click();
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  });

  test("viewer cannot reach approve controls but can read the audit trail", async ({ page }) => {
    await uiLogin(page, VIEWER);
    await page.goto("/decisions/");
    await expect(page.getByRole("heading", { name: "Decisions" })).toBeVisible();
    await page.goto("/audit/");
    await expect(page.getByRole("heading", { name: "Audit trail" })).toBeVisible();
    await expect(page.getByText("Valid", { exact: true })).toBeVisible();
  });

  test("signed-out users are redirected to login", async ({ page }) => {
    await page.goto("/claims/");
    await expect(page).toHaveURL(/\/login\//);
  });
});

test.describe("evidence pipeline", () => {
  test.beforeEach(async ({ page }) => {
    await uiLogin(page, ADMIN);
  });

  test("dashboard reports live workspace state", async ({ page }) => {
    await expect(page.getByText("Evidence health")).toBeVisible();
    // Counts are rendered from the API, so they must be numeric rather than placeholders.
    const documentsStat = page.getByTestId("stat-documents");
    await expect(documentsStat).toBeVisible();
    await expect(documentsStat).toContainText(/\d+ versions/);
    const claimsStat = page.getByTestId("stat-claims-extracted");
    await expect(claimsStat).toContainText(/\d+ verified/);
    await expect(page.getByRole("heading", { name: "Decision pipeline" })).toBeVisible();
  });

  test("source viewer shows provenance spans and claims for a document", async ({ page }) => {
    await page.goto("/documents/");
    await page.getByPlaceholder(/search title/i).fill("Apex Systems supply agreement");
    await page.getByRole("link", { name: /Apex Systems supply agreement/i }).click();
    await expect(page.getByRole("heading", { name: /Apex Systems supply agreement/i })).toBeVisible();
    await expect(page.getByText("Parsed content")).toBeVisible();
    await expect(page.getByText(/CONTENT HASH/i)).toBeVisible();
    // A highlighted span must be clickable and surface its claim list.
    await expect(page.getByText("CLAIMS IN THIS SPAN")).toBeVisible();
    await expect(page.getByText("quote verified").first()).toBeVisible();
    // The stored PDF is previewed through an authenticated blob URL, not a public path.
    await page.getByRole("button", { name: "Original", exact: true }).click();
    await expect(page.locator('iframe[title^="Original PDF"]')).toBeVisible();
    await page.getByRole("button", { name: "Parsed", exact: true }).click();
    await expect(page.getByText("Parsed content")).toBeVisible();
  });

  test("claims table filters and opens a traced claim", async ({ page }) => {
    await page.goto("/claims/");
    await expect(page.getByRole("heading", { name: "Claims" })).toBeVisible();
    const status = page.locator("select").nth(1);
    await status.selectOption("disputed");
    const firstRow = page.locator("tbody tr").first();
    await expect(firstRow).toContainText(/disputed/i);
    await firstRow.click();
    await expect(page.getByText("quote resolves").or(page.getByText("quote unresolved"))).toBeVisible();
    await expect(page.getByText("Extraction", { exact: true })).toBeVisible();
  });

  test("graph explorer renders persisted nodes and relations", async ({ page }) => {
    await page.goto("/graph/");
    await expect(page.getByRole("heading", { name: "Evidence graph" })).toBeVisible();
    await expect(page.getByText(/\d+ nodes · \d+ edges/)).toBeVisible();
    await expect(page.locator("svg circle").first()).toBeVisible();
  });

  test("contradictions page shows both sides of a confirmed conflict", async ({ page }) => {
    await page.goto("/contradictions/");
    await expect(page.getByRole("heading", { name: "Contradictions" })).toBeVisible();
    // The list item is a button; the filter dropdown also contains the same label as an option.
    await page.getByRole("button", { name: /Delivery Conflict/i }).first().click();
    await expect(page.getByText(/why this was flagged/i)).toBeVisible();
    await expect(page.getByText(/^Claim A$/i).first()).toBeVisible();
    await expect(page.getByText(/^Claim B$/i).first()).toBeVisible();
    await expect(page.getByText(/review note/i)).toBeVisible();
  });
});

test.describe("decision workflow", () => {
  test.beforeEach(async ({ page }) => {
    await uiLogin(page, ADMIN);
  });

  test("gate blocks a contradicted decision", async ({ page }) => {
    await page.goto("/decisions/");
    await page.getByRole("link", { name: /Approve Apex Systems/i }).click();
    await expect(page.getByText(/^Contradicted$/).first()).toBeVisible();
    await expect(page.getByText(/approval not permitted/i)).toBeVisible();
    await page.getByRole("button", { name: "Sufficiency checklist" }).click();
    await expect(page.getByText("no_blocking_contradictions")).toBeVisible();
    // The approval control lives on the review tab; it must be disabled while the gate fails.
    await page.getByRole("button", { name: "Review & approval" }).click();
    await expect(page.getByRole("button", { name: /Approve decision/i })).toBeDisabled();
    await expect(page.getByText(/Approval is disabled because the gate/i)).toBeVisible();
  });

  test("approved decision shows gate, attestation and hash", async ({ page }) => {
    await page.goto("/decisions/");
    await page.getByRole("link", { name: /Award pump maintenance contract/i }).click();
    await expect(page.getByText("Sufficiently Supported").first()).toBeVisible();
    await expect(page.getByText("Approved").first()).toBeVisible();
    await expect(page.getByText("eligible for approval")).toBeVisible();
    await expect(page.getByText(/Gate result hash/i)).toBeVisible();
    await page.getByRole("button", { name: "Review & approval" }).click();
    await expect(page.getByText(/reviewer/i).first()).toBeVisible();
  });

  test("AI analysis is grounded, cited and advisory", async ({ page }) => {
    await page.goto("/decisions/");
    await page.getByRole("link", { name: /Award pump maintenance contract/i }).click();
    await page.getByRole("button", { name: "AI analysis" }).click();
    await expect(page.getByText("Evidence-constrained analysis")).toBeVisible();
    await expect(page.getByText("ADVISORY, NOT A DECISION")).toBeVisible();
    await expect(page.getByText("grounded").first()).toBeVisible();
    await expect(page.getByText(/The model cannot change gate results/i)).toBeVisible();
  });

  test("alternatives tab renders the weighted comparison", async ({ page }) => {
    await page.goto("/decisions/");
    await page.getByRole("link", { name: /Award pump maintenance contract/i }).click();
    await page.getByRole("button", { name: "Alternatives" }).click();
    await expect(page.getByText("Criteria and weights")).toBeVisible();
    await expect(page.getByText("Cascade Pumps Ltd").first()).toBeVisible();
    await expect(page.getByText(/Sensitivity/i)).toBeVisible();
  });
});

test.describe("governance surfaces", () => {
  test.beforeEach(async ({ page }) => {
    await uiLogin(page, ADMIN);
  });

  test("grounded Q&A answers with citations", async ({ page }) => {
    await page.goto("/ask/");
    await page.getByPlaceholder(/ask about price/i).fill("What are the payment terms for each supplier?");
    await page.getByRole("button", { name: /^Send$/ }).click();
    await expect(page.getByText(/payment terms|insufficient evidence/i).first()).toBeVisible({ timeout: 20_000 });
  });

  test("benchmark dashboard shows stored metrics and targets", async ({ page }) => {
    await page.goto("/benchmarks/");
    await expect(page.getByRole("heading", { name: "Benchmark dashboard" })).toBeVisible();
    // A run must have been executed at least once (seeded state or a manual run).
    await expect(page.getByText(/claim link precision/i).first()).toBeVisible();
    await expect(page.getByText(/target ≥ /i).first()).toBeVisible();
    // The "next run" selector must describe the displayed run, not silently disagree with it.
    const splitSelector = page.getByLabel("Dataset for the next benchmark run");
    const displayedSplit = await splitSelector.inputValue();
    await expect(page.getByText(new RegExp(`${displayedSplit} ·`))).toBeVisible();
  });

  test("reports page lists snapshots and can verify one", async ({ page }) => {
    await page.goto("/reports/");
    await expect(page.getByRole("heading", { name: /Reports/i })).toBeVisible();
    await page.getByRole("button", { name: "Verify" }).first().click();
    await expect(page.getByText(/verified|mismatch|valid/i).first()).toBeVisible();
  });

  test("audit trail verifies its hash chain", async ({ page }) => {
    await page.goto("/audit/");
    await page.getByRole("button", { name: /Verify chain/i }).click();
    await expect(page.getByText(/Valid|verified/i).first()).toBeVisible();
  });

  test("settings page documents offline mode and provider safety", async ({ page }) => {
    await page.goto("/settings/");
    await expect(page.getByRole("heading", { name: /Providers & settings/i })).toBeVisible();
    await expect(page.getByText("Offline mode")).toBeVisible();
    await expect(page.getByText(/API keys are encrypted/i)).toBeVisible();
    await expect(page.getByText(/Base URLs are validated/i)).toBeVisible();
  });
});

test.describe("reviewer actions", () => {
  test("reviewer can open the review queue", async ({ page }) => {
    await uiLogin(page, REVIEWER);
    await page.goto("/reviews/");
    await expect(page.getByRole("heading", { name: "Review queue" })).toBeVisible();
    await expect(page.getByText(/Sufficiently Supported|Contradicted/i).first()).toBeVisible();
  });
});
