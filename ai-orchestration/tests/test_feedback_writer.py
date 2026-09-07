"""Feedback category: the "learning" write-back, and above all its CLAUDE.md rule 22 guard.

The leakage tests here are the highest-stakes tests in Week 10. If an Evaluation incident can reach
retrieval memory, every number in Week 12 is invalid *and the failure is invisible* - the pipeline
would look like it was working better, not worse. So the guard is tested from both directions: the
writer refuses on its own, and the store refuses even if the writer somehow didn't.

Uses a real isolated ChromaDB (tmp_path-scoped), same pattern as
dataset-tools/tests/test_chroma_store.py - never the project's real collection.
"""
from __future__ import annotations

import pytest

from ai_orchestration import config as ai_config
from ai_orchestration.feedback import writer

ai_config.ensure_sibling_packages_importable()

from dataset_tools import chroma_store, config as dt_config, ingest as dt_ingest  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(dt_config, "CHROMA_DIR", tmp_path / "chroma")
    monkeypatch.setattr(dt_config, "MEMORY_INCIDENTS_FILE", tmp_path / "memory_incidents.jsonl")
    monkeypatch.setattr(dt_config, "EVALUATION_INCIDENTS_FILE", tmp_path / "evaluation_incidents.jsonl")
    monkeypatch.setattr(dt_config, "CHROMA_COLLECTION_NAME", f"test_feedback_{tmp_path.name}")
    monkeypatch.setattr(ai_config, "FEEDBACK_ENABLED", True)
    return tmp_path


def _write_evaluation_set(*incident_ids: str) -> None:
    dt_ingest.write_jsonl(
        [{"incident_id": i, "fault_type": "memory-leak", "root_cause_service": "payment-service",
          "symptom_service": "payment-service", "severity": "medium", "data_source": "real",
          "postmortem_text": f"Incident {i}"} for i in incident_ids],
        dt_config.EVALUATION_INCIDENTS_FILE,
    )


def _completed_result(**overrides) -> dict:
    result = {
        "decision": "anomaly",
        "service": "payment-service",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "anomaly_result": {"anomaly_score": 0.42},
        "root_cause_analysis": {
            "suspected_fault_type": "memory-leak",
            "suspected_root_cause_service": "payment-service",
            "symptom_service": "payment-service",
            "determination_method": "metric_signature",
            "confidence": "high",
            "evidence_summary": "jvm_memory_used_bytes far above baseline",
            "metric_anomalies": [{"metric_name": "jvm_memory_used_bytes", "observed_value": 5.0e8,
                                  "deviation_description": "above documented baseline"}],
        },
        "remediation_proposal": {"recommended_action": "Reset the memory-leak fault injection.",
                                 "action_category": "reset_fault_configuration", "risk_level": "medium"},
        "safety_check": {"passed": True},
        "human_decision": "approved",
        "execution_result": {"status": "executed", "reason": "executed"},
        "recovery_verification": {"status": "recovered", "reason": "recovered", "verified": True},
    }
    result.update(overrides)
    return result


# --- CLAUDE.md rule 22: Evaluation incidents must never enter RAG memory -----------------------

def test_refuses_to_write_back_an_evaluation_incident():
    """The Week 12 scenario: replaying an Evaluation-set incident through the full pipeline must
    teach the retrieval memory nothing about it."""
    _write_evaluation_set("inc-0099")

    result = writer.write_back(_completed_result(), source_incident_id="inc-0099")

    assert result.status == "refused_evaluation_incident"
    assert result.written_incident_id is None
    assert "rule 22" in result.reason

    collection = chroma_store.get_client().get_or_create_collection(dt_config.CHROMA_COLLECTION_NAME)
    assert collection.count() == 0  # nothing was written, not even transiently


def test_store_layer_also_refuses_an_evaluation_derived_write():
    """Independent second layer: even called directly, bypassing the writer's own check, the store
    refuses. Either layer alone would be sufficient - both exist because a silent leak here would
    invalidate Week 12 without producing any visible error."""
    _write_evaluation_set("inc-0099")

    with pytest.raises(chroma_store.EvaluationLeakageError, match="rule 22"):
        chroma_store.add_pipeline_run_incident(
            "run-abc123", "some postmortem text", {"source_incident_id": "inc-0099"},
        )


