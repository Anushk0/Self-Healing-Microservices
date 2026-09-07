"""Categories 10 (ChromaDB indexing only Memory records), 11 (evaluation IDs rejected from
indexing), 12 (retrieval returns valid Memory incidents), and 14 (handling unavailable
services/collection errors).

Uses a real local ChromaDB PersistentClient pointed at a pytest tmp_path (not mocked) - Week 6's
ChromaDB usage is entirely local/offline once the bundled embedding model is cached, so this is
fast and hermetic per test.
"""
from __future__ import annotations

import pytest

from dataset_tools import chroma_store, config, ingest


def _incident(incident_id: str, fault_type: str = "memory-leak", text: str | None = None) -> dict:
    return {
        "incident_id": incident_id,
        "fault_type": fault_type,
        "root_cause_service": "payment-service",
        "symptom_service": "payment-service",
        "severity": "medium",
        "data_source": "synthetic",
        "postmortem_text": text or f"Incident {incident_id}: {fault_type} on payment-service, JVM heap usage rising.",
    }


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHROMA_DIR", tmp_path / "chroma")
    monkeypatch.setattr(config, "MEMORY_INCIDENTS_FILE", tmp_path / "memory_incidents.jsonl")
    monkeypatch.setattr(config, "EVALUATION_INCIDENTS_FILE", tmp_path / "evaluation_incidents.jsonl")
    monkeypatch.setattr(config, "CHROMA_COLLECTION_NAME", f"test_collection_{tmp_path.name}")
    return tmp_path


def test_build_collection_indexes_only_memory_incidents(isolated_store):
    memory = [_incident("mem-1"), _incident("mem-2")]
    evaluation = [_incident("eval-1"), _incident("eval-2")]
    ingest.write_jsonl(memory, config.MEMORY_INCIDENTS_FILE)
    ingest.write_jsonl(evaluation, config.EVALUATION_INCIDENTS_FILE)

    collection = chroma_store.build_collection()

    assert collection.count() == 2
    assert set(collection.get()["ids"]) == {"mem-1", "mem-2"}


def test_build_collection_raises_if_evaluation_id_leaks_into_memory_file(isolated_store):
    """Defense-in-depth check: even if the Memory file were accidentally contaminated with an
    incident ID that also appears in the Evaluation file, build_collection must refuse rather than
    silently index it - this is the code-level enforcement the task requires, not just convention."""
    memory = [_incident("mem-1"), _incident("shared-id")]  # "shared-id" wrongly present in both
    evaluation = [_incident("eval-1"), _incident("shared-id")]
    ingest.write_jsonl(memory, config.MEMORY_INCIDENTS_FILE)
    ingest.write_jsonl(evaluation, config.EVALUATION_INCIDENTS_FILE)

    with pytest.raises(chroma_store.EvaluationLeakageError):
        chroma_store.build_collection()


def test_validate_no_leakage_passes_on_clean_split(isolated_store):
    ingest.write_jsonl([_incident("mem-1")], config.MEMORY_INCIDENTS_FILE)
    ingest.write_jsonl([_incident("eval-1")], config.EVALUATION_INCIDENTS_FILE)
    chroma_store.build_collection()

    assert chroma_store.validate_no_leakage() is True


def test_query_similar_returns_only_memory_incident_ids(isolated_store):
    memory = [
        _incident("mem-mem-leak", fault_type="memory-leak", text="payment-service JVM heap memory usage rising steadily during the fault window"),
        _incident("mem-db-lock", fault_type="db-lock", text="ledger-service HikariCP connection pool exhausted, connections held"),
    ]
    evaluation = [_incident("eval-1", fault_type="memory-leak")]
    ingest.write_jsonl(memory, config.MEMORY_INCIDENTS_FILE)
    ingest.write_jsonl(evaluation, config.EVALUATION_INCIDENTS_FILE)
    chroma_store.build_collection()

    results = chroma_store.query_similar("JVM heap memory usage growing on payment service", n_results=2)

    memory_ids = {"mem-mem-leak", "mem-db-lock"}
    assert all(r["incident_id"] in memory_ids for r in results)
    assert results[0]["incident_id"] == "mem-mem-leak"  # closer semantic match should rank first


def test_query_similar_never_returns_evaluation_ids(isolated_store):
    memory = [_incident("mem-1", text="payment service memory leak, JVM heap grew")]
    evaluation = [_incident("eval-1", text="payment service memory leak, JVM heap grew")]  # near-identical text
    ingest.write_jsonl(memory, config.MEMORY_INCIDENTS_FILE)
    ingest.write_jsonl(evaluation, config.EVALUATION_INCIDENTS_FILE)
    chroma_store.build_collection()

    results = chroma_store.query_similar("payment service memory leak JVM heap", n_results=5)

    assert all(r["incident_id"] != "eval-1" for r in results)


