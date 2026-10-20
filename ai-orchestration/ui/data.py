"""Read-only access to everything the console displays.

Two sources, and nothing else:

  * **Live** - the four services' own `/actuator/health`, `/fault-status` and curated Prometheus
    metrics, fetched through `telemetry.collector` so the console asks exactly the questions the
    collector has asked since Week 5.
  * **On disk** - the evaluation artefacts under `docs/evaluation/`, the trained detector
    metadata, and the Evaluation incident corpus.

Nothing here synthesises a value. When something cannot be read the loader returns `None` or an
explicit `unknown` state, and the page renders that honestly rather than filling the gap.
"""
from __future__ import annotations

import csv
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import streamlit as st

from ai_orchestration import config as ai_config

ai_config.ensure_sibling_packages_importable()

from dataset_tools import config as dt_config  # noqa: E402
from telemetry import collector as telemetry_collector, config as telemetry_config  # noqa: E402

EVAL_DIR = ai_config.REPO_ROOT / "docs" / "evaluation"

# Declared dependency direction of the test bed. payment-service calls the other three
# synchronously, which is what lets a fault elsewhere surface as a payment-side symptom.
DEPENDENCIES: dict[str, list[str]] = {
    "payment-service": ["auth-service", "ledger-service", "notification-service"],
    "auth-service": [],
    "ledger-service": [],
    "notification-service": [],
}

# Which metric each service is worth showing on a health card, and a readable name for it.
HEADLINE_METRIC = {
    "payment-service": ("jvm_memory_used_bytes", "Heap used"),
    "ledger-service": ("hikaricp_connections_active", "Pool connections active"),
    "auth-service": ("http_server_requests_count", "Requests served"),
    "notification-service": ("http_server_requests_avg_duration_ms", "Mean response"),
}


@dataclass
class ServiceState:
    name: str
    base_url: str
    port: str
    status: str                       # healthy | degraded | down | unknown
    health_detail: Optional[dict]
    fault_active: Optional[bool]
    fault_detail: Optional[dict]
    metrics: Optional[dict]
    error: Optional[str]

    @property
    def headline(self) -> tuple[str, str]:
        key, label = HEADLINE_METRIC.get(self.name, ("http_server_requests_count", "Requests"))
        if not self.metrics or self.metrics.get(key) is None:
            return label, "--"
        value = self.metrics[key]
        if key == "jvm_memory_used_bytes":
            return label, f"{value / 1e6:.0f} MB"
        if key.endswith("_ms"):
            return label, f"{value:.0f} ms"
        if isinstance(value, float) and value.is_integer():
            return label, f"{int(value):,}"
        return label, f"{value:,.2f}" if isinstance(value, float) else f"{value}"


# --- live ---------------------------------------------------------------------------------------

@st.cache_data(ttl=10, show_spinner=False)
def service_states() -> list[ServiceState]:
    """Poll every configured service once. A service being unreachable is a normal outcome here,
    not an error: it is recorded as `down` with the transport failure attached."""
    out: list[ServiceState] = []
    for svc in telemetry_config.SERVICES:
        cid = str(uuid.uuid4())
        port = svc.base_url.rsplit(":", 1)[-1]
        health = fault = metrics = None
        error = None
        status = "unknown"
        try:
            health = telemetry_collector.fetch_health(svc.base_url, cid)
            raw = str((health or {}).get("status", "")).upper()
            status = "healthy" if raw == "UP" else ("degraded" if raw else "unknown")
        except Exception as exc:  # noqa: BLE001 - unreachable is a reportable state
            status, error = "down", f"{type(exc).__name__}: {exc}"
        if status != "down":
            try:
                fault = telemetry_collector.fetch_fault_status(svc.base_url, cid)
            except Exception:  # noqa: BLE001 - optional signal
                fault = None
            try:
                metrics = telemetry_collector.fetch_metrics(svc.base_url, cid)
            except Exception:  # noqa: BLE001
                metrics = None
        fault_active = None if fault is None else bool(fault.get("faultActive"))
        if status == "healthy" and fault_active:
            status = "degraded"
        out.append(ServiceState(svc.name, svc.base_url, port, status, health,
                                fault_active, fault, metrics, error))
    return out


