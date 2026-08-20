# API reference

Base URL `http://localhost:8000`. All routes live under `/api`; the browser UI is served from the
same origin, so the frontend calls relative URLs with no CORS setup.

- Interactive docs: <http://localhost:8000/api/docs> (Swagger UI), `/api/redoc`, `/api/openapi.json`
- Machine-readable: `GET /api/openapi.json` — **86 operations across 77 paths, 22 schemas**
- `/docs`, `/redoc` and `/openapi.json` return a 307 redirect to the `/api` equivalents.

## Conventions

| Topic | Behaviour |
| --- | --- |
| Auth | `Authorization: Bearer <jwt>` on everything except `/api/auth/login` and `/api/health*` |
| Errors | `{"detail": "..."}` with 400 (bad input), 401 (no/invalid token), 403 (role), 404 (missing or other workspace), 409 (state conflict), 422 (schema), 429 (rate limit) |
| Pagination | `page` (1-based) and `page_size` (≤ 200) → `{items, total, page, pages, page_size}` |
| Ordering | Stable, total ordering everywhere; repeated calls return identical payloads |
| Correlation | Every response carries `X-Correlation-ID`; the same id appears in the JSON log line |
| Idempotency | Uploading identical bytes returns the existing version with `"duplicate": true` |
| Timestamps | UTC ISO-8601; dates are `YYYY-MM-DD` |

Roles: `admin` > `auditor` > `reviewer` > `viewer`. Permissions are enforced per route
(`claim:read`, `decision:write`, `decision:approve`, `provider:write`, …); unknown roles fail closed.

## Authentication

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"admin@northwind.example","password":"DemoPass!2026"}' | jq -r .access_token)
```

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/auth/login` | Exchange email + password for a bearer token |
| POST | `/api/auth/logout` | Record a logout audit event |
| GET | `/api/auth/me` | Current user, role, permissions, workspace |

## Health and workspace

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Liveness: `{"status":"ok", ...}` |
| GET | `/api/health/ready` | Readiness: database reachable |
| GET | `/api/health/version` | App/report/gate/policy versions |
| GET | `/api/workspace` | Workspace, policy version, gate version, freshness windows, requirements |
| GET | `/api/dashboard` | Every headline metric, computed from stored state |
| GET | `/api/search?q=` | Hybrid claim search (BM25 + embeddings + fusion) |

```json
// GET /api/health/version
{"name":"Evidence-Graph AI Auditor","version":"1.0.0","api":"v1",
 "gate":"gate-1.2","policy":"policy-2026.1","report_spec":"report-1.0"}
```

## Documents and provenance

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/documents` | List with filters (`doc_type`, `status`, `q`) |
| POST | `/api/documents` | Upload (multipart `file`, optional `doc_key`, `doc_type`, dates) |
| GET | `/api/documents/{id}` | Document with all immutable versions and the current one |
| GET | `/api/documents/{id}/versions/{vid}/text` | Extracted text |
| GET | `/api/documents/{id}/versions/{vid}/spans` | Typed spans with locators |
| GET | `/api/documents/{id}/versions/{vid}/file` | Original file (served with a safe content type) |
| POST | `/api/documents/{id}/versions/{vid}/reprocess` | Re-run parsing/extraction on a version |
| DELETE | `/api/documents/{id}/versions/{vid}` | Delete a non-current version |
| GET | `/api/documents/meta/types` | Known document types and their policy roles |

```bash
curl -X POST localhost:8000/api/documents \
  -H "Authorization: Bearer $TOKEN" \
  -F 'file=@datasets/synthetic/dev/files/apex_quote_v1.pdf' \
  -F 'doc_key=apex-quote' -F 'doc_type=quote'
```

Uploading the same `doc_key` with different bytes creates version 2, marks version 1
`superseded`, and records a `SUPERSEDES` edge. Identical bytes return the existing version.

## Claims

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/claims` | Filter by `predicate`, `status`, `document_id`, `decision_id`, `q`; paginated |
| GET | `/api/claims/{id}` | Claim, span, locator, citation resolution, version hash, linked decisions |
| GET | `/api/claims/{id}/trace` | Source → span → claim → decision → analysis chain |
| POST | `/api/claims/{id}/review` | `{"action": "verify" \| "flag" \| "reject" \| "reset", "note": "..."}` |
| GET | `/api/claims/facets` | Predicate/status/document counts for filters |
| GET | `/api/claims/export.csv` | CSV of the current filter |

