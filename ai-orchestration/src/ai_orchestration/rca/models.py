"""Pydantic models for RCA - structured, machine-readable, and never solely reliant on free-form
LLM text. `RootCauseAnalysis.suspected_root_cause_service` and `.confidence` are always set by
deterministic logic (see signatures.py) before any LLM call happens; `llm_reasoning` is an
optional narrative addendum, never the source of the conclusion itself.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Confidence = Literal["low", "medium", "high"]

DeterminationMethod = Literal[
    "metric_signature",                    # a known fault signature matched cleanly
    "metric_signature+rag_agreement",       # signature match corroborated by retrieved incidents
    "metric_signature+rag_disagreement",    # signature match, but retrieved incidents disagree
    "rag_only",                             # no signature match; weak suggestion from retrieval alone
    "fallback_unknown",                     # no signature match and no useful retrieval evidence
    # Week 16 ABLATION ONLY - never produced by the shipped configuration. Retrieval was allowed to
    # override a signature match (config.RAG_MAY_OVERRIDE_SIGNATURE), reconstructing the
    # conventional RAG-first design so its cost can be measured. Seeing this value in a result means
    # that result came from an ablation run, not from the system as designed.
    "rag_override",
]


class MetricAnomaly(BaseModel):
    """One observed metric and how it compares to a documented baseline reference - never a
    fabricated number. `baseline_reference` is None when no documented reference exists for this
    service/metric pair (see rca/signatures.py's BASELINE_REFERENCES for what is documented)."""
    metric_name: str
    observed_value: float
    baseline_reference: Optional[float] = None
    deviation_description: str


class RCAEvidence(BaseModel):
    """The deterministic evidence bundle assembled before any LLM involvement - what the LLM is
    later asked to narrate, not invent."""
    anomaly_service: str
    anomaly_score: float
    anomalous_metrics: list[MetricAnomaly] = Field(default_factory=list)
    fault_status: Optional[dict] = None  # this record's own self-reported /fault-status, if present
    llm_interpretation_summary: Optional[str] = None  # Week 7's interpret_anomaly() abnormal_summary, if available


class RootCauseAnalysis(BaseModel):
    suspected_fault_type: Optional[str] = None  # one of dataset_tools.config.FAULT_TYPES, or None
    suspected_root_cause_service: str
    symptom_service: str
    affected_services: list[str]
    relevant_metrics: list[str]
    metric_anomalies: list[MetricAnomaly] = Field(default_factory=list)
    evidence_summary: str
    llm_reasoning: Optional[str] = None
    confidence: Confidence
    determination_method: DeterminationMethod

    def to_dict(self) -> dict:
        return self.model_dump()
