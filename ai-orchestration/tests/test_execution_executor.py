"""Execution category (part 2): the gates, and the two real mechanisms.

The gate tests matter more than the mechanism tests: they are the difference between "the graph
happens to call this only after approval" and "this refuses to act without approval". Each gate is
tested by handing the executor a state that upstream routing should never produce, and asserting it
refuses anyway.

HTTP is mocked with `responses` (same library and pattern as test_ollama_client.py); Kubernetes is
a MagicMock. Nothing here contacts a real service or cluster."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import responses

from ai_orchestration import config as ai_config
from ai_orchestration.execution.executor import execute_approved_action
from ai_orchestration.execution.kubernetes_client import KubernetesUnavailableError
from ai_orchestration.remediation.models import RemediationProposal

PASSED = {"passed": True, "reason": "ok", "violated_rules": []}


def _proposal(**overrides) -> RemediationProposal:
    defaults = dict(
        recommended_action="Reset the payment-service memory-leak fault injection.",
        target_service="payment-service", action_category="reset_fault_configuration",
        rationale="x", supporting_evidence=[], expected_outcome="x",
        risk_level="medium", confidence="high",
    )
    defaults.update(overrides)
    return RemediationProposal(**defaults)


# --- Gate 1: mandatory human approval ---------------------------------------------------------

@pytest.mark.parametrize("decision", [None, "rejected", "approve", "APPROVED", "yes", ""])
def test_refuses_without_an_exact_approved_decision(decision):
    """Anything other than exactly "approved" must refuse - including near-misses like "approve"
    and "APPROVED", so a typo in a caller can never be read as consent."""
    result = execute_approved_action(_proposal(), "memory-leak", PASSED, decision)
    assert result.status == "refused"
    assert result.succeeded is False
    assert "human approval is mandatory" in result.reason.lower()


# --- Gate 2: deterministic safety validation --------------------------------------------------

@pytest.mark.parametrize("safety_check", [
    None, {}, {"passed": False, "reason": "ledger-service high risk", "violated_rules": ["r"]},
])
def test_human_approval_cannot_override_a_safety_rejection(safety_check):
    result = execute_approved_action(_proposal(), "memory-leak", safety_check, "approved")
    assert result.status == "refused"
    assert "safety" in result.reason.lower()


# --- Gate 3: remediation attempt budget -------------------------------------------------------

def test_refuses_once_the_attempt_budget_is_exhausted():
    result = execute_approved_action(
        _proposal(), "memory-leak", PASSED, "approved",
        attempts_already_made=ai_config.MAX_REMEDIATION_ATTEMPTS,
    )
    assert result.status == "refused"
    assert "budget" in result.reason.lower()


# --- Mechanisms -------------------------------------------------------------------------------

@responses.activate
def test_http_fault_reset_posts_to_the_real_reset_endpoint_and_records_the_response():
    responses.add(responses.POST, "http://localhost:8082/reset-memory-leak",
                  json={"faultActive": False}, status=200)

    result = execute_approved_action(_proposal(), "memory-leak", PASSED, "approved")

    assert result.status == "executed"
    assert result.succeeded is True
    assert result.action.kind == "http_fault_reset"
    assert result.detail["status_code"] == 200
    assert result.detail["response"] == {"faultActive": False}
    assert result.duration_seconds is not None
    assert len(responses.calls) == 1


@responses.activate
def test_http_failure_is_reported_as_failed_not_raised():
    """A failed remediation must come back as structured data - the retry loop routes on it, and a
    raised exception would abort the graph before anything could be recorded or retried."""
    responses.add(responses.POST, "http://localhost:8082/reset-memory-leak", status=500)

    result = execute_approved_action(_proposal(), "memory-leak", PASSED, "approved")

    assert result.status == "failed"
    assert result.succeeded is False
    assert "500" in result.reason


def test_restart_pod_uses_the_injected_kubernetes_client():
    k8s = MagicMock()
    k8s.restart_service.return_value = {"deleted_pods": ["payment-service-abc"]}

    result = execute_approved_action(
        _proposal(action_category="restart_service", risk_level="high"),
        "memory-leak", PASSED, "approved", kubernetes_client=k8s,
    )

    assert result.status == "executed"
    assert result.action.kind == "restart_pod"
    assert result.detail == {"deleted_pods": ["payment-service-abc"]}
    k8s.restart_service.assert_called_once_with("payment-service")


def test_kubernetes_unavailable_is_reported_as_failed():
    k8s = MagicMock()
    k8s.restart_service.side_effect = KubernetesUnavailableError("no cluster")

    result = execute_approved_action(
        _proposal(action_category="restart_service", risk_level="high"),
        "memory-leak", PASSED, "approved", kubernetes_client=k8s,
    )

    assert result.status == "failed"
    assert "no cluster" in result.reason


def test_no_op_category_executes_nothing():
    k8s = MagicMock()
    result = execute_approved_action(
        _proposal(action_category="investigate_manually", risk_level="low"),
        "memory-leak", PASSED, "approved", kubernetes_client=k8s,
    )
    assert result.status == "skipped_no_op"
    assert result.succeeded is False  # nothing changed, so nothing recovered
    k8s.restart_service.assert_not_called()


@responses.activate
def test_dry_run_resolves_the_action_but_sends_nothing(monkeypatch):
    monkeypatch.setattr(ai_config, "EXECUTION_DRY_RUN", True)
    responses.add(responses.POST, "http://localhost:8082/reset-memory-leak", status=200)

    result = execute_approved_action(_proposal(), "memory-leak", PASSED, "approved")

    assert result.status == "dry_run"
    assert result.dry_run is True
    assert result.action.kind == "http_fault_reset"   # it still resolved what it *would* do
    assert result.succeeded is False                   # but nothing changed
    assert len(responses.calls) == 0                   # and nothing was sent


def test_unresolvable_action_is_a_failure_not_a_silent_skip():
    """A proposal the executor cannot carry out is a real failed remediation attempt. Reporting it
    as a no-op would let the retry loop believe an action succeeded."""
    result = execute_approved_action(
        _proposal(target_service="auth-service"), "memory-leak", PASSED, "approved",
    )
    assert result.status == "failed"
    assert "resolution failed" in result.reason.lower()
