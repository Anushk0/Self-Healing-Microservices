"""Audit: the scored output must be derived from telemetry, not be a source constant.

This test exists because of a specific event in the literature. arXiv 2511.15755, "Multi-Agent LLM
Orchestration Achieves Deterministic, High-Quality Decision Support for Incident Response"
(Drammeh, Nov 2025), was **withdrawn by its author** after a code audit found that "the multi-agent
arm's action list is a source constant, not model output, and the scorer reads only that field."
All of its Decision Quality, zero-variance and actionability results were withdrawn.

That is the natural failure mode of the architecture this project also uses: a deliberately
deterministic multi-agent pipeline necessarily contains hardcoded action templates
(`remediation/proposer.py` is exactly that, by design and by declaration). The risk is not having
such templates; it is *scoring* them and reporting the result as a capability.

So the property is asserted here rather than argued in prose: the quantity Week 12 scores
(`suspected_root_cause_service`, and the `suspected_fault_type` it derives from) must vary with the
input telemetry. If someone later refactors RCA into something that returns a constant, this test
fails and the evaluation stops being publishable - which is the point.

See docs/paper/05-related-work.md §5.2 for the write-up, including the second-order disclosure that
root-cause accuracy is arithmetically equal to fault-type accuracy for this system.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ai_orchestration.anomaly.models import AnomalyResult
from ai_orchestration.rag.models import RetrievalResult
from ai_orchestration.rca import analyzer


def _rca(service: str, **metrics):
    anomaly = AnomalyResult(
        service=service, timestamp="2026-01-01T00:00:00+00:00", correlation_id="audit",
        status="scored", anomaly_score=1.0, is_anomaly=True, threshold=0.0,
        relevant_feature_values=metrics, model_version="isolation-forest-v1",
        feature_schema_version="v1",
    )
    empty_retrieval = RetrievalResult(
        query_text="q", incidents=[], source_collection="incident_memory", status="empty",
    )
    client = MagicMock()
    client.is_available.return_value = False   # no LLM: the conclusion must be reached without one
    client.base_url, client.model = "http://localhost:11434", "test"
    return analyzer.analyze(anomaly.to_dict(), empty_retrieval, client)


# The three inputs that matter most: same service, different metrics. If the output were keyed to
# the record's service (or were constant), these three could not disagree.
_SAME_SERVICE_CASES = [
    ({"jvm_memory_used_bytes": 5.0e8}, "memory-leak", "payment-service"),
    ({"http_server_requests_error_count": 25.0}, "auth-key-error", "auth-service"),
    ({"http_client_requests_avg_duration_ms": 5200.0}, "notification-latency", "notification-service"),
]


@pytest.mark.parametrize("metrics,expected_fault,expected_root", _SAME_SERVICE_CASES)
def test_identical_service_different_metrics_yield_different_root_causes(metrics, expected_fault, expected_root):
    rca = _rca("payment-service", **metrics)
    assert rca.suspected_fault_type == expected_fault
    assert rca.suspected_root_cause_service == expected_root
    assert rca.determination_method == "metric_signature"


def test_scored_output_takes_multiple_distinct_values_across_inputs():
    """The headline audit assertion: not a constant."""
    outputs = {_rca("payment-service", **m).suspected_root_cause_service for m, _, _ in _SAME_SERVICE_CASES}
    outputs.add(_rca("ledger-service", hikaricp_connections_active=10.0).suspected_root_cause_service)

    assert len(outputs) == 4, f"expected 4 distinct root causes across inputs, got {sorted(outputs)}"


def test_conclusion_is_reached_without_any_llm_call():
    """The deterministic layer must stand alone - otherwise 'degrades to a correct answer without
    the LLM' is not a property this system has."""
    client = MagicMock()
    client.is_available.return_value = False
    client.base_url, client.model = "http://localhost:11434", "test"

    anomaly = AnomalyResult(
        service="payment-service", timestamp="2026-01-01T00:00:00+00:00", correlation_id="audit",
        status="scored", anomaly_score=1.0, is_anomaly=True, threshold=0.0,
        relevant_feature_values={"http_client_requests_avg_duration_ms": 5200.0},
        model_version="isolation-forest-v1", feature_schema_version="v1",
    )
    empty = RetrievalResult(query_text="q", incidents=[], source_collection="incident_memory", status="empty")

    rca = analyzer.analyze(anomaly.to_dict(), empty, client)

    assert rca.suspected_root_cause_service == "notification-service"
    client.generate.assert_not_called()


def test_no_match_abstains_rather_than_emitting_a_default():
    """A system that always names something cannot be distinguished from one that guesses."""
    rca = _rca("payment-service", jvm_memory_used_bytes=4.0e7)  # everything at baseline

    assert rca.suspected_fault_type is None
    assert rca.determination_method == "fallback_unknown"


def test_the_single_agent_arms_answer_is_genuinely_model_output():
    """The other half of the audit: the baseline's scored field must come from the model, so the
    comparison is between two real outputs rather than a constant and a model."""
    import json

    from ai_orchestration.baseline import single_agent

    def _client_saying(root_cause: str) -> MagicMock:
        client = MagicMock()
        client.is_available.return_value = True
        client.base_url, client.model = "http://localhost:11434", "test"
        client.generate.return_value = json.dumps({
            "root_cause_service": root_cause, "symptom_service": "payment-service",
            "fault_type": "memory-leak", "reasoning": "r", "confidence": "high",
            "action_category": "investigate_manually", "recommended_action": "a",
        })
        return client

    anomaly = AnomalyResult(
        service="payment-service", timestamp="2026-01-01T00:00:00+00:00", correlation_id="audit",
        status="scored", anomaly_score=1.0, is_anomaly=True, threshold=0.0,
        relevant_feature_values={"jvm_memory_used_bytes": 5.0e8},
        model_version="isolation-forest-v1", feature_schema_version="v1",
    )
    empty = RetrievalResult(query_text="q", incidents=[], source_collection="incident_memory", status="empty")

    # Identical telemetry, different model responses -> different scored outputs.
    first = single_agent.diagnose(anomaly, empty, _client_saying("auth-service"))
    second = single_agent.diagnose(anomaly, empty, _client_saying("ledger-service"))

    assert first.suspected_root_cause_service == "auth-service"
    assert second.suspected_root_cause_service == "ledger-service"
