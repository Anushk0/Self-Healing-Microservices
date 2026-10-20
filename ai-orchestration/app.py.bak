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
    "inc-0011", "inc-0010",              # auth-key-error   (cross-service)
    "inc-0067", "inc-0065",              # notification-latency (cross-service) - the headline demo
    "inc-0026", "inc-0024",              # db-lock
    "inc-0036", "inc-0051", "inc-0048", "inc-0038",  # memory-leak
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

_init_session()

st.title("AIOps Human Approval Console")
st.caption(
    "Every remediation proposal that passes the deterministic Safety Validator pauses here via "
    "LangGraph's interrupt() until a human clicks Approve or Reject - the graph cannot proceed on "
    "its own (CLAUDE.md rule 21). Approving executes the action for real, then re-checks whether "
    "the fault actually cleared; if it did not, a different action is proposed and needs its own "
    "approval."
)

with st.sidebar:
    st.subheader("Pipeline")
    arm = st.radio(
        "Which arm to run", ["multi-agent", "single-agent baseline"],
        index=0 if st.session_state.arm == "multi-agent" else 1,
        help="Week 11: both arms share detection, retrieval, safety, approval, execution and "
             "feedback. Only the diagnostic step differs.",
    )
    selected_arm = "multi-agent" if arm == "multi-agent" else "single-agent"
    if selected_arm != st.session_state.arm:
        st.session_state.arm = selected_arm
        st.session_state.graph = _build_graph()
        st.session_state.thread_id = None
        st.session_state.run_state = None
        st.rerun()

    st.subheader("Pipeline status")
    real_models = sorted(
        s for s in st.session_state.detectors if s not in st.session_state.demo_detector_services
    )
    demo_models = sorted(st.session_state.demo_detector_services)
    st.write(f"Real trained models loaded: {real_models or 'none'}")
    st.write(f"Demo (synthetic, in-memory) models: {demo_models or 'none'}")
    ollama_ok = st.session_state.ollama_client.is_available()
    st.write(f"Ollama ({st.session_state.ollama_client.model}): "
             f"{'available' if ollama_ok else 'unavailable - narrative text will be omitted, gracefully'}")

    st.divider()
    st.subheader("Anomaly threshold")
    st.caption(
        "`ANOMALY_SCORE_THRESHOLD` is an intentionally configurable knob (Week 7: 'make anomaly "
        "thresholds configurable'), surfaced here because Isolation Forest **saturates** for values "
        "outside its training range: a 2GB heap scores identically to a 50MB one, so an extreme "
        "fault can sit just below the default boundary. Lower it to run such a sample through the "
        "pipeline - the raw score is always shown, so nothing is hidden by moving the line."
    )
    threshold = st.number_input(
        "Score above which a record is an anomaly", value=float(ai_config.ANOMALY_SCORE_THRESHOLD),
        step=0.01, format="%.4f",
    )
    if threshold != ai_config.ANOMALY_SCORE_THRESHOLD:
        ai_config.ANOMALY_SCORE_THRESHOLD = threshold

    st.divider()
    st.subheader("Train a demo detector")
    st.caption(
        "No real trained model for a service? Train one here on synthetic NORMAL-looking "
        "telemetry, purely to exercise this page's approval flow. Clearly separate from this "
        "project's real models/ - never persisted to disk."
    )
    demo_service = st.selectbox("Service", sorted(ai_config.SERVICE_FEATURE_SCHEMAS), key="demo_service_select")
    if st.button("Train demo detector for this service"):
        _train_demo_detector(demo_service)
        st.success(f"Trained an in-memory demo detector for '{demo_service}'.")
        st.rerun()

    st.divider()
    if st.button("New run (clear current result)"):
        st.session_state.thread_id = None
        st.session_state.run_state = None
        st.rerun()

st.header("1. Provide a telemetry record")
input_mode = st.radio(
    "Source", ["Evaluation incident (real)", "Built-in sample", "Paste raw JSON"], horizontal=True,
)

