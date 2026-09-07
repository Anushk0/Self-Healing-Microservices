"""Deterministic remediation proposal generation - no LLM involved at all. Remediation is exactly
the kind of decision this project's rules say should be "deterministic structured data" rather
than free-form AI output (and the LLM "must not bypass deterministic safety logic") - so unlike
RCA's optional LLM narrative, remediation proposals here are 100% Python logic, always.

`propose_remediation()` never executes anything - it returns a RemediationProposal describing what
a human operator (or, in a later week, an approved automated action) *could* do; nothing in this
module calls Kubernetes, the fault-injection endpoints, or any other side-effecting API.
"""
from __future__ import annotations

from typing import Optional

from ..rca.models import RootCauseAnalysis
from .models import RemediationProposal

# Keyed by fault_type. Every action described here is a *reset of this project's own controlled
# fault injection* (see CLAUDE.md "Fault injection" section) or a well-scoped, conventional
# operational response - never a made-up action name.
_FAULT_REMEDIATION_TEMPLATES: dict[str, dict] = {
    "auth-key-error": {
        "recommended_action": (
            "Restore auth-service's JWT signing key to its original, valid value (reset the "
            "auth-key-error fault injection)."
        ),
        "target_service": "auth-service",
        "action_category": "restore_configuration",
        "expected_outcome": (
            "Token validation succeeds again for both existing and newly issued tokens; "
            "payment-service's elevated http_server_requests_error_count returns to baseline."
        ),
        "risk_level": "medium",
    },
    "memory-leak": {
        "recommended_action": (
            "Reset the payment-service memory-leak fault injection to stop further retained "
            "allocation. If JVM heap usage remains critically high after reset, restart the "
            "payment-service instance to reclaim memory immediately."
        ),
        "target_service": "payment-service",
        "action_category": "reset_fault_configuration",
        "expected_outcome": (
            "New allocation stops immediately; heap usage plateaus and recovers via normal "
            "garbage collection, or immediately if a restart is performed."
        ),
        "risk_level": "medium",
    },
    "db-lock": {
        "recommended_action": (
            "Reset the ledger-service DB-pool-exhaustion fault injection to release the held "
            "HikariCP connections and restore full pool capacity."
        ),
        "target_service": "ledger-service",
        "action_category": "release_resources",
        "expected_outcome": (
            "hikaricp_connections_active returns to baseline; new ledger transactions stop "
            "timing out waiting for a connection."
        ),
        "risk_level": "medium",
    },
    "notification-latency": {
        "recommended_action": (
            "Reset the notification-service latency fault injection to restore immediate "
            "response timing. If elevated latency persists after reset, investigate "
            "notification-service directly, not payment-service - payment-service is only the "
            "symptom-visible service, not the root cause."
        ),
        "target_service": "notification-service",
        "action_category": "reset_fault_configuration",
        "expected_outcome": (
            "payment-service's outbound calls to notification-service return to their normal "
            "~10-60ms duration and no longer approach the client-side timeout."
        ),
        "risk_level": "medium",
    },
}


def _restart_proposal(rca: RootCauseAnalysis, failed_actions: list[dict]) -> RemediationProposal:
    """Escalation rung 2, reached only after the targeted fault-configuration reset (rung 1) has
    already been executed and failed for *this* incident. This is exactly the escalation the Week 8
    memory-leak template's own text already described ("If JVM heap usage remains critically high
    after reset, restart the payment-service instance") - Week 10 makes it an actual, separately
    approvable proposal rather than prose inside another action's description.

    `risk_level` is always "high": restarting an instance drops its in-flight requests. That is
    deliberate and load-bearing, not pessimism - it is what makes the Safety Validator refuse this
    action for ledger-service (financial, mid-transaction state) while still allowing a human to
    consider it for the other three services."""
    previous = "; ".join(
        f"{fa.get('action_category')} on {fa.get('target_service')} ({fa.get('reason') or 'failed'})"
        for fa in failed_actions
    ) or "a previous remediation attempt failed"
    return RemediationProposal(
        recommended_action=(
            f"Restart the {rca.suspected_root_cause_service} instance (delete its pod and let the "
            f"Kubernetes Deployment recreate it), since the targeted fault-configuration reset did "
            f"not resolve the fault."
        ),
        target_service=rca.suspected_root_cause_service,
        action_category="restart_service",
        rationale=(
            f"A less invasive remediation was already attempted and did not resolve this incident: "
            f"{previous}. Restarting the instance clears in-process state (retained heap, held "
            f"connections, mutated in-memory configuration) that a targeted reset did not."
        ),
        supporting_evidence=[rca.evidence_summary] + [
            f"failed attempt: {fa.get('action_category')} -> {fa.get('reason') or 'no reason recorded'}"
            for fa in failed_actions
        ],
        expected_outcome=(
            f"{rca.suspected_root_cause_service} restarts with clean in-process state; the fault's "
            f"primary metric returns to its documented baseline once the new instance is ready."
        ),
        risk_level="high",
        confidence=rca.confidence,
    )


