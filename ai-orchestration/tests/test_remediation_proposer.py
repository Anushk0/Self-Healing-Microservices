"""Remediation category: proposals for each fault, no execution capability, structured
validation, and valid risk/confidence fields."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_orchestration.rca.models import RootCauseAnalysis
from ai_orchestration.remediation.models import RemediationProposal
from ai_orchestration.remediation.proposer import propose_remediation


def _rca(fault_type, root, symptom, confidence="medium", method="metric_signature") -> RootCauseAnalysis:
    return RootCauseAnalysis(
        suspected_fault_type=fault_type, suspected_root_cause_service=root, symptom_service=symptom,
        affected_services=sorted({root, symptom}), relevant_metrics=[], metric_anomalies=[],
        evidence_summary="evidence", confidence=confidence, determination_method=method,
    )


@pytest.mark.parametrize("fault_type,root,symptom,expected_target", [
    ("auth-key-error", "auth-service", "payment-service", "auth-service"),
    ("memory-leak", "payment-service", "payment-service", "payment-service"),
    ("db-lock", "ledger-service", "ledger-service", "ledger-service"),
    ("notification-latency", "notification-service", "payment-service", "notification-service"),
])
def test_proposal_generated_for_each_known_fault(fault_type, root, symptom, expected_target):
    rca = _rca(fault_type, root, symptom)

    proposal = propose_remediation(rca)

    assert isinstance(proposal, RemediationProposal)
    assert proposal.target_service == expected_target
    assert proposal.recommended_action  # non-empty
    assert proposal.rationale
    assert proposal.expected_outcome


def test_notification_latency_proposal_targets_notification_service_not_payment_service():
    """The proposal must act on the root cause, not the symptom-visible service."""
    rca = _rca("notification-latency", "notification-service", "payment-service")

    proposal = propose_remediation(rca)

    assert proposal.target_service == "notification-service"
    assert "notification-service" in proposal.recommended_action


def test_low_confidence_rca_falls_back_to_investigate_manually_regardless_of_fault_type():
    """Do not hardcode the answer solely based on fault_type - a low-confidence RCA must not
    produce a confident, specific state-changing proposal."""
    rca = _rca("memory-leak", "payment-service", "payment-service", confidence="low", method="rag_only")

    proposal = propose_remediation(rca)

    assert proposal.action_category == "investigate_manually"
    assert proposal.risk_level == "low"
    assert proposal.confidence == "low"


def test_unknown_fault_type_falls_back_to_investigate_manually():
    rca = _rca(None, "notification-service", "notification-service", confidence="low", method="fallback_unknown")

    proposal = propose_remediation(rca)

    assert proposal.action_category == "investigate_manually"
    assert proposal.target_service == "notification-service"


def test_requires_human_approval_is_always_true():
    for fault_type, root, symptom in [
        ("auth-key-error", "auth-service", "payment-service"),
        ("memory-leak", "payment-service", "payment-service"),
        ("db-lock", "ledger-service", "ledger-service"),
        ("notification-latency", "notification-service", "payment-service"),
        (None, "payment-service", "payment-service"),
    ]:
        proposal = propose_remediation(_rca(fault_type, root, symptom))
        assert proposal.requires_human_approval is True


def test_proposal_has_no_executable_side_effects():
    """A RemediationProposal is pure data - it exposes no method that could execute anything."""
    proposal = propose_remediation(_rca("db-lock", "ledger-service", "ledger-service"))

    executable_looking_attrs = [a for a in dir(proposal) if a.startswith(("execute", "run", "apply", "invoke_"))]
    assert executable_looking_attrs == []


def test_invalid_risk_level_rejected_by_pydantic_validation():
    with pytest.raises(ValidationError):
        RemediationProposal(
            recommended_action="x", target_service="payment-service", action_category="reset_fault_configuration",
            rationale="x", expected_outcome="x", risk_level="extreme", confidence="medium",
        )


def test_invalid_action_category_rejected_by_pydantic_validation():
    with pytest.raises(ValidationError):
        RemediationProposal(
            recommended_action="x", target_service="payment-service", action_category="delete_everything",
            rationale="x", expected_outcome="x", risk_level="low", confidence="medium",
        )


def test_requires_human_approval_cannot_be_set_false():
    with pytest.raises(ValidationError):
        RemediationProposal(
            recommended_action="x", target_service="payment-service", action_category="investigate_manually",
            rationale="x", expected_outcome="x", risk_level="low", confidence="low",
            requires_human_approval=False,
        )


@pytest.mark.parametrize("fault_type,root,symptom", [
    ("auth-key-error", "auth-service", "payment-service"),
    ("memory-leak", "payment-service", "payment-service"),
    ("db-lock", "ledger-service", "ledger-service"),
    ("notification-latency", "notification-service", "payment-service"),
])
def test_risk_and_confidence_fields_are_valid_enum_values(fault_type, root, symptom):
    proposal = propose_remediation(_rca(fault_type, root, symptom))

    assert proposal.risk_level in ("low", "medium", "high")
    assert proposal.confidence in ("low", "medium", "high")


# --- Week 10: the failed-fix escalation ladder (the Week 8 roadmap item deferred to Week 10) ---

def _failed(action_category: str, target_service: str = "payment-service", reason: str = "HTTP 500") -> dict:
    return {"action_category": action_category, "target_service": target_service, "reason": reason}


def test_no_failed_actions_still_proposes_the_normal_first_action():
    """The ladder must not change behavior for the ordinary, first-attempt case."""
    proposal = propose_remediation(_rca("memory-leak", "payment-service", "payment-service"))
    assert proposal.action_category == "reset_fault_configuration"
    assert proposal.risk_level == "medium"


def test_escalates_to_restart_after_the_targeted_reset_failed():
    rca = _rca("memory-leak", "payment-service", "payment-service")

    proposal = propose_remediation(rca, failed_actions=[_failed("reset_fault_configuration")])

    assert proposal.action_category == "restart_service"
    assert proposal.target_service == "payment-service"
    # Always high risk: restarting drops in-flight requests. This is what makes the Safety
    # Validator refuse the same escalation for ledger-service.
    assert proposal.risk_level == "high"
    assert proposal.requires_human_approval is True
    assert "HTTP 500" in " ".join(proposal.supporting_evidence)


def test_escalates_to_human_after_both_rungs_failed():
    rca = _rca("memory-leak", "payment-service", "payment-service")

    proposal = propose_remediation(rca, failed_actions=[
        _failed("reset_fault_configuration"), _failed("restart_service"),
    ])

    assert proposal.action_category == "investigate_manually"
    assert proposal.risk_level == "low"
    assert "No further automated action" in proposal.recommended_action


def test_never_re_proposes_an_action_that_already_failed():
    """The whole point of feeding failed actions back in: a retry must differ from what failed."""
    rca = _rca("db-lock", "ledger-service", "ledger-service")

    first = propose_remediation(rca)
    second = propose_remediation(rca, failed_actions=[_failed(first.action_category, "ledger-service")])
    third = propose_remediation(rca, failed_actions=[
        _failed(first.action_category, "ledger-service"), _failed(second.action_category, "ledger-service"),
    ])

    assert len({first.action_category, second.action_category, third.action_category}) == 3


def test_db_lock_escalation_produces_the_proposal_the_safety_validator_must_refuse():
    """End-to-end coherence check between Week 10's ladder and Week 9's safety rule: escalating a
    db-lock incident means restarting ledger-service, which is exactly the high-risk-on-ledger case
    the Safety Validator exists to refuse. This is what made that rule reachable in the real
    pipeline (Week 9 documented it as unreachable at the time)."""
    from ai_orchestration.safety.validator import validate_safety

    rca = _rca("db-lock", "ledger-service", "ledger-service")
    escalated = propose_remediation(rca, failed_actions=[_failed("release_resources", "ledger-service")])

    assert escalated.action_category == "restart_service"
    assert escalated.target_service == "ledger-service"

    safety = validate_safety(escalated)
    assert safety.passed is False
    assert any("ledger-service" in rule for rule in safety.violated_rules)


def test_low_confidence_rca_still_falls_back_regardless_of_failed_actions():
    rca = _rca("memory-leak", "payment-service", "payment-service", confidence="low")

    proposal = propose_remediation(rca, failed_actions=[_failed("reset_fault_configuration")])

    assert proposal.action_category == "investigate_manually"
