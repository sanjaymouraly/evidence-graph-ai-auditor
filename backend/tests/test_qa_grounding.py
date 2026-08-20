"""Grounded Q&A: answer focus, citation validity, determinism and refusal.

These tests exercise the offline extractive baseline, which must be useful without any
API key: a question about one attribute should return that attribute (not the vendor's
whole claim list), every statement must cite a real claim, and an unanswerable question
must say so instead of producing plausible-looking text.
"""
from __future__ import annotations

CASCADE = "Award pump maintenance contract to Cascade Pumps"


def _ask(client, tokens, question: str, decision_id: str | None = None) -> dict:
    payload: dict = {"question": question, "use_llm": False}
    if decision_id:
        payload["decision_id"] = decision_id
    response = client.post("/api/analysis/ask", headers=tokens["auditor"], json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_answer_targets_the_asked_attribute(client, tokens, decision_index):
    decision = decision_index[CASCADE]
    answer = _ask(client, tokens, "What is the warranty for Cascade Pumps?", decision["id"])
    assert answer["insufficient_evidence"] is False
    text = answer["answer"].lower()
    assert "36 months" in text
    # The old baseline returned every retrieved claim, so unrelated attributes leaked in.
    assert "risk rating" not in text
    assert "certification" not in text
    assert len(answer["statements"]) <= 3


def test_every_statement_cites_a_resolvable_claim(client, tokens, decision_index):
    decision = decision_index[CASCADE]
    answer = _ask(client, tokens, "What is the total price offered by Cascade Pumps?", decision["id"])
    assert answer["statements"]
    for statement in answer["statements"]:
        assert statement["claim_ids"], statement
        assert statement["supported"] is True
    cited = {c["claim_id"] for c in answer["citations"]}
    for statement in answer["statements"]:
        assert set(statement["claim_ids"]) <= cited
    assert answer["invalid_citations"] == []
    for citation in answer["citations"]:
        assert citation["quote"]
        assert citation["locator"]
        assert citation["document_id"] and citation["version_id"]


def test_same_question_returns_identical_answer(client, tokens, decision_index):
    decision = decision_index[CASCADE]
    first = _ask(client, tokens, "What are the payment terms?", decision["id"])
    second = _ask(client, tokens, "What are the payment terms?", decision["id"])
    assert first["answer"] == second["answer"]
    assert [s["id"] for s in first["statements"]] == [s["id"] for s in second["statements"]]


def test_unanswerable_question_refuses_instead_of_guessing(client, tokens, decision_index):
    decision = decision_index[CASCADE]
    answer = _ask(client, tokens, "What is the capital of France?", decision["id"])
    assert answer["insufficient_evidence"] is True
    assert answer["statements"] == []
    assert answer["answer"].lower().startswith("insufficient evidence")
    assert answer["citations"] == []


def test_answers_do_not_assert_gate_outcomes(client, tokens, decision_index):
    decision = decision_index[CASCADE]
    answer = _ask(client, tokens, "Is Cascade approved by the gate?", decision["id"])
    for statement in answer["statements"]:
        lowered = statement["text"].lower()
        assert "gate passed" not in lowered
        assert "eligible for approval" not in lowered
        assert "is approved" not in lowered


def test_llm_reported_insufficient_evidence_is_propagated(db_session, decision_index, monkeypatch):
    """A model that says the evidence is insufficient must not be rendered as a confident answer.

    The provider returns a valid, cited statement alongside ``insufficient_evidence: true``
    (models commonly do this for partial coverage). The response flag must reflect the model's
    own admission rather than defaulting to False.
    """
    import json

    from sqlalchemy import select

    from app.db.models import DecisionCase, User
    from app.services import analysis as analysis_module
    from app.services.gate import decision_claims
    from app.services.providers import ProviderSpec

    case = db_session.execute(select(DecisionCase).where(DecisionCase.title == CASCADE)).scalars().one()
    user = db_session.execute(select(User).where(User.role == "admin")).scalars().first()
    claims, _, _, _ = decision_claims(db_session, case)
    claim = next(c for c in claims if c.verification_status == "verified")

    class PartialAdapter:
        def generate(self, prompt, system="", **kwargs):
            return {"text": json.dumps({
                "statements": [{"text": "Only one attribute is evidenced for this option.",
                                "evidence_ids": [claim.id], "kind": "gap"}],
                "insufficient_evidence": True,
            }), "prompt_tokens": 10, "completion_tokens": 10, "latency_ms": 1,
                "model": "fake-model", "provider": "fake-provider"}

    monkeypatch.setattr(analysis_module, "select_spec",
                        lambda db, workspace_id, purpose="generation": ProviderSpec(
                            id=None, label="fake-provider", kind="openai",
                            base_url="https://example.invalid", model="fake-model"))
    monkeypatch.setattr(analysis_module, "build_adapter", lambda spec: PartialAdapter())
    monkeypatch.setattr(analysis_module, "record_usage", lambda *args, **kwargs: None)

    result = analysis_module.grounded_answer(
        db_session, user, "What is the warranty for Cascade Pumps?",
        decision_id=case.id, use_llm=True,
    )
    assert result["insufficient_evidence"] is True
    assert result["provider"].startswith("fake-provider")
