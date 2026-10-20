"""A real LangGraph workflow:

    raw telemetry record
            |
      extract_features
            |
      detect_anomaly
            |
        (decision)
       /          \\
   normal       anomaly
      |             |
      |        interpret (Ollama)
      |             |
      |     retrieve_similar_incidents (ChromaDB, Memory-set only)
      |             |
      |     root_cause_analysis (deterministic signature match + RAG cross-check + optional LLM narrative)
      |             |
      |     propose_remediation (deterministic - no LLM, never executes)
      |             |
      |     safety_validator (deterministic, zero AI - see safety/validator.py)
      |             |
      |        (safety_check.passed?)
      |         /          \\
      |       no           yes
      |        |             |
      |        |      human_approval (interrupt() - pauses until Command(resume=...); see below)
      |        |             |
      |        |        (approved?)
      |        |         /        \\
      |        |       no         yes
      |        |        |          |
      |        |        |     execute_action  (Week 10 - the ONLY code that changes the system)
      |        |        |          |
      |        |        |     verify_recovery (re-observes /fault-status + /actuator/health)
      |        |        |          |
      |        |        |    (failed or fault still active, and attempts left?)
      |        |        |      /                                   \\
      |        |        |    yes                                    no
      |        |        |     |                                      |
      |        |        | record_failed_action --> propose_remediation (retry: new proposal,
      |        |        |                          new safety check, NEW human approval)
      |         \\       |                                            |
      |          \\      |                                           /
       \\__________\\_____\\_________ feedback (write-back to ChromaDB, Evaluation-safe)
                                          |
                                       finalize
                                          |
                                         END

The LLM is only ever invoked on the anomaly branch - a normal reading never calls Ollama, never
queries ChromaDB, and never runs RCA/remediation. Detectors and the Ollama client are injected
into `build_graph()` rather than constructed inside node functions, so tests can supply
fakes/mocks and so models are loaded once, not per call.

Week 7 built `extract_features` -> `detect_anomaly` -> `interpret` -> `finalize`. Week 8 added the
three nodes between `interpret` and `finalize`. Week 9 adds `safety_validator` (plain Python, zero
AI - CLAUDE.md rule 20) and `human_approval`: a proposal that fails safety validation is rejected
and routed straight to `finalize`, never reaching a human at all (CLAUDE.md rule 20's "a rule
violation shouldn't even reach the human"); a proposal that passes is paused with langgraph's
`interrupt()` until a human resumes the graph with `Command(resume={"decision": "approved" |
"rejected", ...})` - this requires a checkpointer (see `build_graph()`'s `checkpointer` param and
`app.py`'s Streamlit UI), and is the mechanism that makes human approval *mandatory* rather than
just documented: the graph cannot proceed past this node without an external resume signal
(CLAUDE.md rule 21). See ai-orchestration/README.md "Week 9 architecture".

Week 10 completes the pipeline: `execute_action` (the only code in this project that changes the
running system - three fail-closed gates, see execution/executor.py), `verify_recovery` (re-observe
rather than assume), the failed-fix retry loop back to `propose_remediation` (each retry gets a
fresh safety check and a fresh human approval - a retry never inherits an approval), and `feedback`
(write the finished run back into ChromaDB, with Evaluation-set incidents hard-refused per rule 22).
Callers should build their `.invoke()` config with `default_run_config()` - see its docstring for
why the recursion limit matters once retries exist. See README.md "Week 10 architecture".
"""
from __future__ import annotations

