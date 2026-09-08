"""The simulated human operator used during an automated evaluation run.

Human approval is mandatory and non-bypassable (CLAUDE.md rule 21), and that does not change here -
the graph still pauses at `human_approval` and still cannot proceed without an explicit resume.
What changes is who supplies the decision: during a 48-incident, two-arm evaluation, a person
clicking Approve 96 times would make the run neither reproducible nor finishable, so a documented,
deterministic policy stands in. The Week 12 roadmap anticipates exactly this ("even if you're the
one clicking approve/reject during this run, log every decision").

**The default policy approves everything that reaches it, and that choice is deliberate.**

The tempting alternative - approve only when the proposal targets the true root cause - would use
ground truth to gate execution, so remediation would only ever run on correct diagnoses. Recovery
Success Rate would then measure "how well does remediation work when the diagnosis is already
known to be right", which is not a question anyone asked, and the number would look far better than
the system deserves. A permissive operator instead measures what actually happens when you act on
this system's diagnosis, wrong ones included.

The cost of that choice is stated rather than hidden: under a permissive policy, Approval Accuracy
(was approving this correct in hindsight?) is definitionally equal to diagnostic accuracy, so it is
not an independent measurement. It is still worth logging, because it is what makes Unsafe Action
Prevention meaningful - every unsafe proposal that never reached the operator was stopped by the
deterministic Safety Validator, not by human judgement.
"""
from __future__ import annotations

from typing import Literal

OperatorPolicy = Literal["permissive", "reject_all"]


class SimulatedOperator:
    """Records every decision it makes, so the evaluation can report on the operator itself rather
    than treating it as invisible infrastructure."""

    def __init__(self, policy: OperatorPolicy = "permissive"):
        if policy not in ("permissive", "reject_all"):
            raise ValueError(f"Unknown operator policy {policy!r}")
        self.policy = policy
        self.decisions: list[dict] = []

    def decide(self, interrupt_payload: dict, incident_id: str) -> dict:
        """Returns the `Command(resume=...)` value. `interrupt_payload` is exactly what the graph
        surfaced - the proposal, the RCA, and the safety verdict - i.e. the operator sees precisely
        what a human would see in the Streamlit console, and nothing more. Ground truth is not in
        scope here and is deliberately not a parameter."""
        proposal = interrupt_payload.get("remediation_proposal") or {}

        if self.policy == "reject_all":
            decision, reason = "rejected", "Simulated operator policy 'reject_all'."
        else:
            decision, reason = "approved", (
                "Simulated operator policy 'permissive': approves every proposal that passed the "
                "Safety Validator, so that remediation outcomes are measured on this system's own "
                "diagnoses rather than on ground-truth-gated ones."
            )

        self.decisions.append({
            "incident_id": incident_id,
            "decision": decision,
            "action_category": proposal.get("action_category"),
            "target_service": proposal.get("target_service"),
            "risk_level": proposal.get("risk_level"),
        })
        return {"decision": decision, "reason": reason}