def test_store_refuses_a_sourced_write_when_the_evaluation_file_is_missing():
    """`ingest.read_jsonl` returns [] for a missing file, which would silently turn "I cannot check"
    into "nothing to check". A run derived from a known dataset incident must fail closed instead."""
    assert not dt_config.EVALUATION_INCIDENTS_FILE.exists()

    with pytest.raises(chroma_store.EvaluationLeakageError, match="does not exist"):
        chroma_store.add_pipeline_run_incident(
            "run-abc123", "text", {"source_incident_id": "inc-0042"},
        )


def test_store_refuses_to_write_under_a_non_pipeline_run_id():
    """Prevents a caller from writing under a raw Memory/Evaluation incident ID at all."""
    with pytest.raises(chroma_store.EvaluationLeakageError, match="prefixed with"):
        chroma_store.add_pipeline_run_incident("inc-0042", "text", {})


def test_a_fresh_live_incident_with_no_source_id_is_written():
    """A live anomaly that did not come from the dataset cannot be an Evaluation incident, so the
    "learning" path genuinely works - the guard is not a blanket ban on write-back."""
    result = writer.write_back(_completed_result(), source_incident_id=None)

    assert result.status == "written"
    assert result.written_incident_id.startswith(dt_config.PIPELINE_RUN_ID_PREFIX)

    collection = chroma_store.get_client().get_or_create_collection(dt_config.CHROMA_COLLECTION_NAME)
    assert collection.count() == 1


def test_written_incident_is_retrievable_with_the_same_metadata_shape_as_a_seed_incident():
    """A learned incident has to be usable by rag/retriever.py exactly like a Week 6 seed incident,
    or retrieval would break the first time one is returned."""
    writer.write_back(_completed_result(), source_incident_id=None)

    collection = chroma_store.get_client().get_or_create_collection(dt_config.CHROMA_COLLECTION_NAME)
    metadata = collection.get()["metadatas"][0]

    for key in ("incident_id", "fault_type", "root_cause_service", "symptom_service", "severity",
                "data_source"):
        assert key in metadata, f"retrieval reads metadata[{key!r}] off every document"
    assert metadata["data_source"] == "pipeline_run"   # provenance is never hidden
    assert metadata["fault_type"] == "memory-leak"
    assert all(isinstance(v, (str, int, float, bool)) for v in metadata.values())


# --- Behavior ---------------------------------------------------------------------------------

def test_disabled_feedback_writes_nothing(monkeypatch):
    """Week 12 disables write-back so retrieval memory stays frozen and identical for the
    multi-agent pipeline and the single-agent baseline."""
    monkeypatch.setattr(ai_config, "FEEDBACK_ENABLED", False)

    result = writer.write_back(_completed_result(), source_incident_id=None)

    assert result.status == "disabled"
    collection = chroma_store.get_client().get_or_create_collection(dt_config.CHROMA_COLLECTION_NAME)
    assert collection.count() == 0


def test_non_anomaly_runs_are_not_written_back():
    result = writer.write_back(
        _completed_result(decision="normal", root_cause_analysis=None), source_incident_id=None,
    )
    assert result.status == "skipped_incomplete"


def test_a_failed_remediation_is_still_learned_from_and_recorded_honestly():
    """"This was tried here and it did not work" is at least as useful to a future retrieval as a
    success, and must not be rewritten into one."""
    result = writer.write_back(
        _completed_result(
            execution_result={"status": "failed", "reason": "HTTP 500 from reset endpoint"},
            recovery_verification={"status": "fault_still_active", "verified": False,
                                   "reason": "still faultActive after remediation"},
        ),
        source_incident_id=None,
    )

    assert result.status == "written"
    assert "failed" in result.postmortem_text
    assert "fault_still_active" in result.postmortem_text

    collection = chroma_store.get_client().get_or_create_collection(dt_config.CHROMA_COLLECTION_NAME)
    assert collection.get()["metadatas"][0]["recovery_verified"] is False


def test_postmortem_text_records_a_human_rejection_rather_than_implying_a_fix():
    result = writer.write_back(
        _completed_result(human_decision="rejected", execution_result=None,
                          recovery_verification=None),
        source_incident_id=None,
    )
    assert result.status == "written"
    assert "rejected the proposed remediation" in result.postmortem_text
    assert "nothing was executed" in result.postmortem_text


def test_write_failure_is_reported_not_raised(monkeypatch):
    """A feedback failure must never invalidate a remediation that already happened."""
    def _boom(*args, **kwargs):
        raise RuntimeError("chroma exploded")
    monkeypatch.setattr(chroma_store, "add_pipeline_run_incident", _boom)

    result = writer.write_back(_completed_result(), source_incident_id=None)

    assert result.status == "failed"
    assert "chroma exploded" in result.reason
