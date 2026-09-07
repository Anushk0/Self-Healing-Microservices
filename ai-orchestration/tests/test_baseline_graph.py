"""Week 11 category (part 2): the baseline pipeline end to end, and - the point of the whole week -
proof that it differs from the multi-agent pipeline in the diagnostic step and nowhere else.

`test_both_arms_share_every_non_diagnostic_node` is the load-bearing test here: it asserts node
identity rather than similar behavior, so the two arms cannot silently drift apart as later weeks
change downstream code.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from langgraph.types import Command

from ai_orchestration import config
from ai_orchestration.anomaly.detector import AnomalyDetector
from ai_orchestration.anomaly.feature_extractor import extract_features
from ai_orchestration.baseline import graph as baseline_graph
from ai_orchestration.baseline.graph import build_baseline_graph
from ai_orchestration.execution.models import ExecutableAction, ExecutionResult, RecoveryVerification
from ai_orchestration.feedback.models import FeedbackResult
from ai_orchestration.graph import workflow
from ai_orchestration.graph.workflow import default_run_config
from ai_orchestration.rag.models import RetrievalResult


@pytest.fixture(autouse=True)
def isolated_models_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(config, "ISOLATION_FOREST_CONTAMINATION", "0.05")


@pytest.fixture(autouse=True)
def isolate_side_effects(monkeypatch):
    """Same isolation the multi-agent graph tests use - no network, no cluster, no real ChromaDB."""
    def _empty(query_text, n_results=3):
        return RetrievalResult(query_text=query_text, incidents=[],
                               source_collection="incident_memory", status="empty")

    def _executed(proposal, suspected_fault_type, safety_check, human_decision,
                  attempts_already_made=0, kubernetes_client=None):
        # Mirrors the real executor's contract, including its gates and its no-op handling - a
        # test double that "executed" an investigate_manually proposal would let a genuine
        # regression through.
        if human_decision != "approved" or not (safety_check or {}).get("passed"):
            return ExecutionResult(status="refused", reason="gate failed (test double)")
        if proposal.action_category in ("investigate_manually", "no_action_needed"):
            return ExecutionResult(status="skipped_no_op", reason="no-op category (test double)")
        started = datetime.now(timezone.utc)
        return ExecutionResult(
            status="executed", reason="executed (test double)",
            action=ExecutableAction(kind="http_fault_reset", target_service=proposal.target_service,
                                    description="test double", http_path="/reset-x"),
            started_at=started.isoformat(),
            completed_at=(started + timedelta(seconds=5)).isoformat())

    def _recovered(execution_result, fault_type=None, **kwargs):
        if not execution_result.succeeded:
            return RecoveryVerification(status="not_applicable", verified=False, reason="nothing executed")
        return RecoveryVerification(status="recovered", verified=True, attempts=1,
                                    checked_service=execution_result.action.target_service,
                                    reason="recovered (test double)")

    def _written(result, source_incident_id=None):
        return FeedbackResult(status="written", reason="written (test double)",
                              written_incident_id="run-testdouble",
                              source_incident_id=source_incident_id)

    monkeypatch.setattr(workflow.rag_retriever, "retrieve_similar_incidents", _empty)
    monkeypatch.setattr(workflow, "execute_approved_action", _executed)
    monkeypatch.setattr(workflow.recovery_verify, "verify_recovery", _recovered)
    monkeypatch.setattr(workflow.feedback_writer, "write_back", _written)


def _trained_payment_detector(payment_record_factory) -> AnomalyDetector:
    rng = random.Random(7)
    vectors = [
        extract_features(payment_record_factory(
            jvm_used=40_000_000.0 + rng.uniform(-500_000, 500_000),
            error_count=max(0.0, rng.uniform(-0.3, 0.3)),
            req_count=10.0 + rng.uniform(-2, 2),
            client_duration=30.0 + rng.uniform(-10, 10),
        ))
        for _ in range(50)
    ]
    detector = AnomalyDetector("payment-service")
    detector.train(vectors, training_source_experiment_ids=["exp-1"])
    return detector


def _client(response: dict | str, available: bool = True) -> MagicMock:
    client = MagicMock()
    client.is_available.return_value = available
    client.base_url = "http://localhost:11434"
    client.model = "llama3.2:1b"
    client.generate.return_value = response if isinstance(response, str) else json.dumps(response)
    return client


_GOOD_RESPONSE = {
    "root_cause_service": "payment-service",
    "symptom_service": "payment-service",
    "fault_type": "memory-leak",
    "reasoning": "jvm_memory_used_bytes is far above the documented baseline.",
    "confidence": "high",
    "action_category": "reset_fault_configuration",
    "recommended_action": "Reset the payment-service memory-leak fault injection.",
}


def _run(graph, record, thread_id, decision="approved"):
    cfg = default_run_config(thread_id)
    state = graph.invoke({"raw_record": record, "incident_id": None}, cfg)
    for _ in range(4):
        if "__interrupt__" not in state:
            return state
        state = graph.invoke(Command(resume={"decision": decision, "reason": "test"}), cfg)
    raise AssertionError("baseline graph kept interrupting - it should pause at most once")


def _fault_record(payment_record_factory):
    return payment_record_factory(jvm_used=500_000_000.0, client_duration=100.0)


# --- The comparison property -------------------------------------------------------------------

def test_both_arms_share_every_non_diagnostic_node():
    """Node *identity*, not just similar behavior: the downstream steps are literally the same
    function objects, so the two arms cannot drift apart as later weeks change them. If this ever
    fails, the Week 12 comparison has stopped being about agent decomposition."""
    assert baseline_graph.workflow._extract_features_node is workflow._extract_features_node
    assert baseline_graph.workflow._retrieve_similar_incidents_node is workflow._retrieve_similar_incidents_node
    assert baseline_graph.workflow._safety_validator_node is workflow._safety_validator_node
    assert baseline_graph.workflow._human_approval_node is workflow._human_approval_node
    assert baseline_graph.workflow._verify_recovery_node is workflow._verify_recovery_node
    assert baseline_graph.workflow._feedback_node is workflow._feedback_node
    assert baseline_graph.workflow._finalize_node is workflow._finalize_node


def test_baseline_graph_has_no_multi_agent_diagnostic_nodes(payment_record_factory):
    """The three nodes the single agent replaces must not exist in this graph at all - that is what
    makes it a separate implementation rather than the multi-agent graph with steps disabled."""
    graph = build_baseline_graph({"payment-service": _trained_payment_detector(payment_record_factory)},
                                 _client(_GOOD_RESPONSE))
    nodes = set(graph.get_graph().nodes)

    assert "single_agent_diagnosis" in nodes
    assert "interpret" not in nodes
    assert "root_cause_analysis" not in nodes
    assert "propose_remediation" not in nodes
    # ...while everything downstream is present and identical.
    assert {"safety_validator", "human_approval", "execute_action", "verify_recovery", "feedback"} <= nodes


def test_baseline_never_calls_the_multi_agent_diagnostic_code(payment_record_factory, monkeypatch):
    from ai_orchestration.rca import analyzer as rca_analyzer
    from ai_orchestration.remediation import proposer as remediation_proposer

    monkeypatch.setattr(rca_analyzer, "analyze",
                        MagicMock(side_effect=AssertionError("baseline must not use multi-agent RCA")))
    monkeypatch.setattr(remediation_proposer, "propose_remediation",
                        MagicMock(side_effect=AssertionError("baseline must not use the deterministic proposer")))

    graph = build_baseline_graph({"payment-service": _trained_payment_detector(payment_record_factory)},
                                 _client(_GOOD_RESPONSE))
    _run(graph, _fault_record(payment_record_factory), "b-isolated")


# --- End to end --------------------------------------------------------------------------------

def test_baseline_runs_the_full_pipeline_through_approval_and_execution(payment_record_factory):
    client = _client(_GOOD_RESPONSE)
    graph = build_baseline_graph({"payment-service": _trained_payment_detector(payment_record_factory)}, client)

    final_state = _run(graph, _fault_record(payment_record_factory), "b-1")

    result = final_state["result"]
    assert result["decision"] == "anomaly"
    assert result["single_agent_diagnosis"]["status"] == "diagnosed"
    assert result["root_cause_analysis"]["determination_method"] == "single_agent_llm"
    assert result["root_cause_analysis"]["suspected_root_cause_service"] == "payment-service"
    assert result["safety_check"]["passed"] is True
    assert result["human_decision"] == "approved"
    assert result["execution_result"]["status"] == "executed"
    assert result["recovery_verification"]["verified"] is True
    assert client.generate.call_count == 1  # one LLM call for the entire diagnosis


def test_baseline_result_is_shape_compatible_with_the_multi_agent_result(payment_record_factory):
    """Week 12 loops over both arms and reads the same fields off each; a missing key would break
    the harness mid-evaluation."""
    detector = _trained_payment_detector(payment_record_factory)
    record = _fault_record(payment_record_factory)

    multi = _run(build_graph_for_multi(detector), record, "b-shape-multi")["result"]
    base = _run(build_baseline_graph({"payment-service": detector}, _client(_GOOD_RESPONSE)),
                record, "b-shape-base")["result"]

    assert set(multi) == set(base)
    # ...and the marker that tells them apart is present in exactly one of them.
    assert multi["single_agent_diagnosis"] is None
    assert base["single_agent_diagnosis"] is not None


def build_graph_for_multi(detector):
    from ai_orchestration.graph.workflow import build_graph
    client = MagicMock()
    client.is_available.return_value = False
    client.base_url = "http://localhost:11434"
    client.model = "llama3.2:1b"
    return build_graph({"payment-service": detector}, client)


def test_baseline_human_rejection_still_blocks_execution(payment_record_factory):
    """The baseline is subject to the identical mandatory-approval gate."""
    graph = build_baseline_graph({"payment-service": _trained_payment_detector(payment_record_factory)},
                                 _client(_GOOD_RESPONSE))

    final_state = _run(graph, _fault_record(payment_record_factory), "b-reject", decision="rejected")

    result = final_state["result"]
    assert result["human_decision"] == "rejected"
    assert result["execution_result"] is None


def test_baseline_with_no_llm_produces_no_diagnosis_and_executes_nothing(payment_record_factory):
    """The honest asymmetry, asserted end to end: with Ollama unavailable the multi-agent pipeline
    still reaches a deterministic conclusion, and this one reaches none."""
    graph = build_baseline_graph({"payment-service": _trained_payment_detector(payment_record_factory)},
                                 _client("", available=False))

    final_state = _run(graph, _fault_record(payment_record_factory), "b-nollm")

    result = final_state["result"]
    assert result["single_agent_diagnosis"]["status"] == "llm_unavailable"
    assert result["remediation_proposal"]["action_category"] == "investigate_manually"
    assert result["execution_result"]["status"] == "skipped_no_op"
    assert any("no deterministic fallback" in limitation for limitation in result["limitations"])


def test_baseline_does_not_retry_a_failed_execution(payment_record_factory, monkeypatch):
    """Re-asking the same model the same question is not a comparable escalation to the multi-agent
    ladder, so the baseline deliberately makes exactly one attempt - and Week 12 must therefore
    compare a single attempt per incident for both arms."""
    calls = []

    def _always_fails(proposal, suspected_fault_type, safety_check, human_decision,
                     attempts_already_made=0, kubernetes_client=None):
        calls.append(proposal.action_category)
        return ExecutionResult(
            status="failed", reason="HTTP 500",
            action=ExecutableAction(kind="http_fault_reset", target_service=proposal.target_service,
                                    description="d", http_path="/reset-x"),
            started_at="2026-01-01T00:00:00+00:00", completed_at="2026-01-01T00:00:01+00:00")

    monkeypatch.setattr(workflow, "execute_approved_action", _always_fails)
    graph = build_baseline_graph({"payment-service": _trained_payment_detector(payment_record_factory)},
                                 _client(_GOOD_RESPONSE))

    final_state = _run(graph, _fault_record(payment_record_factory), "b-noretry")

    assert len(calls) == 1
    assert final_state["result"]["execution_result"]["status"] == "failed"
    assert final_state["result"]["failed_actions"] == []
