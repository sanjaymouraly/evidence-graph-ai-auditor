"""SQLAlchemy 2.0 ORM models. IDs are UUID strings for graph portability."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def uid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(30), default="auditor", nullable=False)  # admin|auditor|reviewer|viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Document(Base, TimestampMixin):
    """Logical document; file instances are DocumentVersion rows."""
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("workspace_id", "doc_key", name="uq_document_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True, nullable=False)
    doc_key: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(60), default="generic", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", order_by="DocumentVersion.version_number"
    )


class DocumentVersion(Base, TimestampMixin):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_number", name="uq_doc_version"),
        Index("ix_version_hash", "workspace_id", "content_hash"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True, nullable=False)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True, nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(150), default="application/octet-stream")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="uploaded", index=True)  # uploaded|processing|processed|failed|quarantined
    error: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    superseded_by_version_id: Mapped[str | None] = mapped_column(String(32))
    effective_date: Mapped[date | None] = mapped_column(Date)
    issued_date: Mapped[date | None] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    source_author: Mapped[str | None] = mapped_column(String(300))
    source_org: Mapped[str | None] = mapped_column(String(300))
    primary_entity: Mapped[str | None] = mapped_column(String(300))
    trust_level: Mapped[float] = mapped_column(Float, default=0.7)  # 0..1 reviewer-governed source trust signal
    injection_flags: Mapped[list] = mapped_column(JSON, default=list)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    uploaded_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    document: Mapped[Document] = relationship(back_populates="versions")
    spans: Mapped[list["SourceSpan"]] = relationship(back_populates="version", cascade="all, delete-orphan")


class SourceSpan(Base):
    __tablename__ = "source_spans"
    __table_args__ = (Index("ix_span_version_order", "version_id", "ordinal"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True, nullable=False)
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), index=True, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    locator_type: Mapped[str] = mapped_column(String(30), default="text")  # page|paragraph|cell|text|email_section
    page: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list | None] = mapped_column(JSON)  # [x0,y0,x1,y1]
    paragraph_index: Mapped[int | None] = mapped_column(Integer)
    sheet: Mapped[str | None] = mapped_column(String(200))
    cell_range: Mapped[str | None] = mapped_column(String(50))
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)
    section_label: Mapped[str | None] = mapped_column(String(250))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[DocumentVersion] = relationship(back_populates="spans")


class Entity(Base, TimestampMixin):
    __tablename__ = "entities"
    __table_args__ = (UniqueConstraint("workspace_id", "canonical_name", name="uq_entity_name"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True, nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(300), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), default="organization")
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class Claim(Base, TimestampMixin):
    __tablename__ = "claims"
    __table_args__ = (
        Index("ix_claim_key", "workspace_id", "claim_key"),
        Index("ix_claim_version", "version_id"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True, nullable=False)
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id"), index=True, nullable=False)
    span_id: Mapped[str | None] = mapped_column(ForeignKey("source_spans.id"))
    entity_id: Mapped[str | None] = mapped_column(ForeignKey("entities.id"), index=True)
    subject_text: Mapped[str] = mapped_column(String(300), default="")
    predicate: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    claim_key: Mapped[str] = mapped_column(String(500), nullable=False)
    object_text: Mapped[str] = mapped_column(String(500), default="")
    quoted_span: Mapped[str] = mapped_column(Text, default="")
    value_numeric: Mapped[float | None] = mapped_column(Float)
    value_text: Mapped[str | None] = mapped_column(String(300))
    unit: Mapped[str | None] = mapped_column(String(40))
    currency: Mapped[str | None] = mapped_column(String(8))
    scope: Mapped[str] = mapped_column(String(300), default="")
    qualifiers: Mapped[dict] = mapped_column(JSON, default=dict)
    polarity: Mapped[int] = mapped_column(Integer, default=1)
    effective_date: Mapped[date | None] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    observed_date: Mapped[date | None] = mapped_column(Date)
    extraction_method: Mapped[str] = mapped_column(String(60), default="rule_based")
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.7)
    verification_status: Mapped[str] = mapped_column(String(30), default="unverified", index=True)
    # verified|disputed|unsupported|ambiguous|rejected
    review_status: Mapped[str] = mapped_column(String(30), default="pending")  # pending|accepted|rejected
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    injection_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    duplicate_of_id: Mapped[str | None] = mapped_column(String(32))
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class Edge(Base, TimestampMixin):
    __tablename__ = "edges"
    __table_args__ = (
        UniqueConstraint("workspace_id", "source_type", "source_id", "target_type", "target_id", "relation",
                         name="uq_edge"),
        Index("ix_edge_source", "source_type", "source_id"),
        Index("ix_edge_target", "target_type", "target_id"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True, nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(40), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)
    relation: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="active")  # active|invalidated
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    justification: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_by: Mapped[str | None] = mapped_column(String(64), default="system")


class Contradiction(Base, TimestampMixin):
    __tablename__ = "contradictions"
    __table_args__ = (UniqueConstraint("workspace_id", "claim_a_id", "claim_b_id", "kind", name="uq_contradiction"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True, nullable=False)
    claim_a_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), nullable=False)
    claim_b_id: Mapped[str] = mapped_column(ForeignKey("claims.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(50), default="value_conflict")
    state: Mapped[str] = mapped_column(String(30), default="possible")  # possible|confirmed|dismissed|resolved
    severity: Mapped[str] = mapped_column(String(20), default="medium")  # low|medium|high|critical
    confidence: Mapped[float] = mapped_column(Float, default=0.6)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    detected_by: Mapped[str] = mapped_column(String(50), default="rule_engine")
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    blocking: Mapped[bool] = mapped_column(Boolean, default=False)


class DecisionCase(Base, TimestampMixin):
    __tablename__ = "decision_cases"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    decision_type: Mapped[str] = mapped_column(String(60), nullable=False)  # procurement|engineering_change|project_approval|policy_compliance
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="DRAFT", index=True)
    gate_status: Mapped[str | None] = mapped_column(String(40))
    gate_result: Mapped[dict] = mapped_column(JSON, default=dict)
    gate_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    selected_option: Mapped[str | None] = mapped_column(String(300))
    subject_entity_id: Mapped[str | None] = mapped_column(ForeignKey("entities.id"))
    options: Mapped[list] = mapped_column(JSON, default=list)  # [{name, entity, manual_scores:{criterion:score}}]
    criteria: Mapped[list] = mapped_column(JSON, default=list)  # [{code,label,weight,direction,kind}]
    requirements: Mapped[list] = mapped_column(JSON, default=list)  # case-specific requirement overrides
    policy_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    due_date: Mapped[date | None] = mapped_column(Date)
    as_of_date: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    exception_recorded: Mapped[bool] = mapped_column(Boolean, default=False)
    exception_reason: Mapped[str | None] = mapped_column(Text)
    exception_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    snapshot_id: Mapped[str | None] = mapped_column(String(32))
    documents: Mapped[list] = mapped_column(JSON, default=list)  # document ids (logical)
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class DecisionDocument(Base):
    __tablename__ = "decision_documents"
    __table_args__ = (UniqueConstraint("decision_id", "document_id", name="uq_decision_document"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decision_cases.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GateCheck(Base):
    __tablename__ = "gate_checks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decision_cases.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(250), default="")
    status: Mapped[str] = mapped_column(String(20), default="pass")  # pass|fail|warn|not_applicable
    blocking: Mapped[bool] = mapped_column(Boolean, default=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    refs: Mapped[list] = mapped_column(JSON, default=list)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    gate_version: Mapped[str] = mapped_column(String(30), default="gate-1.0")


class Approval(Base, TimestampMixin):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decision_cases.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)  # approve|reject|request_changes
    comment: Mapped[str] = mapped_column(Text, default="")
    gate_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    signature_hash: Mapped[str] = mapped_column(String(64), default="")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_ws_created", "workspace_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    actor_label: Mapped[str] = mapped_column(String(300), default="system")
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    object_type: Mapped[str] = mapped_column(String(60), default="")
    object_id: Mapped[str] = mapped_column(String(64), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    event_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ProviderConfig(Base, TimestampMixin):
    __tablename__ = "provider_configs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)  # openai|anthropic|openrouter|custom|ollama|offline
    base_url: Mapped[str | None] = mapped_column(String(500))
    api_key_encrypted: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(200))
    embedding_model: Mapped[str | None] = mapped_column(String(200))
    rerank_model: Mapped[str | None] = mapped_column(String(200))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default_generation: Mapped[bool] = mapped_column(Boolean, default=False)
    is_default_embedding: Mapped[bool] = mapped_column(Boolean, default=False)
    generation_params: Mapped[dict] = mapped_column(JSON, default=dict)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    retries: Mapped[int] = mapped_column(Integer, default=2)
    streaming: Mapped[bool] = mapped_column(Boolean, default=False)
    max_context_tokens: Mapped[int] = mapped_column(Integer, default=32000)
    pricing: Mapped[dict] = mapped_column(JSON, default=dict)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_test_message: Mapped[str | None] = mapped_column(Text)


class AIUsage(Base):
    __tablename__ = "ai_usage"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    provider_config_id: Mapped[str | None] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(60), nullable=False)
    model: Mapped[str | None] = mapped_column(String(200))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Analysis(Base):
    __tablename__ = "analyses"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decision_cases.id"), index=True)
    provider_label: Mapped[str] = mapped_column(String(200), default="offline-deterministic")
    model: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="generated")  # generated|flagged|rejected|accepted
    statements: Mapped[list] = mapped_column(JSON, default=list)
    validation: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    recommendation: Mapped[str] = mapped_column(Text, default="")
    usage_id: Mapped[str | None] = mapped_column(String(32))
    snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Snapshot(Base):
    __tablename__ = "snapshots"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decision_cases.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    report_spec_version: Mapped[str] = mapped_column(String(20), default="1.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReportRecord(Base):
    __tablename__ = "reports"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("decision_cases.id"), index=True)
    snapshot_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="pdf")  # pdf|json|manifest|csv
    filename: Mapped[str] = mapped_column(String(300))
    path: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BenchmarkRun(Base):
    __tablename__ = "benchmark_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    dataset: Mapped[str] = mapped_column(String(80), default="heldout")
    status: Mapped[str] = mapped_column(String(30), default="completed")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    confusion: Mapped[dict] = mapped_column(JSON, default=dict)
    # Full scored result (targets, per-case details, errors, environment, corpus counts).
    # Keeping it makes historical runs as inspectable as a fresh one, and lets the dashboard
    # show exactly what was measured when a number was produced.
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Embedding(Base):
    __tablename__ = "embeddings"
    __table_args__ = (UniqueConstraint("workspace_id", "object_type", "object_id", "model", name="uq_embedding"),
                      Index("ix_embedding_ws_model", "workspace_id", "model"))
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    object_type: Mapped[str] = mapped_column(String(40), default="Claim")
    object_id: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, default=256)
    vector: Mapped[list] = mapped_column(JSON, default=list)
    text_hash: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    version_id: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    stage: Mapped[str] = mapped_column(String(60), default="queued")
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
