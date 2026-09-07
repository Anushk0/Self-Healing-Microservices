"""The Safety Validator: plain Python, zero AI, zero LLM calls - a fixed list of hard rules a
RemediationProposal must pass before a human is ever asked to approve it (CLAUDE.md rule 20,
"Safety validation must remain deterministic"). A rule violation is rejected outright and never
reaches human approval at all (Week 9 roadmap: "a rule violation shouldn't even reach the human") -
see graph/workflow.py's routing after `safety_validator`.

Every rule here is independently re-checked in plain Python rather than trusted from Pydantic's own
validation on RemediationProposal/RootCauseAnalysis - defense in depth, since a future bug in those
models (or a hand-constructed dict bypassing them) should not be able to silently skip safety
review.
"""
from __future__ import annotations

from ..remediation.models import RemediationProposal
from .models import SafetyCheckResult

# The only services this project's remediation proposals may ever target (CLAUDE.md's four
# microservices) - anything else is treated as unrecognized and rejected outright.
KNOWN_SERVICES = frozenset({"auth-service", "payment-service", "ledger-service", "notification-service"})

# Mirrors remediation/models.py's ActionCategory Literal - re-declared here so this module's
# whitelist check doesn't silently pass a proposal that only satisfied Pydantic's own type
# validation (see module docstring).
KNOWN_SAFE_ACTION_CATEGORIES = frozenset({
    "restore_configuration", "reset_fault_configuration", "release_resources",
    "restart_service",  # Week 10 - the escalation rung; always risk_level="high" (see proposer.py)
    "investigate_manually", "no_action_needed",
})

# ledger-service holds financial transaction state behind a pessimistic-locking, multi-step
# transactional boundary (see ledger-service's LedgerService.debit()/credit()) - exactly what the
# Week 2 roadmap flagged as "what makes 'just restart the pod' dangerous later (mid-transaction
# state)". A high-risk action there is never allowed to proceed automatically to human approval;
# it is rejected outright and must be handled outside this pipeline.
#
# Week 10 note: this rule stopped being hypothetical. Week 9 documented it as a safety net that
# the then-current proposer could never actually trigger (every Week 8 template was
# `risk_level="medium"`). Week 10's escalation ladder introduced `restart_service`, which is always
# `risk_level="high"` - so a db-lock incident whose connection-release reset fails now really does
# escalate to "restart ledger-service" and really is refused here, before any human is asked. That
# is the rule working as intended on the exact scenario it was written for, not a contrived test.
LEDGER_SERVICE = "ledger-service"


def validate_safety(proposal: RemediationProposal) -> SafetyCheckResult:
    """Runs every hard rule against `proposal` and returns a single pass/fail verdict. All
    violated rules are collected (not just the first one found) so a rejection reason is always
    complete, not partial."""
    violated: list[str] = []

    if proposal.action_category not in KNOWN_SAFE_ACTION_CATEGORIES:
        violated.append(
            f"action_category '{proposal.action_category}' is not in the known-safe whitelist "
            f"{sorted(KNOWN_SAFE_ACTION_CATEGORIES)}."
        )

    if proposal.target_service not in KNOWN_SERVICES:
        violated.append(
            f"target_service '{proposal.target_service}' is not one of this project's known "
            f"services {sorted(KNOWN_SERVICES)}."
        )

    if proposal.target_service == LEDGER_SERVICE and proposal.risk_level == "high":
        violated.append(
            f"High-risk actions are never allowed against '{LEDGER_SERVICE}' - it holds financial "
            f"transaction state behind a transactional, pessimistic-locking boundary. Escalate to "
            f"manual investigation instead."
        )

    if proposal.requires_human_approval is not True:
        violated.append(
            "requires_human_approval must always be True (CLAUDE.md rule 21: human approval must "
            "remain mandatory) - a proposal that does not require it is itself a safety violation."
        )

    if violated:
        return SafetyCheckResult(
            passed=False,
            reason=f"{len(violated)} safety rule(s) violated - this proposal is rejected before "
                   f"reaching human approval.",
            violated_rules=violated,
        )

    return SafetyCheckResult(
        passed=True,
        reason="All safety rules passed - this proposal may proceed to mandatory human approval.",
        violated_rules=[],
    )