```json
// GET /api/claims/{id}  (abridged)
{"id":"75c2…","predicate":"price","subject":"Cascade Pumps Ltd","object":"USD 89,400",
 "verification_status":"verified","extraction_method":"rule_based",
 "quoted_span":"Total price: USD 89,400 for 12 units",
 "span":{"type":"page","label":"PDF page","page":1,"bbox":[46.0,178.3,197.36,187.5]},
 "citation":{"resolves":true,"reason":""},
 "version":{"content_hash":"9f2c…","version_number":1,"is_current":true},
 "document":{"id":"…","title":"Cascade Pumps quotation","doc_type":"quote"},
 "edges":[{"relation":"DERIVED_FROM","source_type":"Claim","target_type":"SourceSpan"},
          {"relation":"SUPPORTS","source_type":"Claim","target_type":"Decision"}]}
```

## Contradictions

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/contradictions` | `state` (`possible`/`confirmed`/`dismissed`), `severity`, `blocking` |
| POST | `/api/contradictions/detect` | Re-run detection for the workspace or one decision |
| GET | `/api/contradictions/{id}` | Both claims side by side, with locators and context |
| POST | `/api/contradictions/{id}/confirm` | Promote `possible` → `confirmed` (`{"note": "..."}`) |
| POST | `/api/contradictions/{id}/dismiss` | Dismiss; **requires a written justification** |
| POST | `/api/contradictions/{id}/resolve` | Resolve after a source correction |
| GET | `/api/contradictions/summary` | Counts by state/severity/blocking |

Only `confirmed` contradictions with a blocking severity fail the gate. Dismissing one does not
delete it and does not change the evidence record; it changes the review state with an audit trail.

## Decisions and the gate

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/decisions` | List with gate status, workflow status, blocking failures |
| POST | `/api/decisions` | Create: `title`, `decision_type` (**required**), plus `options[]`, `as_of_date`, `description`, `document_ids[]` |
| GET | `/api/decisions/types` | Decision types with required sources and requirements |
| GET | `/api/decisions/{id}` | Full case, options, documents, latest gate checks |
| PATCH | `/api/decisions/{id}` | Update title/description/as-of/options |
| POST | `/api/decisions/{id}/documents` | Attach `{"document_ids": [...]}` |
| DELETE | `/api/decisions/{id}/documents/{doc_id}` | Detach |
| POST | `/api/decisions/{id}/evaluate` | **Run the deterministic gate**; optional `{"reason": "..."}`. Returns `{decision, gate}` and stores checks + `result_hash` |
| GET | `/api/decisions/{id}/checklist` | 10 checks with detail and evidence refs |
| GET | `/api/decisions/{id}/mcda` | Weighted comparison at the case's `as_of` date |
| POST | `/api/decisions/{id}/analysis` | Run evidence-constrained analysis (`{"use_llm": false}` for offline) |
| GET | `/api/decisions/{id}/analysis` | Stored analyses with statement validation |
| POST | `/api/decisions/{id}/ask` | Ask a question scoped to this decision |
| POST | `/api/decisions/{id}/attest` | Reviewer attestation: `{"comment": "I reviewed all cited evidence"}` |
| POST | `/api/decisions/{id}/approve` | Approve: `{"comment": "..."}`; refuses unless gate passes, attestation exists, actor ≠ creator |
| POST | `/api/decisions/{id}/reject` | Reject: `{"comment": "..."}` (required by policy) |
| POST | `/api/decisions/{id}/request-changes` | Return to the author: `{"comment": "..."}` |
| POST | `/api/decisions/{id}/reopen` | Reopen an approved/rejected case (audited) |
| POST | `/api/decisions/{id}/exception` | Record an admin exception: `{"reason": "…"}` (≥ 20 chars, never changes gate results) |
| GET | `/api/decisions/{id}/audit` | Decision-scoped audit trail |
| GET | `/api/decisions/{id}/snapshots` / POST | List or create an evidence snapshot |
| GET | `/api/decisions/{id}/usage` | Token/cost usage attributable to the case |