def _escalate_to_human_proposal(rca: RootCauseAnalysis, failed_actions: list[dict]) -> RemediationProposal:
    """Escalation rung 3, the terminal rung: every automated remediation this project knows how to
    perform for this fault has now been tried and failed. Deliberately a `no-op`
    ("investigate_manually") rather than an ever-more-invasive third action - inventing a more
    destructive fallback would be exactly the "hardcode the final answer" failure mode the Week 8
    design rejected."""
    return RemediationProposal(
        recommended_action=(
            f"Escalate to a human operator: every automated remediation available for a "
            f"'{rca.suspected_fault_type}' fault on {rca.suspected_root_cause_service} has already "
            f"been attempted and did not resolve the incident. No further automated action will be "
            f"proposed."
        ),
        target_service=rca.suspected_root_cause_service,
        action_category="investigate_manually",
        rationale=(
            f"{len(failed_actions)} automated remediation attempt(s) failed: "
            + "; ".join(str(fa.get("action_category")) for fa in failed_actions)
            + ". Continuing to retry automatically would not add information."
        ),
        supporting_evidence=[rca.evidence_summary] + [
            f"failed attempt: {fa.get('action_category')} -> {fa.get('reason') or 'no reason recorded'}"
            for fa in failed_actions
        ],
        expected_outcome="A human operator investigates directly; no further automated action is taken.",
        risk_level="low",
        confidence=rca.confidence,
    )


def propose_remediation(
    rca: RootCauseAnalysis, failed_actions: Optional[list[dict]] = None
) -> RemediationProposal:
    """Deterministic fault_type -> action lookup, but the action is never chosen "solely based on
    fault names": a template is only used when the RCA's own confidence is at least "medium" (i.e.
    the fault_type determination was itself evidence-based, not a low-confidence guess); a "low"
    confidence RCA always falls back to a generic, low-risk "investigate_manually" proposal
    regardless of which fault_type happened to be suspected, since that suspicion wasn't strong
    enough to act on.

    Week 10's failed-fix retry loop (deferred here from the Week 8 roadmap, which specified it but
    could not build it before an executor existed) supplies `failed_actions`: every action already
    executed and failed for *this* incident. The escalation ladder is deterministic and strictly
    ordered - targeted fault-configuration reset, then instance restart, then human escalation -
    and never repeats an action category that has already failed. Retrying is routed back to this
    function rather than to RCA (per the roadmap: "so the LLM tries a different fix rather than
    re-diagnosing from scratch"), and each new proposal is re-validated by the Safety Validator and
    re-approved by a human before it can execute - a retry never inherits the previous approval."""
    failed_actions = failed_actions or []
    failed_categories = {fa.get("action_category") for fa in failed_actions}
    template = _FAULT_REMEDIATION_TEMPLATES.get(rca.suspected_fault_type or "")

    if template is not None and rca.confidence != "low":
        if template["action_category"] in failed_categories:
            # Rung 1 already failed for this incident - escalate rather than proposing it again.
            if "restart_service" not in failed_categories:
                return _restart_proposal(rca, failed_actions)
            return _escalate_to_human_proposal(rca, failed_actions)

        return RemediationProposal(
            recommended_action=template["recommended_action"],
            target_service=template["target_service"],
            action_category=template["action_category"],
            rationale=(
                f"RCA determined suspected_fault_type='{rca.suspected_fault_type}' with "
                f"confidence='{rca.confidence}' (method: {rca.determination_method}), pointing to "
                f"{rca.suspected_root_cause_service} as the root cause."
            ),
            supporting_evidence=[rca.evidence_summary] + [
                f"{m.metric_name}={m.observed_value} ({m.deviation_description})" for m in rca.metric_anomalies
            ],
            expected_outcome=template["expected_outcome"],
            risk_level=template["risk_level"],
            confidence=rca.confidence,
        )

    return RemediationProposal(
        recommended_action=(
            f"Insufficient evidence to confidently determine a specific automated remediation for "
            f"{rca.suspected_root_cause_service}. Escalate to a human operator to investigate "
            f"directly before taking any action."
        ),
        target_service=rca.suspected_root_cause_service,
        action_category="investigate_manually",
        rationale=(
            f"RCA confidence was '{rca.confidence}' (method: {rca.determination_method}) - too low "
            f"to safely propose a specific state-changing action."
        ),
        supporting_evidence=[rca.evidence_summary],
        expected_outcome="A human operator confirms the actual root cause before any remediation is attempted.",
        risk_level="low",
        confidence="low",
    )
