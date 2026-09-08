"""Deterministic synthetic incident generation.

Every incident this module produces is clearly labeled `data_source: "synthetic"` and
`generation_method: "deterministic_model"` - never presented as real telemetry. Values are not
arbitrary: each fault's fault-phase metrics are computed from the *actual* formula the
corresponding Java fault mechanism implements (see the code comments below, each citing the real
class/method), and baseline ranges are anchored to values actually observed in the four real
Week 5 experiments (see dataset-tools/README.md for the source numbers). A fixed seed (derived
per-incident from a documented base seed + index) makes every synthetic incident exactly
reproducible.

Parameters are varied only *within* the existing safety limits already enforced by the Java fault
code (e.g. db-lock never holds >= the pool size; memory-leak is always bounded by the real
max-total-bytes cap) - this module does not simulate, and could not simulate, an unsafe fault
configuration, because it reuses those same bounds.
"""
from __future__ import annotations

import random
import zlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import config
from .incidents import compute_deltas, estimate_severity, generate_descriptions


def _stable_fault_type_offset(fault_type: str) -> int:
    """A per-fault-type seed offset that is identical in every process and on every machine.

    This deliberately does NOT use the builtin `hash()`. Python randomises string hashing per
    process unless `PYTHONHASHSEED` is pinned, so `hash(fault_type)` produced a different seed on
    every run - which silently made the "deterministic" generator non-reproducible: incident IDs
    stayed stable (they come from a sort) while every metric value, timestamp and postmortem string
    changed. `zlib.crc32` is specified, stable across processes, machines and Python versions.
    """
    return zlib.crc32(fault_type.encode("utf-8")) % 1000


def _stable_start_offset_seconds(fault_type: str, index: int) -> int:
    """Per-incident second-offset for the synthetic start time, stable across processes.

    Replaces `hash((fault_type, index))`, which had the same per-process randomisation problem as
    `_stable_fault_type_offset` - and was harder to notice, because it only moved timestamps (and
    the postmortem text that embeds them) rather than metric values."""
    return zlib.crc32(f"{fault_type}:{index}".encode("utf-8")) % 60

# Baseline ranges anchored to values actually observed live in the four real Week 5 experiments
# (see dataset-tools/README.md "Synthetic data provenance" for the exact source timestamps).
OBSERVED_BASELINE_RANGES = {
    "auth-service": {"jvm_memory_used_bytes": (35_000_000, 45_000_000), "jvm_memory_committed_bytes": (75_000_000, 90_000_000)},
    "payment-service": {"jvm_memory_used_bytes": (30_000_000, 45_000_000), "jvm_memory_committed_bytes": (70_000_000, 90_000_000)},
    "ledger-service": {"jvm_memory_used_bytes": (60_000_000, 75_000_000), "jvm_memory_committed_bytes": (110_000_000, 130_000_000)},
    "notification-service": {"jvm_memory_used_bytes": (30_000_000, 45_000_000), "jvm_memory_committed_bytes": (70_000_000, 90_000_000)},
}
JVM_MAX_BYTES = 4_294_967_296.0  # observed identical across all four services (-Xmx default)


def _base_service_metrics(rng: random.Random, service: str, request_count: float) -> dict:
    mem_lo, mem_hi = OBSERVED_BASELINE_RANGES[service]["jvm_memory_used_bytes"]
    committed_lo, committed_hi = OBSERVED_BASELINE_RANGES[service]["jvm_memory_committed_bytes"]
    return {
        "jvm_memory_used_bytes": round(rng.uniform(mem_lo, mem_hi), 1),
        "jvm_memory_committed_bytes": round(rng.uniform(committed_lo, committed_hi), 1),
        "jvm_memory_max_bytes": JVM_MAX_BYTES,
        "hikaricp_connections_active": 0.0 if service == "ledger-service" else None,
        "hikaricp_connections_idle": 10.0 if service == "ledger-service" else None,
        "hikaricp_connections_pending": 0.0 if service == "ledger-service" else None,
        "hikaricp_connections_acquire_seconds_avg": round(rng.uniform(0.0002, 0.001), 6) if service == "ledger-service" else None,
        "http_server_requests_count": round(request_count, 1),
        "http_server_requests_error_count": 0.0,
        "http_server_requests_avg_duration_ms": round(rng.uniform(10, 80), 3),
        "http_client_requests_count": round(request_count * 0.4, 1) if service == "payment-service" else None,
        "http_client_requests_avg_duration_ms": round(rng.uniform(10, 60), 3) if service == "payment-service" else None,
        "process_cpu_usage": round(rng.uniform(0.0001, 0.02), 6),
        "system_cpu_usage": round(rng.uniform(0.05, 0.3), 6),
    }


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _phase_bounds(start: datetime, baseline_s: float, fault_s: float, recovery_s: float) -> tuple[dict, dict]:
    injected = start + timedelta(seconds=baseline_s)
    reset = injected + timedelta(seconds=fault_s)
    end = reset + timedelta(seconds=recovery_s)
    phases = {
        "NORMAL": {"start": _iso(start), "end": _iso(injected)},
        "FAULT_INTRODUCTION": {"start": _iso(injected), "end": _iso(injected)},
        "FAULT_ACTIVE": {"start": _iso(injected), "end": _iso(reset)},
        "RECOVERY": {"start": _iso(reset), "end": _iso(end)},
    }
    return phases, {"start": start, "injected": injected, "reset": reset, "end": end}