import dataclasses
import logging
from datetime import datetime, timezone
from typing import Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .. import config as ai_config
from ..anomaly import feature_extractor
from ..anomaly.detector import AnomalyDetector
from ..anomaly.models import AnomalyResult, FeatureVector
from ..llm.interpret import interpret_anomaly
from ..llm.models import AnomalyInterpretation
from ..llm.ollama_client import OllamaClient
from ..rag import retriever as rag_retriever
from ..rag.models import RetrievalResult
from ..execution import verify as recovery_verify
from ..execution.executor import execute_approved_action
from ..execution.models import ExecutionResult
from ..feedback import writer as feedback_writer
from ..rca import analyzer as rca_analyzer
from ..anomaly.gmm_detector import load_all as load_gmm_detectors
from ..anomaly.robust_detector import load_all as load_robust_detectors
from ..rca import signatures as rca_signatures
from ..rca.models import RootCauseAnalysis
from ..remediation import proposer as remediation_proposer
from ..remediation.models import RemediationProposal
from ..safety import validator as safety_validator
from .state import WorkflowState

log = logging.getLogger("ai_orchestration.graph.workflow")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_features_node(state: WorkflowState) -> dict:
    record = state["raw_record"]
    fv = feature_extractor.extract_features(record)
    if fv is None:
        return {"feature_vector": None, "correlation_id": record.get("correlation_id")}
    return {"feature_vector": fv.to_dict(), "correlation_id": fv.correlation_id}


def _make_detect_anomaly_node(detectors: dict[str, AnomalyDetector]):
    # Loaded once per graph build, not per record. Empty unless the robust baselines have been
    # fitted (python -m ai_orchestration.evaluation.prepare --robust).
    robust = load_robust_detectors()
    gmm = load_gmm_detectors()

    def _detect_anomaly_node(state: WorkflowState) -> dict:
        record = state["raw_record"]
        feature_vector_dict = state.get("feature_vector")

        if feature_vector_dict is None:
            result = AnomalyResult(
                service=record.get("service"), timestamp=record.get("timestamp"),
                correlation_id=record.get("correlation_id"), status="insufficient_data",
                anomaly_score=None, is_anomaly=None, threshold=0.0, relevant_feature_values={},
                model_version="isolation-forest-v1", feature_schema_version="n/a",
                reason="No valid feature vector could be extracted from this record",
            )
            return {"anomaly_result": result.to_dict()}

        fv = FeatureVector.from_dict(feature_vector_dict)
        detector = detectors.get(fv.service)

        # Deterministic fault-signature matching, used as a detection signal in its own right.
        # `rca/signatures.py` already encodes what a fault looks like per service; running it here
        # as well as downstream means an incident is not discarded before the deterministic layer
        # has seen it. See config.DETECTION_MODE for the measured comparison and for why the
        # Isolation Forest alone surfaces so little.
        signature_hit = bool(rca_signatures.match_signatures(fv.service, fv.raw_metrics))
        # A signature can only speak for a service it was written for: the four signatures name
        # payment-service and ledger-service as their symptom services, so no signature is ever
        # applicable to auth-service or notification-service. Treating "no signature matched" as
        # "nothing is wrong" on those services would make them permanently undetectable, so where
        # no signature applies the Isolation Forest remains the detector.
        signature_applies = any(
            s.symptom_service == fv.service for s in rca_signatures.FAULT_SIGNATURES
        )
        mode = ai_config.DETECTION_MODE

        if detector is None or not detector.is_trained:
            # No model is no longer fatal: a signature can still identify the fault on its own.
            if mode in ("signature", "either") and signature_hit:
                result = AnomalyResult(
                    service=fv.service, timestamp=fv.timestamp, correlation_id=fv.correlation_id,
                    status="scored", anomaly_score=None, is_anomaly=True,
                    threshold=ai_config.ANOMALY_SCORE_THRESHOLD,
                    relevant_feature_values=fv.raw_metrics, model_version="signature-match-v1",
                    feature_schema_version=ai_config.FEATURE_SCHEMA_VERSION,
                    reason=(f"No trained detector for '{fv.service}', but the metrics match a known "
                            f"fault signature, so this record is treated as anomalous."),
                )
                return {"anomaly_result": result.to_dict(), "detected_at": _now_iso()}
            result = AnomalyResult(
                service=fv.service, timestamp=fv.timestamp, correlation_id=fv.correlation_id,
                status="no_model", anomaly_score=None, is_anomaly=None, threshold=0.0,
                relevant_feature_values=fv.raw_metrics, model_version="isolation-forest-v1",
                feature_schema_version="n/a", reason=f"No trained detector available for service '{fv.service}'",
            )
            return {"anomaly_result": result.to_dict()}

        scored = detector.score(fv)
        forest_hit = bool(scored.is_anomaly)

        learned = {"robust": robust, "gmm": gmm}.get(mode, {}).get(fv.service)
        robust_result = None
        if learned is not None and learned.is_trained:
            try:
                robust_result = learned.score(fv)
            except ValueError as exc:   # schema drift between detectors
                log.warning("%s detector could not score %s: %s", mode, fv.service, exc)

        if robust_result is not None:
            # The robust detector supersedes the forest's verdict *and* its score, because the
            # two are on different scales: sigmas from normal rather than a negated isolation
            # depth. Reporting the forest's number beside a robust verdict would be misleading.
            update: dict = {"anomaly_result": robust_result.to_dict()}
            if robust_result.is_anomaly:
                update["detected_at"] = _now_iso()
            return update

        decided = {
            "isolation_forest": forest_hit,
            "signature": signature_hit if signature_applies else forest_hit,
            "either": forest_hit or signature_hit,
            "robust": forest_hit,   # no learned baseline for this service: fall back
            "gmm": forest_hit,
        }[mode]

        if decided != forest_hit:
            # The score itself is never rewritten - only the verdict drawn from it - so the raw
            # Isolation Forest output stays auditable alongside the decision that was acted on.
            scored = dataclasses.replace(
                scored,
                is_anomaly=decided,
                reason=(
                    f"Detection mode '{mode}': signature "
                    f"{'match=' + str(signature_hit) if signature_applies else 'not applicable to this service'}, "
                    f"Isolation Forest={forest_hit} (score {scored.anomaly_score} against "
                    f"threshold {scored.threshold}). Verdict: "
                    f"{'anomalous' if decided else 'normal'}."
                ),
            )

        update: dict = {"anomaly_result": scored.to_dict()}
        if scored.is_anomaly:
            # Week 10: start the MTTR clock at the moment of detection (see state.py's
            # `detected_at` comment for why this point specifically).
            update["detected_at"] = _now_iso()
        return update

    return _detect_anomaly_node


