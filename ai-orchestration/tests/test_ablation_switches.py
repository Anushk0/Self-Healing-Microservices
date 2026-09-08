"""Week 16 ablation switches.

These two flags are experimental apparatus, so they get the same treatment as any other measuring
instrument: verified to do exactly what they claim, and verified to be OFF by default. A silently-on
ablation flag would corrupt a headline result while leaving the run looking normal, which is the
worst failure mode available here.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ai_orchestration import config as ai_config
from ai_orchestration.anomaly.models import AnomalyResult
from ai_orchestration.rag.models import RetrievalResult, RetrievedIncident
from ai_orchestration.rca import analyzer


def _anomaly(service="payment-service", **metrics) -> dict:
    return AnomalyResult(
        service=service, timestamp="2026-01-01T00:00:00+00:00", correlation_id="c",
        status="scored", anomaly_score=1.0, is_anomaly=True, threshold=0.0,
        relevant_feature_values=metrics, model_version="v1", feature_schema_version="v1",
    ).to_dict()


def _misleading_retrieval() -> RetrievalResult:
    """Exactly the situation the Week 12 run produced on every cross-service incident: retrieval
    confidently returns memory-leak incidents rooted at the symptom service."""
    return RetrievalResult(
        query_text="q", source_collection="incident_memory", status="retrieved",
        incidents=[
            RetrievedIncident(incident_id=f"inc-00{i}", distance=0.1, fault_type="memory-leak",
                              root_cause_service="payment-service", symptom_service="payment-service",
                              severity="medium", data_source="synthetic", postmortem_excerpt="x")
            for i in (37, 42, 44)
        ],
    )


def _client() -> MagicMock:
    client = MagicMock()
    client.is_available.return_value = False   # keep the test on the deterministic path
    client.base_url, client.model = "http://localhost:11434", "test"
    return client


# --- Defaults ---------------------------------------------------------------------------------

def test_both_ablation_switches_are_off_by_default():
    """The shipped configuration must never be an ablation."""
    assert ai_config.RETRIEVAL_ENABLED is True
    assert ai_config.RAG_MAY_OVERRIDE_SIGNATURE is False


# --- rag_override -----------------------------------------------------------------------------

def test_by_default_a_signature_match_survives_disagreeing_retrieval():
    """The shipped behaviour, and the property the Week 12 result attributes its entire margin to."""
    rca = analyzer.analyze(
        _anomaly(http_client_requests_avg_duration_ms=5200.0), _misleading_retrieval(), _client(),
    )

    assert rca.suspected_fault_type == "notification-latency"
    assert rca.suspected_root_cause_service == "notification-service"   # the correct answer
    assert rca.determination_method == "metric_signature+rag_disagreement"


def test_rag_override_ablation_makes_retrieval_win_and_produces_the_wrong_answer(monkeypatch):
    """Turning the constraint off must reproduce the baseline's failure: retrieval drags the
    conclusion to the symptom service. This is what converts "the constraint is why we win" from an
    observation into a controlled result."""
    monkeypatch.setattr(ai_config, "RAG_MAY_OVERRIDE_SIGNATURE", True)

    rca = analyzer.analyze(
        _anomaly(http_client_requests_avg_duration_ms=5200.0), _misleading_retrieval(), _client(),
    )

    assert rca.suspected_fault_type == "memory-leak"                    # retrieval's answer
    assert rca.suspected_root_cause_service == "payment-service"        # the symptom service - wrong
    assert rca.determination_method == "rag_override"
    assert "ABLATION" in rca.evidence_summary   # the run can never be mistaken for a normal one


def test_rag_override_does_nothing_when_retrieval_agrees(monkeypatch):
    """The flag only changes disagreements. Where retrieval already agrees there is nothing to
    override, so an ablation run and a normal run must be identical on those incidents."""
    monkeypatch.setattr(ai_config, "RAG_MAY_OVERRIDE_SIGNATURE", True)
    agreeing = _misleading_retrieval()  # memory-leak incidents...

    rca = analyzer.analyze(_anomaly(jvm_memory_used_bytes=5.0e8), agreeing, _client())  # ...on a memory-leak

    assert rca.suspected_root_cause_service == "payment-service"
    assert rca.determination_method == "metric_signature+rag_agreement"


# --- no_retrieval -----------------------------------------------------------------------------

def test_no_retrieval_ablation_returns_an_empty_result_without_querying_chromadb(monkeypatch):
    """The ablation must remove the *evidence*, not the code path - downstream nodes should behave
    exactly as they do when the collection legitimately has no neighbours."""
    from ai_orchestration.graph import workflow

    monkeypatch.setattr(ai_config, "RETRIEVAL_ENABLED", False)
    spy = MagicMock(side_effect=AssertionError("must not query the collection when ablated"))
    monkeypatch.setattr(workflow.rag_retriever, "retrieve_similar_incidents", spy)

    update = workflow._retrieve_similar_incidents_node(
        {"anomaly_result": _anomaly(jvm_memory_used_bytes=5.0e8), "llm_interpretation": None}
    )

    assert update["retrieval"]["status"] == "empty"
    assert update["retrieval"]["incidents"] == []
    assert "ablation" in update["retrieval"]["reason"].lower()
    spy.assert_not_called()


def test_signature_matching_is_unaffected_by_having_no_retrieval():
    """Predicts the ablation's outcome for the multi-agent arm: retrieval contributes confidence,
    not the conclusion, so removing it should not change which service is named."""
    empty = RetrievalResult(query_text="", incidents=[], source_collection="incident_memory",
                            status="empty")

    rca = analyzer.analyze(_anomaly(http_client_requests_avg_duration_ms=5200.0), empty, _client())

    assert rca.suspected_root_cause_service == "notification-service"   # still correct
    assert rca.determination_method == "metric_signature"               # just without the cross-check
    assert rca.confidence == "medium"                                    # not raised to "high"


@pytest.mark.parametrize("flag", ["RETRIEVAL_ENABLED", "RAG_MAY_OVERRIDE_SIGNATURE"])
def test_ablation_state_is_recorded_in_the_run_environment(flag, monkeypatch):
    """An ablation run's numbers must be self-identifying in its own artefact."""
    from ai_orchestration.evaluation import harness

    monkeypatch.setattr(ai_config, "RETRIEVAL_ENABLED", flag != "RETRIEVAL_ENABLED")
    monkeypatch.setattr(ai_config, "RAG_MAY_OVERRIDE_SIGNATURE", flag == "RAG_MAY_OVERRIDE_SIGNATURE")

    env = harness._environment_block("replay", {}, _client())

    assert env["ablation"] in ("no_retrieval", "rag_override")
    assert env["retrieval_enabled"] is ai_config.RETRIEVAL_ENABLED
    assert env["rag_may_override_signature"] is ai_config.RAG_MAY_OVERRIDE_SIGNATURE