def _params_for(fault_type: str, rng: random.Random) -> dict:
    if fault_type == "memory-leak":
        d = config.FAULT_DEFAULTS["memory-leak"]
        return {
            "chunk_size_bytes": rng.choice([2_000_000, 5_000_000, 8_000_000]),
            "interval_ms": rng.choice([500, 1000, 2000]),
            "max_total_bytes": d["max_total_bytes"],  # safety cap never varied
        }
    if fault_type == "db-lock":
        # Always strictly below pool_size, mirroring the real clamp in DbLockFaultService.
        return {"connections_to_hold": rng.choice([3, 5, 7, 9]), "pool_size": config.FAULT_DEFAULTS["db-lock"]["pool_size"]}
    if fault_type == "notification-latency":
        return {"delay_ms": rng.choice([2000, 3000, 4000, 6000, 8000, 10000]), "client_timeout_ms": config.FAULT_DEFAULTS["notification-latency"]["client_timeout_ms"]}
    return {}


def generate_synthetic_incident(fault_type: str, index: int, base_seed: int, base_start: datetime) -> dict:
    """`index` and `base_seed` together make this fully deterministic and reproducible: the same
    (fault_type, index, base_seed) always yields byte-identical output, in any process, on any
    machine - see `_stable_fault_type_offset` for why the builtin `hash()` cannot be used here."""
    seed = base_seed * 100_000 + _stable_fault_type_offset(fault_type) + index
    rng = random.Random(seed)

    baseline_s = rng.choice([15, 20, 30, 45, 60])
    fault_s = rng.choice([10, 15, 20, 30, 45])
    recovery_s = rng.choice([10, 15, 20, 30])
    interval_s = rng.choice([2, 3, 5, 10])
    request_rate_per_interval = rng.uniform(0.7, 1.3)  # jitter on "how many probes/cycles happened"

    # Same reasoning as `_stable_fault_type_offset`: a builtin `hash()` here would re-introduce
    # per-process variation into every incident's timestamps (and therefore its postmortem text).
    start = base_start + timedelta(
        minutes=index * 7, seconds=_stable_start_offset_seconds(fault_type, index)
    )
    phases, times = _phase_bounds(start, baseline_s, fault_s, recovery_s)

    params = _params_for(fault_type, rng)
    root = config.FAULT_SERVICE_MAP[fault_type]["root_cause_service"]
    symptom = config.FAULT_SERVICE_MAP[fault_type]["symptom_service"]
    affected = sorted({root, symptom})

    baseline_requests = (baseline_s / interval_s) * request_rate_per_interval
    fault_requests = (fault_s / interval_s) * request_rate_per_interval
    recovery_requests = (recovery_s / interval_s) * request_rate_per_interval

    baseline_metrics = {s: _base_service_metrics(rng, s, baseline_requests) for s in config.SERVICES}
    fault_metrics = {s: dict(baseline_metrics[s]) for s in config.SERVICES}
    recovery_metrics = {s: dict(baseline_metrics[s]) for s in config.SERVICES}
    for s in config.SERVICES:
        fault_metrics[s]["http_server_requests_count"] = round(baseline_requests + fault_requests, 1)
        recovery_metrics[s]["http_server_requests_count"] = round(baseline_requests + fault_requests + recovery_requests, 1)

    baseline_probe = {"count": max(1, round(baseline_requests)), "success_rate": 1.0,
                       "avg_duration_ms": round(rng.uniform(10, 25), 2), "notification_status_counts": {"SENT": max(1, round(baseline_requests))}}
    recovery_probe = dict(baseline_probe)
    fault_config_response: dict = {}
    errors_observed: list[str] = []

    fault_probe_count = max(1, round(fault_requests))

    if fault_type == "memory-leak":
        # Mirrors MemoryLeakFaultService.allocateIfEnabled(): a chunk is retained every
        # interval_ms while enabled, capped at max_total_bytes.
        ticks = int((fault_s * 1000) // params["interval_ms"])
        retained_final = min(ticks * params["chunk_size_bytes"], params["max_total_bytes"])
        fault_metrics[root]["jvm_memory_used_bytes"] = round(baseline_metrics[root]["jvm_memory_used_bytes"] + retained_final / 2, 1)
        # Recovery plateaus rather than dropping instantly - matches real observed JVM behavior
        # (reset stops new allocation but doesn't force an immediate GC).
        recovery_metrics[root]["jvm_memory_used_bytes"] = round(baseline_metrics[root]["jvm_memory_used_bytes"] + retained_final, 1)
        fault_config_response = {"faultActive": True, "retainedBytes": retained_final, "message": "Memory leak fault injected."}
        fault_probe = dict(baseline_probe)
        fault_probe["count"] = fault_probe_count

    elif fault_type == "db-lock":
        # Mirrors DbLockFaultService.inject(): connections_to_hold acquired synchronously,
        # instantly reflected in the pool's active/idle counts; released instantly on reset.
        held = params["connections_to_hold"]
        fault_metrics[root]["hikaricp_connections_active"] = float(held)
        fault_metrics[root]["hikaricp_connections_idle"] = float(params["pool_size"] - held)
        fault_config_response = {"success": True, "connectionsHeld": held, "message": f"DB pool exhaustion fault injected: holding {held} of {params['pool_size']} connections."}
        fault_probe = dict(baseline_probe)
        fault_probe["count"] = fault_probe_count

    elif fault_type == "notification-latency":
        # Mirrors NotificationController's sleep + payment-service's existing client timeout.
        delay_ms, timeout_ms = params["delay_ms"], params["client_timeout_ms"]
        if delay_ms > timeout_ms:
            duration = timeout_ms + rng.uniform(15, 35)
            status, success = "FAILED", True  # payment still returns 200 ACCEPTED (best-effort notification)
        else:
            duration = delay_ms + rng.uniform(5, 20)
            status, success = "SENT", True
        fault_metrics[root]["http_client_requests_avg_duration_ms"] = None  # notification-service makes no outbound calls
        fault_metrics["payment-service"]["http_client_requests_avg_duration_ms"] = round(duration, 2)
        fault_config_response = {"faultActive": True, "delayMs": delay_ms, "message": "Notification latency fault injected."}
        fault_probe = {"count": fault_probe_count, "success_rate": 1.0 if success else 0.0,
                        "avg_duration_ms": round(duration, 2), "notification_status_counts": {status: fault_probe_count}}

    else:  # auth-key-error
        # Mirrors JwtSigningKeyProvider.injectFault(): a stale (pre-fault) token fails signature
        # verification immediately; payment-service's own exception handling returns 401 -
        # confirmed empirically against real telemetry (see config.FAULT_SERVICE_MAP comment).
        fault_metrics[symptom]["http_server_requests_error_count"] = float(fault_probe_count)
        fault_config_response = {"faultActive": True, "message": "Auth key error fault injected. JWT validation will fail for tokens issued before this point."}
        fault_probe = {"count": fault_probe_count, "success_rate": 0.0, "avg_duration_ms": round(rng.uniform(5, 15), 2),
                        "notification_status_counts": {}, }
        errors_observed = ["Invalid or expired token"]

    deltas = compute_deltas(baseline_metrics, fault_metrics)

    incident = {
        "incident_id": None,  # assigned later by build_dataset.py once real+synthetic are combined
        "experiment_id": f"synthetic-{fault_type}-{index:04d}",
        "data_source": "synthetic",
        "generation_method": "deterministic_model",
        "fault_type": fault_type,
        "root_cause_service": root,
        "symptom_service": symptom,
        "affected_services": affected,
        "start_time": _iso(times["start"]),
        "end_time": _iso(times["end"]),
        "phases": phases,
        "baseline_metrics": baseline_metrics,
        "fault_metrics": fault_metrics,
        "recovery_metrics": recovery_metrics,
        "metric_deltas": deltas,
        "payment_probe_summary": {"NORMAL": baseline_probe, "FAULT_INTRODUCTION": {"count": 0, "success_rate": None, "avg_duration_ms": None, "notification_status_counts": {}}, "FAULT_ACTIVE": fault_probe, "RECOVERY": recovery_probe},
        "errors_observed": errors_observed,
        "correlation_ids": {"fault_window_sample": []},  # no real requests were made - nothing to sample
        "fault_configuration": fault_config_response,
        "synthetic_parameters": {**params, "baseline_seconds": baseline_s, "fault_seconds": fault_s, "recovery_seconds": recovery_s, "interval_seconds": interval_s, "seed": seed},
    }
    incident["severity"] = estimate_severity(fault_type, deltas, fault_probe)
    incident.update(generate_descriptions(incident))
    return incident


def generate_synthetic_batch(fault_type: str, count: int, base_seed: int = config.SPLIT_SEED, base_start: Optional[datetime] = None) -> list[dict]:
    base_start = base_start or datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [generate_synthetic_incident(fault_type, i, base_seed, base_start) for i in range(count)]
