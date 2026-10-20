"""Week 5 telemetry collector core.

Continuously polls all four microservices' /actuator/health, /actuator/prometheus, and
/fault-status endpoints, normalizes the result into a small curated schema (see README.md for the
full schema), and appends one JSONL record per service per cycle to
telemetry/data/telemetry_<date>.jsonl. Also performs a periodic synthetic end-to-end payment
probe (payment_probe.py) so fault symptoms that only manifest on a real request are captured too.

Tolerant of partial failures by design: each service is polled independently inside its own
try/except. A failure on one service is recorded as a `collection_error` string on that service's
record and never stops collection from the others, and never crashes the process.
"""
from __future__ import annotations

import argparse
import logging
import signal
import time
from pathlib import Path
from typing import Optional

import requests
from prometheus_client.parser import text_string_to_metric_families

from . import config
from .payment_probe import PaymentProbe
from .storage import JsonlWriter
from .util import new_correlation_id, now_iso

try:  # package import, or direct script execution
    from .rates import RateTracker
except ImportError:  # pragma: no cover
    from rates import RateTracker

log = logging.getLogger("telemetry.collector")


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def _get(url: str, correlation_id: str) -> requests.Response:
    return requests.get(url, headers={"X-Correlation-Id": correlation_id}, timeout=config.HTTP_TIMEOUT_SECONDS)


def fetch_health(base_url: str, correlation_id: str) -> dict:
    resp = _get(f"{base_url}/actuator/health", correlation_id)
    resp.raise_for_status()
    body = resp.json()
    components = body.get("components", {})
    return {
        "status": body.get("status"),
        "components": {
            name: comp.get("status")
            for name, comp in components.items()
            if isinstance(comp, dict) and "status" in comp
        },
    }


def fetch_fault_status(base_url: str, correlation_id: str) -> dict:
    resp = _get(f"{base_url}/fault-status", correlation_id)
    resp.raise_for_status()
    return resp.json()


def fetch_metrics(base_url: str, correlation_id: str) -> dict:
    resp = _get(f"{base_url}/actuator/prometheus", correlation_id)
    resp.raise_for_status()
    return parse_metrics(resp.text)


# ---------------------------------------------------------------------------
# Curated Prometheus metric extraction
#
# Deliberately NOT a dump of every exposed metric (that would be noisy and mostly irrelevant to
# the four fault scenarios). Each field below is picked because it directly evidences one of the
# fault scenarios, or is a broadly useful general health/performance signal.
# ---------------------------------------------------------------------------

def _families(text: str) -> dict:
    return {fam.name: fam for fam in text_string_to_metric_families(text)}


def _sum_samples(family, label_filter: Optional[dict] = None) -> Optional[float]:
    """Sums non-negative sample values (Micrometer reports -1 for "no maximum" on some JVM memory
    pools; excluding negatives keeps the aggregate meaningful)."""
    if family is None:
        return None
    total = 0.0
    found = False
    for s in family.samples:
        if label_filter and any(s.labels.get(k) != v for k, v in label_filter.items()):
            continue
        if s.value < 0:
            continue
        total += s.value
        found = True
    return total if found else None


def _first_sample(family) -> Optional[float]:
    if family is None or not family.samples:
        return None
    return family.samples[0].value


def _sum_named_samples(family, sample_name: str, label_filter: Optional[dict] = None) -> Optional[float]:
    """Prometheus summary/histogram metrics (e.g. `foo_seconds`, TYPE summary) are parsed by
    prometheus_client as ONE family keyed by the base name, containing differently-*named*
    samples (`foo_seconds_sum`, `foo_seconds_count`, ...) - not separate families per suffix.
    This sums samples matching a specific sample name within such a family."""
    if family is None:
        return None
    total = 0.0
    found = False
    for s in family.samples:
        if s.name != sample_name:
            continue
        if label_filter and any(s.labels.get(k) != v for k, v in label_filter.items()):
            continue
        total += s.value
        found = True
    return total if found else None


