"""Windowed rates derived from cumulative Prometheus counters.

Micrometer's `http_server_requests_seconds_count`, its error subset, and the matching `_sum` are
all **cumulative since JVM start**. `collector.parse_metrics` reports them as-is, plus a lifetime
average computed as `sum / count`. Neither is a usable signal on a live system:

  * A counter never returns to zero, so once a service has produced a single 4xx or 5xx - one
    mistyped password will do - `http_server_requests_error_count` stays above zero for the life
    of the process. The `auth-key-error` signature fires at `> 0.5`, so it would then match on
    every cycle until the pod restarts, long after the fault was cleared.
  * A lifetime average is dominated by history. At fault onset the elevated latency is diluted by
    every fast request that came before it, so the signature fires late; after recovery the
    average stays elevated, so it clears late.

The synthetic corpus hides both problems. `dataset_tools.synthetic_model` makes the request count
cumulative across phases but resets `http_server_requests_error_count` to 0.0 in the recovery
phase - something a real counter cannot do - so the replayed evaluation never exercises the live
behaviour.

This module keeps the previous reading per (service, metric) and derives the quantities a detector
or a threshold actually wants:

    http_server_requests_rate_per_s          requests per second in the last window
    http_server_requests_error_rate_per_s    errors per second in the last window
    http_server_requests_window_avg_ms       mean latency of requests *in this window*
    http_client_requests_rate_per_s
    http_client_requests_window_avg_ms

Derived fields are added alongside the existing ones, never in place of them, so trained models,
the stored corpus and the replay protocol are all unaffected.

A counter that goes backwards means the process restarted. That is reported as `None` rather than
as a negative rate, because a restart is a gap in observation, not a measurement.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

log = logging.getLogger("telemetry.rates")

# (cumulative count field, cumulative sum field, rate output, windowed-average output)
_PAIRS = (
    ("http_server_requests_count", "http_server_requests_duration_sum_s",
     "http_server_requests_rate_per_s", "http_server_requests_window_avg_ms"),
    ("http_client_requests_count", "http_client_requests_duration_sum_s",
     "http_client_requests_rate_per_s", "http_client_requests_window_avg_ms"),
)

_ERROR_COUNT = "http_server_requests_error_count"
_ERROR_RATE = "http_server_requests_error_rate_per_s"

DERIVED_FIELDS = tuple(
    [rate for _c, _s, rate, _a in _PAIRS]
    + [avg for _c, _s, _r, avg in _PAIRS]
    + [_ERROR_RATE]
)


def _parse_ts(value) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class RateTracker:
    """Holds the previous cumulative reading per service and derives windowed values.

    One instance per collector process. It is deliberately not persisted: after a restart the
    first cycle simply yields `None` for every derived field, which is honest - there is no
    window to measure over yet.
    """

    def __init__(self) -> None:
        self._previous: dict[str, dict] = {}

    def reset(self, service: Optional[str] = None) -> None:
        if service is None:
            self._previous.clear()
        else:
            self._previous.pop(service, None)

    def derive(self, service: str, metrics: Optional[dict], timestamp) -> dict:
        """Returns the derived fields for this cycle, and records the reading for the next one.

        Every derived field is `None` on the first cycle for a service, when a counter has gone
        backwards (process restart), or when the underlying metric is absent.
        """
        derived: dict = {name: None for name in DERIVED_FIELDS}
        if not metrics:
            return derived

        now = _parse_ts(timestamp)
        prev = self._previous.get(service)
        self._previous[service] = {"timestamp": now, "metrics": dict(metrics)}

        if prev is None or now is None or prev.get("timestamp") is None:
            return derived

        elapsed = now - prev["timestamp"]
        if elapsed <= 0:
            return derived

        before = prev["metrics"]

        def delta(field) -> Optional[float]:
            a, b = before.get(field), metrics.get(field)
            if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
                return None
            if b < a:
                log.info("%s: %s went backwards (%s -> %s); treating as a process restart",
                         service, field, a, b)
                return None
            return b - a

        for count_field, sum_field, rate_field, avg_field in _PAIRS:
            d_count = delta(count_field)
            if d_count is not None:
                derived[rate_field] = round(d_count / elapsed, 6)
            d_sum = delta(sum_field)
            if d_count and d_sum is not None:
                derived[avg_field] = round((d_sum / d_count) * 1000.0, 3)

        d_err = delta(_ERROR_COUNT)
        if d_err is not None:
            derived[_ERROR_RATE] = round(d_err / elapsed, 6)

        return derived
