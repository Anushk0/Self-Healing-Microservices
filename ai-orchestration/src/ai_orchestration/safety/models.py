"""Pydantic model for a safety-validation outcome. `validator.py` builds one of these from plain
Python rules, zero AI involved (CLAUDE.md rule 20: "Safety validation must remain deterministic") -
this model is the structured record of that check, not a place a heuristic or an LLM ever writes
to."""
from __future__ import annotations

from pydantic import BaseModel, Field


class SafetyCheckResult(BaseModel):
    passed: bool
    reason: str
    violated_rules: list[str] = Field(default_factory=list)

    def to_dict(self) -> dict:
        return self.model_dump()