def _route_after_detection(state: WorkflowState) -> str:
    anomaly_result = state.get("anomaly_result") or {}
    if anomaly_result.get("status") != "scored":
        return "insufficient_data"
    return "anomaly" if anomaly_result.get("is_anomaly") else "normal"


def _make_interpret_node(ollama_client: OllamaClient):
    def _interpret_node(state: WorkflowState) -> dict:
        anomaly_result = AnomalyResult.from_dict(state["anomaly_result"])
        interpretation = interpret_anomaly(anomaly_result, ollama_client)
        return {"llm_interpretation": interpretation.to_dict()}

    return _interpret_node


def _retrieve_similar_incidents_node(state: WorkflowState) -> dict:
    anomaly_result = state["anomaly_result"]

    if not ai_config.RETRIEVAL_ENABLED:
        # Week 16 memory-ablation only. Returns the same shape an empty retrieval produces, so
        # every downstream node behaves exactly as it would when the collection legitimately has
        # no neighbours - the ablation removes the evidence, not the code path.
        return {"retrieval": RetrievalResult(
            query_text="", incidents=[], source_collection="incident_memory", status="empty",
            reason="Retrieval disabled for ablation (AI_ORCHESTRATION_RETRIEVAL_ENABLED=false)",
        ).to_dict()}

    llm_interpretation = state.get("llm_interpretation") or {}
    abnormal_summary = (
        llm_interpretation.get("abnormal_summary") if llm_interpretation.get("status") == "interpreted" else None
    )
    query_text = rag_retriever.build_query_text(
        service=anomaly_result["service"],
        relevant_feature_values=anomaly_result.get("relevant_feature_values") or {},
        llm_abnormal_summary=abnormal_summary,
    )
    result = rag_retriever.retrieve_similar_incidents(query_text)
    return {"retrieval": result.to_dict()}


