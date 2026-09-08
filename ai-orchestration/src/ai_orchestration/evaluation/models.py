"""Typed records for one Week 12 evaluation run.

`ArmResult` deliberately keeps the *whole* final pipeline result alongside the extracted fields.
The Week 12 roadmap is explicit about wanting "every intermediate output ... you'll want this raw
log for debugging weird numbers, not just final metrics" - a metric that looks wrong is impossible
to investigate from an aggregate alone.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Arm = Literal["multi_agent", "single_agent"]


class ArmResult(BaseModel):
    """What one arm did with one evaluation incident."""
    arm: Arm
    incident_id: str

    # --- ground truth, from the Evaluation-set incident record ---
    true_fault_type: str
    true_root_cause_service: str
    true_symptom_service: str

    # --- what the arm concluded (None when it reached no diagnosis at all) ---
    predicted_fault_type: Optional[str] = None
    predicted_root_cause_service: Optional[str] = None
    confidence: Optional[str] = None
    determination_method: Optional[str] = None
    reasoning: Optional[str] = None

    # --- pipeline behaviour ---
    decision: str = "unknown"                     # anomaly | normal | insufficient_data
    detected_anomaly: bool = False
    safety_passed: Optional[bool] = None
    safety_violations: list[str] = Field(default_factory=list)
    human_decision: Optional[str] = None
    proposed_action_category: Optional[str] = None
    proposed_target_service: Optional[str] = None
    execution_status: Optional[str] = None
    recovery_status: Optional[str] = None
    recovery_verified: Optional[bool] = None
    mttr_seconds: Optional[float] = None
    remediation_attempts: int = 0
    feedback_status: Optional[str] = None

    # --- timing, measured by the harness (see metrics.py's note on the token-cost substitution) ---
    # Detection latency is the detection path ONLY (feature extraction + scoring), measured
    # separately from the graph run. Time-to-first-pause is detection + retrieval + diagnosis.
    # They are separate fields because conflating them overstates detection by orders of magnitude
    # and makes the arms differ on a stage they share identically.
    detection_latency_seconds: Optional[float] = None
    time_to_first_pause_seconds: Optional[float] = None
    total_wall_clock_seconds: float = 0.0
    llm_calls: int = 0

    # --- honesty fields ---
    parse_issues: list[str] = Field(default_factory=list)   # single-agent only
    unsupported_metric_mentions: list[str] = Field(default_factory=list)
    unsupported_significance_claims: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    error: Optional[str] = None                    # the run raised instead of completing

    raw_result: dict[str, Any] = Field(default_factory=dict)

    @property
    def root_cause_correct(self) -> bool:
        return self.predicted_root_cause_service == self.true_root_cause_service

    @property
    def fault_type_correct(self) -> bool:
        return self.predicted_fault_type == self.true_fault_type

    def to_dict(self) -> dict:
        return self.model_dump()


class EvaluationRun(BaseModel):
    """One complete evaluation: every incident through every arm, plus the provenance needed to
    interpret the numbers. `environment` is not decoration - a reader has to be able to tell
    whether Recovery Success Rate was measured against live services or was unmeasurable."""
    started_at: str
    finished_at: Optional[str] = None
    arms: list[Arm] = Field(default_factory=list)
    mode: Literal["replay", "live"] = "replay"
    environment: dict[str, Any] = Field(default_factory=dict)
    dataset: dict[str, Any] = Field(default_factory=dict)
    operator_policy: str = "permissive"
    preflight: dict[str, Any] = Field(default_factory=dict)
    results: list[ArmResult] = Field(default_factory=list)

    def for_arm(self, arm: Arm) -> list[ArmResult]:
        return [r for r in self.results if r.arm == arm]

    def to_dict(self) -> dict:
        return self.model_dump()