def estate_summary(states: list[ServiceState]) -> dict[str, int]:
    counts = {"healthy": 0, "degraded": 0, "down": 0, "unknown": 0}
    for s in states:
        counts[s.status] = counts.get(s.status, 0) + 1
    return counts


# --- on disk ------------------------------------------------------------------------------------

def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - a missing artefact is reported, never invented
        return None


@st.cache_data(show_spinner=False)
def metrics_json() -> Optional[dict]:
    return _read_json(EVAL_DIR / "metrics.json")


@st.cache_data(show_spinner=False)
def negative_control() -> Optional[dict]:
    return _read_json(EVAL_DIR / "negative_control" / "negative_control.json")


@st.cache_data(show_spinner=False)
def threshold_sweep() -> Optional[dict]:
    return _read_json(EVAL_DIR / "threshold_sweep" / "sweep_results.json")


@st.cache_data(show_spinner=False)
def ablation(name: str) -> Optional[dict]:
    return _read_json(EVAL_DIR / "ablations" / name / "metrics.json")


@st.cache_data(show_spinner=False)
def per_incident_rows() -> list[dict]:
    path = EVAL_DIR / "per_incident_results.csv"
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    except Exception:  # noqa: BLE001
        return []


@st.cache_data(show_spinner=False)
def detector_inventory() -> list[dict]:
    """What each persisted detector was actually trained on."""
    out = []
    for service in sorted(ai_config.SERVICE_FEATURE_SCHEMAS):
        meta = _read_json(ai_config.MODELS_DIR / service / "metadata.json")
        out.append({
            "service": service,
            "trained": meta is not None,
            "samples": (meta or {}).get("training_sample_count"),
            "features": len((meta or {}).get("feature_names") or
                            ai_config.SERVICE_FEATURE_SCHEMAS[service]),
            "version": (meta or {}).get("model_version"),
            "source": (meta or {}).get("source"),
        })
    return out


@st.cache_data(show_spinner=False)
def evaluation_incidents() -> list[dict]:
    """The Evaluation corpus. The Memory file is never opened by the console."""
    try:
        from ai_orchestration.evaluation import replay
        return replay.load_evaluation_incidents()
    except Exception:  # noqa: BLE001
        return []


@st.cache_data(show_spinner=False)
def corpus_breakdown() -> dict[str, Any]:
    incidents = evaluation_incidents()
    by_fault: dict[str, int] = {}
    cross = 0
    sources: dict[str, int] = {}
    for inc in incidents:
        by_fault[inc.get("fault_type", "unknown")] = by_fault.get(inc.get("fault_type", "unknown"), 0) + 1
        if inc.get("root_cause_service") != inc.get("symptom_service"):
            cross += 1
        src = inc.get("data_source", "unknown")
        sources[src] = sources.get(src, 0) + 1
    return {"total": len(incidents), "by_fault": by_fault,
            "cross_service": cross, "sources": sources,
            "memory_file": str(dt_config.MEMORY_INCIDENTS_FILE),
            "evaluation_file": str(dt_config.EVALUATION_INCIDENTS_FILE)}


# --- detection coverage, computed live from the shipped models --------------------------------