def _make_root_cause_analysis_node(ollama_client: OllamaClient):
    def _root_cause_analysis_node(state: WorkflowState) -> dict:
        anomaly_result = state["anomaly_result"]
        retrieval = RetrievalResult(**state["retrieval"])
        rca = rca_analyzer.analyze(anomaly_result, retrieval, ollama_client)
        return {"root_cause_analysis": rca.to_dict()}

    return _root_cause_analysis_node


def _propose_remediation_node(state: WorkflowState) -> dict:
    """Also the retry entry point: the failed-fix loop routes back *here*, not to RCA, so a retry
    tries a different fix instead of re-diagnosing from scratch (Week 8 roadmap). Every previously
    failed action is passed to the proposer, which walks a deterministic escalation ladder and
    never re-proposes an action category that already failed for this incident.

    A retry also deliberately clears the previous approval and safety verdict from state: the next
    node is the Safety Validator, and after it a fresh `interrupt()`, so a new action can never
    inherit the human approval that was given for a different action."""
    rca = RootCauseAnalysis(**state["root_cause_analysis"])
    failed_actions = state.get("failed_actions") or []
    proposal = remediation_proposer.propose_remediation(rca, failed_actions=failed_actions)
    return {
        "remediation_proposal": proposal.to_dict(),
        "safety_check": None,
        "human_decision": None,
        "human_decision_reason": None,
        "execution_result": None,
        "recovery_verification": None,
    }


def _safety_validator_node(state: WorkflowState) -> dict:
    proposal = RemediationProposal(**state["remediation_proposal"])
    result = safety_validator.validate_safety(proposal)
    return {"safety_check": result.to_dict()}


def _route_after_safety_check(state: WorkflowState) -> str:
    safety_check = state.get("safety_check") or {}
    return "human_approval" if safety_check.get("passed") else "finalize"


def _human_approval_node(state: WorkflowState) -> dict:
    """Pauses the graph with `interrupt()` until a human resumes it via
    `graph.invoke(Command(resume={"decision": "approved" | "rejected", "reason": ...}), config)`
    using the same `thread_id` (see app.py). Per langgraph's own semantics, everything in this
    node re-runs from the top on resume - the payload build below is side-effect-free, so
    re-executing it is harmless."""
    payload = {
        "remediation_proposal": state["remediation_proposal"],
        "root_cause_analysis": state.get("root_cause_analysis"),
        "safety_check": state.get("safety_check"),
    }
    decision = interrupt(payload)
    return {
        "human_decision": decision.get("decision"),
        "human_decision_reason": decision.get("reason"),
    }


def _route_after_human_decision(state: WorkflowState) -> str:
    """Only an explicit "approved" proceeds to execution. Anything else - a rejection, or any
    unexpected value - routes to feedback and then ends, so the failure mode of an unrecognized
    decision string is "do nothing", never "execute anyway"."""
    return "execute_action" if state.get("human_decision") == "approved" else "feedback"


def _make_execute_action_node(kubernetes_client=None):
    def _execute_action_node(state: WorkflowState) -> dict:
        proposal = RemediationProposal(**state["remediation_proposal"])
        rca = state.get("root_cause_analysis") or {}
        attempts = state.get("remediation_attempts") or 0

        result = execute_approved_action(
            proposal=proposal,
            suspected_fault_type=rca.get("suspected_fault_type"),
            safety_check=state.get("safety_check"),
            human_decision=state.get("human_decision"),
            attempts_already_made=attempts,
            kubernetes_client=kubernetes_client,
        )

        update: dict = {"execution_result": result.to_dict()}
        # Only a real attempt at changing the system consumes the attempt budget. A refusal
        # (not approved / safety failed / budget exhausted) and a pure no-op do not, because
        # nothing was tried.
        if result.status in ("executed", "failed", "dry_run"):
            update["remediation_attempts"] = attempts + 1
        return update

    return _execute_action_node