def test_build_collection_raises_clear_error_when_memory_file_missing(isolated_store):
    # config.MEMORY_INCIDENTS_FILE was never written - simulates the dataset build step having
    # been skipped, which must fail loudly rather than silently building an empty collection.
    with pytest.raises(RuntimeError, match="No memory incidents found"):
        chroma_store.build_collection()


def test_validate_no_leakage_tolerates_missing_evaluation_file(isolated_store):
    # Evaluation file absent (e.g. not yet generated) must not crash validation - there is simply
    # nothing to check leakage against yet.
    ingest.write_jsonl([_incident("mem-1")], config.MEMORY_INCIDENTS_FILE)
    chroma_store.build_collection()

    assert chroma_store.validate_no_leakage() is True


# --- Week 10: feedback write-back of incidents the pipeline itself learned ---------------------

def test_pipeline_run_incident_is_added_alongside_the_memory_seeds(isolated_store):
    ingest.write_jsonl([_incident("mem-1")], config.MEMORY_INCIDENTS_FILE)
    collection = chroma_store.build_collection()

    chroma_store.add_pipeline_run_incident(
        "run-abc123", "Incident run-abc123: memory-leak on payment-service, heap rising.",
        {"incident_id": "run-abc123", "fault_type": "memory-leak",
         "root_cause_service": "payment-service", "symptom_service": "payment-service",
         "severity": "unknown", "data_source": "pipeline_run", "source_incident_id": ""},
    )

    assert collection.count() == 2
    assert set(collection.get()["ids"]) == {"mem-1", "run-abc123"}


def test_validate_no_leakage_accepts_learned_pipeline_runs_but_still_rejects_evaluation_ids(isolated_store):
    """A learned incident is legitimately not in the Week 6 Memory file. Validation has to tell
    that apart from an actual leak, without weakening the Evaluation check itself."""
    ingest.write_jsonl([_incident("mem-1")], config.MEMORY_INCIDENTS_FILE)
    ingest.write_jsonl([_incident("eval-1")], config.EVALUATION_INCIDENTS_FILE)
    collection = chroma_store.build_collection()

    chroma_store.add_pipeline_run_incident(
        "run-abc123", "text", {"incident_id": "run-abc123", "data_source": "pipeline_run"},
    )
    assert chroma_store.validate_no_leakage() is True

    # An Evaluation incident forced in by any other means must still be caught.
    collection.add(ids=["eval-1"], documents=["leaked"], metadatas=[{"incident_id": "eval-1"}])
    with pytest.raises(chroma_store.EvaluationLeakageError):
        chroma_store.validate_no_leakage()


def test_validate_no_leakage_still_rejects_an_arbitrary_unprefixed_id(isolated_store):
    """The prefix allowance must be narrow: anything that is neither a Memory seed nor an
    explicitly-prefixed pipeline run is still an unexplained ID and must fail validation."""
    ingest.write_jsonl([_incident("mem-1")], config.MEMORY_INCIDENTS_FILE)
    collection = chroma_store.build_collection()

    collection.add(ids=["mystery-1"], documents=["?"], metadatas=[{"incident_id": "mystery-1"}])

    with pytest.raises(chroma_store.EvaluationLeakageError, match="not present in the Memory set"):
        chroma_store.validate_no_leakage()


def test_add_pipeline_run_incident_refuses_an_evaluation_derived_write(isolated_store):
    """CLAUDE.md rule 22 at the store layer - the check that matters during Week 12, when
    Evaluation incidents are deliberately replayed through the full pipeline."""
    ingest.write_jsonl([_incident("mem-1")], config.MEMORY_INCIDENTS_FILE)
    ingest.write_jsonl([_incident("eval-1")], config.EVALUATION_INCIDENTS_FILE)
    collection = chroma_store.build_collection()

    with pytest.raises(chroma_store.EvaluationLeakageError, match="rule 22"):
        chroma_store.add_pipeline_run_incident(
            "run-abc123", "text", {"source_incident_id": "eval-1"},
        )

    assert collection.count() == 1  # nothing was written, not even transiently


def test_add_pipeline_run_incident_fails_closed_when_the_evaluation_file_is_missing(isolated_store):
    """`ingest.read_jsonl` returns [] for a missing file, so an unreadable Evaluation set would
    otherwise turn "cannot verify" into a silent "nothing to verify"."""
    ingest.write_jsonl([_incident("mem-1")], config.MEMORY_INCIDENTS_FILE)
    chroma_store.build_collection()
    assert not config.EVALUATION_INCIDENTS_FILE.exists()

    with pytest.raises(chroma_store.EvaluationLeakageError, match="does not exist"):
        chroma_store.add_pipeline_run_incident("run-abc123", "text", {"source_incident_id": "inc-1"})


def test_add_pipeline_run_incident_refuses_a_non_prefixed_id(isolated_store):
    ingest.write_jsonl([_incident("mem-1")], config.MEMORY_INCIDENTS_FILE)
    chroma_store.build_collection()

    with pytest.raises(chroma_store.EvaluationLeakageError, match="prefixed with"):
        chroma_store.add_pipeline_run_incident("inc-0042", "text", {})
