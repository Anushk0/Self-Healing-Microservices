"""Local, persistent ChromaDB retrieval memory - built from the Memory incident set ONLY.

Architectural leakage prevention: `build_collection()` never opens `config.EVALUATION_INCIDENTS_FILE`
- it is not even imported into this module's namespace. The one function that reads incidents for
indexing (`_load_memory_incidents`) hard-codes `config.MEMORY_INCIDENTS_FILE` as its source.

Defense-in-depth: `build_collection()` additionally loads the evaluation incident IDs (read-only,
for comparison purposes only - never embedded) and asserts none of them ended up in the collection
before returning, so a future refactor that accidentally widens the source file would fail loudly
here rather than silently leaking evaluation data into retrieval memory.

Embedding: uses ChromaDB's bundled default embedding function (a small local ONNX model,
downloaded once and cached locally on first use, no network calls after that and no cloud API
dependency) - see dataset-tools/README.md "ChromaDB" section for why this satisfies the "simple
local embedding approach compatible with...future Ollama/LangGraph work" requirement without
introducing a second, conflicting local-model dependency ahead of Week 7.
"""
from __future__ import annotations

import logging

import chromadb

from . import config, ingest

log = logging.getLogger("dataset_tools.chroma_store")


class EvaluationLeakageError(RuntimeError):
    """Raised if an evaluation incident ID is ever found in the Memory collection."""


def _load_memory_incidents() -> list[dict]:
    return ingest.read_jsonl(config.MEMORY_INCIDENTS_FILE)


def _load_evaluation_ids_for_leakage_check() -> set[str]:
    """Reads evaluation incident IDs only - for the post-build leakage assertion below. Never
    reads postmortem/summary text, and nothing here is ever passed to the embedding function."""
    return {inc["incident_id"] for inc in ingest.read_jsonl(config.EVALUATION_INCIDENTS_FILE)}


def get_client() -> chromadb.ClientAPI:
    config.CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(config.CHROMA_DIR))


def _metadata_for(incident: dict) -> dict:
    severity = incident.get("severity")
    return {
        "incident_id": incident["incident_id"],
        "fault_type": incident["fault_type"],
        "root_cause_service": incident["root_cause_service"],
        "symptom_service": incident["symptom_service"],
        "severity": severity if severity is not None else "unknown",
        "data_source": incident["data_source"],
    }


def build_collection(reset: bool = True) -> chromadb.Collection:
    client = get_client()

    if reset:
        try:
            client.delete_collection(config.CHROMA_COLLECTION_NAME)
        except Exception:  # noqa: BLE001 - collection may not exist yet
            pass

    collection = client.get_or_create_collection(
        name=config.CHROMA_COLLECTION_NAME,
        metadata={"description": "Week 6 Memory-set incident retrieval collection (Memory incidents only)"},
    )

    memory_incidents = _load_memory_incidents()
    if not memory_incidents:
        raise RuntimeError(f"No memory incidents found at {config.MEMORY_INCIDENTS_FILE} - build the dataset first")

    # Checked BEFORE add() so a contaminated Memory file (an ID that also appears in the
    # Evaluation file - which should never happen given split.py, but this is the code-level
    # enforcement the task requires) is rejected without ever being written to the collection,
    # even transiently.
    memory_ids = {inc["incident_id"] for inc in memory_incidents}
    evaluation_ids = _load_evaluation_ids_for_leakage_check()
    overlap = memory_ids & evaluation_ids
    if overlap:
        raise EvaluationLeakageError(f"Incident IDs present in both Memory and Evaluation files: {sorted(overlap)}")

    collection.add(
        ids=[inc["incident_id"] for inc in memory_incidents],
        documents=[inc["postmortem_text"] for inc in memory_incidents],
        metadatas=[_metadata_for(inc) for inc in memory_incidents],
    )

    _assert_no_evaluation_leakage(collection)
    log.info("Built ChromaDB collection '%s' with %d Memory incidents", config.CHROMA_COLLECTION_NAME, collection.count())
    return collection


def _assert_no_evaluation_leakage(collection: chromadb.Collection) -> None:
    evaluation_ids = _load_evaluation_ids_for_leakage_check()
    if not evaluation_ids:
        return
    indexed = collection.get(ids=list(evaluation_ids))
    leaked = set(indexed.get("ids") or [])
    if leaked:
        raise EvaluationLeakageError(f"Evaluation incident IDs found in ChromaDB collection: {sorted(leaked)}")


