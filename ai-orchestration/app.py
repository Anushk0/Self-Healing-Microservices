"""Week 9 Streamlit UI: the mandatory human-approval step for the LangGraph pipeline built in
Weeks 7-8 and gated by Week 9's safety validator.

This is a thin operator console around `graph/workflow.py`'s compiled graph - it never proposes,
approves, or executes anything itself. It:

1. Runs one raw telemetry record through the graph.
2. If the graph reaches `human_approval`, it *must* pause there (langgraph's `interrupt()` - see
   workflow.py's docstring) - this page shows the RCA hypothesis, its reasoning chain, the
   retrieved evidence, the safety-validator's verdict, and the proposed action, then waits for an
   explicit Approve/Reject click before resuming the graph with `Command(resume=...)`.
3. If the safety validator rejected the proposal, the graph never pauses at all (CLAUDE.md rule
   20: "a rule violation shouldn't even reach the human") - this page shows the rejection reason
   directly, with no approval controls to click.

Week 10 changed what a click here means: approving now genuinely executes the action against the
running system (an HTTP fault reset, or a pod restart via the Kubernetes API), then re-observes the
target service to check whether the fault actually cleared, and writes the finished incident back
into retrieval memory. The page says so explicitly above the buttons, shows what was executed and
whether recovery was verified, and surfaces dry-run mode when it is on. If the remediation fails or
the fault is still active afterwards, the graph proposes a *different* action and pauses again -
each retry needs its own approval, and the page labels it as a retry rather than repeating the
original prompt.

Week 11 added an arm selector: the same page can drive either the multi-agent pipeline or the
single-agent baseline, which share every node except the diagnostic step. Run with:

    streamlit run app.py

from the ai-orchestration/ directory (after `pip install -r requirements.txt`).
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone

import streamlit as st

# `ui/` sits beside this file; Streamlit runs the script from the repo root, so make the
# console package importable without requiring an install step.
import sys as _sys
from pathlib import Path as _Path
_UI_PARENT = str(_Path(__file__).resolve().parent)
if _UI_PARENT not in _sys.path:
    _sys.path.insert(0, _UI_PARENT)
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from ai_orchestration import config as ai_config
from ai_orchestration.anomaly.detector import AnomalyDetector
from ai_orchestration.anomaly.feature_extractor import extract_features
from ai_orchestration.baseline.graph import build_baseline_graph
from ai_orchestration.graph.workflow import build_graph, default_run_config, load_all_detectors
from ai_orchestration.llm.ollama_client import OllamaClient

ai_config.ensure_sibling_packages_importable()

from dataset_tools import config as dt_config  # noqa: E402
from dataset_tools.synthetic_model import OBSERVED_BASELINE_RANGES  # noqa: E402

from ai_orchestration.evaluation import replay as _replay  # noqa: E402

st.set_page_config(page_title="AIOps Human Approval Console", layout="wide")

# Evaluation incidents the currently-trained detectors actually flag, established by scoring all 48
# through the same feature extractor and detector the pipeline uses (see docs/evaluation/). Listed
# explicitly rather than recomputed on every page load, which would score 48 incidents per rerun.
# Regenerate after retraining detectors - the sidebar's detector inventory tells you when that
# happened, and a stale entry only mislabels the tick, never changes what the pipeline does.
KNOWN_DETECTED_INCIDENTS = {
    "inc-0002", "inc-0004", "inc-0005", "inc-0006", "inc-0010", "inc-0011",
    "inc-0012", "inc-0013", "inc-0014", "inc-0015", "inc-0016", "inc-0017",
    "inc-0018", "inc-0019", "inc-0021", "inc-0023", "inc-0024", "inc-0025",
    "inc-0026", "inc-0027", "inc-0028", "inc-0031", "inc-0033", "inc-0034",
    "inc-0035", "inc-0036", "inc-0039", "inc-0041", "inc-0045", "inc-0048",
    "inc-0050", "inc-0051", "inc-0052", "inc-0053", "inc-0054", "inc-0055",
    "inc-0058", "inc-0059", "inc-0061", "inc-0062", "inc-0063", "inc-0064",
    "inc-0065", "inc-0067",
}


@st.cache_data(show_spinner=False)
def _load_evaluation_incidents_cached() -> list[dict]:
    """Cached so switching arms or clicking Approve does not re-read the Evaluation file. Reads
    only the Evaluation set - this console never opens the Memory file."""
    return _replay.load_evaluation_incidents()


# --- Session bootstrap -------------------------------------------------------------------------

def _init_session() -> None:
    if "checkpointer" in st.session_state:
        return
    st.session_state.checkpointer = MemorySaver()
    st.session_state.ollama_client = OllamaClient()
    st.session_state.detectors = load_all_detectors()  # real, persisted models only - may be empty
    st.session_state.demo_detector_services: set[str] = set()  # tracked separately so the UI can
    # clearly label which services are running on a real trained model vs. this page's synthetic
    # in-memory demo detector (see _train_demo_detector) - never silently blurred together.
    st.session_state.arm = "multi-agent"  # Week 11: which pipeline to run
    st.session_state.graph = _build_graph()
    st.session_state.thread_id = None
    st.session_state.run_state = None  # raw dict returned by the most recent graph.invoke()
    st.session_state.last_incident = None  # so the outcome can name what was run


def _build_graph():
    """Week 11: the same detectors, LLM client and checkpointer feed either arm, so switching arms
    changes only the diagnostic step - exactly the property baseline/graph.py is built around."""
    builder = build_graph if st.session_state.arm == "multi-agent" else build_baseline_graph
    return builder(
        st.session_state.detectors, st.session_state.ollama_client, st.session_state.checkpointer,
    )


def _train_demo_detector(service: str) -> None:
    """Trains a small Isolation Forest on synthetic NORMAL-looking telemetry for `service`, purely
    for exercising this page's approval flow on a machine with no real captured telemetry (no live
    Kubernetes deployment to run Week 5's experiments against). This never touches
    `ai-orchestration/models/` and is never mistaken for one of the project's real trained models -
    see dataset-tools/README.md's real-vs-synthetic labeling precedent (Week 6) for why that
    distinction is always surfaced, never hidden."""
    schema = ai_config.SERVICE_FEATURE_SCHEMAS[service]
    rng = random.Random(42)
    vectors = []
    for _ in range(60):
        record = _normal_record(service, rng)
        fv = extract_features(record)
        if fv is not None:
            vectors.append(fv)
    detector = AnomalyDetector(service)
    detector.train(vectors, training_source_experiment_ids=["streamlit-demo-synthetic"])
    st.session_state.detectors[service] = detector
    st.session_state.demo_detector_services.add(service)
    st.session_state.graph = _build_graph()


# --- Sample telemetry records ------------------------------------------------------------------
# Mirror exactly what telemetry/collector.py's collect_service() produces (see
# ai-orchestration/tests/conftest.py's *_record_factory fixtures for the same shape). Elevated
# values are set relative to the real, documented thresholds in rca/signatures.py, not guessed.

def _record(service: str, metrics: dict, fault_active: bool = False) -> dict:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": service,
        "correlation_id": str(uuid.uuid4()),
        "health": {"status": "UP", "components": {}},
        "metrics": metrics,
        "fault": {"faultActive": fault_active},
        "collection_error": None,
    }


def _base_metrics(rng: random.Random, service: str) -> dict:
    mem_lo, mem_hi = OBSERVED_BASELINE_RANGES[service]["jvm_memory_used_bytes"]
    committed_lo, committed_hi = OBSERVED_BASELINE_RANGES[service]["jvm_memory_committed_bytes"]
    return {
        "jvm_memory_used_bytes": round(rng.uniform(mem_lo, mem_hi), 1),
        "jvm_memory_committed_bytes": round(rng.uniform(committed_lo, committed_hi), 1),
        "jvm_memory_max_bytes": 4_294_967_296.0,
        # Small jitter (not a fixed constant) on every field, even ones whose real baseline is "0" -
        # a zero-variance training feature gives Isolation Forest nothing to split on, so it would
        # never learn to isolate a real deviation on that feature at all (see this file's git
        # history / ai-orchestration/README.md "Week 9 architecture" live-verification note for how
        # this was actually caught: a first version of this demo trainer used literal 0.0/10.0/0.0
        # constants here and the resulting detector failed to flag a live db-lock sample).
        "hikaricp_connections_active": round(max(0.0, rng.uniform(-0.4, 0.4)), 3) if service == "ledger-service" else None,
        "hikaricp_connections_idle": round(rng.uniform(9.0, 10.0), 3) if service == "ledger-service" else None,
        "hikaricp_connections_pending": round(max(0.0, rng.uniform(-0.2, 0.2)), 3) if service == "ledger-service" else None,
        "hikaricp_connections_acquire_seconds_avg": round(rng.uniform(0.0002, 0.001), 6) if service == "ledger-service" else None,
        "http_server_requests_count": round(rng.uniform(8, 12), 1),
        "http_server_requests_error_count": round(max(0.0, rng.uniform(-0.3, 0.3)), 3),
        "http_server_requests_avg_duration_ms": round(rng.uniform(10, 80), 3),
        "http_client_requests_count": round(rng.uniform(3, 7), 1) if service == "payment-service" else None,
        "http_client_requests_avg_duration_ms": round(rng.uniform(10, 60), 3) if service == "payment-service" else None,
        "process_cpu_usage": round(rng.uniform(0.0001, 0.02), 6),
        "system_cpu_usage": round(rng.uniform(0.05, 0.3), 6),
    }


def _normal_record(service: str, rng: random.Random) -> dict:
    return _record(service, _base_metrics(rng, service))


SAMPLE_PRESETS = {
    "normal — auth-service": lambda: _normal_record("auth-service", random.Random()),
    "normal — payment-service": lambda: _normal_record("payment-service", random.Random()),
    "normal — ledger-service": lambda: _normal_record("ledger-service", random.Random()),
    "normal — notification-service": lambda: _normal_record("notification-service", random.Random()),
    "memory-leak (root cause + symptom: payment-service)": lambda: _record(
        "payment-service",
        {**_base_metrics(random.Random(), "payment-service"), "jvm_memory_used_bytes": 500_000_000.0},
        fault_active=True,
    ),
    "db-lock (root cause + symptom: ledger-service)": lambda: _record(
        "ledger-service",
        {**_base_metrics(random.Random(), "ledger-service"), "hikaricp_connections_active": 10.0,
         "hikaricp_connections_idle": 0.0, "hikaricp_connections_pending": 4.0},
        fault_active=True,
    ),
    "auth-key-error (root cause: auth-service, symptom: payment-service)": lambda: _record(
        "payment-service",
        {**_base_metrics(random.Random(), "payment-service"), "http_server_requests_error_count": 20.0},
    ),
    "notification-latency (root cause: notification-service, symptom: payment-service)": lambda: _record(
        "payment-service",
        {**_base_metrics(random.Random(), "payment-service"), "http_client_requests_avg_duration_ms": 5200.0},
    ),
}


# --- Page ----------------------------------------------------------------------------------------
# The shell: theme, navigation and routing. Every page body lives in `ui/`; the pipeline
# bootstrap above is untouched, so the console drives exactly the graph it always did.

from ui import components as c            # noqa: E402
from ui import data as dsrc               # noqa: E402
from ui import incident_center            # noqa: E402
from ui import pages as ui_pages          # noqa: E402
from ui import theme                      # noqa: E402

_init_session()
st.session_state.setdefault("sample_presets", SAMPLE_PRESETS)
st.session_state.setdefault("history", dsrc.load_history())
st.session_state.setdefault("pending_incident", None)
st.session_state.setdefault("recorded_threads", set())
st.session_state.setdefault("nav", "Overview")

theme.inject()

NAV = [
    ("Overview", "Estate state and evaluation of record"),
    ("Incident Center", "Investigate a telemetry record"),
    ("Service Health", "Live Actuator readings"),
    ("Incident History", "Runs performed this session"),
    ("Evaluation", "Benchmarks and ablations"),
    ("Configuration", "Pipeline settings"),
]


def _sidebar() -> None:
    with st.sidebar:
        st.markdown(
            "<div style='padding:4px 12px 18px'>"
            "<div style='display:flex;align-items:center;gap:9px'>"
            "<div style='width:26px;height:26px;border-radius:7px;"
            "background:linear-gradient(135deg,var(--accent),#8B5CF6);display:grid;"
            "place-items:center;color:#fff;font-weight:700;font-size:.8125rem'>A</div>"
            "<div><div style='font-weight:650;font-size:.875rem;letter-spacing:-.01em'>"
            "AIOps Console</div>"
            "<div style='font-size:.6875rem;color:var(--fgf)'>Self-healing microservices</div>"
            "</div></div></div>",
            unsafe_allow_html=True)

        for label, _hint in NAV:
            is_active = st.session_state.nav == label
            if st.button(label, key=f"nav_{label}", use_container_width=True,
                         type="primary" if is_active else "secondary"):
                st.session_state.nav = label
                st.rerun()

        st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
        st.markdown("<div style='padding:0 12px'><div class='eyebrow'>Pipeline arm</div></div>",
                    unsafe_allow_html=True)
        arm = st.radio("Arm", ["multi-agent", "single-agent baseline"],
                       index=0 if st.session_state.arm == "multi-agent" else 1,
                       label_visibility="collapsed")
        if arm != st.session_state.arm:
            st.session_state.arm = arm
            st.session_state.graph = _build_graph()
            st.session_state.run_state = None
            st.session_state.thread_id = None
            st.rerun()

        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
        states = dsrc.service_states()
        counts = dsrc.estate_summary(states)
        ollama_up = st.session_state.ollama_client.is_available()
        estate_tone = ("success" if counts["healthy"] == len(states)
                       else "danger" if counts["down"] else "warn")
        services_pill = c.pill(f"{counts['healthy']}/{len(states)} up", estate_tone)
        model_pill = c.pill("online" if ollama_up else "offline",
                            "success" if ollama_up else "warn")
        exec_pill = c.pill("dry run" if ai_config.EXECUTION_DRY_RUN else "live",
                           "neutral" if ai_config.EXECUTION_DRY_RUN else "danger")
        row = ("<div style='display:flex;justify-content:space-between;"
               "align-items:center'><span class='note'>{}</span>{}</div>")
        st.markdown(
            "<div style='padding:0 12px'><div class='eyebrow'>System</div>"
            "<div style='display:flex;flex-direction:column;gap:7px'>"
            + row.format("Services", services_pill)
            + row.format("Model", model_pill)
            + row.format("Execution", exec_pill)
            + "</div></div>",
            unsafe_allow_html=True)

        st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)
        with st.expander("Advanced"):
            st.caption("Train a throwaway in-memory detector for a service with no persisted "
                       "model. Never written to disk.")
            demo_service = st.selectbox("Service", sorted(ai_config.SERVICE_FEATURE_SCHEMAS),
                                        key="demo_service_select")
            if st.button("Train demo detector", use_container_width=True):
                _train_demo_detector(demo_service)
                dsrc.detection_coverage.clear()
                st.success(f"Demo detector trained for {demo_service}.")


def _record_history() -> None:
    """Append a finished run to the session log, once per thread."""
    state = st.session_state.run_state
    thread = st.session_state.thread_id
    if not state or not thread or "__interrupt__" in state:
        return
    if thread in st.session_state.recorded_threads:
        return
    result = state.get("result") or {}
    incident = (st.session_state.get("pending_incident") or {}).get("incident_id") \
        or result.get("correlation_id") or "ad-hoc record"
    rca = result.get("root_cause_analysis") or {}
    decision = result.get("human_decision")
    safety = result.get("safety_check") or {}

    if result.get("decision") != "anomaly":
        label, tone = "no anomaly", "neutral"
        detail = f"{result.get('service', 'unknown')} scored within its learned normal range"
    elif safety and not safety.get("passed"):
        label, tone = "safety rejected", "danger"
        detail = "Blocked before reaching a human"
    elif decision == "rejected":
        label, tone = "operator rejected", "warn"
        detail = f"Diagnosed {rca.get('suspected_root_cause_service', '?')}; nothing executed"
    elif decision == "approved":
        verification = result.get("recovery_verification") or {}
        if verification.get("verified"):
            label, tone = "resolved", "success"
        else:
            label, tone = "executed, unverified", "warn"
        detail = (f"{rca.get('suspected_fault_type', '?')} → "
                  f"{rca.get('suspected_root_cause_service', '?')}")
    else:
        label, tone = "diagnosed", "info"
        detail = f"{rca.get('suspected_fault_type', '?')} → " \
                 f"{rca.get('suspected_root_cause_service', '?')}"

    entry = {
        "incident": incident, "detail": detail, "label": label, "tone": tone,
        "when": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
        "at": datetime.now(timezone.utc).isoformat(),
        "arm": st.session_state.arm,
        "detection_mode": ai_config.DETECTION_MODE,
    }
    st.session_state.history.append(entry)
    dsrc.append_history(entry)          # survives a console restart
    st.session_state.recorded_threads.add(thread)


_sidebar()
_record_history()

_ROUTES = {
    "Overview": ui_pages.overview,
    "Incident Center": incident_center.render,
    "Service Health": ui_pages.service_health,
    "Incident History": ui_pages.history,
    "Evaluation": ui_pages.evaluation,
    "Configuration": ui_pages.configuration,
}
_ROUTES.get(st.session_state.nav, ui_pages.overview)()
