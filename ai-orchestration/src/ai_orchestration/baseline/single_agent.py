"""Week 11's single-agent baseline: one LLM call that does the whole diagnosis.

This is a genuinely separate implementation, not the multi-agent graph with nodes switched off -
per the Week 11 roadmap, sharing the diagnostic machinery would risk an unfair comparison (shared
bugs, and accidental leakage of deterministic conclusions into the "LLM-only" arm). Nothing in this
module imports `rca/`, `remediation/`, or `llm/interpret.py`.

What IS shared, deliberately, is everything that is not the diagnostic step: the same Isolation
Forest detection upstream, the same RAG retrieval, and the same Safety Validator / human approval /
execution / verification / feedback downstream (see `baseline/graph.py`). That is what isolates the
variable under study to *agent decomposition* rather than to "one arm had retrieval and the other
did not", or "one arm was allowed to execute and the other was not".

The asymmetry this baseline exposes on purpose: the multi-agent pipeline reaches a deterministic
conclusion with no LLM at all and only *narrates* with one, so it degrades to a still-correct
answer when Ollama is unavailable. This baseline has nothing to fall back on - no LLM means no
diagnosis. That is a real property of single-agent designs, so it is reported honestly
(`status="llm_unavailable"`) rather than hidden behind a deterministic backstop that would make the
comparison meaningless.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from ..anomaly.models import AnomalyResult
from ..llm.ollama_client import OllamaClient, OllamaUnavailableError
from ..rag.models import RetrievalResult
from ..remediation.models import RemediationProposal
from .models import SingleAgentDiagnosis
from .prompts import build_single_agent_prompt

log = logging.getLogger("ai_orchestration.baseline.single_agent")

KNOWN_SERVICES = frozenset({"auth-service", "payment-service", "ledger-service", "notification-service"})
KNOWN_FAULT_TYPES = frozenset({"auth-key-error", "memory-leak", "db-lock", "notification-latency"})
KNOWN_ACTION_CATEGORIES = frozenset({
    "restore_configuration", "reset_fault_configuration", "release_resources", "restart_service",
    "investigate_manually", "no_action_needed",
})

_REQUIRED_KEYS = ("root_cause_service", "fault_type", "reasoning", "confidence", "action_category")


def diagnose(
    anomaly_result: AnomalyResult, retrieval: RetrievalResult, client: OllamaClient
) -> SingleAgentDiagnosis:
    """One prompt, one call, one answer. Never raises - an unavailable or malformed LLM is a
    measurable outcome of this design, not an exception to propagate."""
    if not client.is_available():
        return SingleAgentDiagnosis(
            status="llm_unavailable",
            reason=(
                f"Ollama server not reachable at {client.base_url}. The single-agent baseline has "
                f"no deterministic fallback by design, so no diagnosis was produced."
            ),
        )

    prompt = build_single_agent_prompt(anomaly_result, retrieval)
    try:
        raw_response = client.generate(prompt, expect_json=True)
    except OllamaUnavailableError as exc:
        return SingleAgentDiagnosis(status="llm_unavailable", reason=str(exc))

    try:
        parsed = json.loads(raw_response)
    except json.JSONDecodeError as exc:
        return SingleAgentDiagnosis(
            status="llm_error", reason=f"Response was not valid JSON: {exc}", raw_response=raw_response,
        )
    if not isinstance(parsed, dict):
        return SingleAgentDiagnosis(
            status="llm_error", reason="Response JSON was not an object.", raw_response=raw_response,
        )

    missing = [k for k in _REQUIRED_KEYS if k not in parsed]
    if missing:
        return SingleAgentDiagnosis(
            status="llm_error", reason=f"Response missing required keys: {missing}",
            raw_response=raw_response,
        )

    issues: list[str] = []
    root_cause_service = _validated_service(parsed.get("root_cause_service"), "root_cause_service", issues)
    symptom_service = _validated_service(
        parsed.get("symptom_service") or anomaly_result.service, "symptom_service", issues,
    ) or anomaly_result.service

    fault_type = str(parsed.get("fault_type") or "").strip()
    if fault_type not in KNOWN_FAULT_TYPES:
        if fault_type and fault_type != "unknown":
            issues.append(f"fault_type {fault_type!r} is not one of the four known fault types")
        fault_type = None

    action_category = str(parsed.get("action_category") or "").strip()
    if action_category not in KNOWN_ACTION_CATEGORIES:
        issues.append(
            f"action_category {action_category!r} is not in the allowed action set; coerced to "
            f"'investigate_manually' so it cannot be executed"
        )
        action_category = "investigate_manually"

    confidence = str(parsed.get("confidence") or "low").strip().lower()
    if confidence not in ("low", "medium", "high"):
        issues.append(f"confidence {confidence!r} is not low/medium/high; treated as 'low'")
        confidence = "low"

    if root_cause_service is None:
        # Nothing usable to act on. Falling back to the symptom service would silently invent the
        # single most important field being measured, so the diagnosis is downgraded instead.
        issues.append("no usable root_cause_service; downgraded to a non-actionable diagnosis")
        action_category = "investigate_manually"

    return SingleAgentDiagnosis(
        status="diagnosed",
        suspected_fault_type=fault_type,
        suspected_root_cause_service=root_cause_service,
        symptom_service=symptom_service,
        reasoning=str(parsed.get("reasoning") or ""),
        confidence=confidence,
        proposed_action_category=action_category,
        proposed_action=str(parsed.get("recommended_action") or "").strip() or None,
        target_service=root_cause_service or symptom_service,
        raw_response=raw_response,
        parse_issues=issues,
    )


def _validated_service(value, field_name: str, issues: list[str]) -> Optional[str]:
    name = str(value or "").strip()
    if name in KNOWN_SERVICES:
        return name
    if name:
        issues.append(f"{field_name} {name!r} is not one of this project's four services")
    return None


def to_remediation_proposal(diagnosis: SingleAgentDiagnosis) -> RemediationProposal:
    """Converts the single agent's answer into the exact same `RemediationProposal` type the
    multi-agent pipeline produces, so both arms flow through byte-identical downstream safety,
    approval, and execution code.

    Note what this conversion does NOT do: it does not repair a bad diagnosis, and it does not
    relax any invariant. `requires_human_approval` is still `Literal[True]`, the action category is
    still constrained to the known set, and a `restart_service` proposal is still marked high risk -
    so the Safety Validator judges the baseline's proposals by exactly the same rules, including
    refusing a restart of ledger-service."""
    if diagnosis.status != "diagnosed" or not diagnosis.target_service:
        return RemediationProposal(
            recommended_action=(
                "No automated remediation: the single-agent baseline did not produce a usable "
                "diagnosis. Escalate to a human operator."
            ),
            target_service=diagnosis.target_service or "payment-service",
            action_category="investigate_manually",
            rationale=diagnosis.reason or "Single-agent diagnosis unavailable or unusable.",
            supporting_evidence=diagnosis.parse_issues,
            expected_outcome="A human operator investigates directly.",
            risk_level="low",
            confidence="low",
        )

    category = diagnosis.proposed_action_category or "investigate_manually"
    return RemediationProposal(
        recommended_action=diagnosis.proposed_action or f"Perform '{category}' on {diagnosis.target_service}.",
        target_service=diagnosis.target_service,
        action_category=category,  # type: ignore[arg-type]  # validated against KNOWN_ACTION_CATEGORIES above
        rationale=(
            f"Single-agent baseline concluded fault_type="
            f"{diagnosis.suspected_fault_type or 'unknown'} with confidence "
            f"{diagnosis.confidence} in one LLM call. Reasoning: {diagnosis.reasoning}"
        ),
        supporting_evidence=diagnosis.parse_issues or [],
        expected_outcome="The suspected fault on the identified root cause service is resolved.",
        # Same rule the multi-agent proposer uses: a restart is always high risk, so the Safety
        # Validator treats both arms' restart proposals identically.
        risk_level="high" if category == "restart_service" else (
            "low" if category in ("investigate_manually", "no_action_needed") else "medium"
        ),
        confidence=diagnosis.confidence or "low",  # type: ignore[arg-type]
    )