record: dict | None = None
if input_mode == "Evaluation incident (real)":
    # Replays an actual held-out Evaluation incident through the same adapter the Week 12 harness
    # uses. This is the option to demo with: the synthetic built-in samples below are *not*
    # detected by the real trained detectors (every one scores below the threshold - a consequence
    # of the Isolation Forest saturation documented in the sidebar), so they cannot exercise the
    # anomaly branch without moving the threshold. These can.
    try:
        incidents = _load_evaluation_incidents_cached()
    except Exception as exc:  # noqa: BLE001 - dataset may not be built yet
        incidents = []
        st.warning(f"Could not load the Evaluation set ({exc}). Build the dataset first.")

    if incidents:
        detected_first = sorted(
            incidents, key=lambda i: (i["incident_id"] not in KNOWN_DETECTED_INCIDENTS, i["incident_id"])
        )
        def _label(inc: dict) -> str:
            mark = "✓ detected" if inc["incident_id"] in KNOWN_DETECTED_INCIDENTS else "  not detected"
            cross = "cross-service" if inc["root_cause_service"] != inc["symptom_service"] else "same-service"
            return (f"{mark} | {inc['incident_id']} | {inc['fault_type']} | {cross} "
                    f"| cause={inc['root_cause_service']}")
        chosen = st.selectbox("Evaluation incident", detected_first, format_func=_label)
        st.caption(
            "Ticked incidents are the ones the current detectors actually flag "
            f"({len(KNOWN_DETECTED_INCIDENTS)} of {len(incidents)}). The rest are shown so the "
            "detection limitation is visible rather than hidden - selecting one will correctly "
            "produce 'normal'."
        )
        try:
            record = _replay.incident_to_raw_record(chosen)
            st.json(record, expanded=False)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Could not replay {chosen['incident_id']}: {exc}")

elif input_mode == "Built-in sample":
    preset_name = st.selectbox("Sample", list(SAMPLE_PRESETS))
    record = SAMPLE_PRESETS[preset_name]()
    st.warning(
        "These synthetic samples are **not detected** by the real trained detectors — each scores "
        "below the threshold. Use *Evaluation incident (real)* above for a working demo, or lower "
        "the anomaly threshold in the sidebar."
    )
    st.json(record, expanded=False)
else:
    import json
    raw_text = st.text_area(
        "Raw telemetry record JSON (same shape as telemetry/collector.py's output)", height=200,
    )
    if raw_text.strip():
        try:
            record = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            st.error(f"Invalid JSON: {exc}")

run_disabled = record is None or st.session_state.run_state is not None
if st.button("Run detection pipeline", disabled=run_disabled, type="primary"):
    st.session_state.thread_id = str(uuid.uuid4())
    cfg = default_run_config(st.session_state.thread_id)
    st.session_state.run_state = st.session_state.graph.invoke(
        {"raw_record": record, "incident_id": None}, cfg,
    )
    st.rerun()