def parse_metrics(text: str) -> dict:
    fams = _families(text)
    metrics: dict = {}

    # --- MEMORY_LEAK fault (payment-service) / general JVM health (all services) ---
    metrics["jvm_memory_used_bytes"] = _sum_samples(fams.get("jvm_memory_used_bytes"), {"area": "heap"})
    metrics["jvm_memory_committed_bytes"] = _sum_samples(fams.get("jvm_memory_committed_bytes"), {"area": "heap"})
    metrics["jvm_memory_max_bytes"] = _sum_samples(fams.get("jvm_memory_max_bytes"), {"area": "heap"})

    # --- DB_POOL_EXHAUSTION fault (ledger-service only; None on the other three) ---
    metrics["hikaricp_connections_active"] = _first_sample(fams.get("hikaricp_connections_active"))
    metrics["hikaricp_connections_idle"] = _first_sample(fams.get("hikaricp_connections_idle"))
    metrics["hikaricp_connections_pending"] = _first_sample(fams.get("hikaricp_connections_pending"))
    acquire_fam = fams.get("hikaricp_connections_acquire_seconds")
    acquire_sum = _sum_named_samples(acquire_fam, "hikaricp_connections_acquire_seconds_sum")
    acquire_count = _sum_named_samples(acquire_fam, "hikaricp_connections_acquire_seconds_count")
    metrics["hikaricp_connections_acquire_seconds_avg"] = (
        round(acquire_sum / acquire_count, 6) if acquire_sum is not None and acquire_count else None
    )

    # --- AUTH_KEY_ERROR fault / general error-rate visibility (inbound, every service) ---
    server_fam = fams.get("http_server_requests_seconds")
    server_count = _sum_named_samples(server_fam, "http_server_requests_seconds_count")
    server_sum = _sum_named_samples(server_fam, "http_server_requests_seconds_sum")
    error_count = 0.0
    error_found = False
    if server_fam is not None:
        for s in server_fam.samples:
            if s.name == "http_server_requests_seconds_count" and s.labels.get("outcome") in ("CLIENT_ERROR", "SERVER_ERROR"):
                error_count += s.value
                error_found = True
    metrics["http_server_requests_count"] = server_count
    metrics["http_server_requests_error_count"] = error_count if error_found else (0.0 if server_count is not None else None)
    metrics["http_server_requests_avg_duration_ms"] = (
        round((server_sum / server_count) * 1000, 3) if server_sum is not None and server_count else None
    )

    # --- NOTIFICATION_LATENCY fault as seen from the caller (payment-service's outbound calls
    # only; None on the other three, since only payment-service makes outbound calls) ---
    client_fam = fams.get("http_client_requests_seconds")
    client_count = _sum_named_samples(client_fam, "http_client_requests_seconds_count")
    client_sum = _sum_named_samples(client_fam, "http_client_requests_seconds_sum")
    metrics["http_client_requests_count"] = client_count
    metrics["http_client_requests_avg_duration_ms"] = (
        round((client_sum / client_count) * 1000, 3) if client_sum is not None and client_count else None
    )

    # --- Raw cumulative totals, kept so rates can be derived downstream -----------------------
    # `http_server_requests_count`, `..._error_count` and the two `..._avg_duration_ms` fields
    # above are all *cumulative since JVM start*: the counts only ever increase, and the averages
    # are lifetime totals (sum / count), not current latency. That has two consequences on a live
    # system which the replayed synthetic corpus does not reproduce:
    #
    #   - After the first 4xx/5xx the service ever returns - one mistyped password is enough -
    #     `http_server_requests_error_count` stays above zero until the pod restarts, so the
    #     auth-key-error signature (`> 0.5`) matches on every subsequent cycle.
    #   - A lifetime average is diluted at fault onset and stays elevated long after recovery,
    #     so the notification-latency signature both fires late and clears late.
    #
    # Exposing the raw sums alongside the existing fields lets `rates.RateTracker` derive windowed
    # values without changing any existing field, so trained models and the replay corpus are
    # unaffected.
    metrics["http_server_requests_duration_sum_s"] = server_sum
    metrics["http_client_requests_duration_sum_s"] = client_sum

    # --- General process signals ---
    metrics["process_cpu_usage"] = _first_sample(fams.get("process_cpu_usage"))
    metrics["system_cpu_usage"] = _first_sample(fams.get("system_cpu_usage"))

    return metrics


