"""Evaluation category (part 1): the incident -> raw record adapter and the ground-truth labels.

This adapter is the single point where a stored incident becomes pipeline input, so a mistake here
would silently bias every number in Week 12. The tests pin the three decisions documented in
replay.py: symptom-service telemetry, fault-window phase, and no self-reported fault flag.
"""
from __future__ import annotations

import pytest

from ai_orchestration.evaluation import replay


def _incident(fault_type="notification-latency", root="notification-service",
              symptom="payment-service", **overrides) -> dict:
    incident = {
        "incident_id": "inc-0042",
        "fault_type": fault_type,
        "root_cause_service": root,
        "symptom_service": symptom,
        "start_time": "2026-01-01T00:00:00+00:00",
        "phases": {"FAULT_ACTIVE": {"start": "2026-01-01T00:05:00+00:00"}},
        "baseline_metrics": {symptom: {"jvm_memory_used_bytes": 40_000_000.0}},
        "fault_metrics": {symptom: {"jvm_memory_used_bytes": 41_000_000.0,
                                    "http_client_requests_avg_duration_ms": 5100.0}},
        "recovery_metrics": {symptom: {"jvm_memory_used_bytes": 40_500_000.0}},
    }
    incident.update(overrides)
    return incident


def test_replays_the_symptom_services_fault_window_telemetry():
    record = replay.incident_to_raw_record(_incident())

    assert record["service"] == "payment-service"          # symptom service, not the root cause
    assert record["metrics"]["http_client_requests_avg_duration_ms"] == 5100.0  # fault window
    assert record["timestamp"] == "2026-01-01T00:05:00+00:00"
    assert record["collection_error"] is None


def test_self_reported_fault_flag_is_withheld():
    """/fault-status is effectively an answer key for same-service faults. A replayed incident must
    not come with one, or the pipeline could 'diagnose' by reading a debug endpoint."""
    record = replay.incident_to_raw_record(_incident(fault_type="memory-leak",
                                                     root="payment-service", symptom="payment-service"))
    assert record["fault"] is None


def test_record_shape_matches_what_the_collector_produces():
    record = replay.incident_to_raw_record(_incident())
    assert set(record) == {"timestamp", "service", "correlation_id", "health", "metrics",
                           "fault", "collection_error"}


def test_missing_fault_window_metrics_raise_rather_than_being_synthesised():
    incident = _incident()
    incident["fault_metrics"] = {}
    with pytest.raises(replay.ReplayError, match="refusing to synthesise"):
        replay.incident_to_raw_record(incident)


def test_ground_truth_labels_are_cross_checked_against_fault_service_map():
    truth = replay.ground_truth(_incident())
    assert truth["root_cause_service"] == "notification-service"
    assert truth["symptom_service"] == "payment-service"
    assert truth["is_cross_service"] is True


def test_a_mislabelled_incident_fails_loudly():
    """A silently mislabelled incident would score every arm wrong and look like a model failure."""
    bad = _incident(root="payment-service")  # notification-latency's root cause is notification-service
    with pytest.raises(replay.ReplayError, match="disagree with FAULT_SERVICE_MAP"):
        replay.ground_truth(bad)


@pytest.mark.parametrize("fault_type,root,symptom,cross", [
    ("memory-leak", "payment-service", "payment-service", False),
    ("db-lock", "ledger-service", "ledger-service", False),
    ("auth-key-error", "auth-service", "payment-service", True),
    ("notification-latency", "notification-service", "payment-service", True),
])
def test_all_four_fault_types_label_correctly(fault_type, root, symptom, cross):
    truth = replay.ground_truth(_incident(fault_type=fault_type, root=root, symptom=symptom))
    assert truth["is_cross_service"] is cross
