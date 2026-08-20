# Contributing

This project's value is auditability, so changes are judged by whether they preserve
deterministic behaviour and a defensible evidence trail — not only by whether the happy path
works.

## Ground rules

1. **The gate is deterministic.** Never let a model, a wall-clock read, or iteration order change a
   gate outcome. If you add an input, it must be stored, versioned and included in the gate hash.
2. **No evidence without provenance.** Every claim must resolve to a stored source span and a
   document version. New predicates need an extraction rule, a locator and a test.
3. **Fail closed on ambiguity.** Unsupported provider responses, invalid citations, expired
   evidence and parser failures must be surfaced. Do not silently substitute plausible output.
4. **Do not weaken tests to make a change pass.** If an invariant is wrong, change the invariant
   deliberately and explain it in the pull request and `CHANGELOG.md`.
5. **Keep the offline path working.** A fresh checkout with no API key must seed, evaluate,
   analyse, ask questions, generate reports and pass the test suite.

## Development setup

Requires Python 3.12 and Node.js 20 (the versions used by the Docker image and CI).

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt

cd frontend
npm ci
npm run build
cd ..

cp .env.example .env                                   # optional
PYTHONPATH=backend .venv/bin/python -c "from app.seed import seed_demo; seed_demo()"
PYTHONPATH=backend .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000> and sign in as `admin@northwind.example` / `DemoPass!2026`.
The demo workspace is synthetic; see [`datasets/synthetic/README.md`](datasets/synthetic/README.md).

## Checks before opening a pull request

```bash
.venv/bin/ruff check backend/app backend/tests scripts
PYTHONPATH=backend .venv/bin/pytest backend/tests -q

cd frontend
npx tsc --noEmit
npm run test:unit
npm run build
# with the server running:
EGA_BASE_URL=http://127.0.0.1:8000 npx playwright test
cd ..

PYTHONPATH=backend .venv/bin/python scripts/run_benchmark.py --dataset all
```

The benchmark CLI exits non-zero when a target is missed. Do not commit a run that lowers a
target without documenting why in `docs/benchmarks.md`.

## Adding a parser

1. Implement a parser in `backend/app/parsers/` that returns a `ParsedDocument` with typed spans
   and locators (page + bbox, paragraph, sheet + cell, or character offsets).
2. Register the extension in `backend/app/parsers/registry.py` and add it to the upload
   allow-list. Keep the existing size/page/zip caps.
3. Flag instruction-like text with the shared injection detector; flagged spans must not be
   extracted into claims.
4. Add a fixture and a provenance test in `backend/tests/`. The test must assert the locator and
   the quoted text, not only the extracted value.
5. Update the format table in `README.md` and `docs/architecture.md`.

## Adding a predicate

1. Add the predicate and label in `backend/app/services/predicates.py`.
2. Add an extraction rule with a quoted sentence and confidence in
   `backend/app/services/claims.py` (or the relevant extractor).
3. Add a contradiction rule only if the new values can genuinely conflict; scope, period and
   currency must be checked before reporting a conflict.
4. Add tests for extraction, normalisation and citation resolution.
5. If the predicate participates in a gate requirement, update the policy in
   `backend/app/services/policy.py` and add an acceptance scenario.

## Changing gate policy

Gate policy is versioned (`policy-2026.1`, gate `gate-1.2`). A policy change requires:

- a new policy version (never mutate a shipped version in place);
- a migration note for existing decisions;
- a regression test proving old snapshots still verify;
- an update to `docs/architecture.md` and the gate checks listed in `README.md`.

## Database changes

Use Alembic, not ad-hoc DDL:

```bash
cd backend
PYTHONPATH=. alembic revision -m "describe change"
PYTHONPATH=. alembic upgrade head
PYTHONPATH=. alembic downgrade -1
PYTHONPATH=. alembic upgrade head
```

The startup path can auto-create a fresh schema, but production upgrades must go through
migrations. Verify the downgrade works; a revision that cannot be reversed is not acceptable for
an evidence system.

## Benchmarks and ground truth

- `dev` is the development split; `heldout` must remain untouched while tuning rules.
- Add labels to `evaluation/ground_truth/` before changing extraction code, not after.
- Never tune on `heldout`. If you do, say so and the numbers are no longer held-out.
- Benchmark result JSON belongs in `evaluation/benchmarks/`; scratch files are ignored.

## Commits and pull requests

- Keep commits focused and describe the invariant being preserved or changed.
- Do not commit `backend/data/`, `.env`, API keys, generated reports, Playwright artefacts or
  benchmark scratch directories.
- In the pull request, list the commands you ran and their results. If something is unverified,
  say so explicitly.
- Security issues should follow [`SECURITY.md`](SECURITY.md), not a public issue.

## License

By contributing you agree that your contribution is licensed under the Apache-2.0 license in
[`LICENSE`](LICENSE).