def _verify_recovery_node(state: WorkflowState) -> dict:
    execution_result = ExecutionResult(**state["execution_result"])
    rca = state.get("root_cause_analysis") or {}
    verification = recovery_verify.verify_recovery(
        execution_result, fault_type=rca.get("suspected_fault_type")
    )

    update: dict = {"recovery_verification": verification.to_dict()}

    # MTTR, per the Week 10 roadmap: "measured from when the anomaly was first detected to
    # execution completion". Recorded whenever an execution actually completed, successful or not;
    # whether that execution *worked* is a separate, independently recorded fact
    # (`recovery_verification.verified`), so Week 12 can compute both a raw MTTR and a
    # recovery-only MTTR without either being silently baked in here.
    detected_at, completed_at = state.get("detected_at"), execution_result.completed_at
    if detected_at and completed_at and execution_result.status in ("executed", "failed"):
        try:
            delta = datetime.fromisoformat(completed_at) - datetime.fromisoformat(detected_at)
            seconds = delta.total_seconds()
            if seconds < 0:
                # Cannot happen when both timestamps come from this process's own clock, but a
                # negative MTTR is meaningless and would silently skew Week 12's aggregates - so it
                # is dropped and logged rather than recorded.
                log.warning(
                    "Refusing to record a negative MTTR (%.3fs) - detected_at=%r is after "
                    "completed_at=%r", seconds, detected_at, completed_at,
                )
            else:
                update["mttr_seconds"] = round(seconds, 3)
        except ValueError:  # pragma: no cover - both values are produced by _now_iso()
            log.warning("Could not compute MTTR from detected_at=%r completed_at=%r", detected_at, completed_at)

    return update


def _route_after_verification(state: WorkflowState) -> str:
    """The failed-fix retry decision. A retry is warranted when the system is demonstrably still
    broken - either the action itself failed, or it "succeeded" but the fault is still active
    (a False Recovery). Both are real outcomes worth escalating on; treating only the first as a
    failure would let a no-op reset that changed nothing look like a fix.

    `remediation_attempts` is checked here as well as inside the executor, so the graph stops
    looping even if a future caller invoked the executor differently."""
    execution_result = state.get("execution_result") or {}
    verification = state.get("recovery_verification") or {}
    attempts = state.get("remediation_attempts") or 0

    if attempts >= ai_config.MAX_REMEDIATION_ATTEMPTS:
        return "feedback"

    execution_failed = execution_result.get("status") == "failed"
    false_recovery = verification.get("status") == "fault_still_active"
    if execution_failed or false_recovery:
        return "propose_remediation"

    return "feedback"


def _record_failed_action(state: WorkflowState) -> dict:
    """Appends the just-failed action to `failed_actions` so the next proposal differs from it.
    Kept as its own tiny node rather than folded into the routing function because LangGraph's
    conditional-edge functions decide *where to go* and must not mutate state - doing both in one
    place is how retry loops quietly stop recording their own history."""
    execution_result = state.get("execution_result") or {}
    verification = state.get("recovery_verification") or {}
    action = execution_result.get("action") or {}
    proposal = state.get("remediation_proposal") or {}

    failed = list(state.get("failed_actions") or [])
    failed.append({
        "action_category": proposal.get("action_category"),
        "target_service": proposal.get("target_service"),
        "action_kind": action.get("kind"),
        "execution_status": execution_result.get("status"),
        "reason": execution_result.get("reason") if execution_result.get("status") == "failed"
                  else verification.get("reason"),
        "attempt": state.get("remediation_attempts"),
    })
    log.info("Recorded failed remediation attempt %d: %s", len(failed), failed[-1]["reason"])
    return {"failed_actions": failed}


