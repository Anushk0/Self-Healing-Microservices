"""Execution category (part 1): deterministic resolution of an approved proposal into a concrete
action. Pure functions, no I/O - nothing here contacts a service or a cluster."""
from __future__ import annotations

import pytest

from ai_orchestration.execution.actions import ActionResolutionError, resolve_action
from ai_orchestration.remediation.models import RemediationProposal


def _proposal(**overrides) -> RemediationProposal:
    defaults = dict(
        recommended_action="x", target_service="payment-service",
        action_category="reset_fault_configuration", rationale="x", supporting_evidence=[],
        expected_outcome="x", risk_level="medium", confidence="high",
    )
    defaults.update(overrides)
    return RemediationProposal(**defaults)


@pytest.mark.parametrize("fault_type,service,category,expected_path", [
    ("auth-key-error", "auth-service", "restore_configuration", "/reset-auth-key"),
    ("memory-leak", "payment-service", "reset_fault_configuration", "/reset-memory-leak"),
    ("db-lock", "ledger-service", "release_resources", "/reset-db-lock"),
    ("notification-latency", "notification-service", "reset_fault_configuration", "/reset-latency"),
])
def test_each_fault_resolves_to_its_real_reset_endpoint(fault_type, service, category, expected_path):
    """The paths come from Week 5's telemetry.experiment.FAULTS, which the experiment orchestrator
    already uses against the real Java services - so this also proves the executor and the
    experiment runner can never drift apart."""
    action = resolve_action(_proposal(target_service=service, action_category=category), fault_type)
    assert action.kind == "http_fault_reset"
    assert action.http_path == expected_path
    assert action.target_service == service
    assert action.fault_type == fault_type


def test_restart_service_resolves_to_pod_restart():
    action = resolve_action(
        _proposal(action_category="restart_service", risk_level="high"), "memory-leak",
    )
    assert action.kind == "restart_pod"
    assert action.target_service == "payment-service"


@pytest.mark.parametrize("category", ["investigate_manually", "no_action_needed"])
def test_escalation_categories_resolve_to_no_op(category):
    action = resolve_action(_proposal(action_category=category, risk_level="low"), "memory-leak")
    assert action.kind == "no_op"


def test_service_mismatch_is_refused_rather_than_guessed():
    """A proposal that names a different service than the fault's real owner means the proposal and
    the RCA disagree - resolving it by POSTing to whichever service was named would hide that."""
    with pytest.raises(ActionResolutionError, match="refusing to guess"):
        resolve_action(
            _proposal(target_service="auth-service", action_category="reset_fault_configuration"),
            "memory-leak",  # memory-leak's reset endpoint belongs to payment-service
        )


def test_unknown_fault_type_cannot_resolve_a_reset():
    with pytest.raises(ActionResolutionError, match="not one of the four controlled faults"):
        resolve_action(_proposal(), None)


def test_unknown_action_category_has_no_mechanism():
    tampered = RemediationProposal.model_construct(
        recommended_action="x", target_service="payment-service", action_category="rm_-rf",
        rationale="x", supporting_evidence=[], expected_outcome="x", risk_level="low",
        confidence="low", requires_human_approval=True,
    )
    with pytest.raises(ActionResolutionError, match="no mechanism"):
        resolve_action(tampered, "memory-leak")
