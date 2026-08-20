"""Hybrid retrieval: persisted embeddings (cosine) + BM25 keyword search + source-aware filters.

The default embedder is a deterministic local hashing embedder so retrieval works with
zero external credentials. Any configured embedding provider can be used instead.
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Claim, Document, DocumentVersion, Embedding, Entity, SourceSpan
from app.services.predicates import P
from app.services.serializers import span_locator_label

TOKEN = re.compile(r"[a-z0-9][a-z0-9_.%/-]+")
STOP = {"the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "is", "are", "was", "were", "with",
        "by", "as", "at", "from", "that", "this", "it", "be", "we", "our", "their", "which", "what"}


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN.findall((text or "").lower()) if t not in STOP and len(t) > 1]


class LocalHashEmbedder:
    """Deterministic feature-hashing embedder. Not a semantic transformer; documented as such."""
    name = "local-hashing-256"

    def __init__(self, dim: int = 256):
        self.dim = dim

    def _features(self, text: str) -> Counter:
        toks = tokenize(text)
        feats: Counter = Counter()
        for t in toks:
            feats[f"w:{t}"] += 1.0
        for a, b in zip(toks, toks[1:]):
            feats[f"b:{a}_{b}"] += 0.6
        compact = re.sub(r"\s+", " ", (text or "").lower())
        for i in range(0, max(0, len(compact) - 2), 2):
            feats[f"c:{compact[i:i+3]}"] += 0.25
        for num in re.findall(r"\d+(?:\.\d+)?", text or ""):
            feats[f"n:{num}"] += 1.2
        return feats

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = np.zeros(self.dim, dtype=np.float32)
            for feat, weight in self._features(text).items():
                h = int(hashlib.blake2b(feat.encode(), digest_size=8).hexdigest(), 16)
                idx = h % self.dim
                sign = 1.0 if (h >> 63) & 1 else -1.0
                vec[idx] += sign * weight
            norm = float(np.linalg.norm(vec))
            out.append((vec / norm).tolist() if norm > 0 else vec.tolist())
        return out


def cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    if va.shape != vb.shape or not va.size:
        return 0.0
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(np.dot(va, vb) / denom) if denom else 0.0


def claim_text(claim: Claim, span: SourceSpan | None = None, document: Document | None = None) -> str:
    """Searchable representation of a claim.

    Document titles carry entity and document-type signal that short atomic claims (a row
    in a risk register, a single certification line) do not; they are indexed with the claim.
    """
    label = P[claim.predicate].label if claim.predicate in P else claim.predicate
    parts = [claim.subject_text, label, claim.object_text, claim.scope, claim.quoted_span]
    if span is not None:
        parts.append(span.section_label or "")
    if document is not None:
        parts.append(document.title)
    return " | ".join(p for p in parts if p)


def ensure_embeddings(db: Session, workspace_id: str, model_name: str, dim: int = 256,
                      embedder=None) -> int:
    claims = db.execute(select(Claim).where(Claim.workspace_id == workspace_id)).scalars().all()
    existing = {(e.object_id, e.model): e for e in db.execute(
        select(Embedding).where(Embedding.workspace_id == workspace_id, Embedding.model == model_name)).scalars()}
    spans = {s.id: s for s in db.execute(select(SourceSpan).where(SourceSpan.workspace_id == workspace_id)).scalars()}
    versions = {v.id: v for v in db.execute(select(DocumentVersion).where(
        DocumentVersion.workspace_id == workspace_id)).scalars()}
    documents = {d.id: d for d in db.execute(select(Document).where(Document.workspace_id == workspace_id)).scalars()}
    to_embed = []
    for c in claims:
        version = versions.get(c.version_id)
        text = claim_text(c, spans.get(c.span_id), documents.get(version.document_id) if version else None)
        text_hash = hashlib.sha256(text.encode()).hexdigest()
        record = existing.get((c.id, model_name))
        if record and record.text_hash == text_hash:
            continue
        to_embed.append((c, text, text_hash, record))
    if not to_embed:
        return 0
    embedder = embedder or LocalHashEmbedder(dim)
    vectors = embedder.embed([t for _, t, _, _ in to_embed])
    for (claim, text, text_hash, record), vec in zip(to_embed, vectors):
        if record:
            record.vector = vec
            record.text_hash = text_hash
            record.dim = len(vec)
        else:
            db.add(Embedding(workspace_id=workspace_id, object_type="Claim", object_id=claim.id,
                             model=model_name, dim=len(vec), vector=vec, text_hash=text_hash))
    db.commit()
    return len(to_embed)


def _filters_apply(claim: Claim, version: DocumentVersion, document: Document, filters: dict) -> bool:
    if filters.get("document_ids") and document.id not in filters["document_ids"]:
        return False
    if filters.get("doc_types") and document.doc_type not in filters["doc_types"]:
        return False
    if filters.get("predicates") and claim.predicate not in filters["predicates"]:
        return False
    if filters.get("verification_status") and claim.verification_status not in filters["verification_status"]:
        return False
    if filters.get("entity_id") and claim.entity_id != filters["entity_id"]:
        return False
    if filters.get("current_only", True) and not version.is_current:
        return False
    return True


def hybrid_search(db: Session, workspace_id: str, query: str, *, top_k: int = 10,
                  filters: dict | None = None, embedding_model: str | None = None,
                  embedder=None, rerank: bool = True) -> dict:
    filters = filters or {}
    rows = db.execute(
        select(Claim, DocumentVersion, Document, SourceSpan)
        .join(DocumentVersion, Claim.version_id == DocumentVersion.id)
        .join(Document, DocumentVersion.document_id == Document.id)
        .outerjoin(SourceSpan, Claim.span_id == SourceSpan.id)
        .where(Claim.workspace_id == workspace_id)
    ).all()
    candidates = []
    for claim, version, document, span in rows:
        if not _filters_apply(claim, version, document, filters):
            continue
        candidates.append({"claim": claim, "version": version, "document": document, "span": span})
    if not candidates:
        return {"query": query, "results": [], "method": "none", "total_candidates": 0}
    corpus_tokens = [tokenize(claim_text(c["claim"], c["span"], c["document"])) for c in candidates]
    df: Counter = Counter()
    for toks in corpus_tokens:
        df.update(set(toks))
    n_docs = len(corpus_tokens)
    avg_len = sum(len(t) for t in corpus_tokens) / max(n_docs, 1)
    q_tokens = tokenize(query)
    k1, b = 1.5, 0.75
    keyword_scores = []
    for toks in corpus_tokens:
        tf = Counter(toks)
        score = 0.0
        for term in q_tokens:
            if tf[term] <= 0:
                continue
            idf = math.log(1 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
            denom = tf[term] + k1 * (1 - b + b * len(toks) / max(avg_len, 1))
            score += idf * tf[term] * (k1 + 1) / denom
        keyword_scores.append(score)
    max_kw = max(keyword_scores) or 1.0
    model_name = embedding_model or (embedder.name if embedder else LocalHashEmbedder().name)
    if embedder is None:
        embedder = LocalHashEmbedder()
    q_vec = embedder.embed([query])[0]
    stored = {(e.object_id): e for e in db.execute(
        select(Embedding).where(Embedding.workspace_id == workspace_id,
                                Embedding.model == model_name)).scalars()}
    vector_scores = []
    for c in candidates:
        rec = stored.get(c["claim"].id)
        if rec and rec.vector:
            vector_scores.append(max(0.0, cosine(q_vec, rec.vector)))
        else:
            vector_scores.append(max(0.0, cosine(q_vec, embedder.embed(
                [claim_text(c["claim"], c["span"], c["document"])])[0])))
    q_norm = set(q_tokens)
    # Entity-aware prior: a query that names a supplier/asset should not rank a competitor's
    # paperwork above the named entity's own evidence. Mentions are resolved against the
    # workspace entity table, never against hard-coded names.
    query_lower = (query or "").lower()
    mention_ids = set()
    for entity in db.execute(select(Entity).where(Entity.workspace_id == workspace_id)).scalars():
        names = [entity.canonical_name, *(entity.aliases or [])]
        if any(len(name or "") >= 4 and name.lower() in query_lower for name in names):
            mention_ids.add(entity.id)
    scored = []
    for i, c in enumerate(candidates):
        claim = c["claim"]
        boost = 0.0
        text = claim_text(claim, c["span"]).lower()
        if any(t in text for t in q_norm):
            boost += 0.03
        if query and query.lower() in text:
            boost += 0.05
        if filters.get("decision_id") and c["version"].is_current:
            boost += 0.01
        if mention_ids and claim.entity_id and claim.entity_id in mention_ids:
            boost += 0.10
        # Staleness: evidence that lapsed well before the retrieval reference date is weaker
        # support than live evidence, even when its wording matches the query closely.
        as_of = filters.get("as_of")
        valid_until = claim.valid_until or c["version"].valid_until
        if as_of and valid_until:
            expired_days = (as_of - valid_until).days
            if expired_days > 90:
                boost -= 0.12
            elif expired_days >= 0:
                boost -= 0.05
        # Absolute cosine (not max-normalised): similarity scale is meaningful, and normalising
        # by the best candidate would turn hash-embedding noise into a high relative score.
        score = 0.45 * min(1.0, vector_scores[i]) + 0.45 * (keyword_scores[i] / max_kw) + boost
        if rerank:
            overlap = len(q_norm & set(tokenize(text))) / max(len(q_norm), 1)
            score = 0.8 * score + 0.2 * overlap
        if score <= 0:
            continue
        scored.append((score, c, {"vector": round(vector_scores[i], 4),
                                  "keyword": round(keyword_scores[i] / max_kw, 4),
                                  "match": round(score, 4)}))
    scored.sort(key=lambda x: -x[0])
    results = []
    for score, c, components in scored[:top_k]:
        claim = c["claim"]
        results.append({
            "score": round(score, 4), "components": components, "claim_id": claim.id,
            "predicate": claim.predicate, "label": P[claim.predicate].label if claim.predicate in P else claim.predicate,
            "subject": claim.subject_text, "value": claim.object_text, "quoted_span": claim.quoted_span,
            "verification_status": claim.verification_status, "confidence": claim.extraction_confidence,
            "document_id": c["document"].id, "document_title": c["document"].title,
            "doc_type": c["document"].doc_type, "version_id": c["version"].id,
            "version_number": c["version"].version_number, "is_current": c["version"].is_current,
            "span_id": c["span"].id if c["span"] else None,
            "locator": span_locator_label(c["span"]) or "span",
            "effective_date": str(claim.effective_date or ""),
            "valid_until": str(claim.valid_until or ""),
        })
    return {"query": query, "results": results, "total_candidates": len(candidates),
            "method": f"hybrid(bm25+{model_name}+feature-rerank)" if rerank else f"hybrid(bm25+{model_name})",
            "filters": filters}


def answer_context(db: Session, workspace_id: str, claim_ids: list[str]) -> list[dict]:
    if not claim_ids:
        return []
    claims = db.execute(select(Claim).where(Claim.workspace_id == workspace_id,
                                            Claim.id.in_(claim_ids))).scalars().all()
    versions = {v.id: v for v in db.execute(select(DocumentVersion).where(
        DocumentVersion.workspace_id == workspace_id)).scalars()}
    docs = {d.id: d for d in db.execute(select(Document).where(Document.workspace_id == workspace_id)).scalars()}
    spans = {s.id: s for s in db.execute(select(SourceSpan).where(SourceSpan.workspace_id == workspace_id)).scalars()}
    out = []
    for c in claims:
        v = versions.get(c.version_id)
        d = docs.get(v.document_id) if v else None
        s = spans.get(c.span_id)
        out.append({"claim_id": c.id, "predicate": c.predicate, "value": c.object_text,
                    "subject": c.subject_text, "verification_status": c.verification_status,
                    "quoted_span": c.quoted_span,
                    "document": {"id": d.id, "title": d.title, "doc_type": d.doc_type} if d else None,
                    "version": {"id": v.id, "number": v.version_number, "is_current": v.is_current} if v else None,
                    "locator": {"page": s.page, "sheet": s.sheet, "cell": s.cell_range,
                                "paragraph": s.paragraph_index, "char_start": s.char_start,
                                "char_end": s.char_end} if s else None})
    return out