def _feedback_node(state: WorkflowState) -> dict:
    """Writes the finished run into retrieval memory. Runs on every terminal path that produced a
    diagnosis - including safety rejections, human rejections, and failed remediations - because
    "this was tried here and did not work" is exactly as useful to a future retrieval as a success
    (see feedback/writer.py)."""
    result_view = {
        "decision": _route_after_detection(state),
        "service": (state.get("raw_record") or {}).get("service"),
        "timestamp": (state.get("raw_record") or {}).get("timestamp"),
        "anomaly_result": state.get("anomaly_result"),
        "root_cause_analysis": state.get("root_cause_analysis"),
        "remediation_proposal": state.get("remediation_proposal"),
        "safety_check": state.get("safety_check"),
        "human_decision": state.get("human_decision"),
        "execution_result": state.get("execution_result"),
        "recovery_verification": state.get("recovery_verification"),
    }
    feedback_result = feedback_writer.write_back(result_view, source_incident_id=state.get("incident_id"))
    return {"feedback_result": feedback_result.to_dict()}


def _finalize_node(state: WorkflowState) -> dict:
    """Assembles the final structured result. Required fields (Week 7 spec): correlation/incident
    identifiers where available, telemetry evidence, anomaly result, LLM interpretation, and
    confidence/limitations - present on every path, not just the anomaly one, so Week 8 can rely
    on a single consistent output shape regardless of which branch ran."""
    record = state["raw_record"]
    anomaly_result = state.get("anomaly_result") or {}
    llm_interpretation = state.get("llm_interpretation")
    retrieval = state.get("retrieval")
    root_cause_analysis = state.get("root_cause_analysis")
    remediation_proposal = state.get("remediation_proposal")
    safety_check = state.get("safety_check")
    human_decision = state.get("human_decision")
    human_decision_reason = state.get("human_decision_reason")
    execution_result = state.get("execution_result")
    recovery_verification = state.get("recovery_verification")
    feedback_result = state.get("feedback_result")
    decision = _route_after_detection(state)

    limitations: list[str] = []
    if anomaly_result.get("status") == "insufficient_data":
        limitations.append("Anomaly detection was not run: no valid feature vector for this record.")
    if anomaly_result.get("status") == "no_model":
        limitations.append("No trained model available for this service.")
    if llm_interpretation and llm_interpretation.get("status") == "llm_unavailable":
        limitations.append("LLM interpretation unavailable: " + str(llm_interpretation.get("reason")))
    if llm_interpretation and llm_interpretation.get("status") == "llm_error":
        limitations.append("LLM interpretation failed: " + str(llm_interpretation.get("reason")))
    if retrieval and retrieval.get("status") == "collection_unavailable":
        limitations.append("RAG retrieval unavailable: " + str(retrieval.get("reason")))
    if retrieval and retrieval.get("status") == "empty":
        limitations.append("RAG retrieval returned no similar incidents.")
    if root_cause_analysis and not root_cause_analysis.get("llm_reasoning"):
        limitations.append("RCA proceeded without an LLM narrative (Ollama unavailable or returned an invalid response).")
    single_agent_diagnosis = state.get("single_agent_diagnosis")
    if single_agent_diagnosis and single_agent_diagnosis.get("status") != "diagnosed":
        limitations.append(
            f"The single-agent baseline produced no usable diagnosis "
            f"({single_agent_diagnosis.get('status')}): {single_agent_diagnosis.get('reason')}. "
            f"Unlike the multi-agent pipeline, it has no deterministic fallback by design."
        )
    if single_agent_diagnosis and single_agent_diagnosis.get("parse_issues"):
        limitations.append(
            "The single-agent baseline's response needed correction before it could be used: "
            + "; ".join(single_agent_diagnosis["parse_issues"])
        )
    if root_cause_analysis and root_cause_analysis.get("confidence") == "low":
        limitations.append("Root cause analysis confidence is low - treat suspected_root_cause_service as tentative.")
    if safety_check and not safety_check.get("passed"):
        limitations.append(
            "Remediation proposal failed safety validation and was rejected before reaching human "
            "approval: " + str(safety_check.get("reason"))
        )
    if safety_check and safety_check.get("passed") and human_decision == "rejected":
        limitations.append(
            "A human operator rejected the proposed remediation" +
            (f": {human_decision_reason}" if human_decision_reason else ".") +
            " No remediation was executed."
        )
    if execution_result and execution_result.get("status") == "failed":
        limitations.append("Remediation execution failed: " + str(execution_result.get("reason")))
    if execution_result and execution_result.get("status") == "refused":
        limitations.append("Remediation execution was refused: " + str(execution_result.get("reason")))
    if execution_result and execution_result.get("dry_run"):
        limitations.append(
            "Execution ran in dry-run mode - the action was resolved and logged but deliberately "
            "not carried out, so nothing about the running system changed."
        )
    if recovery_verification and recovery_verification.get("status") == "fault_still_active":
        limitations.append(
            "False recovery: remediation reported success but the target service still reports the "
            "fault active - " + str(recovery_verification.get("reason"))
        )
    if recovery_verification and recovery_verification.get("status") in ("unreachable", "unhealthy"):
        limitations.append("Recovery could not be confirmed: " + str(recovery_verification.get("reason")))
    if (state.get("remediation_attempts") or 0) >= ai_config.MAX_REMEDIATION_ATTEMPTS:
        limitations.append(
            f"The remediation-attempt budget ({ai_config.MAX_REMEDIATION_ATTEMPTS}) was exhausted "
            f"for this incident; no further automated remediation was attempted."
        )
    if feedback_result and feedback_result.get("status") == "refused_evaluation_incident":
        limitations.append(
            "This run was NOT written back into retrieval memory because it replays an Evaluation "
            "set incident (CLAUDE.md rule 22) - this is the leakage guard working, not an error."
        )
    if feedback_result and feedback_result.get("status") == "failed":
        limitations.append("Feedback write-back failed: " + str(feedback_result.get("reason")))

    result = {
        "correlation_id": state.get("correlation_id") or record.get("correlation_id"),
        "incident_id": state.get("incident_id"),
        "service": record.get("service"),
        "timestamp": record.get("timestamp"),
        "decision": decision,
        "telemetry_evidence": {
            "metrics": record.get("metrics"),
            "fault": record.get("fault"),
            "collection_error": record.get("collection_error"),
        },
        "anomaly_result": anomaly_result,
        "llm_interpretation": llm_interpretation,
        "retrieval": retrieval,
        "root_cause_analysis": root_cause_analysis,
        "remediation_proposal": remediation_proposal,
        "safety_check": safety_check,
        "human_decision": human_decision,
        "human_decision_reason": human_decision_reason,
        "execution_result": execution_result,
        "recovery_verification": recovery_verification,
        "mttr_seconds": state.get("mttr_seconds"),
        "remediation_attempts": state.get("remediation_attempts") or 0,
        "failed_actions": state.get("failed_actions") or [],
        "feedback_result": feedback_result,
        # Present only on runs produced by the Week 11 single-agent baseline graph; always None for
        # the multi-agent pipeline, which is what lets an evaluation harness tell the two apart
        # from the result alone.
        "single_agent_diagnosis": state.get("single_agent_diagnosis"),
        "limitations": limitations,
    }
    return {"result": result}


