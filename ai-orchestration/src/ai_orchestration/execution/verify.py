"""Post-execution recovery verification: did the action actually fix it?

The Week 12 roadmap is explicit that Recovery Success Rate must be measured by "re-checking the
service's health after execution, not just 'action ran without erroring'", and that False Recovery
Rate is "% where the system reported success but the fault was actually still present". Both are
only measurable if something re-observes the system after acting - that is this module.

It re-uses Week 5's own `telemetry.collector.fetch_fault_status` / `fetch_health` rather than
re-implementing endpoint knowledge, so verification asks the services the exact same questions the
telemetry collector has been asking since Week 5.

Retries with a short interval because remediation is not instantaneous: a restarted pod needs to
come back, and a memory-leak reset needs a GC cycle before heap settles. Reporting "not recovered"
one millisecond after acting would be measuring the delay, not the outcome.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from .. import config as ai_config
from .models import ExecutionResult, RecoveryVerification

ai_config.ensure_sibling_packages_importable()

from telemetry import collector as telemetry_collector, config as telemetry_config  # noqa: E402

log = logging.getLogger("ai_orchestration.execution.verify")


def _base_url(service: str) -> Optional[str]:
    for svc in telemetry_config.SERVICES:
        if svc.name == service:
            return svc.base_url
    return None


def verify_recovery(
    execution_result: ExecutionResult,
    fault_type: Optional[str] = None,
    attempts: Optional[int] = None,
    interval_seconds: Optional[float] = None,
    sleep=time.sleep,
) -> RecoveryVerification:
    """Re-checks the service the action targeted. `sleep` is injectable purely so tests don't have
    to wait in real time; production always uses `time.sleep`."""
    if not execution_result.succeeded or execution_result.action is None:
        return RecoveryVerification(
            status="not_applicable", verified=False, attempts=0,
            reason=(
                f"Nothing was executed (execution status={execution_result.status!r}), so there is "
                f"no remediation outcome to verify."
            ),
        )

    service = execution_result.action.target_service
    base_url = _base_url(service)
    if base_url is None:
        return RecoveryVerification(
            status="unreachable", verified=False, attempts=0, checked_service=service,
            reason=f"No configured base URL for '{service}' - cannot verify recovery.",
        )

    attempts = attempts if attempts is not None else ai_config.RECOVERY_VERIFICATION_ATTEMPTS
    interval = interval_seconds if interval_seconds is not None else ai_config.RECOVERY_VERIFICATION_INTERVAL_SECONDS

    last: RecoveryVerification | None = None
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            sleep(interval)
        last = _check_once(base_url, service, attempt)
        if last.verified:
            log.info("Recovery verified for '%s' after %d attempt(s)", service, attempt)
            return last
        log.info("Recovery not yet verified for '%s' (attempt %d/%d): %s", service, attempt, attempts, last.status)

    return last  # type: ignore[return-value]  # attempts >= 1, so `last` is always set


def _check_once(base_url: str, service: str, attempt: int) -> RecoveryVerification:
    correlation_id = str(uuid.uuid4())
    detail: dict = {}

    try:
        fault_status = telemetry_collector.fetch_fault_status(base_url, correlation_id)
        detail["fault_status"] = fault_status
    except Exception as exc:  # noqa: BLE001 - any transport/parse problem means "cannot verify"
        return RecoveryVerification(
            status="unreachable", verified=False, attempts=attempt, checked_service=service,
            reason=f"Could not read {service}'s /fault-status: {type(exc).__name__}: {exc}",
            detail=detail,
        )

    if fault_status.get("faultActive") is True:
        return RecoveryVerification(
            status="fault_still_active", verified=False, attempts=attempt, checked_service=service,
            reason=(
                f"{service} still reports faultActive=true after remediation - the action ran but "
                f"did not clear the fault (this is a False Recovery, not a success)."
            ),
            detail=detail,
        )

    try:
        health = telemetry_collector.fetch_health(base_url, correlation_id)
        detail["health"] = health
    except Exception as exc:  # noqa: BLE001
        return RecoveryVerification(
            status="unreachable", verified=False, attempts=attempt, checked_service=service,
            reason=f"{service}'s fault flag is clear but /actuator/health is unreadable: "
                   f"{type(exc).__name__}: {exc}",
            detail=detail,
        )

    if str(health.get("status")).upper() != "UP":
        return RecoveryVerification(
            status="unhealthy", verified=False, attempts=attempt, checked_service=service,
            reason=f"{service}'s fault flag is clear but its health is {health.get('status')!r}, not UP.",
            detail=detail,
        )

    return RecoveryVerification(
        status="recovered", verified=True, attempts=attempt, checked_service=service,
        reason=f"{service} reports faultActive=false and health UP after remediation.",
        detail=detail,
    )
