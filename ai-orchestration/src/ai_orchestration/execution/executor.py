"""The only code in this project that changes the running system.

Every gate is re-checked here, immediately before acting, rather than trusted from upstream graph
routing. That is deliberate: the graph already routes so that this node is only reached after the
Safety Validator passed and a human approved, but "the graph should not have called us" is not a
safety property - refusing to act when the preconditions are not literally present in the state we
were handed is. All three gates fail closed:

1. `human_decision` must be exactly `"approved"` (CLAUDE.md rule 21 - mandatory human approval).
2. `safety_check["passed"]` must be True (rule 20 - deterministic safety validation).
3. The incident must not already have used its remediation-attempt budget
   (`config.MAX_REMEDIATION_ATTEMPTS`).

A refusal is never an exception and never a success: it is an `ExecutionResult(status="refused")`
carrying the reason, so a refused execution is as visible in the final result and in Week 12's
metrics as a failed one.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from .. import config as ai_config
from ..remediation.models import RemediationProposal
from .actions import ActionResolutionError, resolve_action
from .kubernetes_client import KubernetesClient, KubernetesUnavailableError
from .models import ExecutableAction, ExecutionResult

ai_config.ensure_sibling_packages_importable()

from telemetry import config as telemetry_config  # noqa: E402

log = logging.getLogger("ai_orchestration.execution.executor")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _service_base_url(service: str) -> str:
    """Reuses Week 5's service registry, so the executor talks to exactly the same URLs the
    telemetry collector polls - and inherits the same env-var overrides that already let this code
    run unchanged against native processes, Docker Compose, or `kubectl port-forward`."""
    for svc in telemetry_config.SERVICES:
        if svc.name == service:
            return svc.base_url
    raise ActionResolutionError(f"No configured base URL for service '{service}'")


def _refused(reason: str, action: Optional[ExecutableAction] = None) -> ExecutionResult:
    log.warning("Execution refused: %s", reason)
    return ExecutionResult(status="refused", action=action, reason=reason)


def execute_approved_action(
    proposal: RemediationProposal,
    suspected_fault_type: Optional[str],
    safety_check: Optional[dict],
    human_decision: Optional[str],
    attempts_already_made: int = 0,
    kubernetes_client: Optional[KubernetesClient] = None,
) -> ExecutionResult:
    """Executes one approved remediation action, or refuses. Never raises: every failure mode is
    reported as a structured `ExecutionResult`, because a crash here would leave the pipeline
    unable to record what it did or did not do to the system."""

    # --- Gate 1: mandatory human approval -----------------------------------------------------
    if human_decision != "approved":
        return _refused(
            f"Not executed: human_decision is {human_decision!r}, not 'approved'. Human approval is "
            f"mandatory and is never inferred (CLAUDE.md rule 21)."
        )

    # --- Gate 2: deterministic safety validation ----------------------------------------------
    if not (safety_check or {}).get("passed"):
        return _refused(
            "Not executed: the Safety Validator did not pass this proposal. A human approval can "
            "never override a safety rejection (CLAUDE.md rule 20)."
        )

    # --- Gate 3: remediation attempt budget ---------------------------------------------------
    if attempts_already_made >= ai_config.MAX_REMEDIATION_ATTEMPTS:
        return _refused(
            f"Not executed: this incident already used its remediation-attempt budget "
            f"({attempts_already_made}/{ai_config.MAX_REMEDIATION_ATTEMPTS})."
        )

    try:
        action = resolve_action(proposal, suspected_fault_type)
    except ActionResolutionError as exc:
        log.error("Could not resolve an executable action: %s", exc)
        return ExecutionResult(
            status="failed", action=None, reason=f"Action resolution failed: {exc}",
            started_at=_now_iso(), completed_at=_now_iso(), duration_seconds=0.0,
        )

    if action.kind == "no_op":
        return ExecutionResult(
            status="skipped_no_op", action=action, reason=action.description,
            started_at=_now_iso(), completed_at=_now_iso(), duration_seconds=0.0,
        )

    if ai_config.EXECUTION_DRY_RUN:
        log.info("DRY RUN - would have executed: %s", action.description)
        return ExecutionResult(
            status="dry_run", action=action, dry_run=True,
            reason=f"Dry run enabled (AI_ORCHESTRATION_EXECUTION_DRY_RUN=true) - resolved but did "
                   f"not execute: {action.description}",
            started_at=_now_iso(), completed_at=_now_iso(), duration_seconds=0.0,
        )

    started_at, started_perf = _now_iso(), time.perf_counter()
    try:
        if action.kind == "http_fault_reset":
            detail = _execute_http_fault_reset(action)
        elif action.kind == "restart_pod":
            client = kubernetes_client or KubernetesClient()
            detail = client.restart_service(action.target_service)
        else:  # pragma: no cover - ExecutableAction.kind is a closed Literal
            raise ActionResolutionError(f"Unhandled action kind {action.kind!r}")

        duration = time.perf_counter() - started_perf
        log.info("Executed %s on %s in %.2fs", action.kind, action.target_service, duration)
        return ExecutionResult(
            status="executed", action=action, reason=f"Executed: {action.description}",
            detail=detail, started_at=started_at, completed_at=_now_iso(),
            duration_seconds=round(duration, 3),
        )
    except (requests.RequestException, KubernetesUnavailableError, ActionResolutionError) as exc:
        duration = time.perf_counter() - started_perf
        log.error("Execution failed for %s on %s: %s", action.kind, action.target_service, exc)
        return ExecutionResult(
            status="failed", action=action,
            reason=f"Execution failed: {type(exc).__name__}: {exc}",
            started_at=started_at, completed_at=_now_iso(), duration_seconds=round(duration, 3),
        )


def _execute_http_fault_reset(action: ExecutableAction) -> dict:
    url = f"{_service_base_url(action.target_service)}{action.http_path}"
    response = requests.post(url, timeout=ai_config.EXECUTION_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    try:
        body = response.json()
    except ValueError:
        body = {"raw": response.text}
    return {"url": url, "status_code": response.status_code, "response": body}
