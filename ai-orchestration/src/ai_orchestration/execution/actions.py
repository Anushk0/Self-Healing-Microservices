"""Deterministic resolution of an approved `RemediationProposal` into a concrete `ExecutableAction`.

This module is the *only* place that decides what a proposal physically means, and it is plain
Python with no LLM involvement - the same reasoning as `remediation/proposer.py` and
`safety/validator.py`. It cannot express an action outside `ExecutableAction.kind`'s three
literals, so there is no path from any proposal (however it was authored - including Week 11's
single-agent baseline) to an arbitrary command.

Fault-reset paths are read from Week 5's `telemetry.experiment.FAULTS`, the same mapping the
experiment orchestrator already uses to inject and reset faults - reused, never re-declared, so
these can never drift apart from the endpoints the Java services actually expose.
"""
from __future__ import annotations

from .. import config as ai_config
from ..remediation.models import RemediationProposal
from .models import ExecutableAction

ai_config.ensure_sibling_packages_importable()

from telemetry.experiment import FAULTS  # noqa: E402

# Categories whose meaning is "undo this project's controlled fault injection on the target
# service", which is a targeted HTTP POST to that service's own reset endpoint.
_FAULT_RESET_CATEGORIES = frozenset({
    "restore_configuration",       # auth-key-error  -> /reset-auth-key
    "reset_fault_configuration",   # memory-leak, notification-latency
    "release_resources",           # db-lock         -> /reset-db-lock
})

_NO_OP_CATEGORIES = frozenset({"investigate_manually", "no_action_needed"})


class ActionResolutionError(RuntimeError):
    """Raised when an approved proposal cannot be turned into a concrete action. Never swallowed
    into a successful-looking no-op: the executor reports it as `status="failed"`, because a
    proposal the system cannot carry out is a real failure of that remediation attempt, not a
    quietly-skipped step."""


def resolve_action(proposal: RemediationProposal, suspected_fault_type: str | None) -> ExecutableAction:
    category = proposal.action_category

    if category in _NO_OP_CATEGORIES:
        return ExecutableAction(
            kind="no_op",
            target_service=proposal.target_service,
            description=(
                f"No action executed: '{category}' is an escalation to a human operator, not "
                f"something this pipeline performs."
            ),
        )

    if category == "restart_service":
        return ExecutableAction(
            kind="restart_pod",
            target_service=proposal.target_service,
            description=(
                f"Delete {proposal.target_service}'s pod(s) so its Kubernetes Deployment recreates "
                f"them with clean in-process state."
            ),
            fault_type=suspected_fault_type,
        )

    if category in _FAULT_RESET_CATEGORIES:
        if suspected_fault_type not in FAULTS:
            raise ActionResolutionError(
                f"Cannot resolve a fault-reset action: suspected_fault_type="
                f"{suspected_fault_type!r} is not one of the four controlled faults {sorted(FAULTS)}."
            )
        fault = FAULTS[suspected_fault_type]
        # Cross-check, not assumption: the service the proposal targets must be the service that
        # actually owns this fault's reset endpoint. A mismatch means the proposal and the RCA
        # disagree about where the root cause is, which must never be papered over by POSTing to
        # whichever service happens to be named.
        if fault["service"] != proposal.target_service:
            raise ActionResolutionError(
                f"Proposal targets {proposal.target_service!r} but the reset endpoint for fault "
                f"{suspected_fault_type!r} belongs to {fault['service']!r} - refusing to guess."
            )
        return ExecutableAction(
            kind="http_fault_reset",
            target_service=proposal.target_service,
            description=f"POST {fault['reset_path']} on {proposal.target_service} to clear the "
                        f"'{suspected_fault_type}' fault.",
            http_path=fault["reset_path"],
            fault_type=suspected_fault_type,
        )

    raise ActionResolutionError(
        f"Unrecognized action_category {category!r} - the executor has no mechanism for it."
    )