def add_pipeline_run_incident(incident_id: str, postmortem_text: str, metadata: dict) -> None:
    """Week 10 feedback write-back: adds ONE incident learned from a completed pipeline run to the
    same collection Week 6 seeded.

    This is the only write path into the collection other than `build_collection()`, deliberately -
    keeping every write in this module means CLAUDE.md rule 22 ("Never allow Evaluation Set
    incidents into RAG memory") is enforced in exactly one place. Three checks, all fail-closed,
    all *before* anything is embedded or written:

    1. The new ID must carry `config.PIPELINE_RUN_ID_PREFIX`, so a caller can never write under a
       raw Memory/Evaluation incident ID (accidentally or otherwise).
    2. If this run came from a known dataset incident (`metadata["source_incident_id"]`), that
       source ID must not be an Evaluation incident. This is the check that actually matters
       during Week 12: replaying Evaluation incidents through the pipeline must never teach the
       retrieval memory about them.
    3. If a source ID is present but the Evaluation file cannot be read, the write is REFUSED
       rather than allowed. An unreadable Evaluation file makes check 2 unanswerable, and
       `ingest.read_jsonl` returns `[]` for a missing file - which would otherwise turn "I cannot
       check" into a silent "nothing to check".
    """
    if not incident_id.startswith(config.PIPELINE_RUN_ID_PREFIX):
        raise EvaluationLeakageError(
            f"Refusing to write incident_id={incident_id!r}: feedback write-back may only add IDs "
            f"prefixed with {config.PIPELINE_RUN_ID_PREFIX!r}."
        )

    source_incident_id = metadata.get("source_incident_id")
    if source_incident_id:
        if not config.EVALUATION_INCIDENTS_FILE.exists():
            raise EvaluationLeakageError(
                f"Refusing to write a pipeline-run incident derived from source incident "
                f"{source_incident_id!r}: the Evaluation set file "
                f"({config.EVALUATION_INCIDENTS_FILE}) does not exist, so it cannot be proven that "
                f"this is not an Evaluation incident."
            )
        if source_incident_id in _load_evaluation_ids_for_leakage_check():
            raise EvaluationLeakageError(
                f"Refusing to write a pipeline-run incident derived from Evaluation incident "
                f"{source_incident_id!r} - Evaluation incidents must never enter RAG memory "
                f"(CLAUDE.md rule 22)."
            )

    client = get_client()
    collection = client.get_or_create_collection(name=config.CHROMA_COLLECTION_NAME)
    collection.add(ids=[incident_id], documents=[postmortem_text], metadatas=[metadata])
    log.info("Wrote pipeline-run incident '%s' into collection '%s'", incident_id, config.CHROMA_COLLECTION_NAME)


def validate_no_leakage() -> bool:
    """Standalone validation entry point - re-runs the same leakage check against whatever
    collection currently exists on disk, without rebuilding it. Returns True if clean."""
    client = get_client()
    collection = client.get_or_create_collection(name=config.CHROMA_COLLECTION_NAME)
    _assert_no_evaluation_leakage(collection)

    memory_ids = {inc["incident_id"] for inc in _load_memory_incidents()}
    all_indexed_ids = set(collection.get()["ids"])
    # Week 10: incidents the pipeline itself learned are legitimately not in the Week 6 Memory
    # file, but they are still not allowed to be Evaluation incidents - which the
    # `_assert_no_evaluation_leakage` call above has already proven for the whole collection,
    # and which `add_pipeline_run_incident` enforces at write time by source ID as well.
    extra = {i for i in all_indexed_ids - memory_ids if not i.startswith(config.PIPELINE_RUN_ID_PREFIX)}
    if extra:
        raise EvaluationLeakageError(f"Collection contains IDs not present in the Memory set: {sorted(extra)}")

    learned = sum(1 for i in all_indexed_ids if i.startswith(config.PIPELINE_RUN_ID_PREFIX))
    log.info(
        "Leakage validation passed: %d indexed IDs (%d Week 6 Memory seeds + %d learned pipeline "
        "runs), none from the Evaluation set",
        len(all_indexed_ids), len(all_indexed_ids) - learned, learned,
    )
    return True


def query_similar(query_text: str, n_results: int = 3) -> list[dict]:
    client = get_client()
    collection = client.get_or_create_collection(name=config.CHROMA_COLLECTION_NAME)
    result = collection.query(query_texts=[query_text], n_results=n_results)

    matches = []
    ids = result["ids"][0]
    documents = result["documents"][0]
    metadatas = result["metadatas"][0]
    distances = result["distances"][0]
    for i in range(len(ids)):
        matches.append({
            "incident_id": ids[i],
            "distance": distances[i],
            "metadata": metadatas[i],
            "postmortem_text": documents[i],
        })
    return matches


if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    if len(sys.argv) > 1 and sys.argv[1] == "validate":
        validate_no_leakage()
    elif len(sys.argv) > 1 and sys.argv[1] == "query":
        query_text = " ".join(sys.argv[2:]) or "payment service returning errors"
        print(json.dumps(query_similar(query_text), indent=2))
    else:
        build_collection()
