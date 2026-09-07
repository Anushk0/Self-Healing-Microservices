"""Week 10 feedback write-back - the "learning" step.

Turns a finished pipeline run (telemetry -> RCA -> proposal -> approval -> execution -> recovery
verification) into the same kind of postmortem prose Week 6 indexed, and writes it into the same
ChromaDB collection, so later incidents can retrieve from things this system actually did, not just
from the original 20 seed incidents.

Two properties this module is careful about:

1. **CLAUDE.md rule 22 is enforced twice, independently.** Here, by refusing outright to build a
   write for any incident whose `incident_id` is in the Evaluation set; and again inside
   `dataset_tools.chroma_store.add_pipeline_run_incident()`, which re-checks by source ID and
   fails closed if it cannot check. Either layer alone would be sufficient; both exist because a
   silent leak here would invalidate every number in Week 12 and would be invisible in the output.
2. **What gets written reflects what actually happened, including failure.** A run whose
   remediation failed, or was rejected by a human, or whose recovery could not be verified, is
   still worth remembering - and is recorded as such, never rewritten into a success story. A
   future retrieval that surfaces "this exact remediation was tried here and did not work" is more
   useful than one that only ever sees wins.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from .. import config as ai_config
from .models import FeedbackResult

ai_config.ensure_sibling_packages_importable()

from dataset_tools import chroma_store, config as dt_config, ingest as dt_ingest  # noqa: E402

log = logging.getLogger("ai_orchestration.feedback.writer")


def _evaluation_ids() -> set[str]:
    """Read-only, IDs only - the same narrow read `chroma_store` performs for its own leakage
    assertion. Nothing from the Evaluation file other than its IDs is ever loaded here, and none of
    it is ever embedded."""
    return {inc["incident_id"] for inc in dt_ingest.read_jsonl(dt_config.EVALUATION_INCIDENTS_FILE)}


def build_postmortem_text(result: dict) -> str:
    """Mirrors the shape of Week 6's `dataset_tools.incidents.generate_descriptions()` postmortem
    prose (Incident / Root cause / Symptom / Affected services / outcome), so a written-back
    incident embeds into the same semantic neighbourhood as the seed incidents rather than looking
    like a different kind of document. Every value is read from the run's own structured result -
    nothing here invents a metric or an outcome."""
    rca = result.get("root_cause_analysis") or {}
    proposal = result.get("remediation_proposal") or {}
    execution = result.get("execution_result") or {}
    verification = result.get("recovery_verification") or {}
    anomaly = result.get("anomaly_result") or {}

    fault_type = rca.get("suspected_fault_type") or "unknown"
    root = rca.get("suspected_root_cause_service") or result.get("service") or "unknown"
    symptom = rca.get("symptom_service") or result.get("service") or "unknown"
    metric_lines = "; ".join(
        f"{m.get('metric_name')}={m.get('observed_value')} ({m.get('deviation_description')})"
        for m in (rca.get("metric_anomalies") or [])
    ) or "no individual metric exceeded a documented threshold"

    human_decision = result.get("human_decision")
    if human_decision == "approved":
        decision_line = f"A human operator approved the proposed remediation."
    elif human_decision == "rejected":
        decision_line = f"A human operator rejected the proposed remediation; nothing was executed."
    else:
        decision_line = (
            "The proposal never reached a human operator (it was rejected by the deterministic "
            "Safety Validator first)."
            if (result.get("safety_check") or {}).get("passed") is False
            else "No human decision was recorded for this run."
        )

    outcome_line = (
        f"Execution: {execution.get('status', 'not reached')} - {execution.get('reason', 'n/a')} "
        f"Recovery verification: {verification.get('status', 'not reached')} - "
        f"{verification.get('reason', 'n/a')}"
    )

    closing = (
        f"This pipeline-run incident is recorded so future root-cause-analysis retrieval can see "
        f"how a '{fault_type}' scenario"
        + (
            f" where the symptom service ({symptom}) differs from the root cause service ({root})"
            if root != symptom else f" localized to {root}"
        )
        + " was diagnosed and what the attempted remediation actually achieved."
    )

    return (
        f"Incident {result.get('_feedback_incident_id', 'run')} (pipeline_run data, fault type "
        f"'{fault_type}').\n"
        f"Observed at: {result.get('timestamp')} on {result.get('service')} "
        f"(anomaly_score={anomaly.get('anomaly_score')}).\n"
        f"Root cause: {root} - determined by {rca.get('determination_method', 'n/a')} with "
        f"confidence {rca.get('confidence', 'n/a')}. {rca.get('evidence_summary', '')}\n"
        f"Symptom: observed on {symptom}. Metric evidence: {metric_lines}.\n"
        f"Proposed remediation: {proposal.get('recommended_action', 'none')} "
        f"(category {proposal.get('action_category', 'n/a')}, risk {proposal.get('risk_level', 'n/a')}).\n"
        f"{decision_line}\n"
        f"{outcome_line}\n"
        f"{closing}"
    )


def write_back(result: dict, source_incident_id: Optional[str] = None) -> FeedbackResult:
    """Writes one completed run into retrieval memory. Never raises - a feedback failure must not
    invalidate a remediation that already happened, so problems are reported as
    `FeedbackResult(status="failed")` and surfaced in the final result's limitations instead."""
    if not ai_config.FEEDBACK_ENABLED:
        return FeedbackResult(
            status="disabled",
            reason="Feedback write-back is disabled (AI_ORCHESTRATION_FEEDBACK_ENABLED=false) - "
                   "retrieval memory is intentionally frozen, e.g. during an evaluation run.",
            source_incident_id=source_incident_id,
        )

    # Rule 22, layer 1. Checked before any text is built, let alone embedded.
    if source_incident_id and source_incident_id in _evaluation_ids():
        log.warning("Refusing feedback write-back for Evaluation incident '%s'", source_incident_id)
        return FeedbackResult(
            status="refused_evaluation_incident",
            reason=(
                f"Incident '{source_incident_id}' belongs to the Evaluation set. Evaluation "
                f"incidents must never enter RAG memory (CLAUDE.md rule 22), so this run was not "
                f"written back."
            ),
            source_incident_id=source_incident_id,
        )

    rca = result.get("root_cause_analysis")
    if result.get("decision") != "anomaly" or not rca:
        return FeedbackResult(
            status="skipped_incomplete",
            reason=(
                f"Nothing to learn: decision={result.get('decision')!r} and root cause analysis "
                f"{'was not reached' if not rca else 'is present'} - only completed anomaly "
                f"investigations are written back."
            ),
            source_incident_id=source_incident_id,
        )

    incident_id = f"{dt_config.PIPELINE_RUN_ID_PREFIX}{uuid.uuid4().hex[:12]}"
    postmortem_text = build_postmortem_text({**result, "_feedback_incident_id": incident_id})
    metadata = _build_metadata(result, rca, incident_id, source_incident_id)

    try:
        chroma_store.add_pipeline_run_incident(incident_id, postmortem_text, metadata)
    except Exception as exc:  # noqa: BLE001 - see docstring: never break a completed remediation
        log.error("Feedback write-back failed: %s", exc)
        return FeedbackResult(
            status="failed",
            reason=f"Write-back failed: {type(exc).__name__}: {exc}",
            source_incident_id=source_incident_id,
            postmortem_text=postmortem_text,
        )

    return FeedbackResult(
        status="written",
        reason=f"Wrote pipeline-run incident '{incident_id}' into the retrieval memory collection.",
        written_incident_id=incident_id,
        source_incident_id=source_incident_id,
        postmortem_text=postmortem_text,
    )


