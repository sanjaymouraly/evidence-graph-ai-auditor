"""Seed a workspace from a synthetic dataset. Idempotent per workspace slug unless --force."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.core.security import hash_password
from app.db.models import BenchmarkRun, DecisionCase, User, Workspace
from app.db.session import init_db, session_scope
from app.services.analysis import run_analysis
from app.services.audit import append_event
from app.services.contradictions import detect_contradictions
from app.services.decisions import create_case
from app.services.gate import create_attestation, evaluate_case, final_approval
from app.services.ingestion import ingest_document, process_version
from app.services.reports import generate_report
from app.services.retrieval import ensure_embeddings

log = logging.getLogger(__name__)
DATASET_ROOT = Path(__file__).resolve().parent.parent.parent / "datasets" / "synthetic"


def load_manifest(dataset: str) -> tuple[Path, dict]:
    base = DATASET_ROOT / dataset
    manifest_path = base / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Dataset manifest missing: {manifest_path}")
    return base, json.loads(manifest_path.read_text())


def seed_dataset_into_workspace(db, base: Path, manifest: dict, *, workspace_slug: str,
                                workspace_name: str) -> dict:
    ws = Workspace(name=workspace_name, slug=workspace_slug,
                   settings={"seeded_from": manifest.get("dataset"), "demo": True})
    db.add(ws)
    db.flush()
    users = {}
    for u in manifest["users"]:
        user = User(workspace_id=ws.id, email=u["email"].lower(), name=u["name"], role=u["role"],
                    password_hash=hash_password(u["password"]), is_active=True)
        db.add(user)
        users[u["email"].lower()] = user
    db.commit()
    admin = next((u for u in users.values() if u.role == "admin"), list(users.values())[0])
    reviewer = next((u for u in users.values() if u.role == "reviewer"), admin)
    doc_ids: dict[str, str] = {}
    ingested = []
    for doc in manifest["documents"]:
        path = base / "files" / doc["file"]
        data = path.read_bytes()
        result = ingest_document(
            db, admin, doc["file"], data, title=doc["title"], doc_type=doc["doc_type"],
            doc_key=doc["doc_key"], effective_date=doc.get("effective_date"),
            valid_until=doc.get("valid_until"), primary_entity=doc.get("primary_entity"),
            trust_level=float(doc.get("trust_level", 0.7)), description="Synthetic dataset document",
        )
        if result.get("duplicate"):
            doc_ids[doc["doc_key"]] = result["document_id"]
            continue
        processed = process_version(result["version_id"], admin.id)
        ingested.append({"file": doc["file"], **result, "processing": processed})
        doc_ids[doc["doc_key"]] = result["document_id"]
        log.info("seeded %s -> %s (%s)", doc["file"], processed.get("status"),
                 f"{processed.get('claims', 0)} claims")
    # Contradictions must exist before decisions are evaluated; otherwise gate results
    # would be computed against an incomplete evidence state.
    detect_contradictions(db, ws.id, commit=True)
    decisions = []
    for spec in manifest["decisions"]:
        case = create_case(
            db, admin, title=spec["title"], decision_type=spec["decision_type"],
            description=spec.get("description", ""), options=spec.get("options", []),
            as_of_date=spec.get("as_of_date"),
            document_ids=[doc_ids[k] for k in spec.get("documents", []) if k in doc_ids],
        )
        action_results = []
        for action in spec.get("actions", []):
            if action == "evaluate":
                res = evaluate_case(db, case, actor=admin, reason="seed evaluation")
                action_results.append({"action": "evaluate", "status": res["status"]})
            elif action == "analysis":
                analysis = run_analysis(db, admin, case, use_llm=True)
                action_results.append({"action": "analysis", "status": analysis.status,
                                       "provider": analysis.provider_label})
            elif action == "attest":
                res = create_attestation(db, case, reviewer, "Seed reviewer attestation after evidence review")
                action_results.append({"action": "attest", **res})
            elif action == "approve":
                res = final_approval(db, case, reviewer, "Seed approval after gate verification")
                action_results.append({"action": "approve", **res})
        decisions.append({"id": case.id, "title": case.title, "status": case.status,
                          "gate_status": case.gate_status, "actions": action_results})
    detect_contradictions(db, ws.id, commit=True)
    for case in db.execute(select(DecisionCase).where(DecisionCase.workspace_id == ws.id)).scalars():
        if (case.gate_result or {}) and case.status not in {"APPROVED", "REJECTED"}:
            evaluate_case(db, case, reason="post-ingestion reconciliation")
    ensure_embeddings(db, ws.id, "local-hashing-256", get_settings().embedding_dim)
    append_event(db, ws.id, "workspace.seeded", "Workspace", ws.id,
                 {"dataset": manifest.get("dataset"), "documents": len(manifest["documents"]),
                  "decisions": len(decisions)}, actor=admin, commit=True)
    return {"workspace_id": ws.id, "workspace_slug": workspace_slug, "users": list(users.keys()),
            "documents": ingested, "decisions": decisions}


def seed_demo(*, force: bool = False) -> dict:
    init_db()
    base, manifest = load_manifest("dev")
    with session_scope() as db:
        existing = db.execute(select(Workspace).where(Workspace.slug == "synth-dev")).scalar_one_or_none()
        if existing and not force:
            return {"skipped": True, "workspace_id": existing.id, "reason": "workspace already seeded"}
        if existing and force:
            raise ValueError("Workspace 'synth-dev' already exists. Use scripts/purge_workspace.py to remove it first.")
        result = seed_dataset_into_workspace(db, base, manifest, workspace_slug="synth-dev",
                                             workspace_name=manifest["workspace"]["name"])
    with session_scope() as db:
        admin = db.execute(select(User).where(User.workspace_id == result["workspace_id"],
                                              User.role == "admin")).scalars().first()
        cases = [c for c in result["decisions"] if c["status"] in {"APPROVED", "SUFFICIENTLY_SUPPORTED"}]
        for target in cases[:2]:
            case = db.get(DecisionCase, target["id"])
            if case:
                generate_report(db, case, "pdf", admin)
                generate_report(db, case, "json", admin)

        # A fresh installation must have something real on the benchmark dashboard. Run the
        # published offline baseline for both splits once and store the actual results; the
        # dashboard then renders stored runs instead of an empty state. Failures are logged
        # and never block seeding.
        from app.services.benchmark import run_benchmark
        for dataset in ("dev", "heldout"):
            already = db.execute(select(BenchmarkRun).where(
                BenchmarkRun.workspace_id == result["workspace_id"],
                BenchmarkRun.dataset == dataset)).scalars().first()
            if already:
                continue
            try:
                run_benchmark(db, admin, dataset, include_llm=False)
                log.info("seeded benchmark run for %s", dataset)
            except Exception:  # pragma: no cover - optional demo enrichment
                log.exception("benchmark seeding failed for %s; dashboard will show empty state", dataset)
    return result


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Seed the demo workspace")
    parser.add_argument("--dataset", default="dev")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    out = seed_demo(force=args.force)
    print(json.dumps(out, indent=2, default=str)[:4000])
