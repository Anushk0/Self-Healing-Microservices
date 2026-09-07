"""The single-agent baseline pipeline, as a separate compiled graph.

    raw telemetry record
            |
      extract_features          <- IDENTICAL node object as the multi-agent graph
            |
      detect_anomaly            <- IDENTICAL
            |
        (decision)
       /          \\
   normal       anomaly
      |             |
      |     retrieve_similar_incidents   <- IDENTICAL (same collection, same query builder)
      |             |
      |     single_agent_diagnosis       <- THE ONLY DIFFERENCE: one LLM call replaces
      |             |                       interpret + root_cause_analysis + propose_remediation
      |     safety_validator             <- IDENTICAL
      |             |
      |     human_approval (interrupt)   <- IDENTICAL
      |             |
      |     execute_action               <- IDENTICAL
      |             |
      |     verify_recovery              <- IDENTICAL
      |             |
       \\      feedback / finalize        <- IDENTICAL
            |
           END

Why it is built this way (Week 11 roadmap): the roadmap warns against "just turning off nodes in
your multi-agent graph", because sharing the *diagnostic* machinery risks shared bugs and leakage
of deterministic conclusions into the LLM-only arm. So the diagnostic step is a separate module
(`baseline/single_agent.py`) that imports nothing from `rca/`, `remediation/`, or `llm/interpret.py`.

But the roadmap equally requires routing "the single agent's proposed action through the same
Safety Validator and Human Approval and Execution steps", because the comparison "should only
differ in the diagnostic step". Reusing the *same node functions* for everything else is what
guarantees that: they are imported, not copied, so the two arms cannot drift apart, and any
difference Week 12 measures is attributable to the diagnosis rather than to a downstream
discrepancy.

The isolated variable is therefore **agent decomposition**, not retrieval: both arms get the same
RAG-retrieved incidents. Whether RAG helps is a separate question (a memory-ablation experiment,
listed in the roadmap's Week 16 buffer), deliberately not confounded with this one.

There is one unavoidable asymmetry, and it is a property of the designs rather than of this
harness: the retry loop. The multi-agent proposer walks a deterministic escalation ladder, so a
retry is guaranteed to differ from what already failed. Asking the same LLM the same question again
is not - so this graph does not retry, and Week 12 must compare a single attempt per incident for
both arms to stay fair. See `_route_after_verification_no_retry` below.
"""
from __future__ import annotations

import logging
from typing import Optional

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from ..anomaly.detector import AnomalyDetector
from ..anomaly.models import AnomalyResult
from ..graph import workflow
from ..graph.state import WorkflowState
from ..llm.ollama_client import OllamaClient
from ..rag.models import RetrievalResult
from . import single_agent

log = logging.getLogger("ai_orchestration.baseline.graph")


def _make_single_agent_node(ollama_client: OllamaClient):
    def _single_agent_diagnosis_node(state: WorkflowState) -> dict:
        anomaly_result = AnomalyResult.from_dict(state["anomaly_result"])
        retrieval = RetrievalResult(**state["retrieval"])

        diagnosis = single_agent.diagnose(anomaly_result, retrieval, ollama_client)
        proposal = single_agent.to_remediation_proposal(diagnosis)

        # Written into the same state fields the multi-agent graph uses, so the downstream nodes -
        # and Week 12's evaluation harness - read one consistent shape from both arms. The
        # `single_agent_diagnosis` field carries the extra baseline-only detail (raw response,
        # parse issues) that has no multi-agent equivalent.
        return {
            "single_agent_diagnosis": diagnosis.to_dict(),
            "root_cause_analysis": _as_rca_shaped_view(diagnosis),
            "remediation_proposal": proposal.to_dict(),
        }

    return _single_agent_diagnosis_node


def _as_rca_shaped_view(diagnosis) -> dict:
    """The single agent's conclusion, expressed in the same field names the multi-agent RCA uses.

    This is a *view for comparison*, not a claim that the two were produced the same way -
    `determination_method` is explicitly `"single_agent_llm"` so no downstream consumer or results
    table can mistake an LLM's one-shot answer for a deterministic signature match."""
    return {
        "suspected_fault_type": diagnosis.suspected_fault_type,
        "suspected_root_cause_service": diagnosis.suspected_root_cause_service or "unknown",
        "symptom_service": diagnosis.symptom_service or "unknown",
        "affected_services": sorted(
            {s for s in (diagnosis.suspected_root_cause_service, diagnosis.symptom_service) if s}
        ),
        "relevant_metrics": [],
        "metric_anomalies": [],
        "evidence_summary": diagnosis.reason or "Single-agent LLM diagnosis.",
        "llm_reasoning": diagnosis.reasoning,
        "confidence": diagnosis.confidence or "low",
        "determination_method": "single_agent_llm",
    }


def _route_after_verification_no_retry(state: WorkflowState) -> str:
    """The baseline does not retry - see this module's docstring for why re-asking the same model
    the same question is not a comparable escalation. Kept as an explicit named function rather
    than a bare edge so the difference from the multi-agent graph is visible in the code, not just
    in prose."""
    return "feedback"


def build_baseline_graph(
    detectors: dict[str, AnomalyDetector],
    ollama_client: Optional[OllamaClient] = None,
    checkpointer: Optional[BaseCheckpointSaver] = None,
    kubernetes_client=None,
):
    """Compiles the single-agent baseline. Same signature as `graph.workflow.build_graph()` on
    purpose, so an evaluation harness can hold both arms in the same shape and swap one for the
    other. Use `graph.workflow.default_run_config()` for the invoke config here too."""
    ollama_client = ollama_client or OllamaClient()
    checkpointer = checkpointer or MemorySaver()

    graph = StateGraph(WorkflowState)
    # Identical node objects to the multi-agent graph - imported, never re-implemented.
    graph.add_node("extract_features", workflow._extract_features_node)
    graph.add_node("detect_anomaly", workflow._make_detect_anomaly_node(detectors))
    graph.add_node("retrieve_similar_incidents", workflow._retrieve_similar_incidents_node)
    graph.add_node("single_agent_diagnosis", _make_single_agent_node(ollama_client))
    graph.add_node("safety_validator", workflow._safety_validator_node)
    graph.add_node("human_approval", workflow._human_approval_node)
    graph.add_node("execute_action", workflow._make_execute_action_node(kubernetes_client))
    graph.add_node("verify_recovery", workflow._verify_recovery_node)
    graph.add_node("feedback", workflow._feedback_node)
    graph.add_node("finalize", workflow._finalize_node)

    graph.add_edge(START, "extract_features")
    graph.add_edge("extract_features", "detect_anomaly")
    graph.add_conditional_edges(
        "detect_anomaly", workflow._route_after_detection,
        {"anomaly": "retrieve_similar_incidents", "normal": "finalize", "insufficient_data": "finalize"},
    )
    graph.add_edge("retrieve_similar_incidents", "single_agent_diagnosis")
    graph.add_edge("single_agent_diagnosis", "safety_validator")
    graph.add_conditional_edges(
        "safety_validator", workflow._route_after_safety_check,
        {"human_approval": "human_approval", "finalize": "feedback"},
    )
    graph.add_conditional_edges(
        "human_approval", workflow._route_after_human_decision,
        {"execute_action": "execute_action", "feedback": "feedback"},
    )
    graph.add_edge("execute_action", "verify_recovery")
    graph.add_conditional_edges(
        "verify_recovery", _route_after_verification_no_retry, {"feedback": "feedback"},
    )
    graph.add_edge("feedback", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer)
