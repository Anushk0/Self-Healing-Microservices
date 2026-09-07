"""Explicit typed state for the Week 7 LangGraph workflow.

`total=False` because the state is built up incrementally node-by-node - not every field is
present at every point in the graph."""
from __future__ import annotations

from typing import Optional, TypedDict


class WorkflowState(TypedDict, total=False):
    # --- input ---
    raw_record: dict  # one raw telemetry record, exactly as telemetry/collector.py writes it
    incident_id: Optional[str]  # caller-supplied, if this run is associated with a known incident

    # --- populated by extract_features_node ---
    feature_vector: Optional[dict]  # anomaly.models.FeatureVector.to_dict(), or None
    correlation_id: Optional[str]

    # --- populated by detect_anomaly_node ---
    anomaly_result: Optional[dict]  # anomaly.models.AnomalyResult.to_dict()

    # --- populated by the routing decision ---
    decision: str  # "normal" | "anomaly" | "insufficient_data"

    # --- populated by interpret_node (anomaly branch only) ---
    llm_interpretation: Optional[dict]  # llm.models.AnomalyInterpretation.to_dict()

    # --- Week 8: populated by retrieve_similar_incidents_node (anomaly branch only) ---
    retrieval: Optional[dict]  # rag.models.RetrievalResult.to_dict()

    # --- Week 8: populated by root_cause_analysis_node (anomaly branch only) ---
    root_cause_analysis: Optional[dict]  # rca.models.RootCauseAnalysis.to_dict()

    # --- Week 8: populated by propose_remediation_node (anomaly branch only) ---
    remediation_proposal: Optional[dict]  # remediation.models.RemediationProposal.to_dict() - a
    # PROPOSAL only; nothing in this graph executes it (see remediation/proposer.py's docstring)

    # --- Week 9: populated by safety_validator_node (anomaly branch only) ---
    safety_check: Optional[dict]  # safety.models.SafetyCheckResult.to_dict()

    # --- Week 9: populated by human_approval_node (anomaly branch, only if safety_check passed) ---
    # human_approval_node uses langgraph's interrupt() to pause the graph until a human resumes it
    # with Command(resume={"decision": ..., "reason": ...}) - see graph/workflow.py's docstring and
    # app.py's Approve/Reject buttons. Never set on the normal/insufficient_data/no_model paths, and
    # never set when safety_check failed (that path skips straight to finalize - see
    # CLAUDE.md "a rule violation shouldn't even reach the human").
    human_decision: Optional[str]  # "approved" | "rejected"
    human_decision_reason: Optional[str]

    # --- Week 10: execution, recovery verification, feedback ---
    # Set by detect_anomaly_node the moment an anomaly is first flagged - the start of the clock
    # for mttr_seconds. Deliberately captured at detection, not at graph start, so time spent
    # waiting for a human to click Approve is inside MTTR (it genuinely is part of time-to-repair).
    detected_at: Optional[str]  # ISO 8601 UTC

    execution_result: Optional[dict]        # execution.models.ExecutionResult.to_dict()
    recovery_verification: Optional[dict]   # execution.models.RecoveryVerification.to_dict()
    mttr_seconds: Optional[float]           # detected_at -> last completed execution

    # The failed-fix retry loop (Week 8's roadmap item, buildable only now that an executor
    # exists). `failed_actions` is what makes a retry propose something *different*: it is fed
    # straight into remediation.proposer.propose_remediation(). `remediation_attempts` counts
    # executions actually attempted, and is checked against config.MAX_REMEDIATION_ATTEMPTS by
    # both the routing function and the executor itself.
    remediation_attempts: int
    failed_actions: list[dict]

    feedback_result: Optional[dict]  # feedback.models.FeedbackResult.to_dict()

    # --- Week 11: set ONLY by the single-agent baseline graph (baseline/graph.py) ---
    # The multi-agent graph never writes this field, so its presence in a result is itself the
    # unambiguous marker of which arm produced that result. The baseline also writes
    # `root_cause_analysis` and `remediation_proposal` in the shared shape so both arms can be
    # compared field-by-field in Week 12 - with `determination_method="single_agent_llm"` making
    # the provenance explicit rather than inferred.
    single_agent_diagnosis: Optional[dict]  # baseline.models.SingleAgentDiagnosis.to_dict()

    # --- populated by finalize_node ---
    result: dict  # the final structured output - see workflow.py's finalize_node docstring
