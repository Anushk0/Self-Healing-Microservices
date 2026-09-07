"""Categories 10 (LangGraph normal path) and 11 (LangGraph anomaly path), plus the
insufficient-data path (no valid feature vector), no-model path, Week 8's RCA/RAG/remediation
extension of the anomaly path, and Week 9's safety_validator/human_approval extension.

Week 9 note: `build_graph()` now always compiles with a checkpointer (required for
`human_approval`'s `interrupt()`/`Command(resume=...)` to work at all - see workflow.py's
docstring), so every `.invoke()` call below must pass a `config` with a `thread_id`, even on
paths that never reach `human_approval` - a checkpointer raises if `configurable.thread_id` is
missing, regardless of whether this particular run ever interrupts. `_run_to_completion()` is the
one helper every test uses to stay correct if that requirement ever changes."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from langgraph.types import Command

from ai_orchestration import config
from ai_orchestration.anomaly.detector import AnomalyDetector
from ai_orchestration.anomaly.feature_extractor import extract_features
from ai_orchestration.execution.models import ExecutableAction, ExecutionResult, RecoveryVerification
from ai_orchestration.feedback.models import FeedbackResult
from ai_orchestration.graph import workflow
from ai_orchestration.graph.workflow import build_graph, default_run_config
from ai_orchestration.rag.models import RetrievalResult, RetrievedIncident
from ai_orchestration.remediation.models import RemediationProposal


@pytest.fixture(autouse=True)
def isolated_models_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MODELS_DIR", tmp_path / "models")
    # See test_detector.py's isolated_models_dir fixture for why: "auto" contamination's fixed
    # -0.5 offset isn't well-calibrated for a tiny unit-test-sized training set.
    monkeypatch.setattr(config, "ISOLATION_FOREST_CONTAMINATION", "0.05")


@pytest.fixture(autouse=True)
def mock_retrieval(monkeypatch):
    """Isolates every graph test from the real on-disk ChromaDB collection - retrieval behavior
    itself is covered exhaustively in test_rag_retriever.py. Defaults to an empty result; tests
    that care about non-empty retrieval override this within their own body."""
    def _empty(query_text, n_results=3):
        return RetrievalResult(query_text=query_text, incidents=[], source_collection="incident_memory", status="empty")
    monkeypatch.setattr(workflow.rag_retriever, "retrieve_similar_incidents", _empty)


@pytest.fixture(autouse=True)
def mock_side_effecting_week10_nodes(monkeypatch):
    """Week 10: no graph test may touch the real system. `execute_action` would otherwise POST to a
    real service, `verify_recovery` would poll one, and `feedback` would write into the project's
    real ChromaDB collection.

    Defaults here are the *successful* path, so existing Weeks 7-9 assertions stay about what they
    were about; tests that care about execution failure, false recovery, or write-back behavior
    override these within their own body. The real implementations are covered directly in
    test_execution_executor.py, test_recovery_verification.py, and test_feedback_writer.py."""
    def _executed(proposal, suspected_fault_type, safety_check, human_decision,
                  attempts_already_made=0, kubernetes_client=None):
        # Mirrors the real executor's contract, including its gates and its no-op handling.
        if human_decision != "approved" or not (safety_check or {}).get("passed"):
            return ExecutionResult(status="refused", reason="gate failed (test double)")
        if proposal.action_category in ("investigate_manually", "no_action_needed"):
            return ExecutionResult(status="skipped_no_op", reason="no-op category (test double)")
        # Timestamps are generated relative to *now* rather than hardcoded, because MTTR is
        # measured against the real `detected_at` the graph stamps at detection time - a fixed
        # past date would produce a nonsensical negative interval.
        started = datetime.now(timezone.utc)
        return ExecutionResult(
            status="executed", reason="executed (test double)",
            action=ExecutableAction(kind="http_fault_reset", target_service=proposal.target_service,
                                    description="test double", http_path="/reset-x",
                                    fault_type=suspected_fault_type),
            started_at=started.isoformat(),
            completed_at=(started + timedelta(seconds=20)).isoformat(),
            duration_seconds=20.0,
        )

    def _recovered(execution_result, fault_type=None, **kwargs):
        if not execution_result.succeeded:
            return RecoveryVerification(status="not_applicable", verified=False,
                                        reason="nothing executed (test double)")
        return RecoveryVerification(status="recovered", verified=True, attempts=1,
                                    checked_service=execution_result.action.target_service,
                                    reason="recovered (test double)")

    def _written(result, source_incident_id=None):
        return FeedbackResult(status="written", reason="written (test double)",
                              written_incident_id="run-testdouble",
                              source_incident_id=source_incident_id)

    monkeypatch.setattr(workflow, "execute_approved_action", _executed)
    monkeypatch.setattr(workflow.recovery_verify, "verify_recovery", _recovered)
    monkeypatch.setattr(workflow.feedback_writer, "write_back", _written)


def _thread_config(thread_id: str) -> dict:
    return default_run_config(thread_id)


def _run_to_completion(graph, initial_state: dict, thread_id: str, decision: str = "approved",
                       reason: str = "looks correct", max_resumes: int = 5) -> dict:
    """Runs a graph invocation to a final `result`, transparently resuming past `human_approval`'s
    mandatory interrupt - most tests in this file only care about the deterministic
    detection/RCA/remediation/safety logic upstream of that pause, not the approval mechanics
    themselves (which get their own dedicated tests below).

    Resumes in a loop rather than once, because Week 10's retry loop can legitimately pause more
    than once for a single incident: every retry proposes a *different* action and therefore needs
    its own, separate human approval. `max_resumes` is a test-side guard so a routing bug shows up
    as a clear assertion failure instead of an infinite loop."""
    cfg = _thread_config(thread_id)
    state = graph.invoke(initial_state, cfg)
    for _ in range(max_resumes):
        if "__interrupt__" not in state:
            return state
        state = graph.invoke(Command(resume={"decision": decision, "reason": reason}), cfg)
    assert "__interrupt__" not in state, f"graph still interrupting after {max_resumes} resumes"
    return state


def _trained_auth_detector(auth_record_factory) -> AnomalyDetector:
    rng = random.Random(42)
    vectors = [
        extract_features(auth_record_factory(
            jvm_used=40_000_000.0 + rng.uniform(-500_000, 500_000),
            error_count=max(0.0, rng.uniform(-0.3, 0.3)),
            req_count=10.0 + rng.uniform(-2, 2),
        ))
        for _ in range(50)
    ]
    detector = AnomalyDetector("auth-service")
    detector.train(vectors, training_source_experiment_ids=["exp-1"])
    return detector


def test_workflow_normal_path_never_calls_llm(auth_record_factory, monkeypatch):
    detector = _trained_auth_detector(auth_record_factory)
    ollama_client = MagicMock()
    retrieval_spy = MagicMock(side_effect=AssertionError("retrieval must not run on the normal path"))
    monkeypatch.setattr(workflow.rag_retriever, "retrieve_similar_incidents", retrieval_spy)
    graph = build_graph({"auth-service": detector}, ollama_client)

    normal_record = auth_record_factory(jvm_used=40_000_100.0, req_count=10.0)
    final_state = graph.invoke({"raw_record": normal_record, "incident_id": None}, _thread_config("normal-1"))

    result = final_state["result"]
    assert result["decision"] == "normal"
    assert result["llm_interpretation"] is None
    assert result["anomaly_result"]["status"] == "scored"
    # Week 8 additions must also stay untouched on the normal path - no RAG, no RCA, no remediation.
    assert result["retrieval"] is None
    assert result["root_cause_analysis"] is None
    assert result["remediation_proposal"] is None
    # Week 9: safety/human-approval must also stay untouched on the normal path.
    assert result["safety_check"] is None
    assert result["human_decision"] is None
    ollama_client.is_available.assert_not_called()
    ollama_client.generate.assert_not_called()
    retrieval_spy.assert_not_called()


def test_workflow_anomaly_path_calls_llm_and_includes_interpretation(auth_record_factory):
    detector = _trained_auth_detector(auth_record_factory)
    ollama_client = MagicMock()
    ollama_client.is_available.return_value = True
    ollama_client.model = "llama3.2:1b"
    # The anomaly path now makes two distinct LLM calls (Week 8 extension): interpret_anomaly()
    # first, then the RCA narrative in root_cause_analysis - both go through this same mock's
    # generate(), so the mock must be able to answer either shape depending on which prompt it's
    # given, unlike Week 7's single-call version of this test.
    def _generate(prompt, expect_json=True):
        if "observed_evidence" in prompt:
            return '{"observed_evidence": "jvm_memory_used_bytes is far above baseline", "inference": "consistent with a memory-related fault"}'
        return (
            '{"abnormal_summary": "memory spike", "affected_service": "auth-service", '
            '"significant_metrics": ["jvm_memory_used_bytes"], "evidence": "value far above baseline", '
            '"confidence": "high"}'
        )
    ollama_client.generate.side_effect = _generate
    graph = build_graph({"auth-service": detector}, ollama_client)

    fault_like_record = auth_record_factory(jvm_used=4_000_000_000.0, error_count=50.0, req_count=500.0)
    final_state = _run_to_completion(
        graph, {"raw_record": fault_like_record, "incident_id": "inc-test"}, "anomaly-1",
    )

    result = final_state["result"]
    assert result["decision"] == "anomaly"
    assert result["incident_id"] == "inc-test"
    assert result["llm_interpretation"]["status"] == "interpreted"
    assert result["llm_interpretation"]["affected_service"] == "auth-service"
    assert ollama_client.generate.call_count == 2  # one for interpretation, one for RCA narrative
    # Week 8: RCA and remediation must both be reached and populated on the anomaly path.
    assert result["root_cause_analysis"] is not None
    assert result["root_cause_analysis"]["suspected_root_cause_service"] == "auth-service"
    assert result["root_cause_analysis"]["llm_reasoning"] is not None
    assert result["remediation_proposal"] is not None
    assert result["remediation_proposal"]["requires_human_approval"] is True
    assert result["retrieval"] is not None
    # Week 9: a valid proposal must pass safety and record the (here, approved) human decision.
    assert result["safety_check"]["passed"] is True
    assert result["human_decision"] == "approved"
    assert result["human_decision_reason"] == "looks correct"


def test_workflow_anomaly_path_degrades_gracefully_when_ollama_unavailable(auth_record_factory):
    detector = _trained_auth_detector(auth_record_factory)
    ollama_client = MagicMock()
    ollama_client.is_available.return_value = False
    ollama_client.base_url = "http://localhost:11434"
    ollama_client.model = "llama3.2:1b"
    graph = build_graph({"auth-service": detector}, ollama_client)

    fault_like_record = auth_record_factory(jvm_used=4_000_000_000.0, error_count=50.0, req_count=500.0)
    final_state = _run_to_completion(graph, {"raw_record": fault_like_record, "incident_id": None}, "anomaly-2")

    result = final_state["result"]
    assert result["decision"] == "anomaly"
    assert result["llm_interpretation"]["status"] == "llm_unavailable"
    assert any("LLM interpretation unavailable" in limitation for limitation in result["limitations"])
    # Week 8: the rest of the pipeline (RAG, RCA, remediation) still produces deterministic
    # structured output even with no LLM available at all - it just has no narrative text.
    assert result["retrieval"] is not None
    assert result["root_cause_analysis"] is not None
    assert result["root_cause_analysis"]["llm_reasoning"] is None
    assert result["remediation_proposal"] is not None
    assert result["remediation_proposal"]["requires_human_approval"] is True
    # Week 9: safety validation and human approval still run even with no LLM available.
    assert result["safety_check"]["passed"] is True
    assert result["human_decision"] == "approved"
    ollama_client.generate.assert_not_called()  # never reached generate() at all, on either call site


def test_workflow_insufficient_data_path_skips_detection_and_llm():
    ollama_client = MagicMock()
    graph = build_graph({}, ollama_client)

    broken_record = {
        "timestamp": "2026-01-01T00:00:00+00:00", "service": "auth-service", "correlation_id": "c1",
        "health": None, "metrics": None, "fault": None, "collection_error": "health: Connection refused",
    }
    final_state = graph.invoke({"raw_record": broken_record, "incident_id": None}, _thread_config("insufficient-1"))

    result = final_state["result"]
    assert result["decision"] == "insufficient_data"
    assert result["llm_interpretation"] is None
    assert result["safety_check"] is None
    assert result["human_decision"] is None
    ollama_client.generate.assert_not_called()


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


def test_workflow_anomaly_path_reaches_rca_retrieval_and_remediation_with_correct_fault_signature(payment_record_factory, monkeypatch):
    """End-to-end (mocked LLM/retrieval, real signature matching + remediation) check that the
    full Week 8 extension produces a coherent memory-leak conclusion and matching proposal."""
    detector = _trained_payment_detector(payment_record_factory)
    ollama_client = MagicMock()
    ollama_client.is_available.return_value = False  # keep this test focused on the deterministic path
    ollama_client.base_url = "http://localhost:11434"
    ollama_client.model = "llama3.2:1b"
    graph = build_graph({"payment-service": detector}, ollama_client)

    fault_like_record = payment_record_factory(jvm_used=500_000_000.0, client_duration=100.0)
    final_state = _run_to_completion(graph, {"raw_record": fault_like_record, "incident_id": None}, "anomaly-3")

    result = final_state["result"]
    assert result["decision"] == "anomaly"
    rca = result["root_cause_analysis"]
    assert rca["suspected_fault_type"] == "memory-leak"
    assert rca["suspected_root_cause_service"] == "payment-service"
    assert rca["symptom_service"] == "payment-service"

    remediation = result["remediation_proposal"]
    assert remediation["target_service"] == "payment-service"
    assert remediation["action_category"] == "reset_fault_configuration"
    assert remediation["requires_human_approval"] is True
    # Week 9: the real remediation-proposal templates never assign risk_level="high", so this
    # naturally-produced proposal always passes safety validation (see test_safety_validator.py
    # for the rejection-path rules exercised directly, and the monkeypatched rejection test below
    # for how the graph behaves when a proposal *does* fail).
    assert result["safety_check"]["passed"] is True


def test_workflow_anomaly_path_uses_retrieved_incidents_in_rca(payment_record_factory, monkeypatch):
    retrieved = RetrievalResult(
        query_text="q", source_collection="incident_memory", status="retrieved",
        incidents=[RetrievedIncident(
            incident_id="inc-0037", distance=0.05, fault_type="memory-leak",
            root_cause_service="payment-service", symptom_service="payment-service",
            severity="medium", data_source="real", postmortem_excerpt="payment-service memory leak",
        )],
    )
    monkeypatch.setattr(workflow.rag_retriever, "retrieve_similar_incidents", lambda *a, **k: retrieved)

    detector = _trained_payment_detector(payment_record_factory)
    ollama_client = MagicMock()
    ollama_client.is_available.return_value = False
    ollama_client.base_url = "http://localhost:11434"
    ollama_client.model = "llama3.2:1b"
    graph = build_graph({"payment-service": detector}, ollama_client)

    fault_like_record = payment_record_factory(jvm_used=500_000_000.0, client_duration=100.0)
    final_state = _run_to_completion(graph, {"raw_record": fault_like_record, "incident_id": None}, "anomaly-4")

    result = final_state["result"]
    assert result["retrieval"]["status"] == "retrieved"
    assert result["retrieval"]["incidents"][0]["incident_id"] == "inc-0037"
    assert result["root_cause_analysis"]["determination_method"] == "metric_signature+rag_agreement"
    assert result["root_cause_analysis"]["confidence"] == "high"


def test_workflow_no_model_path_reports_no_model_status(auth_record_factory):
    ollama_client = MagicMock()
    graph = build_graph({}, ollama_client)  # empty registry - no detector for any service

    record = auth_record_factory()
    final_state = graph.invoke({"raw_record": record, "incident_id": None}, _thread_config("no-model-1"))

    result = final_state["result"]
    assert result["anomaly_result"]["status"] == "no_model"
    assert result["decision"] == "insufficient_data"
    assert any("No trained model" in limitation for limitation in result["limitations"])


# --- Week 9: safety_validator / human_approval ------------------------------------------------

def test_workflow_human_approval_interrupts_with_the_expected_payload_and_resumes_on_approval(payment_record_factory):
    detector = _trained_payment_detector(payment_record_factory)
    ollama_client = MagicMock()
    ollama_client.is_available.return_value = False
    ollama_client.base_url = "http://localhost:11434"
    ollama_client.model = "llama3.2:1b"
    graph = build_graph({"payment-service": detector}, ollama_client)
    cfg = _thread_config("interrupt-1")

    fault_like_record = payment_record_factory(jvm_used=500_000_000.0, client_duration=100.0)
    paused_state = graph.invoke({"raw_record": fault_like_record, "incident_id": None}, cfg)

    assert "__interrupt__" in paused_state
    assert "result" not in paused_state  # finalize has not run yet - the graph is genuinely paused
    interrupt_payload = paused_state["__interrupt__"][0].value
    assert interrupt_payload["remediation_proposal"]["target_service"] == "payment-service"
    assert interrupt_payload["safety_check"]["passed"] is True
    assert interrupt_payload["root_cause_analysis"]["suspected_fault_type"] == "memory-leak"

    resumed_state = graph.invoke(Command(resume={"decision": "approved", "reason": "confirmed via dashboard"}), cfg)
    result = resumed_state["result"]
    assert result["human_decision"] == "approved"
    assert result["human_decision_reason"] == "confirmed via dashboard"
    assert not any("rejected" in limitation.lower() for limitation in result["limitations"])


def test_workflow_human_rejection_is_recorded_and_flagged_in_limitations(payment_record_factory):
    detector = _trained_payment_detector(payment_record_factory)
    ollama_client = MagicMock()
    ollama_client.is_available.return_value = False
    ollama_client.base_url = "http://localhost:11434"
    ollama_client.model = "llama3.2:1b"
    graph = build_graph({"payment-service": detector}, ollama_client)
    cfg = _thread_config("interrupt-2")

    fault_like_record = payment_record_factory(jvm_used=500_000_000.0, client_duration=100.0)
    graph.invoke({"raw_record": fault_like_record, "incident_id": None}, cfg)
    resumed_state = graph.invoke(
        Command(resume={"decision": "rejected", "reason": "want to investigate manually first"}), cfg,
    )

    result = resumed_state["result"]
    assert result["human_decision"] == "rejected"
    assert result["human_decision_reason"] == "want to investigate manually first"
    assert any("rejected the proposed remediation" in limitation for limitation in result["limitations"])
    assert any("want to investigate manually first" in limitation for limitation in result["limitations"])


def test_workflow_safety_rejection_skips_human_approval_entirely(payment_record_factory, monkeypatch):
    """A proposal that fails safety validation must never reach `human_approval` - the graph
    should run straight through to `finalize` in a single `.invoke()` call, with no interrupt at
    all. The real remediation-proposal templates never produce a failing proposal today (see
    test_workflow_anomaly_path_reaches_rca_retrieval_and_remediation... above), so this test
    monkeypatches propose_remediation to return one, exactly like test_rag_retriever.py-style
    tests monkeypatch a single collaborator to exercise an otherwise-unreachable branch."""
    unsafe_proposal = RemediationProposal(
        recommended_action="Restart the ledger-service pod immediately.",
        target_service="ledger-service", action_category="release_resources",
        rationale="test", supporting_evidence=[], expected_outcome="test",
        risk_level="high", confidence="high",
    )
    monkeypatch.setattr(workflow.remediation_proposer, "propose_remediation",
                        lambda rca, failed_actions=None: unsafe_proposal)

    detector = _trained_payment_detector(payment_record_factory)
    ollama_client = MagicMock()
    ollama_client.is_available.return_value = False
    ollama_client.base_url = "http://localhost:11434"
    ollama_client.model = "llama3.2:1b"
    graph = build_graph({"payment-service": detector}, ollama_client)
    cfg = _thread_config("safety-reject-1")

    fault_like_record = payment_record_factory(jvm_used=500_000_000.0, client_duration=100.0)
    final_state = graph.invoke({"raw_record": fault_like_record, "incident_id": None}, cfg)

    assert "__interrupt__" not in final_state  # never paused - rejected before human_approval
    result = final_state["result"]
    assert result["safety_check"]["passed"] is False
    assert any("ledger-service" in rule for rule in result["safety_check"]["violated_rules"])
    assert result["human_decision"] is None  # human approval was never reached
    assert any("failed safety validation" in limitation for limitation in result["limitations"])


# --- Week 10: execution, recovery verification, retry loop, feedback --------------------------

def _payment_graph(payment_record_factory, **build_kwargs):
    detector = _trained_payment_detector(payment_record_factory)
    ollama_client = MagicMock()
    ollama_client.is_available.return_value = False
    ollama_client.base_url = "http://localhost:11434"
    ollama_client.model = "llama3.2:1b"
    return build_graph({"payment-service": detector}, ollama_client, **build_kwargs)


def _fault_record(payment_record_factory):
    return payment_record_factory(jvm_used=500_000_000.0, client_duration=100.0)


def test_approved_action_reaches_execution_verification_and_feedback(payment_record_factory):
    graph = _payment_graph(payment_record_factory)

    final_state = _run_to_completion(
        graph, {"raw_record": _fault_record(payment_record_factory), "incident_id": None}, "w10-1",
    )

    result = final_state["result"]
    assert result["human_decision"] == "approved"
    assert result["execution_result"]["status"] == "executed"
    assert result["recovery_verification"]["status"] == "recovered"
    assert result["recovery_verification"]["verified"] is True
    assert result["feedback_result"]["status"] == "written"
    assert result["remediation_attempts"] == 1


def test_mttr_is_measured_from_detection_to_execution_completion(payment_record_factory):
    graph = _payment_graph(payment_record_factory)

    final_state = _run_to_completion(
        graph, {"raw_record": _fault_record(payment_record_factory), "incident_id": None}, "w10-mttr",
    )

    assert final_state["result"]["mttr_seconds"] is not None
    assert final_state["result"]["mttr_seconds"] > 0


def test_a_human_rejection_never_reaches_execution(payment_record_factory):
    """The single most important Week 10 assertion: "rejected" must mean nothing was executed."""
    graph = _payment_graph(payment_record_factory)

    final_state = _run_to_completion(
        graph, {"raw_record": _fault_record(payment_record_factory), "incident_id": None},
        "w10-reject", decision="rejected", reason="not now",
    )

    result = final_state["result"]
    assert result["human_decision"] == "rejected"
    assert result["execution_result"] is None      # execute_action was never entered
    assert result["recovery_verification"] is None
    assert result["remediation_attempts"] == 0
    assert result["feedback_result"]["status"] == "written"  # the rejection is still learned from


def test_safety_rejection_reaches_feedback_but_never_execution(payment_record_factory, monkeypatch):
    unsafe_proposal = RemediationProposal(
        recommended_action="Restart the ledger-service pod immediately.",
        target_service="ledger-service", action_category="restart_service",
        rationale="test", supporting_evidence=[], expected_outcome="test",
        risk_level="high", confidence="high",
    )
    monkeypatch.setattr(workflow.remediation_proposer, "propose_remediation",
                        lambda rca, failed_actions=None: unsafe_proposal)
    graph = _payment_graph(payment_record_factory)

    final_state = graph.invoke(
        {"raw_record": _fault_record(payment_record_factory), "incident_id": None},
        _thread_config("w10-safety"),
    )

    assert "__interrupt__" not in final_state
    result = final_state["result"]
    assert result["safety_check"]["passed"] is False
    assert result["human_decision"] is None
    assert result["execution_result"] is None
    assert result["feedback_result"] is not None  # still recorded


def test_failed_execution_retries_with_a_different_action_and_a_fresh_approval(payment_record_factory, monkeypatch):
    """The Week 8 roadmap's failed-fix retry loop, finally wired: the retry must (a) route back to
    remediation rather than RCA, (b) propose a *different* action, and (c) require its own human
    approval rather than inheriting the previous one."""
    executed_categories: list[str] = []
    approvals: list[str] = []

    def _fail_first_succeed_second(proposal, suspected_fault_type, safety_check, human_decision,
                                   attempts_already_made=0, kubernetes_client=None):
        executed_categories.append(proposal.action_category)
        approvals.append(human_decision)
        action = ExecutableAction(kind="http_fault_reset", target_service=proposal.target_service,
                                  description="d", http_path="/reset-x")
        if len(executed_categories) == 1:
            return ExecutionResult(status="failed", action=action, reason="HTTP 500 from reset endpoint",
                                   started_at="2026-01-01T00:00:00+00:00",
                                   completed_at="2026-01-01T00:00:01+00:00")
        return ExecutionResult(status="executed", action=action, reason="ok",
                               started_at="2026-01-01T00:00:02+00:00",
                               completed_at="2026-01-01T00:00:03+00:00")

    monkeypatch.setattr(workflow, "execute_approved_action", _fail_first_succeed_second)
    graph = _payment_graph(payment_record_factory)

    final_state = _run_to_completion(
        graph, {"raw_record": _fault_record(payment_record_factory), "incident_id": None}, "w10-retry",
    )

    # (b) a different action the second time - the escalation ladder, not a blind repeat
    assert executed_categories == ["reset_fault_configuration", "restart_service"]
    # (c) two separate approvals were required; the second execution did not inherit the first
    assert approvals == ["approved", "approved"]

    result = final_state["result"]
    assert result["remediation_attempts"] == 2
    assert len(result["failed_actions"]) == 1
    assert result["failed_actions"][0]["action_category"] == "reset_fault_configuration"
    assert "HTTP 500" in result["failed_actions"][0]["reason"]
    assert result["execution_result"]["status"] == "executed"


def test_false_recovery_also_triggers_a_retry_and_stops_at_the_attempt_cap(payment_record_factory, monkeypatch):
    """An action that "succeeded" while the fault is still active is a False Recovery, and must be
    escalated exactly like an outright failure - otherwise a reset that changed nothing would be
    recorded as a fix. The attempt cap must then stop the loop."""
    attempts: list[str] = []

    def _always_executes(proposal, suspected_fault_type, safety_check, human_decision,
                         attempts_already_made=0, kubernetes_client=None):
        attempts.append(proposal.action_category)
        return ExecutionResult(
            status="executed", reason="ok",
            action=ExecutableAction(kind="http_fault_reset", target_service=proposal.target_service,
                                    description="d", http_path="/reset-x"),
            started_at="2026-01-01T00:00:00+00:00", completed_at="2026-01-01T00:00:01+00:00")

    def _still_faulty(execution_result, fault_type=None, **kwargs):
        return RecoveryVerification(
            status="fault_still_active", verified=False, attempts=3,
            checked_service=execution_result.action.target_service,
            reason="still faultActive after remediation")

    monkeypatch.setattr(workflow, "execute_approved_action", _always_executes)
    monkeypatch.setattr(workflow.recovery_verify, "verify_recovery", _still_faulty)
    graph = _payment_graph(payment_record_factory)

    final_state = _run_to_completion(
        graph, {"raw_record": _fault_record(payment_record_factory), "incident_id": None},
        "w10-false-recovery", max_resumes=6,
    )

    result = final_state["result"]
    assert result["remediation_attempts"] == config.MAX_REMEDIATION_ATTEMPTS
    assert attempts[0] == "reset_fault_configuration"
    assert attempts[1] == "restart_service"
    assert any("False recovery" in limitation for limitation in result["limitations"])
    assert any("budget" in limitation for limitation in result["limitations"])


def test_evaluation_incident_is_never_written_back_through_the_graph(payment_record_factory, monkeypatch):
    """Rule 22 at the graph level: the incident_id threaded through state is what feedback uses as
    its source ID, so replaying an Evaluation incident must produce a visible refusal."""
    captured = {}

    def _capture(result, source_incident_id=None):
        captured["source_incident_id"] = source_incident_id
        return FeedbackResult(
            status="refused_evaluation_incident",
            reason="Incident belongs to the Evaluation set (CLAUDE.md rule 22).",
            source_incident_id=source_incident_id)

    monkeypatch.setattr(workflow.feedback_writer, "write_back", _capture)
    graph = _payment_graph(payment_record_factory)

    final_state = _run_to_completion(
        graph, {"raw_record": _fault_record(payment_record_factory), "incident_id": "inc-0099"},
        "w10-eval",
    )

    assert captured["source_incident_id"] == "inc-0099"
    result = final_state["result"]
    assert result["feedback_result"]["status"] == "refused_evaluation_incident"
    assert any("Evaluation set incident" in limitation for limitation in result["limitations"])


def test_normal_path_still_reaches_none_of_week_10s_nodes(auth_record_factory):
    detector = _trained_auth_detector(auth_record_factory)
    ollama_client = MagicMock()
    graph = build_graph({"auth-service": detector}, ollama_client)

    final_state = graph.invoke(
        {"raw_record": auth_record_factory(jvm_used=40_000_100.0, req_count=10.0), "incident_id": None},
        _thread_config("w10-normal"),
    )

    result = final_state["result"]
    assert result["decision"] == "normal"
    assert result["execution_result"] is None
    assert result["recovery_verification"] is None
    assert result["feedback_result"] is None
    assert result["mttr_seconds"] is None
    assert result["remediation_attempts"] == 0