@st.cache_data(show_spinner=False)
def detection_coverage(mode: str) -> Optional[dict]:
    """Which Evaluation incidents the current detectors surface under `mode`.

    Computed by running the real detection node over the corpus, not read from a table, so it
    stays correct when the detection mode or the models change.
    """
    try:
        from ai_orchestration.graph.workflow import (_extract_features_node,
                                                     _make_detect_anomaly_node,
                                                     load_all_detectors)
        from ai_orchestration.evaluation import replay
    except Exception:  # noqa: BLE001
        return None

    incidents = evaluation_incidents()
    if not incidents:
        return None

    previous = ai_config.DETECTION_MODE
    ai_config.DETECTION_MODE = mode
    try:
        node = _make_detect_anomaly_node(load_all_detectors())
        detected, by_fault, cross_total, cross_hit = [], {}, 0, 0
        for inc in incidents:
            state = {"raw_record": replay.incident_to_raw_record(inc)}
            state.update(_extract_features_node(state))
            state.update(node(state))
            hit = bool((state.get("anomaly_result") or {}).get("is_anomaly"))
            fault = inc.get("fault_type", "unknown")
            seen, total = by_fault.get(fault, (0, 0))
            by_fault[fault] = (seen + int(hit), total + 1)
            if hit:
                detected.append(inc["incident_id"])
            if inc.get("root_cause_service") != inc.get("symptom_service"):
                cross_total += 1
                cross_hit += int(hit)
    except Exception:  # noqa: BLE001
        return None
    finally:
        ai_config.DETECTION_MODE = previous

    return {"mode": mode, "detected": sorted(detected), "total": len(incidents),
            "by_fault": by_fault, "cross_hit": cross_hit, "cross_total": cross_total}

# --- optional observability stack ---------------------------------------------------------------

GRAFANA_URL = os.environ.get("AIOPS_GRAFANA_URL", "http://localhost:3000")
PROMETHEUS_URL = os.environ.get("AIOPS_PROMETHEUS_URL", "http://localhost:9090")
GRAFANA_DASHBOARD_UID = "aiops-fault-signals"


@st.cache_data(ttl=15, show_spinner=False)
def observability_status() -> dict:
    """Is the optional Prometheus/Grafana stack running?

    Both are optional: the pipeline reads each service's /actuator/prometheus directly and does
    not depend on either. The console only offers the live graphs when they are actually up.
    """
    import requests
    out = {"prometheus": False, "grafana": False, "targets_up": None, "targets_total": None}
    try:
        r = requests.get(f"{PROMETHEUS_URL}/-/ready", timeout=2)
        out["prometheus"] = r.status_code == 200
    except Exception:  # noqa: BLE001
        pass
    if out["prometheus"]:
        try:
            r = requests.get(f"{PROMETHEUS_URL}/api/v1/targets", timeout=3)
            active = (r.json().get("data") or {}).get("activeTargets") or []
            out["targets_total"] = len(active)
            out["targets_up"] = sum(1 for t in active if t.get("health") == "up")
        except Exception:  # noqa: BLE001
            pass
    try:
        r = requests.get(f"{GRAFANA_URL}/api/health", timeout=2)
        out["grafana"] = r.status_code == 200
    except Exception:  # noqa: BLE001
        pass
    return out


def grafana_embed_url(panel_id: int | None = None, minutes: int = 15) -> str:
    """A kiosk-mode dashboard URL, or a single-panel URL when `panel_id` is given."""
    base = f"{GRAFANA_URL}/d/{GRAFANA_DASHBOARD_UID}/fault-signals"
    params = f"?orgId=1&from=now-{minutes}m&to=now&refresh=5s&theme=dark"
    if panel_id is not None:
        return f"{GRAFANA_URL}/d-solo/{GRAFANA_DASHBOARD_UID}/fault-signals{params}&panelId={panel_id}"
    return base + params + "&kiosk"

# --- investigation log --------------------------------------------------------------------------
#
# The console previously kept its run log in session state alone, so the History page was empty
# on every restart and served no purpose. Runs are now appended to a JSONL file beside the other
# evaluation artefacts. This records what the operator did in the console; it is entirely separate
# from the pipeline's own write-back into retrieval memory, which remains governed by rule 22.

HISTORY_FILE = EVAL_DIR / "console_history.jsonl"


def append_history(entry: dict) -> None:
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with HISTORY_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception:  # noqa: BLE001 - a console log must never break an investigation
        pass


def load_history(limit: int = 500) -> list[dict]:
    try:
        with HISTORY_FILE.open(encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh if line.strip()]
        return rows[-limit:]
    except Exception:  # noqa: BLE001 - absent file simply means nothing has been investigated
        return []


def clear_history() -> None:
    try:
        HISTORY_FILE.unlink()
    except Exception:  # noqa: BLE001
        pass