# ---------------------------------------------------------------------------
# One collection cycle
# ---------------------------------------------------------------------------

def collect_service(service: config.ServiceConfig) -> dict:
    correlation_id = new_correlation_id()
    record: dict = {
        "timestamp": now_iso(),
        "service": service.name,
        "correlation_id": correlation_id,
        "health": None,
        "metrics": None,
        "fault": None,
        "collection_error": None,
    }

    def _note_error(stage: str, exc: Exception) -> None:
        existing = record["collection_error"]
        message = f"{stage}: {exc}"
        record["collection_error"] = f"{existing}; {message}" if existing else message

    try:
        record["health"] = fetch_health(service.base_url, correlation_id)
    except Exception as exc:  # noqa: BLE001 - a bad service must never stop the whole cycle
        _note_error("health", exc)

    try:
        record["metrics"] = fetch_metrics(service.base_url, correlation_id)
    except Exception as exc:  # noqa: BLE001
        _note_error("metrics", exc)

    try:
        record["fault"] = fetch_fault_status(service.base_url, correlation_id)
    except Exception as exc:  # noqa: BLE001
        _note_error("fault-status", exc)

    return record


def collect_cycle(services: Optional[list] = None) -> list:
    services = services if services is not None else config.SERVICES
    return [collect_service(s) for s in services]


# ---------------------------------------------------------------------------
# Continuous collection loop
# ---------------------------------------------------------------------------

class Collector:
    def __init__(self, interval: Optional[float] = None, probe_payment: bool = True,
                 data_dir: Optional[Path] = None):
        self.interval = interval if interval is not None else config.COLLECTION_INTERVAL_SECONDS
        self.probe_payment = probe_payment
        resolved_data_dir = data_dir if data_dir is not None else config.DATA_DIR
        self._telemetry_writer = JsonlWriter(resolved_data_dir, "telemetry")
        self._probe_writer = JsonlWriter(resolved_data_dir, "payment_probes")
        self._payment_probe = PaymentProbe()
        self._stop = False
        self._rates = RateTracker()

    def run_once(self) -> None:
        for record in collect_cycle():
            # Cumulative counters are useless as live signals (see telemetry/rates.py); derive
            # windowed rates alongside them. Purely additive - existing fields are untouched.
            record["derived"] = self._rates.derive(
                record.get("service"), record.get("metrics"), record.get("timestamp")
            )
            self._telemetry_writer.write(record)
            if record["collection_error"]:
                log.warning("collection error for %s: %s", record["service"], record["collection_error"])

        if self.probe_payment:
            probe_record = self._payment_probe.probe()
            self._probe_writer.write(probe_record)
            if not probe_record.get("success"):
                log.info("payment probe did not succeed: %s", probe_record)

    def request_stop(self, *_args) -> None:
        self._stop = True

    def run(self, duration: Optional[float] = None, install_signal_handlers: bool = True) -> None:
        if install_signal_handlers:
            signal.signal(signal.SIGINT, self.request_stop)
            signal.signal(signal.SIGTERM, self.request_stop)
        log.info("telemetry collector starting (interval=%ss, duration=%s)", self.interval, duration or "unbounded")
        start = time.monotonic()
        try:
            while not self._stop:
                cycle_start = time.monotonic()
                self.run_once()
                if duration is not None and (time.monotonic() - start) >= duration:
                    break
                elapsed = time.monotonic() - cycle_start
                time.sleep(max(0.0, self.interval - elapsed))
        finally:
            self.close()
            log.info("telemetry collector stopped")

    def close(self) -> None:
        self._telemetry_writer.close()
        self._probe_writer.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Week 5 telemetry collector")
    parser.add_argument("--interval", type=float, default=None, help="Seconds between collection cycles")
    parser.add_argument("--duration", type=float, default=None, help="Stop after N seconds (default: run until Ctrl+C)")
    parser.add_argument("--no-probe", action="store_true", help="Disable the synthetic payment probe")
    parser.add_argument("--data-dir", type=str, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

    data_dir = Path(args.data_dir) if args.data_dir else None
    collector = Collector(interval=args.interval, probe_payment=not args.no_probe, data_dir=data_dir)
    collector.run(duration=args.duration)


if __name__ == "__main__":
    main()
