"""Typed output of the Week 11 single-agent baseline.

Deliberately mirrors the *information* the multi-agent pipeline produces across three separate
steps (RCA conclusion + reasoning + a remediation action) so the two systems can be compared field
by field in Week 12 - but it is produced by one LLM call, in one shot, with no deterministic
signature matching, no separate remediation logic, and nothing that can override the model.

`parse_issues` exists because the single agent's failure modes are a *result*, not an inconvenience:
a model that names a service that does not exist, or picks an action outside the allowed set, is
demonstrating exactly the weakness this baseline is meant to measure. Those events are recorded
rather than silently repaired, so Week 12's hallucination rate can count them.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

SingleAgentStatus = Literal[
    "diagnosed",         # the model returned a usable, parseable diagnosis
    "llm_unavailable",   # Ollama unreachable/timed out - the baseline produces nothing at all
    "llm_error",         # the model responded, but not with usable JSON
]


class SingleAgentDiagnosis(BaseModel):
    status: SingleAgentStatus
    suspected_fault_type: Optional[str] = None
    suspected_root_cause_service: Optional[str] = None
    symptom_service: Optional[str] = None
    reasoning: Optional[str] = None
    confidence: Optional[str] = None
    proposed_action_category: Optional[str] = None
    proposed_action: Optional[str] = None
    target_service: Optional[str] = None
    raw_response: Optional[str] = None
    reason: Optional[str] = None  # populated when status != "diagnosed"

    # Every place the model's answer had to be corrected or rejected before it could be used
    # downstream. An empty list means the model's output was usable exactly as given.
    parse_issues: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict:
        return self.model_dump()
