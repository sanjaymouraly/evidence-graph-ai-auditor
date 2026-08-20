"""Reproducible benchmark harness over the shipped synthetic datasets.

The harness never special-cases filenames, IDs or expected answers inside application
logic. It builds a disposable database, runs the same ingestion / contradiction / gate
pipeline the product uses, and compares the results with independently authored labels
in ``evaluation/ground_truth/<dataset>.json``.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import BASE_DIR, get_settings
from app.core.security import hash_password
from app.db.models import (Base, Claim, Contradiction, DecisionCase, Document, DocumentVersion,
                           Edge, SourceSpan, User, Workspace)
from app.db.session import override_session_factory
from app.services.analysis import deterministic_analysis, validate_statements
from app.services.claims import citation_resolves
from app.services.contradictions import detect_contradictions
from app.services.decisions import create_case
from app.services.gate import create_attestation, evaluate_case, final_approval, decision_claims
from app.services.ingestion import ingest_document, process_version
from app.services.normalize import normalize_standard, normalize_text_value
from app.services.predicates import P
from app.services.retrieval import ensure_embeddings, hybrid_search

REPO_ROOT = BASE_DIR.parent
DATASET_DIR = REPO_ROOT / "datasets" / "synthetic"
GROUND_TRUTH_DIR = REPO_ROOT / "evaluation" / "ground_truth"
TARGETS = {"claim_link_precision": 0.90, "contradiction_recall": 0.85, "citation_validity": 0.98,
           "gate_accuracy": 0.85}


def load_ground_truth(dataset: str) -> dict:
    path = GROUND_TRUTH_DIR / f"{dataset}.json"
    if not path.exists():
        raise FileNotFoundError(f"Ground truth for dataset '{dataset}' not found at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_manifest(dataset: str) -> dict:
    path = DATASET_DIR / dataset / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"Dataset manifest not found at {path}; run datasets/synthetic/build.py")
    return json.loads(path.read_text(encoding="utf-8"))


def _value_matches(claim: Claim, label: dict) -> bool:
    predicate = label["predicate"]
    spec = P.get(predicate)
    if spec is None:
        return False
    kind = spec.kind
    expected = label.get("value")
    if kind in {"money", "percent", "number", "duration"}:
        if claim.value_numeric is None or expected is None:
            return False
        expected_f = float(expected)
        tolerance = {"money": 0.005, "percent": 0.01, "number": 0.01, "duration": 1.0}[kind]
        if kind == "duration":
            if abs(claim.value_numeric - expected_f) > tolerance:
                return False
        else:
            scale = max(abs(expected_f), abs(claim.value_numeric), 1e-9)
            if abs(claim.value_numeric - expected_f) > max(tolerance, tolerance * scale):
                return False
        if label.get("currency") and (claim.currency or "").upper() != label["currency"].upper():
            return False
        if label.get("unit") and (claim.unit or "").lower() != label["unit"].lower():
            return False
        return True
    if kind == "date":
        return (claim.value_text or "").startswith(str(expected))
    if kind == "ordinal":
        return (claim.value_text or "").strip().lower() == str(expected).strip().lower()
    if kind == "standard":
        return normalize_standard(claim.value_text or claim.object_text) == normalize_standard(str(expected))
    return normalize_text_value(str(expected)) in normalize_text_value(claim.value_text or claim.object_text)


def _label_entity_ok(claim: Claim, label: dict, entities: dict[str, str]) -> bool:
    if not label.get("entity"):
        return True
    canonical = entities.get(claim.entity_id or "")
    return bool(canonical) and normalize_text_value(canonical) == normalize_text_value(label["entity"])


def _doc_key_for(claim: Claim, versions: dict[str, DocumentVersion], documents: dict[str, Document]) -> str:
    version = versions.get(claim.version_id)
    document = documents.get(version.document_id) if version else None
    return document.doc_key if document else ""


def _safe_div(numerator: float, denominator: float) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _seeded_workspace(db: Session, manifest: dict) -> tuple[Workspace, User, User, dict[str, str]]:
    ws = Workspace(name=f"Benchmark {manifest['dataset']}", slug=f"bench-{manifest['dataset']}-{int(time.time())}",
                   settings={"benchmark": True, "dataset": manifest["dataset"]})
    db.add(ws)
    db.flush()
    admin_spec = next(u for u in manifest["users"] if u["role"] == "admin")
    reviewer_spec = next((u for u in manifest["users"] if u["role"] == "reviewer"), admin_spec)
    admin = User(workspace_id=ws.id, email=f"bench-{admin_spec['email']}", name=admin_spec["name"],
                 password_hash=hash_password("Benchmark!2026"), role="admin")
    reviewer = User(workspace_id=ws.id, email=f"bench-{reviewer_spec['email']}", name=reviewer_spec["name"],
                    password_hash=hash_password("Benchmark!2026"), role="reviewer")
    db.add_all([admin, reviewer])
    db.commit()
    doc_ids: dict[str, str] = {}
    for spec in manifest["documents"]:
        path = DATASET_DIR / manifest["dataset"] / "files" / spec["file"]
        result = ingest_document(
            db, admin, spec["file"], path.read_bytes(), title=spec["title"], doc_type=spec["doc_type"],
            doc_key=spec["doc_key"], effective_date=spec.get("effective_date"),
            valid_until=spec.get("valid_until"), primary_entity=spec.get("primary_entity"),
            trust_level=float(spec.get("trust_level", 0.7)), description="Benchmark corpus document",
        )
        doc_ids[spec["doc_key"]] = result["document_id"]
        if not result.get("duplicate"):
            process_version(result["version_id"], actor_id=admin.id)
    return ws, admin, reviewer, doc_ids


def run_benchmark(primary_db: Session, user: User, dataset: str = "heldout", *,
                  include_llm: bool = False, limit: int | None = None) -> dict:
    manifest = load_manifest(dataset)
    truth = load_ground_truth(dataset)
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc)
    errors: list[dict] = []
    tmp = tempfile.mkdtemp(prefix=f"ega-bench-{dataset}-")
    engine = create_engine(f"sqlite:///{Path(tmp) / 'benchmark.db'}",
                           connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    settings = get_settings()
    workspace_id = None
    try:
        with override_session_factory(factory):
            db = factory()
            ws, admin, reviewer, doc_ids = _seeded_workspace(db, manifest)
            workspace_id = ws.id
            detect_contradictions(db, ws.id, as_of=_parse_date(truth.get("as_of")), commit=True)
            ensure_embeddings(db, ws.id, "local-hashing-256", settings.embedding_dim)

            decisions = []
            specs = manifest["decisions"][:limit] if limit else manifest["decisions"]
            for spec in specs:
                try:
                    case = create_case(
                        db, admin, title=spec["title"], decision_type=spec["decision_type"],
                        description=spec.get("description", ""), options=spec.get("options", []),
                        as_of_date=spec.get("as_of_date"),
                        document_ids=[doc_ids[k] for k in spec.get("documents", []) if k in doc_ids],
                    )
                except Exception as exc:  # pragma: no cover - surfaced in report
                    errors.append({"stage": "create_decision", "decision": spec["title"], "error": str(exc)[:300]})
                    continue
                actions = spec.get("actions", [])
                if actions:
                    evaluate_case(db, case, actor=admin, reason="benchmark evaluation")
                    if "attest" in actions:
                        try:
                            create_attestation(db, case, reviewer, "Benchmark reviewer attestation")
                        except Exception as exc:
                            errors.append({"stage": "attest", "decision": spec["title"], "error": str(exc)[:300]})
                    if "approve" in actions:
                        try:
                            final_approval(db, case, reviewer, "Benchmark approval after gate verification")
                        except Exception as exc:
                            errors.append({"stage": "approve", "decision": spec["title"], "error": str(exc)[:300]})
                decisions.append(case)

            claims, versions, documents, spans = _all_claims(db, ws.id)
            entities = {e.id: e.canonical_name for e in _entities(db, ws.id)}
            contradiction_rows = db.execute(
                select(Contradiction).where(Contradiction.workspace_id == ws.id)).scalars().all()

            metrics, details = _score(truth, claims, versions, documents, spans, entities,
                                      contradiction_rows, decisions, db, ws.id, include_llm=include_llm)
            corpus = {
                "documents": len({d.id for d in documents.values()}),
                "versions": len(versions), "claims": len(claims),
                "decisions": len(decisions), "contradictions": len(contradiction_rows),
            }
            db.close()
    finally:
        engine.dispose()
        shutil.rmtree(tmp, ignore_errors=True)
        if workspace_id:
            shutil.rmtree(Path(settings.upload_dir) / workspace_id, ignore_errors=True)

    duration = round(time.perf_counter() - started, 3)
    targets = {name: {"target": target, "actual": metrics.get(name), "pass": (metrics.get(name) or 0) >= target}
               for name, target in TARGETS.items()}
    result = {
        "dataset": dataset, "status": "completed",
        "started_at": started_at.isoformat(), "duration_seconds": duration,
        "corpus": corpus, "metrics": metrics, "targets": targets, "confusion": details.pop("confusion"),
        "details": details, "errors": errors,
        "environment": {"offline_baseline": True, "include_llm": include_llm,
                        "embedding": "local-hashing-256", "gate": "gate-1.2", "policy": "policy-2026.1"},
    }
    from app.db.models import BenchmarkRun
    run = BenchmarkRun(workspace_id=user.workspace_id, dataset=dataset, status="completed",
                       metrics=result["metrics"], confusion=result["confusion"],
                       payload={**result, "run_id": None},
                       duration_seconds=duration, created_by=user.id)
    primary_db.add(run)
    primary_db.commit()
    result["run_id"] = run.id
    run.payload = {**result}
    primary_db.add(run)
    primary_db.commit()
    return result


def _parse_date(value: str | None):
    from datetime import date
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _all_claims(db: Session, workspace_id: str):
    rows = db.execute(
        select(Claim, DocumentVersion, Document, SourceSpan)
        .join(DocumentVersion, Claim.version_id == DocumentVersion.id)
        .join(Document, DocumentVersion.document_id == Document.id)
        .outerjoin(SourceSpan, Claim.span_id == SourceSpan.id)
        .where(Claim.workspace_id == workspace_id)
    ).all()
    claims = [r[0] for r in rows]
    versions = {v.id: v for v in db.execute(
        select(DocumentVersion).where(DocumentVersion.workspace_id == workspace_id)).scalars()}
    documents = {d.id: d for d in db.execute(
        select(Document).where(Document.workspace_id == workspace_id)).scalars()}
    spans = {s.id: s for s in db.execute(
        select(SourceSpan).where(SourceSpan.workspace_id == workspace_id)).scalars()}
    return claims, versions, documents, spans


def _entities(db: Session, workspace_id: str):
    from app.db.models import Entity
    return db.execute(select(Entity).where(Entity.workspace_id == workspace_id)).scalars().all()


def _claim_sort_key(claim: Claim) -> tuple:
    return (claim.predicate, claim.value_numeric if claim.value_numeric is not None else -1,
            claim.value_text or "")


def _score(truth: dict, claims: list[Claim], versions: dict[str, DocumentVersion],
           documents: dict[str, Document], spans: dict[str, SourceSpan], entities: dict[str, str],
           contradictions: list[Contradiction], decisions: list[DecisionCase], db: Session,
           workspace_id: str, *, include_llm: bool = False) -> tuple[dict, dict]:
    in_scope_predicates = set(truth.get("claim_predicates_in_scope", []))
    labels = truth["claims"]
    scope_pairs = {(label["doc_key"], label["predicate"]) for label in labels}
    current_claims = [c for c in claims if versions.get(c.version_id) and versions[c.version_id].is_current
                      and not c.duplicate_of_id and c.review_status != "rejected"]
    in_scope = [c for c in current_claims
                if c.predicate in in_scope_predicates
                and (_doc_key_for(c, versions, documents), c.predicate) in scope_pairs]
    matched_claims: list[Claim] = []
    matched_labels: set[int] = set()
    claim_matches: dict[str, dict] = {}
    for claim in in_scope:
        doc_key = _doc_key_for(claim, versions, documents)
        for index, label in enumerate(labels):
            if index in matched_labels:
                continue
            if label["doc_key"] != doc_key or label["predicate"] != claim.predicate:
                continue
            if not _label_entity_ok(claim, label, entities):
                continue
            if _value_matches(claim, label):
                matched_labels.add(index)
                matched_claims.append(claim)
                claim_matches[claim.id] = {"label": label, "claim": claim}
                break
    claim_precision = _safe_div(len(matched_claims), len(in_scope))
    claim_recall = _safe_div(len(matched_labels), len(labels))
    claim_f1 = _safe_div(2 * claim_precision * claim_recall, claim_precision + claim_recall)

    link_ok = 0
    for claim in matched_claims:
        span = spans.get(claim.span_id)
        resolves, _ = citation_resolves(claim, span)
        if resolves:
            link_ok += 1
    claim_link_precision = _safe_div(link_ok, len(matched_claims))

    citation_ok = 0
    citation_total = 0
    for claim in current_claims:
        span = spans.get(claim.span_id)
        if span is None:
            continue
        citation_total += 1
        resolves, _ = citation_resolves(claim, span)
        citation_ok += 1 if resolves else 0
    citation_validity = _safe_div(citation_ok, citation_total)

    active = [c for c in contradictions if c.state in {"possible", "confirmed"}]
    matched_contradictions: list[Contradiction] = []
    contradiction_details = []
    claim_by_id = {c.id: c for c in claims}
    for pair in truth["contradiction_pairs"]:
        found = None
        for contradiction in active:
            if contradiction in matched_contradictions:
                continue
            a = claim_by_id.get(contradiction.claim_a_id)
            b = claim_by_id.get(contradiction.claim_b_id)
            if not a or not b:
                continue
            if (_claim_matches_side(a, pair["a"], versions, documents, entities)
                    and _claim_matches_side(b, pair["b"], versions, documents, entities)) or \
               (_claim_matches_side(a, pair["b"], versions, documents, entities)
                    and _claim_matches_side(b, pair["a"], versions, documents, entities)):
                found = contradiction
                break
        expected = pair.get("expected_state")
        blocking_expected = pair.get("expected_blocking")
        if found:
            matched_contradictions.append(found)
            contradiction_details.append({
                "id": pair["id"], "found": True, "kind": found.kind, "state": found.state,
                "blocking": found.blocking, "kind_ok": found.kind == pair.get("expected_kind"),
                "state_ok": found.state == expected,
                "blocking_ok": found.blocking == blocking_expected,
            })
        else:
            contradiction_details.append({"id": pair["id"], "found": False,
                                         "expected_kind": pair.get("expected_kind")})
    contradiction_recall = _safe_div(len(matched_contradictions), len(truth["contradiction_pairs"]))
    contradiction_precision = _safe_div(len(matched_contradictions), len(active))
    contradiction_f1 = _safe_div(2 * contradiction_precision * contradiction_recall,
                                 contradiction_precision + contradiction_recall)
    blocking_ok = [d for d in contradiction_details if d["found"] and d["blocking_ok"]]
    contradiction_blocking_accuracy = _safe_div(len(blocking_ok), len(matched_contradictions))

    false_positive_pairs = []
    for pair in truth.get("non_contradiction_pairs", []):
        for contradiction in active:
            a = claim_by_id.get(contradiction.claim_a_id)
            b = claim_by_id.get(contradiction.claim_b_id)
            if not a or not b:
                continue
            if (_claim_matches_side(a, pair["a"], versions, documents, entities)
                    and _claim_matches_side(b, pair["b"], versions, documents, entities)) or \
               (_claim_matches_side(a, pair["b"], versions, documents, entities)
                    and _claim_matches_side(b, pair["a"], versions, documents, entities)):
                false_positive_pairs.append({"id": pair["id"], "reason": pair.get("reason"),
                                             "kind": contradiction.kind})
    unlabelled_detections = [c for c in active if c not in matched_contradictions]

    decision_by_title = {d.title: d for d in decisions}
    gate_correct = 0
    final_correct = 0
    blocking_check_correct = 0
    decision_details = []
    confusion: dict[str, dict[str, int]] = {}
    for spec in truth["decisions"]:
        case = decision_by_title.get(spec["title"])
        if case is None:
            decision_details.append({"title": spec["title"], "found": False})
            continue
        expected_gate = spec.get("expected_gate_status")
        actual_gate = case.gate_status
        expected_final = spec.get("expected_final_status")
        actual_final = case.status
        blocking = list((case.gate_result or {}).get("blocking_failures", []))
        checks_ok = all(check in blocking for check in spec.get("expected_blocking_checks", []))
        if expected_gate is None:
            gate_match = actual_gate is None
        else:
            gate_match = actual_gate == expected_gate
        final_match = actual_final == expected_final
        gate_correct += 1 if gate_match else 0
        final_correct += 1 if final_match else 0
        blocking_check_correct += 1 if checks_ok else 0
        key_expected = expected_gate or "NOT_EVALUATED"
        key_actual = actual_gate or "NOT_EVALUATED"
        confusion.setdefault(key_expected, {})
        confusion[key_expected][key_actual] = confusion[key_expected].get(key_actual, 0) + 1
        decision_details.append({
            "title": spec["title"], "found": True,
            "expected_gate": expected_gate, "actual_gate": actual_gate, "gate_match": gate_match,
            "expected_final": expected_final, "actual_final": actual_final, "final_match": final_match,
            "expected_blocking_checks": spec.get("expected_blocking_checks", []),
            "actual_blocking_checks": blocking, "blocking_checks_ok": checks_ok,
            "warnings": (case.gate_result or {}).get("warnings", []),
        })
    gate_accuracy = _safe_div(gate_correct, len(truth["decisions"]))
    final_status_accuracy = _safe_div(final_correct, len(truth["decisions"]))
    blocking_check_recall = _safe_div(blocking_check_correct, len(truth["decisions"]))

    obsolete_details = []
    for entry in truth.get("obsolete", []):
        doc_claims = [c for c in current_claims if _doc_key_for(c, versions, documents) == entry["doc_key"]]
        unsupported = [c for c in doc_claims
                       if c.verification_status == "unsupported"
                       and "expired" in str((c.meta or {}).get("verification_reason", "")).lower()]
        obsolete_details.append({"doc_key": entry["doc_key"], "claims": len(doc_claims),
                                 "flagged_unsupported": len(unsupported),
                                 "pass": bool(doc_claims) and len(unsupported) == len(doc_claims)})
    freshness_detection_accuracy = _safe_div(sum(1 for d in obsolete_details if d["pass"]),
                                             len(obsolete_details))

    missing_details = []
    for entry in truth.get("missing_documents", []):
        case = decision_by_title.get(entry["decision_title"])
        found_missing = set()
        if case:
            for check in (case.gate_result or {}).get("checks", []):
                if check.get("code") == "mandatory_sources":
                    for doc_type in entry["missing_doc_types"]:
                        if doc_type in check.get("detail", ""):
                            found_missing.add(doc_type)
        missing_details.append({"decision": entry["decision_title"],
                                "expected": entry["missing_doc_types"],
                                "detected": sorted(found_missing),
                                "pass": set(entry["missing_doc_types"]) <= found_missing})
    missing_document_accuracy = _safe_div(sum(1 for d in missing_details if d["pass"]),
                                          len(missing_details))

    supersession_details = []
    for entry in truth.get("supersession", []):
        document = next((d for d in documents.values() if d.doc_key == entry["document"]), None)
        ok = False
        detail = {}
        if document:
            doc_versions = [v for v in versions.values() if v.document_id == document.id]
            current = next((v for v in doc_versions if v.is_current), None)
            previous = [v for v in doc_versions if not v.is_current]
            has_edge = any(e.relation == "SUPERSEDES" and e.source_type == "DocumentVersion"
                           for e in db.execute(select(Edge).where(
                               Edge.workspace_id == workspace_id,
                               Edge.target_type == "DocumentVersion",
                               Edge.target_id.in_([v.id for v in previous]) if previous else False)).scalars()) \
                if previous else False
            current_value = None
            previous_value = None
            if current:
                current_claims = [c for c in claims if c.version_id == current.id and c.predicate == "price"]
                current_value = current_claims[0].value_numeric if current_claims else None
            if previous:
                prev_claims = [c for c in claims if c.version_id == previous[0].id and c.predicate == "price"]
                previous_value = prev_claims[0].value_numeric if prev_claims else None
            ok = bool(current and previous and has_edge
                      and current_value == entry["current_value"]
                      and previous_value == entry["previous_value"])
            detail = {"current_value": current_value, "previous_value": previous_value,
                      "supersedes_edge": has_edge, "versions": len(doc_versions)}
        supersession_details.append({"document": entry["document"], "pass": ok, **detail})
    supersession_accuracy = _safe_div(sum(1 for d in supersession_details if d["pass"]),
                                      len(supersession_details))

    retrieval_details = []
    recall_hits = 0
    mrr_total = 0.0
    for spec in truth["decisions"]:
        relevant = set(spec.get("relevant_doc_keys", []))
        case = decision_by_title.get(spec["title"])
        if not relevant or case is None:
            continue
        query = f"{case.title}. {case.description or ''}".strip()
        # Rank distinct sources from the top 25 retrieved claims, then score the first five
        # sources. This measures source-level retrieval, not the density of claims per page.
        # Source-level retrieval: rank each source by its single best-matching claim, then
        # score the top five sources. Ranking claims directly would reward sources that
        # merely contain many near-duplicate lines (a spreadsheet) over a short certificate.
        result = hybrid_search(db, workspace_id, query, top_k=60,
                               filters={"as_of": case.as_of_date} if case.as_of_date else None)
        best: dict[str, float] = {}
        for row in result["results"]:
            best[row["document_id"]] = max(best.get(row["document_id"], 0.0), row["score"])
        ranked_doc_ids = sorted(best, key=best.get, reverse=True)
        doc_keys = [documents[d].doc_key for d in ranked_doc_ids
                    if d in documents and documents[d].doc_key][:5]
        hits = relevant & set(doc_keys)
        recall = _safe_div(len(hits), len(relevant))
        recall_hits += recall
        rank = next((i + 1 for i, key in enumerate(doc_keys) if key in relevant), None)
        mrr = round(1 / rank, 4) if rank else 0.0
        mrr_total += mrr
        retrieval_details.append({"decision": spec["title"], "recall_at_5": recall, "mrr": mrr,
                                  "retrieved_doc_keys": doc_keys, "relevant_doc_keys": sorted(relevant)})
    retrieval_recall = _safe_div(recall_hits, len(retrieval_details))
    retrieval_mrr = _safe_div(mrr_total, len(retrieval_details))

    injected = []
    for doc_key in (truth.get("injection_defense", {}).get("doc_keys") or []):
        document = next((d for d in documents.values() if d.doc_key == doc_key), None)
        if document is None:
            injected.append({"doc_key": doc_key, "found": False, "contained": False})
            continue
        versions = db.execute(select(DocumentVersion).where(DocumentVersion.document_id == document.id)).scalars().all()
        flagged = any(bool(v.injection_flags) for v in versions)
        leaked = db.execute(select(func.count(Claim.id)).where(
            Claim.version_id.in_([v.id for v in versions]), Claim.injection_flag.is_(False))).scalar() or 0
        injected.append({"doc_key": doc_key, "found": True, "flagged": flagged,
                         "unflagged_claims": int(leaked), "contained": bool(flagged and leaked == 0)})
    injection_rate = (_safe_div(sum(1 for i in injected if i.get("contained")), len(injected))
                      if injected else None)

    unsupported_total = 0
    statements_total = 0
    analysis_details = []
    for case in decisions:
        if not (case.gate_result or {}):
            continue
        decision_claim_list, decision_versions, decision_documents, _ = decision_claims(db, case)
        analysis = deterministic_analysis(case, case.gate_result, decision_claim_list,
                                          decision_versions, decision_documents, {})
        statements = analysis.get("statements", [])
        index = {c.id: c for c in decision_claim_list}
        validated, validation = validate_statements(statements, index, case.gate_result,
                                                    context_text=case.title)
        unsupported = [s for s in validated if not s.get("supported")]
        statements_total += len(statements)
        unsupported_total += len(unsupported)
        analysis_details.append({"decision": case.title, "statements": len(statements),
                                 "unsupported": len(unsupported),
                                 "validation": validation})
    unsupported_rate = _safe_div(unsupported_total, statements_total)

    claim_error_examples = [{"doc_key": _doc_key_for(c, versions, documents), "predicate": c.predicate,
                             "value": c.object_text, "quote": (c.quoted_span or "")[:160]}
                            for c in in_scope if c not in matched_claims][:25]
    missing_labels = [labels[i] for i in range(len(labels)) if i not in matched_labels]

    metrics = {
        "claim_precision": claim_precision, "claim_recall": claim_recall, "claim_f1": claim_f1,
        "claim_link_precision": claim_link_precision,
        "citation_validity": citation_validity,
        "contradiction_precision": contradiction_precision,
        "contradiction_recall": contradiction_recall,
        "contradiction_f1": contradiction_f1,
        "contradiction_blocking_accuracy": contradiction_blocking_accuracy,
        "gate_accuracy": gate_accuracy, "final_status_accuracy": final_status_accuracy,
        "blocking_check_recall": blocking_check_recall,
        "freshness_detection_accuracy": freshness_detection_accuracy,
        "missing_document_accuracy": missing_document_accuracy,
        "supersession_accuracy": supersession_accuracy,
        "retrieval_recall_at_5": retrieval_recall, "retrieval_mrr": retrieval_mrr,
        "injection_containment": injection_rate,
        "unsupported_conclusion_rate": unsupported_rate,
        "counts": {
            "labelled_claims": len(labels), "matched_labels": len(matched_labels),
            "produced_in_scope_claims": len(in_scope), "matched_claims": len(matched_claims),
            "labelled_contradictions": len(truth["contradiction_pairs"]),
            "matched_contradictions": len(matched_contradictions),
            "active_contradictions": len(active),
            "unlabelled_active_contradictions": len(unlabelled_detections),
            "citation_checked_claims": citation_total,
            "decisions": len(truth["decisions"]),
        },
    }
    details = {
        "confusion": {"gate": confusion},
        "claims": {"missing_labels": missing_labels, "unexpected_claims": claim_error_examples,
                   "matched": len(matched_claims), "in_scope": len(in_scope)},
        "contradictions": contradiction_details,
        "unlabelled_contradictions": [{"kind": c.kind, "state": c.state, "severity": c.severity,
                                       "blocking": c.blocking, "reasoning": c.reasoning[:220]}
                                      for c in unlabelled_detections],
        "non_contradictions_flagged": false_positive_pairs,
        "decisions": decision_details,
        "missing_documents": missing_details,
        "obsolete_evidence": obsolete_details,
        "supersession": supersession_details,
        "retrieval": retrieval_details, "injection_defense": injected,
        "analysis": analysis_details,
    }
    return metrics, details


def _claim_matches_side(claim: Claim, side: dict, versions: dict[str, DocumentVersion],
                        documents: dict[str, Document], entities: dict[str, str]) -> bool:
    if _doc_key_for(claim, versions, documents) != side["doc_key"]:
        return False
    if claim.predicate != side["predicate"]:
        return False
    return _value_matches(claim, side) and _label_entity_ok(claim, side, entities)
