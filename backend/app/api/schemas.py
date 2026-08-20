"""Pydantic request/response schemas for the public API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

DECISION_TYPES = ("procurement", "engineering_change", "project_approval", "policy_compliance")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(StrictModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class UserCreate(StrictModel):
    email: str
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=256)
    role: Literal["admin", "auditor", "reviewer", "viewer"] = "viewer"

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if "@" not in v or v.startswith("@") or v.endswith("@"):
            raise ValueError("invalid email address")
        return v


class UserPatch(StrictModel):
    name: str | None = Field(default=None, max_length=200)
    role: Literal["admin", "auditor", "reviewer", "viewer"] | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)


class OptionInput(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    entity_id: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class CriterionInput(StrictModel):
    code: str
    label: str | None = None
    weight: float = Field(ge=0, le=1)
    direction: Literal["maximize", "minimize"] = "maximize"


class DecisionCreate(StrictModel):
    title: str = Field(min_length=3, max_length=300)
    decision_type: Literal["procurement", "engineering_change", "project_approval", "policy_compliance"]
    description: str = Field(default="", max_length=4000)
    options: list[OptionInput | str] = Field(default_factory=list)
    criteria: list[CriterionInput] | None = None
    as_of_date: str | None = None
    due_date: str | None = None
    requirements: list[dict[str, Any]] | None = None
    document_ids: list[str] = Field(default_factory=list)


class DecisionUpdate(StrictModel):
    title: str | None = Field(default=None, min_length=3, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    status: str | None = None
    selected_option: str | None = None
    as_of_date: str | None = None
    due_date: str | None = None
    options: list[OptionInput | str] | None = None
    criteria: list[CriterionInput] | None = None
    requirements: list[dict[str, Any]] | None = None
    subject_entity_id: str | None = None


class DocumentAttach(StrictModel):
    document_ids: list[str] = Field(min_length=1, max_length=200)


class EvaluateRequest(StrictModel):
    reason: str = Field(default="gate evaluated", max_length=300)


class CommentRequest(StrictModel):
    comment: str = Field(default="", max_length=4000)


class ExceptionRequest(StrictModel):
    reason: str = Field(min_length=20, max_length=2000)


class ClaimReview(StrictModel):
    action: Literal["verify", "reject", "flag", "reset"]
    note: str = Field(default="", max_length=1000)


class ContradictionAction(StrictModel):
    note: str = Field(default="", max_length=1000)


class AnalysisRequest(StrictModel):
    use_llm: bool = True


class AskRequest(StrictModel):
    question: str = Field(min_length=3, max_length=2000)
    decision_id: str | None = None
    top_k: int = Field(default=8, ge=1, le=20)
    use_llm: bool = True


class ProviderCreate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["openai", "anthropic", "openrouter", "custom", "ollama", "offline"]
    base_url: str = Field(default="", max_length=500)
    api_key: str = Field(default="", max_length=1000)
    model: str = Field(default="", max_length=200)
    embedding_model: str = Field(default="", max_length=200)
    rerank_model: str = Field(default="", max_length=200)
    enabled: bool = True
    is_default_generation: bool = False
    is_default_embedding: bool = False
    generation_params: dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    retries: int = Field(default=2, ge=0, le=10)
    streaming: bool = False
    max_context_tokens: int = Field(default=16000, ge=256, le=2_000_000)
    pricing: dict[str, Any] = Field(default_factory=dict)


class ProviderUpdate(StrictModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = Field(default=None, max_length=1000, description="Omit or send empty to keep the stored key")
    model: str | None = None
    embedding_model: str | None = None
    rerank_model: str | None = None
    enabled: bool | None = None
    is_default_generation: bool | None = None
    is_default_embedding: bool | None = None
    generation_params: dict[str, Any] | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)
    retries: int | None = Field(default=None, ge=0, le=10)
    streaming: bool | None = None
    max_context_tokens: int | None = Field(default=None, ge=256, le=2_000_000)
    pricing: dict[str, Any] | None = None


class ReportRequest(StrictModel):
    decision_id: str
    kind: Literal["pdf", "json", "manifest", "csv"] = "pdf"
    snapshot_id: str | None = None


class BenchmarkRunRequest(StrictModel):
    dataset: Literal["heldout", "dev"] = "heldout"
    include_llm: bool = False
    limit: int | None = Field(default=None, ge=1, le=100)


class ApiMessage(BaseModel):
    detail: str
