"""Typed shapes for Week 10's execution step.

`ExecutionResult` is the first object in this project that records something the system actually
*did* to the running system, rather than something it concluded or proposed. It is deliberately
explicit about which of the three possible outcomes occurred - executed, refused, or failed -
because Week 12's Recovery Success Rate and False Recovery Rate both depend on being able to tell
"the action ran and the fault is gone" apart from "the action ran and the fault is still here" and
from "the action never ran at all".
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# What the executor is physically capable of doing. Anything not in this list cannot be executed
# at all - there is no generic "run arbitrary command" path anywhere in this package.
ActionKind = Literal["http_fault_reset", "restart_pod", "no_op"]

ExecutionStatus = Literal[
    "executed",        # the action was carried out and the underlying call succeeded
    "dry_run",         # resolved and logged, deliberately not carried out (config.EXECUTION_DRY_RUN)
    "skipped_no_op",   # the approved action was investigate_manually / no_action_needed
    "refused",         # a gate said no (not approved, safety not passed, attempt cap reached)
    "failed",          # the action was attempted and the underlying call raised/errored
]


class ExecutableAction(BaseModel):
    """The concrete, resolved thing to do - derived deterministically from a RemediationProposal
    plus the RCA's suspected fault type (see actions.py). Kept separate from RemediationProposal so
    that a proposal stays a *recommendation* and this stays the *mechanism*."""
    kind: ActionKind
    target_service: str
    description: str
    http_path: Optional[str] = None   # only for kind="http_fault_reset"
    fault_type: Optional[str] = None  # the controlled fault this reset corresponds to, if any


class ExecutionResult(BaseModel):
    status: ExecutionStatus
    action: Optional[ExecutableAction] = None
    reason: str
    detail: dict = Field(default_factory=dict)  # raw response / API detail, for the audit trail
    started_at: Optional[str] = None            # ISO 8601 UTC
    completed_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    dry_run: bool = False

    @property
    def succeeded(self) -> bool:
        """True only when something was really carried out successfully. `dry_run` and
        `skipped_no_op` are deliberately NOT successes for recovery-measurement purposes - nothing
        changed in the system, so nothing can be said to have recovered."""
        return self.status == "executed"

    def to_dict(self) -> dict:
        return self.model_dump()


class RecoveryVerification(BaseModel):
    """The answer to "did that actually fix it?", measured against the target service's own
    /fault-status and /actuator/health - never inferred from the fact that the remediation API call
    returned 200. `verified=False` with `status="fault_still_active"` is precisely Week 12's False
    Recovery case."""
    status: Literal[
        "recovered",            # fault flag cleared and health is UP
        "fault_still_active",   # the service still reports the fault active
        "unhealthy",            # fault flag cleared but the service is not UP
        "unreachable",          # could not be checked (service down / network error)
        "not_applicable",       # nothing was executed, so there is nothing to verify
    ]
    verified: bool
    reason: str
    attempts: int = 0
    checked_service: Optional[str] = None
    detail: dict = Field(default_factory=dict)

    def to_dict(self) -> dict:
        return self.model_dump()
