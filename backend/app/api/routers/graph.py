"""Evidence graph exploration endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.security import require_permission
from app.db.models import User
from app.db.session import get_db
from app.services.graph import build_graph, graph_stats, impact_of_version, queries

router = APIRouter(prefix="/api/graph", tags=["graph"])

GRAPH_QUERIES = {
    "evidence_for_decision": "All claims supporting a decision case",
    "contradictory_claims_for_entity": "Contradictions involving an entity",
    "decisions_on_obsolete_records": "Decisions whose evidence contains stale material",
    "missing_requirement_coverage": "Unmet requirements for a decision",
    "trace_recommendation": "Trace AI statements back to source spans",
}


@router.get("")
def graph(node_types: str | None = Query(default=None, description="Comma separated node types"),
          relations: str | None = Query(default=None),
          q: str | None = None, focus: str | None = Query(default=None, description="Node id or search term"),
          depth: int = Query(default=2, ge=1, le=4),
          decision_id: str | None = None,
          limit: int = Query(default=400, ge=10, le=2000),
          user: User = Depends(require_permission("graph:read")),
          db: Session = Depends(get_db)) -> dict:
    result = build_graph(
        db, user.workspace_id,
        node_types={t.strip() for t in node_types.split(",") if t.strip()} if node_types else None,
        relations={r.strip().upper() for r in relations.split(",") if r.strip()} if relations else None,
        q=q, focus_key=focus, depth=depth, limit=limit, decision_id=decision_id,
    )
    result["available_queries"] = GRAPH_QUERIES
    return result


@router.get("/stats")
def stats(user: User = Depends(require_permission("graph:read")), db: Session = Depends(get_db)) -> dict:
    return graph_stats(db, user.workspace_id)


@router.get("/queries/{name}")
def run_query(name: str, decision_id: str | None = None, entity_id: str | None = None,
              analysis_id: str | None = None,
              user: User = Depends(require_permission("graph:read")),
              db: Session = Depends(get_db)) -> dict:
    if name not in GRAPH_QUERIES:
        raise HTTPException(status_code=404, detail=f"Unknown query. Available: {sorted(GRAPH_QUERIES)}")
    params = {k: v for k, v in {"decision_id": decision_id, "entity_id": entity_id,
                                "analysis_id": analysis_id}.items() if v}
    try:
        return {"query": name, "params": params, "result": queries(db, user.workspace_id, name, **params)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/impact/{version_id}")
def version_impact(version_id: str, user: User = Depends(require_permission("graph:read")),
                   db: Session = Depends(get_db)) -> dict:
    result = impact_of_version(db, user.workspace_id, version_id)
    if not result:
        raise HTTPException(status_code=404, detail="Version not found")
    return result