def _build_metadata(result: dict, rca: dict, incident_id: str, source_incident_id: Optional[str]) -> dict:
    """Metadata keys deliberately match what `rag/retriever.py` reads off a retrieved document, so
    a learned incident is retrievable exactly like a Week 6 seed incident - plus provenance fields
    that make it obvious in any audit that this one came from a pipeline run.

    ChromaDB metadata values must be primitives, so every value here is a str/bool/number and
    `None` is replaced with an explicit string rather than omitted (an absent key would make
    retrieval's `metadata[...]` lookups inconsistent between seed and learned incidents)."""
    execution = result.get("execution_result") or {}
    verification = result.get("recovery_verification") or {}
    return {
        "incident_id": incident_id,
        "fault_type": rca.get("suspected_fault_type") or "unknown",
        "root_cause_service": rca.get("suspected_root_cause_service") or "unknown",
        "symptom_service": rca.get("symptom_service") or "unknown",
        "severity": "unknown",  # severity is a Week 6 dataset-level heuristic over a full
                                # experiment's phases; a single live record has no equivalent, and
                                # inventing one here would be a fabricated field.
        "data_source": "pipeline_run",
        "source_incident_id": source_incident_id or "",
        "written_at": datetime.now(timezone.utc).isoformat(),
        "determination_method": rca.get("determination_method") or "unknown",
        "confidence": rca.get("confidence") or "unknown",
        "human_decision": result.get("human_decision") or "none",
        "execution_status": execution.get("status") or "not_reached",
        "recovery_verified": bool(verification.get("verified")),
    }
