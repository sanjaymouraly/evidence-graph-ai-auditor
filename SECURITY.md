# Security policy

## Reporting a vulnerability

Do **not** open a public issue for a suspected vulnerability. Report it privately through the
repository host's security advisory feature (GitHub: *Security → Advisories → Report a
vulnerability*), including:

- affected version or commit;
- the smallest reproducible request or file;
- impact (confidentiality, integrity, availability, or evidence integrity);
- whether the issue requires an authenticated role.

Please allow a reasonable disclosure window before publishing details. There is no bug bounty and
no guaranteed response time; this is a small project, not a commercial support offering.

## Supported versions

| Version | Supported |
| --- | --- |
| `1.x` | Yes, security fixes land on the latest `1.x` release |
| `< 1.0` | No |

## Threat model

The system is a single-tenant-per-workspace audit application. The assets that matter are the
**integrity of evidence and gate outcomes**, the **confidentiality of uploaded documents**, and
**provider credentials**. The primary adversaries considered are:

| Adversary | Capability assumed | Primary controls |
| --- | --- | --- |
| Malicious uploaded document | Controls bytes and text in a PDF/DOCX/XLSX/CSV/email | Parser isolation, size/page/zip caps, prompt-injection screening, untrusted-data framing, no execution |
| Authenticated user with a lower role | Can call the API directly, not only the UI | Server-side RBAC on every route, workspace-scoped queries, 404 for cross-workspace ids |
| Attacker controlling a provider URL | Can create/edit a provider as admin, or influence config | Scheme/host validation, DNS resolution checks, metadata/private-range blocking, no redirects |
| Network attacker | Can observe unencrypted traffic in a misconfigured deployment | `EGA_REQUIRE_HTTPS`, HSTS/proxy guidance, no credentials in URLs |
| Curious operator | Can read the database file and process memory | API keys encrypted at rest; secrets never returned by the API or logged |
| Model output | May be wrong, hostile or injected by document text | Citation validation, number grounding, gate-outcome assertion rejection, advisory-only status |

Out of scope: a compromised host/root user, a compromised model provider, supply-chain compromise
of a pinned dependency, and legal/compliance certification. The application is a review aid, not a
certified audit product.

## Security controls in this repository

### Authentication and authorisation

- Password hashes use the configured password hasher; login is rate-limited
  (`EGA_AUTH_RATE_LIMIT_PER_MINUTE`).
- JWTs are signed with `EGA_SECRET_KEY`; production startup refuses a missing or placeholder
  secret.
- Every API route declares a required permission (`document:read`, `decision:approve`,
  `provider:create`, `*`, …). UI hiding is not a control.
- Workspace isolation is enforced in queries, not by trusting client-supplied workspace ids.
  Cross-workspace document ids return 404.
- A decision's creator cannot approve their own case; attestation and approval are separate
  actions bound to a gate snapshot hash.

### Uploads and parsing

- Extension and MIME allow-list; configurable upload size, PDF page and zip-entry limits.
- Filenames are sanitised; files are stored under a per-workspace, per-version directory with
  exclusive creation (no overwrite race).
- Downloads verify that the resolved file path is inside the caller's workspace upload root.
- Parsers never execute document content. PDFs without a text layer are rejected/warned rather
  than silently OCR'd.
- Instruction-like spans are flagged at ingest and excluded from claim extraction and gate
  coverage.

### AI providers

- Provider base URLs must be `http`/`https`, must not embed credentials, and are rejected if they
  are — or resolve to — loopback, private, link-local, reserved, multicast, unspecified or cloud
  metadata addresses. DNS is resolved at validation time, not only for literal IPs. Redirects are
  not followed.
- Local/private endpoints (Ollama, LAN gateways) are disabled unless
  `EGA_ALLOW_LOCAL_PROVIDER_URLS=true`. Keep this false in shared deployments.
- API keys are encrypted with a key derived from `EGA_SECRET_KEY`, masked in responses, redacted
  from error strings, and never written to reports or benchmark artefacts.
- Model output is untrusted: statements need resolvable claim citations, numbers must be grounded
  in cited evidence, and assertions about gate/approval outcomes are rejected. The model cannot
  mutate gate state.
- Provider calls have timeouts and bounded retries. A failure falls back to deterministic
  analysis with the reason recorded, rather than fabricating a narrative.

### Integrity and audit

- Document versions are immutable and content-hashed. Supersession is explicit.
- The audit log is an append-only SHA-256 hash chain; `/api/audit/verify` reports the first break.
- Reports bind a frozen snapshot: document hashes, claim set, gate result and audit head.
  Regenerating from a snapshot can be compared byte-for-byte via the stored manifest.
- Gate results are deterministic and versioned (`gate-1.2`, `policy-2026.1`); exceptions are
  recorded alongside results and never change them.

### Operations

- Structured JSON logs with request correlation ids.
- `/api/health` (liveness), `/api/health/ready` (database checks) and `/api/health/version`.
- Production refuses to start with an invalid/missing secret.
- The Docker image runs as an unprivileged uid and writes only to `/app/data`.
- The compose file requires `EGA_SECRET_KEY` to be set (it fails fast otherwise).

## Hardening checklist for a real deployment

- [ ] Set a unique, high-entropy `EGA_SECRET_KEY`; do not reuse the example value. Rotating it
      invalidates stored provider keys and active sessions.
- [ ] Set `EGA_ENVIRONMENT=production` and `EGA_REQUIRE_HTTPS=true` behind a TLS-terminating
      proxy. Forward only trusted proxy headers.
- [ ] Keep `EGA_ALLOW_LOCAL_PROVIDER_URLS=false` unless you deliberately need Ollama/LAN access.
- [ ] Put PostgreSQL and the data volume on encrypted storage; restrict filesystem access to the
      service account.
- [ ] Back up the database and uploads together; a database backup without its uploads cannot
      verify citations.
- [ ] Run one writer/migration process at a time. The container starts two workers; verify your
      deployment's migration strategy before scaling further.
- [ ] Set rate limits appropriate to your traffic; the defaults are development-oriented.
- [ ] Review the demo seeder. Set `EGA_SEED_DEMO_DATA=false` in production unless the synthetic
      workspace is intentional.
- [ ] Restrict egress from the application host if provider URLs should only reach an allow-list.
- [ ] Monitor failed logins, provider failures, audit verification failures and ingestion errors.
- [ ] Treat model output as advisory. Require a human attestation and approval for decisions.

## Known security-relevant limitations

- **No MFA/SSO.** Authentication is password + JWT. Use an upstream identity-aware proxy if you
  need SSO or MFA.
- **Single-tenant seeding.** Workspace creation and tenant administration are not self-service.
- **No OCR sandbox.** OCR is not implemented; parsers run in-process, so a parser vulnerability
  would affect the API process. Run untrusted uploads in a separate worker if that is in scope.
- **DNS rebinding window.** Provider URLs are re-validated and DNS is resolved per request, but a
  determined attacker who controls DNS could still race resolution and connection. Egress
  filtering is the stronger control.
- **No formal external audit or penetration test.** The test suite exercises the controls above;
  it is not a substitute for an independent review.
- **Model providers receive evidence.** When a remote provider is enabled, the prompt includes
  retrieved claims and quoted spans. Do not enable a remote provider for documents that may not
  leave your network; use Ollama/offline mode instead.

## Dependency and disclosure policy

Dependencies are pinned in `backend/requirements*.txt` and `frontend/package-lock.json`. Security
fixes should update the relevant lockfile and note the change in `CHANGELOG.md`. Do not add a
dependency that executes uploaded content or phones home during ingestion.
