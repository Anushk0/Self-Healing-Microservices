"""Safety category: `validate_safety()` is plain Python and deterministic - no mocks needed, since
there is nothing async/external to fake. Covers the whitelist rules, the ledger-service high-risk
rule, and the requires_human_approval defense-in-depth check."""
from __future__ import annotations

from ai_orchestration.remediation.models import RemediationProposal
from ai_orchestration.safety.validator import validate_safety


def _proposal(**overrides) -> RemediationProposal:
    defaults = dict(
        recommended_action="Reset the notification-service latency fault injection.",
        target_service="notification-service",
        action_category="reset_fault_configuration",
        rationale="RCA determined suspected_fault_type='notification-latency' with confidence='high'.",
        supporting_evidence=["http_client_requests_avg_duration_ms=5000.0"],
        expected_outcome="Latency returns to baseline.",
        risk_level="medium",
        confidence="high",
    )
    defaults.update(overrides)
    return RemediationProposal(**defaults)


def test_valid_proposal_passes():
    result = validate_safety(_proposal())
    assert result.passed is True
    assert result.violated_rules == []
    assert "passed" in result.reason.lower()


def test_all_four_fault_target_services_pass_at_medium_risk():
    for service in ("auth-service", "payment-service", "ledger-service", "notification-service"):
        result = validate_safety(_proposal(target_service=service, risk_level="medium"))
        assert result.passed is True, f"{service} at medium risk should pass"


def test_high_risk_action_on_ledger_service_is_rejected():
    result = validate_safety(_proposal(target_service="ledger-service", risk_level="high"))
    assert result.passed is False
    assert any("ledger-service" in rule for rule in result.violated_rules)


def test_high_risk_action_on_non_ledger_service_still_passes():
    # The rule is specifically about ledger-service's financial/transactional state, not a blanket
    # "no high risk anywhere" rule - see validator.py's module docstring.
    result = validate_safety(_proposal(target_service="payment-service", risk_level="high"))
    assert result.passed is True


def test_unknown_target_service_is_rejected():
    # Pydantic's RemediationProposal.target_service is a plain str, not a Literal - a bad value
    # must still be caught here, independently, per validator.py's "defense in depth" docstring.
    result = validate_safety(_proposal(target_service="some-other-service"))
    assert result.passed is False
    assert any("not one of this project's known services" in rule for rule in result.violated_rules)


def test_requires_human_approval_false_is_rejected():
    # RemediationProposal's own type (Literal[True]) prevents this in practice, but the validator
    # re-checks it anyway per the module's defense-in-depth docstring - constructing the model
    # with model_construct() bypasses Pydantic validation entirely, simulating a hand-built dict.
    tampered = RemediationProposal.model_construct(
        recommended_action="x", target_service="auth-service", action_category="no_action_needed",
        rationale="x", supporting_evidence=[], expected_outcome="x", risk_level="low",
        confidence="low", requires_human_approval=False,
    )
    result = validate_safety(tampered)
    assert result.passed is False
    assert any("requires_human_approval" in rule for rule in result.violated_rules)


def test_unknown_action_category_is_rejected():
    tampered = RemediationProposal.model_construct(
        recommended_action="x", target_service="auth-service", action_category="delete_everything",
        rationale="x", supporting_evidence=[], expected_outcome="x", risk_level="low",
        confidence="low", requires_human_approval=True,
    )
    result = validate_safety(tampered)
    assert result.passed is False
    assert any("not in the known-safe whitelist" in rule for rule in result.violated_rules)


def test_multiple_violations_are_all_collected_not_just_the_first():
    tampered = RemediationProposal.model_construct(
        recommended_action="x", target_service="ledger-service", action_category="delete_everything",
        rationale="x", supporting_evidence=[], expected_outcome="x", risk_level="high",
        confidence="low", requires_human_approval=False,
    )
    result = validate_safety(tampered)
    assert result.passed is False
    assert len(result.violated_rules) == 3  # unknown category, ledger+high-risk, requires_human_approval
