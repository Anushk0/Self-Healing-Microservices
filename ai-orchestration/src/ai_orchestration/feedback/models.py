"""Typed result of Week 10's feedback write-back step.

`status` distinguishes the three legitimate non-writes from an actual failure, because "we chose
not to learn from this incident" and "we tried to learn and it broke" are very different things to
see in a result - and one of them (`refused_evaluation_incident`) is a safety property working
correctly, not a problem.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

FeedbackStatus = Literal[
    "written",                        # added to the retrieval memory
    "disabled",                       # config.FEEDBACK_ENABLED is false (e.g. Week 12 eval runs)
    "refused_evaluation_incident",    # CLAUDE.md rule 22 - never learn from an Evaluation incident
    "skipped_incomplete",             # nothing worth learning (no anomaly / no RCA reached)
    "failed",                         # the write itself errored
]


class FeedbackResult(BaseModel):
    status: FeedbackStatus
    reason: str
    written_incident_id: Optional[str] = None
    source_incident_id: Optional[str] = None
    postmortem_text: Optional[str] = None

    def to_dict(self) -> dict:
        return self.model_dump()
