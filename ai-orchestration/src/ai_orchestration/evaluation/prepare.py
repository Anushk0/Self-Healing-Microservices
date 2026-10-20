"""Prepares an evaluation environment on a machine that has no captured raw telemetry.

Week 7's normal training path (`anomaly/train.py`) learns from raw per-cycle telemetry produced by
live experiments against running services. On a machine with no cluster there is no such telemetry,
so nothing can be trained and every incident would score `no_model` - which would make an
evaluation impossible rather than merely weaker.

This module trains the same per-service Isolation Forests from the **Memory-set incidents' own
non-faulty phase metrics** instead: `baseline_metrics` (NORMAL) and `recovery_metrics` (RECOVERY,
i.e. after the fault was reset). Both represent the system behaving normally, which is exactly the
selection rule `feature_extractor.load_memory_eligible_baseline_records()` already applies.

Two properties are preserved exactly, because they are what make the evaluation meaningful:

- **The Memory/Evaluation boundary.** `EVALUATION_INCIDENTS_FILE` is never read here. It is not
  imported, not opened, and there is no parameter that could point this at it - the same one-way
  discipline `chroma_store.build_collection()` uses.
- **No fault-window data.** Only NORMAL and RECOVERY phases are used, so a detector never learns
  what a fault looks like; it learns only what normal looks like, and flags departures.

The honest difference from Week 7's path, which any results table using these models must state:
these vectors are *per-incident phase averages*, not raw per-cycle observations, so each incident
contributes one point per phase rather than dozens. The sample count per service lands in the same
range as Week 7's real training run (~40), but the variance structure is smoother than live
telemetry's.
"""
from __future__ import annotations

import logging

from .. import config as ai_config
from ..anomaly.detector import AnomalyDetector
from ..anomaly.models import FeatureVector

ai_config.ensure_sibling_packages_importable()

from dataset_tools import config as dt_config, ingest as dt_ingest  # noqa: E402

log = logging.getLogger("ai_orchestration.evaluation.prepare")

# Which phases may train a detector. Sourced from config so the choice is one documented
# switch rather than a literal buried here; see ai_config.TRAINING_PHASES for why the
# shipped default (NORMAL + RECOVERY) is known to be wrong but kept for reproducibility.
NON_FAULTY_PHASES = tuple(ai_config.TRAINING_PHASES)


def _load_memory_incidents() -> list[dict]:
    """Reads the Memory file and only the Memory file (see module docstring)."""
    incidents = dt_ingest.read_jsonl(dt_config.MEMORY_INCIDENTS_FILE)
    if not incidents:
        raise RuntimeError(
            f"No Memory incidents at {dt_config.MEMORY_INCIDENTS_FILE} - build the dataset first "
            f"(python -m dataset_tools.build_dataset)."
        )
    return incidents


def build_training_vectors(service: str, memory_incidents: list[dict]) -> list[FeatureVector]:
    schema = ai_config.SERVICE_FEATURE_SCHEMAS[service]
    vectors: list[FeatureVector] = []

    for incident in memory_incidents:
        for phase_key in NON_FAULTY_PHASES:
            metrics = (incident.get(phase_key) or {}).get(service)
            if not metrics:
                continue
            values = [metrics.get(name) for name in schema]
            if any(v is None for v in values):
                # Same rule as feature_extractor: a partial observation is skipped, never imputed.
                continue
            vectors.append(FeatureVector(
                service=service,
                timestamp=incident.get("start_time") or "",
                correlation_id=f"prepare-{incident.get('incident_id')}-{phase_key}",
                feature_names=list(schema),
                values=[float(v) for v in values],
                raw_metrics={name: metrics.get(name) for name in schema},
            ))
    return vectors


def train_from_memory_set(save: bool = True) -> dict[str, dict]:
    """Trains and (by default) persists one detector per service. Returns a per-service summary
    including the sample count, so a thin training set is visible rather than implicit."""
    memory_incidents = _load_memory_incidents()
    memory_ids = sorted(inc["incident_id"] for inc in memory_incidents)
    summary: dict[str, dict] = {}

    for service in ai_config.SERVICE_FEATURE_SCHEMAS:
        vectors = build_training_vectors(service, memory_incidents)
        if not vectors:
            summary[service] = {"trained": False, "reason": "no complete non-faulty observations"}
            log.warning("No training vectors for '%s'", service)
            continue

        detector = AnomalyDetector(service)
        metadata = detector.train(vectors, training_source_experiment_ids=["memory-set-phase-averages"])
        if save:
            detector.save()
        summary[service] = {
            "trained": True,
            "training_sample_count": metadata.training_sample_count,
            "feature_count": len(metadata.feature_names),
            "source": "Memory-set NORMAL + RECOVERY phase averages (no Evaluation data, no fault windows)",
        }
        log.info("Trained '%s' on %d vectors", service, metadata.training_sample_count)

    summary["_provenance"] = {
        "memory_incident_count": len(memory_incidents),
        "memory_incident_ids": memory_ids,
        "evaluation_file_read": False,
        "phases_used": list(NON_FAULTY_PHASES),
    }
    return summary


if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")
    print(json.dumps(train_from_memory_set(), indent=2))
