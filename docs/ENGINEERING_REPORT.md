# Engineering completion report

**Project:** Evidence-Graph AI Auditor for High-Stakes Business Decisions
**Repository:** `evidence-graph-ai-auditor`
**Version:** 1.0.0 · gate `gate-1.2` · policy `policy-2026.1`
**Verification window:** 2026-10-21 18:25 – 2026-10-22 16:53 UTC, single Linux sandbox
**Status:** functionally complete and verified offline on SQLite and PostgreSQL 16.15. The Docker
image was built and the production SQLite stack was run through `docker compose`; the exact
compose PostgreSQL pair could not establish container-to-container TCP in this sandbox, so the
same image was verified against a host-published PostgreSQL 16.15 database instead. GitHub
Actions was **not** executed. See [§9](#9-unverified-and-blocked) before treating this as
production-ready.

This report is written to be audited itself: every number below is reproducible with the
commands in [§4](#4-how-to-run-and-reproduce), and where something could not be verified it says
so instead of implying success.

---

## 1. What was built

A working web application, not a prototype. Documents go in, typed claims come out with
byte-precise provenance, contradictions are detected and shown side by side, and a decision can
only be approved when a **deterministic** gate passes and a **human** attests — with models
relegated to explanation.

| Layer | Implementation |
| --- | --- |
| Backend | Python 3.12 / 3.13 / FastAPI 0.115 / Pydantic 2.11 / SQLAlchemy 2.0 / Alembic; 86 API operations across 77 paths, 22 schemas |
| Parsing | PDF (page + bbox), DOCX (paragraph), XLSX/CSV (sheet + cell), TXT/MD/JSON (char offsets), EML (message-id + section) |
| Evidence model | Immutable versions, hashes, supersession, typed claims, entity resolution, normalisation, freshness, contradiction candidates vs confirmed |
| Graph | Persisted typed nodes/edges, projected into NetworkX; explorer, impact analysis, named native queries (Neo4j adapter is a stub, not selectable) |
| Gate | 10 deterministic checks, 7 workflow states, explicit transitions, SHA-256 result hash, attestation bound to the evidence state |
| AI | Provider-agnostic (OpenAI-compatible, Anthropic, OpenRouter, custom, Ollama, offline), separate generation/embedding/rerank roles, citation validation, injection defence, grounded Q&A |
| Frontend | Next.js 15.5 static export served by FastAPI, the 15 required screens across 17 routes, typed client, RBAC-aware UI |
| Evaluation | Synthetic corpus, independent dev/held-out labels, metric suite with targets, in-app dashboard, persisted run payloads |
| Governance | Hash-chained audit log, snapshots with manifest verification, PDF/JSON/CSV reports |
| Ops | Dockerfile + compose (optional PostgreSQL), CI workflow with a Postgres parity job, migrations, structured logs with correlation ids, health/readiness |

**Size:** 70 Python files (11.7k lines), 33 TypeScript files (6.3k lines), 2.4k lines of
documentation, 6.9 MB of repository content excluding dependencies, build output and `.git`.

---

## 2. Verified capabilities

Each row was exercised in this environment, not inferred from code.

| Capability | Evidence |
| --- | --- |
| Parse seven formats with real provenance | `test_api.py`, `test_claims.py`; source viewer screenshot shows a PDF span highlighted with page/bbox |
| Immutable versions + supersession | `apex_quote_v1 → v2` yields version 2 current, v1 superseded, `SUPERSEDES` edge, benchmark `supersession_accuracy = 1.0` |
| Typed claims with locators | 111 claims over 24 documents; `citation_validity = 1.0` |
| Contradiction detection | 4 confirmed conflicts in the dev corpus, 3 blocking; Apex price/delivery/payment terms shown with both sources |
| Decision gate blocks bad cases | Apex `CONTRADICTED`, Borealis `EVIDENCE_INCOMPLETE` (expired evidence), M2 `EVIDENCE_INCOMPLETE` (missing risk register) |
| Gate passes good cases | Cascade 10/10 checks, `SUFFICIENTLY_SUPPORTED` → attestation → `APPROVED` with `result_hash` |
| Approval guard rails | Approving an incomplete case returns **409** with the failing checks; approval by the author is refused; evidence changed after attestation invalidates it |
| Exceptions never falsify results | `test_gate_integrity.py`; exception is recorded with reason and does not move status, blocking flags or hash |
| Grounded Q&A | "What is the warranty for Cascade Pumps?" → "Warranty: 36 months" with page-1 citation; "capital of France?" → `insufficient_evidence: true` |
| Injection containment | `vendor_note_injection.txt` flagged at ingest, 0 claims extracted, `injection_containment = 1.0` |
| Provider safety | Keys encrypted and masked, base-URL validation rejects `127.0.0.1` and metadata IPs, connection tests never echo secrets (`test_scenario_9`) |
| Offline operation | Whole demo runs with no API key: analysis provider label `offline-deterministic`, Q&A `offline-retrieval` |
| Reproducible reports | PDF/JSON/CSV generated with citations, contradictions, methodology, model metadata, snapshot id and manifest hashes (`test_scenario_10`) |
| Tamper-evident audit | `GET /api/audit/verify` → `valid: true` on the final seeded workspace; decision audit tab shows the chain |
| Live local model | Ollama `gemma:2b` verified live: provider connection test and model discovery, grounded Q&A returned a cited warranty answer in 47.8 s, and LLM analysis returned 2 validated statements in 153 s (cold model load) |
| Benchmarks in-app | Dashboard renders stored runs; metrics and targets come from `benchmark_runs.payload`. The "next run" selector follows the displayed run's split |
| RBAC | Viewer cannot create/approve/upload; reviewer attests but cannot administer; admin-only provider and user management (`test_api.py`, `test_permissions.ts`) |
| Migrations | `alembic upgrade head` → `downgrade base` → `upgrade head` clean on fresh SQLite and PostgreSQL 16.15 databases (`0f37c30a0822` → `7c1a51f4b902`, 22 tables) |
| Engine parity | The whole suite passes on both engines (**86/86** each) and the dashboard, audit chain and benchmarks return identical results on PostgreSQL 16.15 |
| Production startup guards | The built image refuses to start without `EGA_SECRET_KEY` (verified: process exits with `RuntimeError: Invalid configuration`), and two workers migrate + seed exactly once |

### The ten mandatory acceptance scenarios

All ten pass, through the real HTTP API, in `backend/tests/test_acceptance_scenarios.py`:

1. Supplier price contradiction blocks the decision, both sides cited from distinct documents.
2. Outdated evidence (expired certificate) marks claims `unsupported` and blocks Borealis.
3. Missing required evidence blocks milestone M2 and approval returns 409.
4. Fully verified decision reaches `APPROVED` with attestation and gate-snapshot hash.
5. Unsupported AI statement is rejected by citation validation (unit-level, adversarial input).
6. Document revision invalidates dependent reviews (`INVALIDATES` edge + review reset).
7. Every displayed claim traces back to its original span, document version and hash.
8. Offline operation: analysis and Q&A work with no provider configured.
9. Provider configuration is masked and SSRF-safe.
10. Report regenerated from a snapshot is byte-reproducible against the stored manifest.

---

## 3. Confirmed URLs and state

Served by the local app and by the production container (`0.0.0.0:8000`):

| URL | Result |
| --- | --- |
| `http://localhost:8000/` | Dashboard, HTTP 200 |
| `/documents/`, `/claims/`, `/graph/`, `/contradictions/`, `/decisions/`, `/reviews/`, `/ask/`, `/benchmarks/`, `/reports/`, `/audit/`, `/settings/` | 200, rendered |
| `/api/health` · `/api/health/ready` · `/api/health/version` | `ok` · `ready` · versions |
| `/api/docs` · `/api/redoc` · `/api/openapi.json` | Swagger UI · ReDoc · schema (77 paths / 22 schemas) |
| `/docs` · `/redoc` · `/openapi.json` | 307 → `/api/...` |
| `/api/audit/verify` | `valid: true` (live counter; 120 events at final capture) |

The same build also runs as the exported container image (uid 10001, `EGA_ENVIRONMENT=production`,
2 uvicorn workers). It was built with Docker and started with `docker compose up -d` on `:8000`:
`/`, `/claims/` and `/_next/static/*` returned 200, `/api/health/ready` reported
`{"status":"ready","environment":"production","workspaces":1,"documents":24,"claims":111,
"decisions":7}`, login + dashboard + `/api/audit/verify` (`valid: true`) all passed, a benchmark
run inside the container completed, and the Playwright suite passed **18/18**.

The same image was then connected to a host-published PostgreSQL 16.15 database on `:8002` using
host networking (equivalent to the application configuration the compose `postgres` profile
describes). Alembic created the schema (22 app tables plus `alembic_version`), two workers seeded
exactly once, `/api/health/ready` returned the same 24/111/7 counts, dashboard and audit
verification matched the SQLite run, a held-out benchmark took 5.7 s with all four targets at 1.0
(`retrieval_recall_at_5` 0.8917), and Playwright passed **18/18**. The authenticated original-PDF
preview was included in that run. The `vector` extension was deliberately *not* installed and
nothing failed — independent confirmation that the application does not depend on pgvector today.

**Seeded state:** 24 documents · 25 versions · 111 claims (94 verified, 8 disputed, 9 unsupported)
· 4 confirmed contradictions (3 blocking) · 7 decisions — 1 `CONTRADICTED`, 2 `EVIDENCE_INCOMPLETE`,
2 `SUFFICIENTLY_SUPPORTED`, 1 `APPROVED`, 1 `DRAFT` · evidence-health score 84.7.

Demo users (`<role>@northwind.example`, password `DemoPass!2026`): `admin`, `auditor`,
`reviewer`, `viewer`.

---

## 4. How to run and reproduce

### Ubuntu / Linux / macOS

```bash
cd evidence-graph-ai-auditor
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
cd frontend && npm ci && cd ..

cp .env.example .env                          # optional; defaults are fine
PYTHONPATH=backend .venv/bin/python -c "from app.seed import seed_demo; seed_demo()"

# build the UI once, then serve everything from FastAPI
cd frontend && npm run build && cd ..
PYTHONPATH=backend .venv/bin/uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000
# open http://localhost:8000  → login admin@northwind.example / DemoPass!2026
```

### Windows (PowerShell)

```powershell
cd evidence-graph-ai-auditor
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements-dev.txt
cd frontend; npm ci; cd ..

Copy-Item .env.example .env
$env:PYTHONPATH = "backend"
python -c "from app.seed import seed_demo; seed_demo()"
cd frontend; npm run build; cd ..
uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000
```

### Tests, lint, benchmarks

```bash
.venv/bin/ruff check backend/app backend/tests scripts
PYTHONPATH=backend .venv/bin/pytest backend/tests -q                 # 86 passed
# same suite on PostgreSQL:
EGA_TEST_DATABASE_URL=postgresql+psycopg://ega:ega@localhost:5432/ega_test \
  PYTHONPATH=backend .venv/bin/pytest backend/tests -q                 # 86 passed
cd frontend && npx tsc --noEmit && npm run test:unit                # 18 passed
EGA_BASE_URL=http://127.0.0.1:8000 npx playwright test              # 18 passed (needs a running server)
cd .. && PYTHONPATH=backend .venv/bin/python scripts/run_benchmark.py --dataset all
```

### Docker

```bash
cp .env.example .env         # then set a real EGA_SECRET_KEY; the image refuses to boot without one
docker compose build
docker compose up -d         # http://localhost:8000
docker compose --profile postgres up    # app on :8001 against PostgreSQL 16
```

The SQLite compose stack was executed in this environment: production startup, login, audit,
benchmark and Playwright all passed. Compose itself is now exercised, but the **PostgreSQL
compose pair was not**, because this sandbox blocks container-to-container TCP. That application
configuration was verified with a host-published PostgreSQL instead. See §9 for the precise caveat.

---

## 5. Test results

| Suite | Command | Result |
| --- | --- | --- |
| Backend (SQLite) | `pytest backend/tests -q` | **86 passed** (8.7 s) |
| Backend (PostgreSQL 16.15) | `EGA_TEST_DATABASE_URL=… pytest backend/tests -q` | **86 passed** (10.1 s) |
| Lint | `ruff check backend/app backend/tests scripts` | clean |
| Types | `tsc --noEmit` | clean |
| Frontend unit | `vitest run` | **18 passed** |
| Production build | `next build` | success, **20 static routes**, 103 kB shared JS |
| End-to-end | `npx playwright test` | **18 passed** against the local server (36.0 s), the Docker SQLite stack (37.8 s) and the Docker image on PostgreSQL (35.1 s) |
| Migrations | `alembic upgrade/downgrade/upgrade` on fresh SQLite and PostgreSQL DBs | clean, 22 tables |
| Benchmarks | `scripts/run_benchmark.py --dataset all` | all four targets PASS on both splits |

Test composition: ingestion and parsing, normalisation, claim extraction and review, contradiction
lifecycle, gate integrity (including exception and hash invariants), MCDA date semantics, grounded
Q&A focus and refusal, API/RBAC/tenancy, configuration fail-fast, multi-worker startup seeding, and
the ten acceptance scenarios.

---

## 6. Benchmark results (real, reproducible)

Offline baseline, deterministic, no model calls. Dev 4.0 s, held-out 3.1 s on 2 vCPU.

| Metric | Target | dev | held-out |
| --- | --- | --- | --- |
| claim_link_precision | ≥ 0.90 | 1.000 | 1.000 |
| contradiction_recall | ≥ 0.85 | 1.000 | 1.000 |
| citation_validity | ≥ 0.98 | 1.000 | 1.000 |
| gate_accuracy | ≥ 0.85 | 1.000 | 1.000 |
| claim precision / recall / F1 | — | 1.000 | 1.000 |
| contradiction precision / F1 | — | 1.000 | 1.000 |
| blocking_check_recall · freshness_detection · missing_document · supersession | — | 1.000 | 1.000 |
| **retrieval_recall_at_5** | — | **0.8167** | **0.8917** |
| retrieval_MRR | — | 1.000 | 1.000 |
| injection_containment | — | 1.000 | n/a |
| unsupported_conclusion_rate | — | 0.000 | 0.000 |

Gate confusion matrices are diagonal on both splits. Artefacts:
`evaluation/benchmarks/{dev,heldout}-20261022T1557*.json` plus `latest-{dev,heldout}.json`; the two
most recent API-triggered runs are stored in `benchmark_runs` and are what the dashboard renders.

**Read this honestly.** Perfect scores mean the synthetic corpus uses the phrasings and formats the
rule extractor targets; they are not evidence of real-world accuracy. The informative number is
recall@5 (0.82–0.89): the offline hashing embedder ranks the single best source first but misses
secondary documents that use different vocabulary. Full error analysis, including five defects the
benchmark exposed and their fixes, is in [`docs/benchmarks.md`](benchmarks.md#4-error-analysis).

---

## 7. Documents, screenshots and artefacts

| Artefact | Location |
| --- | --- |
| Orientation, quick starts, walkthrough | [`README.md`](../README.md) |
| Architecture, data model, trust boundaries | [`docs/architecture.md`](architecture.md) |
| API reference with payloads and curl flows | [`docs/API.md`](API.md) |
| Benchmark methodology, results, error analysis | [`docs/benchmarks.md`](benchmarks.md) |
| Evaluation harness and label format | [`evaluation/README.md`](../evaluation/README.md) |
| Dataset scenarios and file map | [`datasets/synthetic/README.md`](../datasets/synthetic/README.md) |
| Contribution rules | [`CONTRIBUTING.md`](../CONTRIBUTING.md) |
| Threat model and hardening checklist | [`SECURITY.md`](../SECURITY.md) |
| Release notes incl. every fixed defect | [`CHANGELOG.md`](../CHANGELOG.md) |
| 21 genuine UI captures | [`docs/images/`](images/) — captured 2026-10-22 16:23 UTC by `scripts/capture_screenshots.mjs` |

Screenshots are real browser captures against the live server with the seeded workspace; the
Grounded Q&A capture submits a real question and shows the returned citation, the Alternatives
capture shows the actual weighted scores (Cascade 0.800 vs Delta 0.200), and
`source-viewer-original.png` shows the authenticated PDF itself rendered in the source viewer
(captured headed under Xvfb because headless Chromium renders PDFs blank).

---

## 8. Provider setup

| Mode | Configuration |
| --- | --- |
| Offline (default) | No provider rows; analysis label `offline-deterministic`, Q&A `offline-retrieval` |
| OpenAI / compatible | `kind=openai`, `base_url=https://api.openai.com/v1`, key, model; works with vLLM/LM Studio/Azure-style gateways via `kind=custom` |
| Anthropic | `kind=anthropic`, key, model |
| OpenRouter | `kind=openrouter`, key, `vendor/model` |
| Ollama | `kind=ollama`, `base_url=http://localhost:11434`, requires `EGA_ALLOW_LOCAL_PROVIDER_URLS=true` |
| Custom | `kind=custom` with any OpenAI-compatible `/chat/completions` and `/embeddings` |

Roles are independent: generation, embedding and reranking can each point at a different provider.
Keys are encrypted with a key derived from `EGA_SECRET_KEY` (`sha256("ega-secret-v1:" + secret)`),
returned only as masked hints, and scrubbed from logs. Changing `EGA_SECRET_KEY` invalidates stored
keys — re-enter them.

**Paid endpoints not verified:** no paid credentials were available in this environment, so
OpenAI, Anthropic, OpenRouter and vendor-specific gateways were exercised only against mocks.
The local Ollama path was verified live with `gemma:2b`: connection test and discovery succeeded,
grounded Q&A returned a cited answer, and LLM analysis passed citation validation. Request
construction, retry/error handling, cost accounting and SSRF defence are unit/integration tested.

---

## 9. Unverified and blocked

| Item | Status | Why | What a reviewer should do |
| --- | --- | --- | --- |
| Docker image | **Built and run** | Built with Docker Desktop's daemon here; ran as uid 10001, production mode, two uvicorn workers. Health, login, audit, benchmark and Playwright passed. | Re-run `docker build` once in your registry environment |
| `docker compose up` (SQLite) | **Executed** | Production SQLite stack started on `:8000`; migrations, single seed, health, login, audit, benchmark and Playwright **18/18** passed | Re-run once on a normal host |
| `docker compose --profile postgres` | **Not executed as a pair** | This sandbox blocks container-to-container TCP, so the app container could not reach the `postgres` service. The *same app image* was verified against a host-published PostgreSQL 16.15 database over host networking; the exact compose wiring remains unproven | Run `docker compose --profile postgres up --build` on a normal host and confirm `/api/health/ready` |
| Docker `HEALTHCHECK` | **Configured, exercised through compose** | Compose reported the container healthy via `/api/health` once the SQLite stack was up | `docker inspect --format '{{.State.Health.Status}}' ega-app` after `compose up` |
| GitHub Actions CI | **Never run** | No runner; YAML parsed and every `run:` block syntax-checked, job graph reviewed (6 jobs incl. PostgreSQL parity) | Push to a branch and watch the first pipeline |
| Windows instructions | **Not executed** | Linux-only environment | Run the PowerShell block on a Windows host |
| PostgreSQL | **Executed** | PostgreSQL 16.15 installed/containerised locally; migrations, seed, full suite (**86/86**), benchmark parity and Playwright **18/18** all ran against it, including from the app image | nothing — but re-run once in CI |
| pgvector ANN index | **Not implemented** | Claim embeddings are a `JSON` column; cosine similarity is computed in Python (`services/retrieval.py`). The compose image ships the extension but no `vector` column or index is used | Add a `Vector` column + HNSW index and a dialect-aware retrieval path; measure recall/latency before switching |
| Neo4j store | **Stub, not wired** | `Neo4jGraphStore` only connects and exposes a projection query; it does not implement the `GraphStore` protocol | Implement the protocol and a factory in `services/graph.py`, then test against a server |
| Paid LLM providers | **Not executed** | No paid credentials; offline mode is fully functional and local Ollama was verified live | Configure one paid provider, run `/api/providers/{id}/test`, then `--include-llm` |
| Local Ollama | **Executed** | `gemma:2b` connection test, discovery, grounded Q&A and LLM analysis all passed against `http://localhost:11434` | Re-run with the model you intend to use; cold loads are slow here |
| OCR for scanned PDFs | **Not implemented** | Would add system dependencies and a new failure surface | Use PDFs with a text layer, or add an OCR parser with tests |
| Multi-worker / load behaviour | **Correctness verified, throughput not measured** | 2 uvicorn workers verified in the image (migrations, single seed, health); no load test | Load-test against PostgreSQL before scaling |
| Real-world extraction accuracy | **Unknown** | Evaluation corpus is synthetic | Expect materially lower recall on real contracts; budget for parser/predicate work |

---

## 10. Defects found and fixed during verification

Recorded because a report that only lists successes is not evidence of engineering.

1. **Decision-date vs. verification-date bug (functional, high).** Option comparison dropped claims
   that had expired "today" even when they were valid on the case's `as_of` date, so the comparison
   showed blanks next to a gate that used those claims. Fixed by scoring against `as_of` with an
   explicit staleness check; three regression tests added.
2. **`/api/workspace` returned 500 on every call** — the handler asked the policy dict for a
   `gate_version` key that only exists as a module constant. Fixed, with a regression test.
3. **Approval could be silently invalidated by re-analysis** — the attestation hash included an
   advisory check, so regenerating an analysis on unchanged evidence demoted `APPROVED`. The hash
   now binds deterministic checks only, and real demotions emit an audit event.
4. **Grounded Q&A answered with the whole claim list** — a warranty question also listed
   certification and risk. Answers are now ranked by the question's terms against predicate and
   value, deduplicated, capped at three statements, with an explicit refusal path. Covered by
   `test_qa_grounding.py`.
5. **Citations could have an empty locator** for text/email/spreadsheet spans. A shared
   `span_locator_label` now renders every span type.
6. **SPA catch-all shadowed `/docs`, `/redoc`, `/openapi.json`** — they returned the app shell with
   HTTP 200. Now 307 to the `/api` equivalents.
7. **Benchmark dashboard crashed on stored runs** — payload was not persisted; now stored whole.
8. **Non-deterministic citation selection** and unstable evidence ordering — fixed with a total
   order; repeated runs are byte-identical.
9. **CVE-2025-66478** in Next.js 15.1.6 — upgraded to 15.5.28.
10. Two README claims were wrong and corrected rather than shipped: a non-existent OCR setting and
    an overstated rate-limit default. The Docker/CI "verified" wording was also removed.
11. **Login returned 500 in the container image (production, high).** The image sets
    `EGA_ENVIRONMENT=production`; with no `EGA_SECRET_KEY` the app started anyway and every login
    failed at token signing. `Settings.startup_errors()` now runs in `lifespan`, logs a structured
    `configuration error` line and aborts startup. Verified inside the real image: the process exits
    with `RuntimeError: Invalid configuration: EGA_SECRET_KEY must be set to a unique value in
    production` and `Application startup failed. Exiting.` Two tests (`test_config_defaults.py`)
    pin both directions — production without a secret refuses, development does not.
12. **Multi-worker startup race created `workspaces.slug` duplicates.** `uvicorn --workers 2` runs the
    lifespan once per worker; both saw an empty database and both inserted the demo workspace, so
    the loser logged `sqlite3.IntegrityError: UNIQUE constraint failed: workspaces.slug` as an ERROR
    on first boot. `main.py` now serialises seeding with an `flock` on `<data_dir>/.seed.lock`,
    re-checks the workspace count under the lock, and downgrades a lost `IntegrityError` to an
    informational "created by another process". Covered by `test_startup_seeding.py` (three threads
    race a real seed and must produce exactly one workspace). Fix confirmed in the rebuilt image:
    one seed, no error, both workers healthy.
13. **JSON `DISTINCT` was invalid on PostgreSQL.** `SELECT DISTINCT` over a row containing JSON
    columns raised `could not identify an equality operator for type json`, so the dashboard 500'd on
    Postgres only. Replaced with id-only subqueries; a suite that previously passed 76/76 on SQLite
    and failed 2 tests on Postgres now passes 86/86 on both, which is why the CI workflow gained a
    real `backend-postgres` job.
14. **Embeddings were documented as pgvector but are stored as JSON.** Rather than ship the
    marketing version of the stack, the README, architecture diagram, `.env.example`, CHANGELOG and
    this report now say PostgreSQL without pgvector indexing and record the ANN index as roadmap.
15. **A model's "insufficient evidence" flag was ignored.** `grounded_answer` never propagated
    `llm_insufficient`, so a partial answer rendered as confident. The flag now propagates and the
    UI warns; regression test in `test_qa_grounding.py`.
16. **A failed reprocess deleted valid evidence.** Derived spans and claims were removed before new
    extraction completed. Rebuilds now run in a savepoint; failure restores the previous derived
    state and marks the version failed. Regression test in `test_ingestion_resilience.py`.
17. **Cross-workspace download used a substring check.** A path merely containing the workspace id
    could pass. Authorisation now resolves the stored path and requires it to be inside that
    workspace's upload root; regression test in `test_api.py`.
18. **SSRF validation checked literal IPs only.** A public-looking hostname resolving to loopback,
    a private range or cloud metadata could bypass it. Validation now resolves DNS, follows no
    redirects, and rejects any non-public address; tests cover DNS-based loopback and metadata
    targets.
19. **Benchmark selector disagreed with the displayed run.** The history defaults to the latest
    run (held-out in the seeded state), while the selector still said "dev split", implying a filter
    that did not exist. The selector is now labelled "Next run" and tracks the displayed run's
    split. The Playwright test asserts that they agree.

Static checks now clean: `ruff` 0 findings, `tsc` 0 errors, no broken relative links across the
documentation set.

---

## 11. GitHub readiness

| Check | State |
| --- | --- |
| Git repository initialised | yes — one coherent initial commit on `main`, working tree clean, **nothing pushed** |
| Commit hygiene | all tracked files added in a single purpose-built commit; generated runtime state excluded by `.gitignore` |
| `.gitignore` covers Python, Node, data, uploads, `.env`, caches | yes |
| `.env` excluded; `.env.example` contains no secrets | yes |
| Tracked artefacts | source, migrations, seeds, datasets, benchmark JSON, screenshots, docs |
| Untracked runtime state | `backend/data/` (SQLite + uploads + generated reports) — ignored |
| Licence | Apache-2.0 (`LICENSE`) |
| CI workflow present | `.github/workflows/ci.yml` (unexecuted) |
| Secrets scan performed | manual grep for key patterns during build; no keys in tracked files |
| Remote / push | **not configured — no remote was added**, per instruction not to publish |

To publish: create an empty remote, then `git remote add origin <url> && git push -u origin main`.
No remote is configured here, per the instruction not to publish without authorisation.

---

## 12. Ten-minute demo script

1. **Dashboard** — 24 documents, 111 claims, 4 contradictions, evidence-health 84.7.
2. **Documents → Apex quotation** — two versions; v1 superseded; hash and page locator visible.
3. **Claims → filter `price`** — the same predicate from quotation and contract, values differ.
4. **Contradictions** — Apex price conflict, blocking, both quotes shown; dismiss requires a reason.
5. **Decisions → Apex** — `CONTRADICTED`; open the Sufficiency checklist to see `no_blocking_contradictions` fail.
6. **Decisions → Cascade** — 10/10 checks pass, `SUFFICIENTLY_SUPPORTED`; Alternatives shows 0.800 vs 0.200 with stable sensitivity.
7. **Review & approval** — attestation hash, then `APPROVED` with `result_hash`.
8. **Grounded Q&A** — ask "What is the warranty for Cascade Pumps?"; then ask about the capital of France to show refusal.
9. **Benchmarks** — dev/held-out metrics against targets and the confusion matrix.
10. **Audit trail** — verify the hash chain; export CSV; open a report and its snapshot manifest.

---

## 13. Bottom line

The evidence graph, the deterministic gate, the provenance chain, the offline AI baseline, the
evaluation harness and the 15-screen UI are implemented, tested and verified working in this
environment. Benchmarks meet their targets on both splits, and the one genuinely informative
metric — retrieval recall — is reported at 0.82–0.89 rather than hidden.

Docker packaging is proven for the SQLite compose stack, and the same image is verified against
PostgreSQL when the database is reachable. Three pieces remain unproven: the exact compose
PostgreSQL pair (blocked by this sandbox's container networking), GitHub Actions (no runner), and
paid provider round-trips (no credentials). Everything else marked "not verified" above requires
an external service or a different OS. Until those are run, this should be treated as a fully
working local application with partially proven packaging.
