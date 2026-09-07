"""Recovery-verification category: the difference between "the action ran" and "the fault is gone".

The `fault_still_active` case is the important one - it is exactly Week 12's False Recovery Rate,
and the whole reason verification re-observes the service instead of trusting the remediation call's
own 200 response."""
from __future__ import annotations

import pytest
import responses

from ai_orchestration.execution.models import ExecutableAction, ExecutionResult
from ai_orchestration.execution.verify import verify_recovery

PAYMENT = "http://localhost:8082"


def _executed(status: str = "executed", service: str = "payment-service") -> ExecutionResult:
    return ExecutionResult(
        status=status, reason="test",
        action=ExecutableAction(kind="http_fault_reset", target_service=service,
                                description="test", http_path="/reset-memory-leak",
                                fault_type="memory-leak"),
        started_at="2026-01-01T00:00:00+00:00", completed_at="2026-01-01T00:00:05+00:00",
    )


def _fault_status(active: bool):
    responses.add(responses.GET, f"{PAYMENT}/fault-status", json={"faultActive": active}, status=200)


def _health(status: str = "UP"):
    responses.add(responses.GET, f"{PAYMENT}/actuator/health",
                  json={"status": status, "components": {}}, status=200)


@responses.activate
def test_fault_cleared_and_healthy_is_a_verified_recovery():
    _fault_status(False)
    _health("UP")

    verification = verify_recovery(_executed(), attempts=1, sleep=lambda s: None)

    assert verification.status == "recovered"
    assert verification.verified is True
    assert verification.checked_service == "payment-service"


@responses.activate
def test_fault_still_active_is_a_false_recovery_not_a_success():
    """The remediation call succeeded, but the service still says the fault is on. This must never
    be reported as recovered - it is the exact case Week 12 counts as a False Recovery."""
    for _ in range(3):
        _fault_status(True)

    verification = verify_recovery(_executed(), attempts=3, sleep=lambda s: None)

    assert verification.status == "fault_still_active"
    assert verification.verified is False
    assert verification.attempts == 3  # it really retried before concluding
    assert "false recovery" in verification.reason.lower()


@responses.activate
def test_retries_until_the_fault_clears():
    """Remediation is not instantaneous (a restarted pod has to come back). Concluding failure on
    the first check would measure the delay, not the outcome."""
    _fault_status(True)
    _fault_status(False)
    _health("UP")

    verification = verify_recovery(_executed(), attempts=5, sleep=lambda s: None)

    assert verification.status == "recovered"
    assert verification.attempts == 2


@responses.activate
def test_fault_cleared_but_service_unhealthy_is_not_a_recovery():
    _fault_status(False)
    _health("DOWN")

    verification = verify_recovery(_executed(), attempts=1, sleep=lambda s: None)

    assert verification.status == "unhealthy"
    assert verification.verified is False


@responses.activate
def test_unreachable_service_is_reported_as_unverifiable_not_recovered():
    responses.add(responses.GET, f"{PAYMENT}/fault-status", body=ConnectionError("down"))

    verification = verify_recovery(_executed(), attempts=1, sleep=lambda s: None)

    assert verification.status == "unreachable"
    assert verification.verified is False


@pytest.mark.parametrize("status", ["failed", "refused", "skipped_no_op", "dry_run"])
def test_nothing_executed_means_nothing_to_verify(status):
    """A dry run and a no-op changed nothing, so there is no recovery to claim - verification must
    not go and check a service that this run never touched and then credit itself with its health."""
    verification = verify_recovery(_executed(status=status), attempts=1, sleep=lambda s: None)

    assert verification.status == "not_applicable"
    assert verification.verified is False