```json
// GET /api/decisions/{id}/checklist  (abridged)
{"status":"SUFFICIENTLY_SUPPORTED","gate_status":"SUFFICIENTLY_SUPPORTED",
 "gate_version":"gate-1.2","policy_version":"policy-2026.1","as_of":"2025-09-01",
 "checks":[{"code":"documents_attached","label":"Evidence documents attached","status":"pass",
            "blocking":false,"detail":"3 document(s), 3 current version(s), 8 claim(s) in scope.",
            "refs":[{"type":"Document","id":"34a4…"}]}, "..."]}
```

State machine: `DRAFT → EVIDENCE_INCOMPLETE | CONTRADICTED → REVIEW_REQUIRED |
SUFFICIENTLY_SUPPORTED → APPROVED | REJECTED`, with `reopen` back to `REVIEW_REQUIRED`. Illegal
transitions return 409 and are not written.

Approval requires all of: gate status `SUFFICIENTLY_SUPPORTED`, no blocking failures, a reviewer
attestation whose pre-attestation hash matches the current evidence hash, an approver role, and an
approver different from the case creator. `SUFFICIENTLY_SUPPORTED` is not `APPROVED` — the second
is a human act on top of a machine result.

```json
// POST /api/decisions/{id}/evaluate  (abridged)
{"gate": {"gate_version": "gate-1.2", "policy_version": "policy-2026.1", "as_of": "2025-09-01",
          "status": "EVIDENCE_INCOMPLETE",
          "checks": [{"code": "mandatory_sources", "status": "fail", "blocking": true,
                      "detail": "Missing mandatory document type(s): risk_register."}, "..."]},
 "decision": {"id": "…", "status": "EVIDENCE_INCOMPLETE", "gate_status": "EVIDENCE_INCOMPLETE"}}
```

An ineligible approval is refused with **409** and an actionable message, for example:

```json
{"detail":"Decision is not eligible for approval: gate status is EVIDENCE_INCOMPLETE
 (blocking failures: mandatory_sources, requirement_coverage; warnings: none)."}
```

Attestation is bound to the evidence state: if documents or claims change after it was recorded,
the attestation no longer satisfies the check and the reviewer has to attest again.

## Grounded Q&A

```bash
curl -X POST localhost:8000/api/analysis/ask \
  -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"question":"What is the warranty for Cascade Pumps?","decision_id":"<id>","use_llm":false}'
```

```json
{"question":"What is the warranty for Cascade Pumps?",
 "answer":"Cascade Pumps Ltd — Warranty: 36 months. [1]",
 "statements":[{"id":"S-QA-0","text":"Cascade Pumps Ltd — Warranty: 36 months.",
                "claim_ids":["c807…"],"supported":true,"validation_notes":[]}],
 "citations":[{"claim_id":"c807…","document_title":"Cascade Pumps quotation",
               "quote":"Warranty: 36 months from commissioning","locator":"page 1",
               "verification_status":"verified"}],
 "insufficient_evidence":false,"search_method":"hybrid(bm25+hashing-embedding+feature-rerank)",
 "provider":"offline-retrieval","invalid_citations":[]}
```

Questions with no supporting claim return `insufficient_evidence: true`, an explicit message and no
statements. Statements that fail citation validation are excluded from the answer and listed in
`invalid_citations`. See `/api/analysis/usage` for token and cost accounting.

## Graph

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/graph?decision_id=&entity_id=&limit=` | Nodes and edges for the explorer |
| GET | `/api/graph/stats` | Coverage, verified rate, contradiction counts, expiry |
| GET | `/api/graph/impact/{version_id}` | Documents/claims/decisions affected by a version |
| GET | `/api/graph/queries/{name}?decision_id=` | Named native queries (below) |

Named queries: `evidence_for_decision`, `trace_recommendation`, `missing_requirement_coverage`,
`decisions_on_obsolete_records`, `contradictory_claims_for_entity` (requires `entity_id`).
Edge types: `SUPPORTS`, `CONTRADICTS`, `DERIVED_FROM`, `MENTIONS`, `SUPERSEDES`, `REQUIRES`,
`DEPENDS_ON`, `INVALIDATES`, `REVIEWED_BY`, `INFORMS_DECISION` (plus structural `PART_OF`).

## Providers

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/providers` | List with **masked** key hints only |
| POST | `/api/providers` | Add: `name` + `kind` (required), `base_url`, `api_key`, `model`, `embedding_model`, `rerank_model`, `is_default_generation`, `is_default_embedding` |
| PATCH | `/api/providers/{id}` | Update name/model/enabled/default flags |
| DELETE | `/api/providers/{id}` | Remove (also wipes the stored key) |
| POST | `/api/providers/{id}/test` | Live connection test (never echoes the key) |
| POST | `/api/providers/{id}/discover-models` | List models the endpoint advertises |
| GET | `/api/providers/types` | Supported kinds and default base URLs |
| GET | `/api/providers/usage/summary` | Tokens, requests and estimated cost by provider/model |

