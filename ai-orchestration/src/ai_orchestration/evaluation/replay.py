"""Turns a stored Evaluation-set incident back into the one raw telemetry record a monitoring
system would actually have seen at the moment of the incident.

Both arms are fed the identical record produced here, so any difference between them comes from
the diagnosis rather than from the input.

Three decisions worth stating, because each one could quietly bias the results:

1. **Which service's record?** The *symptom* service - `incident["symptom_service"]`. That is where
   the signal is visible; a real monitor has no way to know in advance that the cause is elsewhere.
   For `auth-key-error` and `notification-latency` this means the pipeline is handed
   payment-service's telemetry and must still name a different service as the root cause, which is
   precisely the capability this project set out to test.

2. **Which phase?** `fault_metrics` - the incident's own measured FAULT_ACTIVE-window averages.
   Nothing is invented here; the values are what Week 6 reconstructed from the experiment.

3. **The self-reported fault flag is deliberately withheld** (`fault: None`). Each service exposes
   `/fault-status`, which says outright whether its own fault injection is on - for a same-service
   fault that is effectively the answer key. Including it would let a system "diagnose" db-lock and
   memory-leak by reading a debug endpoint, and would flatter both arms unequally (the multi-agent
   RCA puts it in its evidence bundle; the single-agent prompt does not). A real unknown incident
   does not come with a flag saying which fault it is, so neither does a replayed one.
"""
from __future__ import annotations

from typing import Optional

from .. import config as ai_config

ai_config.ensure_sibling_packages_importable()

from dataset_tools import config as dt_config  # noqa: E402


class ReplayError(RuntimeError):
    """Raised when an incident cannot produce a usable record - never papered over with defaults,
    since a fabricated record would be scored as though it were real evidence."""


def incident_to_raw_record(incident: dict, service: Optional[str] = None) -> dict:
    """`service` overrides which service's telemetry to replay; it defaults to the symptom service
    and exists so a future experiment can ask "what if we had looked at a different service?"."""
    service = service or incident.get("symptom_service")
    if not service:
        raise ReplayError(f"Incident {incident.get('incident_id')!r} has no symptom_service.")

    fault_metrics = (incident.get("fault_metrics") or {}).get(service)
    if not fault_metrics:
        raise ReplayError(
            f"Incident {incident.get('incident_id')!r} has no fault-window metrics for "
            f"'{service}' - refusing to synthesise them."
        )

    phases = incident.get("phases") or {}
    timestamp = (phases.get("FAULT_ACTIVE") or {}).get("start") or incident.get("start_time")

    return {
        "timestamp": timestamp,
        "service": service,
        "correlation_id": f"eval-{incident.get('incident_id')}",
        "health": {"status": "UP", "components": {}},
        "metrics": dict(fault_metrics),
        # See this module's docstring, decision 3.
        "fault": None,
        "collection_error": None,
    }


def ground_truth(incident: dict) -> dict:
    """The labels an arm is scored against. `root_cause_service` is the Week 5 roadmap's own
    ground-truth label ("manually set root_cause_service as ground truth since you know which
    fault you injected"), and is cross-checked here against Week 6's FAULT_SERVICE_MAP so a
    mislabelled incident fails loudly rather than silently scoring every arm wrong."""
    fault_type = incident.get("fault_type")
    expected = dt_config.FAULT_SERVICE_MAP.get(fault_type)
    if expected is None:
        raise ReplayError(f"Incident {incident.get('incident_id')!r} has unknown fault_type {fault_type!r}.")

    root = incident.get("root_cause_service")
    symptom = incident.get("symptom_service")
    if root != expected["root_cause_service"] or symptom != expected["symptom_service"]:
        raise ReplayError(
            f"Incident {incident.get('incident_id')!r} labels ({root}, {symptom}) disagree with "
            f"FAULT_SERVICE_MAP for '{fault_type}' "
            f"({expected['root_cause_service']}, {expected['symptom_service']})."
        )

    return {
        "fault_type": fault_type,
        "root_cause_service": root,
        "symptom_service": symptom,
        "is_cross_service": root != symptom,
    }


def load_evaluation_incidents() -> list[dict]:
    """Reads the Evaluation set, and *only* the Evaluation set. The Memory file is never opened by
    this module - the same one-way discipline `chroma_store` uses in the opposite direction."""
    from dataset_tools import ingest as dt_ingest

    incidents = dt_ingest.read_jsonl(dt_config.EVALUATION_INCIDENTS_FILE)
    if not incidents:
        raise ReplayError(
            f"No evaluation incidents found at {dt_config.EVALUATION_INCIDENTS_FILE}. Build the "
            f"dataset first (python -m dataset_tools.build_dataset)."
        )
    return incidents
