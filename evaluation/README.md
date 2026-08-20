# Evaluation

Tooling that measures the auditor against labelled ground truth. Nothing here is used by the
application at runtime; the benchmark runs in an isolated database and never reads ground truth
before scoring.

```
evaluation/
├── ground_truth/
│   ├── dev.json        # 45 labelled claims, 4 contradiction pairs, 7 decisions
│   └── heldout.json    # 51 labelled claims, 4 contradiction pairs, 6 decisions
└── benchmarks/
    ├── dev-<UTC>.json        # immutable artefact per run
    ├── heldout-<UTC>.json
    └── latest-dev.json       # symlinked copy the README/dashboard read
```

## Running

```bash
PYTHONPATH=backend .venv/bin/python scripts/run_benchmark.py --dataset all
PYTHONPATH=backend .venv/bin/python scripts/run_benchmark.py --dataset heldout --include-llm
```

`--include-llm` additionally asks the configured generation provider to summarise each blocking
decision and validates every statement's citations. Without credentials the run refuses and says
so; it never silently downgrades to the offline baseline and reports a model score.

The same evaluation is available in the UI (**Benchmarks** screen) and over the API, which stores
the full payload in `benchmark_runs` so historical runs render identically.

## Ground-truth format

`ground_truth/<split>.json`:

| Key | Meaning |
| --- | --- |
| `claim_predicates_in_scope` | Predicates the run is allowed to score; claims outside are ignored, not counted as errors |
| `claims[]` | `{doc_key, predicate, subject, value, unit/currency, locator}` — the link the extractor must produce |
| `contradiction_pairs[]` | `{a, b, kind, blocking}` — pairs that must be confirmed |
| `non_contradiction_pairs[]` | Pairs that must **not** be confirmed (different scope/period) |
| `supersession[]` | Document with two versions and the winning value |
| `obsolete[]` | Documents that are expired at `as_of` and whose claims must not count |
| `missing_documents[]` | Decision plus the source types it lacks |
| `decisions[]` | Expected gate status, workflow status, blocking check |
| `citation_spot_checks[]` | Claim plus the exact quoted substring that must resolve |
| `injection_defense` | Documents whose content must be flagged and excluded |

Labels are authored from the scenario specification, not from application output. See
`docs/benchmarks.md` for the metric definitions, the recorded results and the error analysis.

## Adding a case

1. Add the documents to `datasets/synthetic/build.py` and rebuild.
2. Ingest them locally and inspect what the extractor produced — *then* write the label that
   describes what the document actually asserts, independently of what the extractor found.
3. Add the expected gate outcome (`SUFFICIENTLY_SUPPORTED`, `CONTRADICTED`,
   `EVIDENCE_INCOMPLETE`, …) and, if applicable, the missing-requirement or blocking check.
4. Run both splits; a dev-only improvement that regresses held-out is a red flag, not a win.

Do not add labels keyed on document filenames, ids or ordering. The scoring code must remain
content-driven: a label identifies a document by `doc_key`, and the matcher compares predicates,
subjects and normalised values.

## Regression gates

`scripts/run_benchmark.py` exits non-zero when any metric falls below its target
(`claim_link_precision ≥ 0.90`, `contradiction_recall ≥ 0.85`, `citation_validity ≥ 0.98`,
`gate_accuracy ≥ 0.85`). CI runs both splits on every push, so a change that damages extraction
fails the build rather than quietly shipping.