Roles are independent: generation, embedding and reranking can use different providers. Base URLs
are validated (scheme, host, private ranges, cloud metadata IPs) unless local URLs are explicitly
allowed for Ollama. Keys are encrypted at rest and appear only as `sk-…1234`-style hints.

## Benchmarks

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/benchmarks` | Stored runs with metrics and targets |
| POST | `/api/benchmarks/run` | `{"dataset": "dev" \| "heldout", "include_llm": false, "limit": null}` (the CLI adds `all`) |
| GET | `/api/benchmarks/{run_id}` | Full payload: confusion matrices, per-case detail, environment |
| GET | `/api/benchmarks/ground-truth/{dataset}` | Labelled claims/pairs/decisions for review |

## Reports, snapshots and audit

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/reports` | List generated reports |
| POST | `/api/decisions/{id}/reports` | `{"decision_id": "...", "kind": "pdf" \| "json" \| "manifest" \| "csv"}` |
| GET | `/api/reports/{id}` | Report metadata, manifest hashes |
| GET | `/api/reports/{id}/download` | Download the artefact |
| POST | `/api/reports/{id}/regenerate` | Rebuild with the current spec |
| GET | `/api/decisions/{id}/snapshots` | Evidence snapshots |
| POST | `/api/decisions/{id}/snapshots` | Create a snapshot (canonical payload hash) |
| GET | `/api/snapshots/{id}/download` | Snapshot JSON |
| GET | `/api/snapshots/{id}/verify` | **Recompute** the hash and compare |
| GET | `/api/audit?event_type=&actor_id=&limit=` | Append-only audit log |
| GET | `/api/audit/verify` | Verify the whole hash chain |
| GET | `/api/audit/export.csv` | CSV export of the trail |

```json
// GET /api/audit/verify
{"events":117,"valid":true,"errors":[],
 "head_hash":"55522cbc…","verified_at":"2026-10-21T18:47:05.290978+00:00"}
```

## Users

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/users` | List workspace users |
| POST | `/api/users` | Create a user: `email`, `name`, `password`, `role` (`admin` only) |
| PATCH | `/api/users/{id}` | Change role/name/active flag (`admin` only) |

## Example: full decision flow by hand

```bash
# 1. create
ID=$(curl -s -X POST localhost:8000/api/decisions -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{
    "title":"Cascade Pumps maintenance award","decision_type":"procurement",
    "options":[{"name":"Cascade Pumps Ltd"}],"as_of_date":"2025-09-01"}' | jq -r .id)

# 2. attach evidence (document ids from /api/documents)
curl -X POST localhost:8000/api/decisions/$ID/documents -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"document_ids":["<doc1>","<doc2>"]}'

# 3. evaluate the gate (deterministic, no model involved)
curl -X POST localhost:8000/api/decisions/$ID/evaluate -H "Authorization: Bearer $TOKEN"

# 4. run an offline analysis (advisory only)
curl -X POST localhost:8000/api/decisions/$ID/analysis -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"use_llm": false}'

# 5. reviewer attests, then a different user approves
curl -X POST localhost:8000/api/decisions/$ID/attest -H "Authorization: Bearer $REVIEWER" \
  -H 'content-type: application/json' -d '{"comment":"I reviewed all cited evidence."}'
curl -X POST localhost:8000/api/decisions/$ID/approve -H "Authorization: Bearer $ADMIN" \
  -H 'content-type: application/json' -d '{"comment":"Approved per policy-2026.1"}'
```

## Rate limits

`EGA_API_RATE_LIMIT_PER_MINUTE` (default 600) applies per client to the general API, with a
separate, stricter bucket for login. Exceeding either returns 429 with `Retry-After`. Limits are
in-process, so a multi-replica deployment needs a shared limiter in front.
