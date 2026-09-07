"""Week 11 category (part 1): the single-agent baseline's own diagnostic step.

Two things are being pinned down here. First, that it works - one LLM call in, a usable structured
diagnosis out. Second, and more importantly for the paper, that its *failure* modes are recorded
rather than repaired: a hallucinated service, an invented action, or an unavailable model must all
produce visible, countable evidence, because those are results Week 12 needs to measure, not
inconveniences to paper over.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from ai_orchestration.anomaly.models import AnomalyResult
from ai_orchestration.baseline import single_agent
from ai_orchestration.rag.models import RetrievalResult, RetrievedIncident


def _anomaly(service: str = "payment-service", **feature_values) -> AnomalyResult:
    return AnomalyResult(
        service=service, timestamp="2026-01-01T00:00:00+00:00", correlation_id="c1",
        status="scored", anomaly_score=0.5, is_anomaly=True, threshold=0.0,
        relevant_feature_values=feature_values or {"jvm_memory_used_bytes": 5.0e8},
        model_version="isolation-forest-v1", feature_schema_version="v1",
    )


def _retrieval(*incidents: RetrievedIncident) -> RetrievalResult:
    return RetrievalResult(
        query_text="q", incidents=list(incidents), source_collection="incident_memory",
        status="retrieved" if incidents else "empty",
    )


def _client(response: str | dict, available: bool = True) -> MagicMock:
    client = MagicMock()
    client.is_available.return_value = available
    client.base_url = "http://localhost:11434"
    client.model = "llama3.2:1b"
    client.generate.return_value = response if isinstance(response, str) else json.dumps(response)
    return client


def _valid_response(**overrides) -> dict:
    body = {
        "root_cause_service": "notification-service",
        "symptom_service": "payment-service",
        "fault_type": "notification-latency",
        "reasoning": "payment-service's outbound call duration is far above baseline.",
        "confidence": "high",
        "action_category": "reset_fault_configuration",
        "recommended_action": "Reset the notification-service latency fault injection.",
    }
    body.update(overrides)
    return body


def test_produces_a_structured_diagnosis_from_one_llm_call():
    client = _client(_valid_response())

    diagnosis = single_agent.diagnose(_anomaly(), _retrieval(), client)

    assert diagnosis.status == "diagnosed"
    assert diagnosis.suspected_root_cause_service == "notification-service"
    assert diagnosis.symptom_service == "payment-service"
    assert diagnosis.suspected_fault_type == "notification-latency"
    assert diagnosis.parse_issues == []
    assert client.generate.call_count == 1  # exactly one call - that is the whole design


def test_the_prompt_contains_the_same_evidence_and_retrieved_incidents_the_multi_agent_arm_gets():
    """Fairness check: if the baseline were given less evidence, the comparison would measure the
    evidence gap rather than the agent decomposition."""
    client = _client(_valid_response())
    retrieved = RetrievedIncident(
        incident_id="inc-0037", distance=0.05, fault_type="notification-latency",
        root_cause_service="notification-service", symptom_service="payment-service",
        severity="high", data_source="real", postmortem_excerpt="notification latency incident",
    )

    single_agent.diagnose(_anomaly(http_client_requests_avg_duration_ms=5200.0), _retrieval(retrieved), client)

    prompt = client.generate.call_args[0][0]
    assert "5200.0" in prompt                 # the current anomaly's real metric value
    assert "inc-0037" in prompt               # the retrieved incident
    assert "notification latency incident" in prompt


def test_unavailable_llm_yields_no_diagnosis_at_all():
    """The designed asymmetry: the multi-agent pipeline still reaches a deterministic conclusion
    without an LLM; this one cannot, and says so instead of inventing a fallback."""
    client = _client("", available=False)

    diagnosis = single_agent.diagnose(_anomaly(), _retrieval(), client)

    assert diagnosis.status == "llm_unavailable"
    assert diagnosis.suspected_root_cause_service is None
    client.generate.assert_not_called()


@pytest.mark.parametrize("bad_response", ["not json at all", "[1, 2, 3]", '{"partial": true}'])
def test_malformed_or_incomplete_responses_are_reported_as_llm_error(bad_response):
    diagnosis = single_agent.diagnose(_anomaly(), _retrieval(), _client(bad_response))

    assert diagnosis.status == "llm_error"
    assert diagnosis.raw_response == bad_response


def test_a_hallucinated_service_is_recorded_not_silently_accepted():
    """A model naming a service that does not exist is exactly the hallucination Week 12 counts."""
    client = _client(_valid_response(root_cause_service="billing-service"))

    diagnosis = single_agent.diagnose(_anomaly(), _retrieval(), client)

    assert diagnosis.suspected_root_cause_service is None
    assert any("billing-service" in issue for issue in diagnosis.parse_issues)
    # Cannot act on a service that does not exist.
    assert diagnosis.proposed_action_category == "investigate_manually"


def test_an_invented_action_is_coerced_to_a_non_executable_one_and_recorded():
    client = _client(_valid_response(action_category="delete_the_database"))

    diagnosis = single_agent.diagnose(_anomaly(), _retrieval(), client)

    assert diagnosis.proposed_action_category == "investigate_manually"
    assert any("delete_the_database" in issue for issue in diagnosis.parse_issues)


def test_an_unknown_fault_type_is_dropped_rather_than_passed_through():
    client = _client(_valid_response(fault_type="cosmic-rays"))

    diagnosis = single_agent.diagnose(_anomaly(), _retrieval(), client)

    assert diagnosis.suspected_fault_type is None
    assert any("cosmic-rays" in issue for issue in diagnosis.parse_issues)


# --- Conversion into the shared proposal type --------------------------------------------------

def test_converted_proposal_still_requires_human_approval():
    """The baseline gets no exemption from any downstream invariant."""
    diagnosis = single_agent.diagnose(_anomaly(), _retrieval(), _client(_valid_response()))

    proposal = single_agent.to_remediation_proposal(diagnosis)

    assert proposal.requires_human_approval is True
    assert proposal.target_service == "notification-service"
    assert proposal.action_category == "reset_fault_configuration"


def test_a_baseline_restart_proposal_is_high_risk_exactly_like_the_multi_agent_one():
    """So the Safety Validator judges both arms by identical rules - including refusing a restart
    of ledger-service."""
    from ai_orchestration.safety.validator import validate_safety

    client = _client(_valid_response(root_cause_service="ledger-service", fault_type="db-lock",
                                     action_category="restart_service"))
    diagnosis = single_agent.diagnose(_anomaly("ledger-service"), _retrieval(), client)

    proposal = single_agent.to_remediation_proposal(diagnosis)
    assert proposal.risk_level == "high"

    safety = validate_safety(proposal)
    assert safety.passed is False
    assert any("ledger-service" in rule for rule in safety.violated_rules)


def test_an_unusable_diagnosis_converts_to_a_non_executable_escalation():
    client = _client("", available=False)
    diagnosis = single_agent.diagnose(_anomaly(), _retrieval(), client)

    proposal = single_agent.to_remediation_proposal(diagnosis)

    assert proposal.action_category == "investigate_manually"
    assert proposal.risk_level == "low"
    assert proposal.requires_human_approval is True