def default_run_config(thread_id: str) -> dict:
    """The config every caller should use for `.invoke()`.

    Two things must be right or the graph misbehaves in ways that only show up late:
    a `thread_id` (required by the checkpointer that Week 9's `interrupt()` depends on), and a
    `recursion_limit` above LangGraph's default of 25. The Week 10 retry loop can legitimately run
    ~22 supersteps on a three-attempt incident (10 for the first attempt plus 5 per retry, plus
    feedback and finalize), which is close enough to 25 that a slightly longer path would abort a
    real remediation mid-flight with a recursion error rather than finishing it."""
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": ai_config.GRAPH_RECURSION_LIMIT,
    }


def build_graph(
    detectors: dict[str, AnomalyDetector],
    ollama_client: Optional[OllamaClient] = None,
    checkpointer: Optional[BaseCheckpointSaver] = None,
    kubernetes_client=None,
):
    """Compiles the workflow graph. `detectors` is a {service_name: AnomalyDetector} registry
    (see anomaly/detector.py); `ollama_client` defaults to an OllamaClient built from env vars if
    not supplied. `checkpointer` defaults to an in-process `MemorySaver` - Week 9's mandatory
    human-approval `interrupt()` requires one to be compiled in (langgraph raises if you try to use
    `interrupt()`/`Command(resume=...)` without one), so every caller must also pass a `thread_id`
    via `config={"configurable": {"thread_id": ...}}` on every `.invoke()`/`.stream()` call, even on
    paths that never reach `human_approval` - see app.py and tests/test_workflow.py for the
    pattern. Returns a compiled LangGraph graph."""
    ollama_client = ollama_client or OllamaClient()
    checkpointer = checkpointer or MemorySaver()

    graph = StateGraph(WorkflowState)
    graph.add_node("extract_features", _extract_features_node)
    graph.add_node("detect_anomaly", _make_detect_anomaly_node(detectors))
    graph.add_node("interpret", _make_interpret_node(ollama_client))
    graph.add_node("retrieve_similar_incidents", _retrieve_similar_incidents_node)
    graph.add_node("root_cause_analysis", _make_root_cause_analysis_node(ollama_client))
    graph.add_node("propose_remediation", _propose_remediation_node)
    graph.add_node("safety_validator", _safety_validator_node)
    graph.add_node("human_approval", _human_approval_node)
    graph.add_node("execute_action", _make_execute_action_node(kubernetes_client))
    graph.add_node("verify_recovery", _verify_recovery_node)
    graph.add_node("record_failed_action", _record_failed_action)
    graph.add_node("feedback", _feedback_node)
    graph.add_node("finalize", _finalize_node)

    graph.add_edge(START, "extract_features")
    graph.add_edge("extract_features", "detect_anomaly")
    graph.add_conditional_edges(
        "detect_anomaly", _route_after_detection,
        {"anomaly": "interpret", "normal": "finalize", "insufficient_data": "finalize"},
    )
    graph.add_edge("interpret", "retrieve_similar_incidents")
    graph.add_edge("retrieve_similar_incidents", "root_cause_analysis")
    graph.add_edge("root_cause_analysis", "propose_remediation")
    graph.add_edge("propose_remediation", "safety_validator")
    # A safety rejection still goes through feedback (so "this was refused, and why" is learnable)
    # but never through human_approval or execution.
    graph.add_conditional_edges(
        "safety_validator", _route_after_safety_check,
        {"human_approval": "human_approval", "finalize": "feedback"},
    )
    graph.add_conditional_edges(
        "human_approval", _route_after_human_decision,
        {"execute_action": "execute_action", "feedback": "feedback"},
    )
    graph.add_edge("execute_action", "verify_recovery")
    # The failed-fix retry loop: back to propose_remediation (not root_cause_analysis), via a node
    # that first records what just failed so the next proposal is guaranteed to differ.
    graph.add_conditional_edges(
        "verify_recovery", _route_after_verification,
        {"propose_remediation": "record_failed_action", "feedback": "feedback"},
    )
    graph.add_edge("record_failed_action", "propose_remediation")
    graph.add_edge("feedback", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer)


def load_all_detectors() -> dict[str, AnomalyDetector]:
    """Loads every trained detector found under config.MODELS_DIR. Services with no persisted
    model are simply absent from the returned dict - detect_anomaly_node handles that as
    status="no_model", not an error."""
    from .. import config

    detectors: dict[str, AnomalyDetector] = {}
    for service in config.SERVICE_FEATURE_SCHEMAS:
        try:
            detectors[service] = AnomalyDetector.load(service)
        except FileNotFoundError:
            log.info("No trained model for '%s' - it will report status='no_model'", service)
    return detectors