if st.session_state.run_state is not None and st.session_state.thread_id is not None:
    state = st.session_state.run_state
    st.header("2. Result")

    paused = "__interrupt__" in state
    if paused:
        payload = state["__interrupt__"][0].value
        rca = payload.get("root_cause_analysis")
        proposal = payload["remediation_proposal"]
        safety_check = payload["safety_check"]

        attempts = len(state.get("failed_actions") or [])
        if attempts:
            st.warning(
                f"Retry {attempts + 1}: {attempts} earlier remediation attempt(s) for this incident "
                f"failed or did not clear the fault, so a different action is now proposed. Each "
                f"retry needs its own approval — this is not the action you approved before."
            )
            for failed in state.get("failed_actions") or []:
                st.write(f"- previously tried `{failed.get('action_category')}` → {failed.get('reason')}")

        st.info("Awaiting human approval — the graph is paused and will not proceed without a decision.")
        if ai_config.EXECUTION_DRY_RUN:
            st.warning(
                "Dry-run mode is ON (AI_ORCHESTRATION_EXECUTION_DRY_RUN=true): approving will "
                "resolve and log the action but deliberately not carry it out."
            )
        else:
            st.error(
                "Approving will **actually execute** this action against the running system "
                "(Week 10). Rejecting executes nothing."
            )

        col1, col2 = st.columns(2)
        with col1:
            st.subheader("Root cause hypothesis")
            if rca:
                st.write(f"**Suspected fault type:** {rca.get('suspected_fault_type') or 'unknown'}")
                st.write(f"**Suspected root cause service:** {rca['suspected_root_cause_service']}")
                st.write(f"**Symptom-visible service:** {rca['symptom_service']}")
                st.write(f"**Confidence:** {rca['confidence']} (method: {rca['determination_method']})")
                st.write(f"**Evidence:** {rca['evidence_summary']}")
                if rca.get("llm_reasoning"):
                    st.write("**LLM reasoning chain:**")
                    st.write(rca["llm_reasoning"])
                if rca.get("metric_anomalies"):
                    st.write("**Anomalous metrics:**")
                    for m in rca["metric_anomalies"]:
                        st.write(f"- {m['metric_name']} = {m['observed_value']} ({m['deviation_description']})")
        with col2:
            st.subheader("Proposed remediation")
            st.write(f"**Action:** {proposal['recommended_action']}")
            st.write(f"**Target service:** {proposal['target_service']}")
            st.write(f"**Category:** {proposal['action_category']} | **Risk:** {proposal['risk_level']} "
                     f"| **Confidence:** {proposal['confidence']}")
            st.write(f"**Rationale:** {proposal['rationale']}")
            st.write(f"**Expected outcome:** {proposal['expected_outcome']}")
            st.success(f"Safety validator: PASSED — {safety_check['reason']}")

        st.subheader("3. Decision")
        reason = st.text_input("Reason (optional, recorded with your decision)", key="decision_reason")
        approve_col, reject_col = st.columns(2)
        cfg = default_run_config(st.session_state.thread_id)
        if approve_col.button("✅ Approve", type="primary"):
            st.session_state.run_state = st.session_state.graph.invoke(
                Command(resume={"decision": "approved", "reason": reason}), cfg,
            )
            st.rerun()
        if reject_col.button("❌ Reject"):
            st.session_state.run_state = st.session_state.graph.invoke(
                Command(resume={"decision": "rejected", "reason": reason}), cfg,
            )
            st.rerun()

    else:
        result = state["result"]
        st.write(f"**Decision:** {result['decision']}")
        st.write(f"**Service:** {result['service']}")

        if result["decision"] == "anomaly":
            st.subheader("Anomaly")
            ar = result["anomaly_result"]
            st.write(f"Anomaly score: {ar['anomaly_score']} (threshold {ar['threshold']})")

            rca = result["root_cause_analysis"]
            if rca:
                st.subheader("Root cause analysis")
                st.write(f"Suspected fault type: **{rca.get('suspected_fault_type') or 'unknown'}**")
                st.write(f"Root cause service: **{rca['suspected_root_cause_service']}** "
                         f"(symptom seen on {rca['symptom_service']})")
                st.write(f"Confidence: {rca['confidence']} ({rca['determination_method']})")

            proposal = result["remediation_proposal"]
            if proposal:
                st.subheader("Remediation proposal")
                st.write(proposal["recommended_action"])

            safety_check = result["safety_check"]
            if safety_check and not safety_check["passed"]:
                st.error(f"Rejected by Safety Validator before reaching human approval: {safety_check['reason']}")
                for rule in safety_check["violated_rules"]:
                    st.write(f"- {rule}")
            elif result["human_decision"] == "approved":
                st.success(f"Approved by a human operator" +
                           (f": {result['human_decision_reason']}" if result["human_decision_reason"] else "."))
            elif result["human_decision"] == "rejected":
                st.warning(f"Rejected by a human operator" +
                           (f": {result['human_decision_reason']}" if result["human_decision_reason"] else "."))

            # --- Week 10: what was actually done, and whether it worked ---
            execution = result.get("execution_result")
            if execution:
                st.subheader("Execution")
                st.write(f"**Status:** `{execution['status']}` — {execution['reason']}")
                if execution.get("action"):
                    st.write(f"**Action performed:** `{execution['action']['kind']}` on "
                             f"{execution['action']['target_service']}")
                if execution.get("detail"):
                    with st.expander("Execution detail (audit trail)"):
                        st.json(execution["detail"])

            verification = result.get("recovery_verification")
            if verification:
                st.subheader("Recovery verification")
                if verification["verified"]:
                    st.success(f"{verification['status']} — {verification['reason']}")
                elif verification["status"] == "fault_still_active":
                    st.error(f"False recovery — {verification['reason']}")
                else:
                    st.warning(f"{verification['status']} — {verification['reason']}")

            if result.get("mttr_seconds") is not None:
                st.metric("MTTR (detection → execution complete)", f"{result['mttr_seconds']:.1f}s")
            if result.get("remediation_attempts"):
                st.write(f"**Remediation attempts:** {result['remediation_attempts']} "
                         f"(cap {ai_config.MAX_REMEDIATION_ATTEMPTS})")

            feedback = result.get("feedback_result")
            if feedback:
                st.subheader("Feedback write-back")
                if feedback["status"] == "written":
                    st.success(f"Learned: {feedback['reason']}")
                elif feedback["status"] == "refused_evaluation_incident":
                    st.info(f"Not learned (leakage guard working as intended): {feedback['reason']}")
                else:
                    st.write(f"`{feedback['status']}` — {feedback['reason']}")

            diagnosis = result.get("single_agent_diagnosis")
            if diagnosis:
                st.subheader("Single-agent baseline diagnosis (Week 11)")
                st.write(f"**Status:** `{diagnosis['status']}`")
                if diagnosis.get("reasoning"):
                    st.write(diagnosis["reasoning"])
                if diagnosis.get("parse_issues"):
                    st.warning("The model's response needed correction before it could be used:")
                    for issue in diagnosis["parse_issues"]:
                        st.write(f"- {issue}")
        else:
            st.write("No anomaly detected (or insufficient data / no trained model) - safety "
                     "validation and human approval were not reached.")

        if result["limitations"]:
            st.subheader("Limitations / caveats")
            for limitation in result["limitations"]:
                st.write(f"- {limitation}")

        with st.expander("Full result JSON"):
            st.json(result)
