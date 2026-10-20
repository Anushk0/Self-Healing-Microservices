"""Incident Center - the investigation workspace.

Presentation only. Every decision shown here was made by the compiled LangGraph pipeline:
this module invokes the graph, reads the state it returns, and renders it. It never decides
whether something is anomalous, never approves anything on the operator's behalf, and never
reports an outcome the backend has not produced.
"""
from __future__ import annotations

import json
import uuid
from typing import Optional

import streamlit as st
from langgraph.types import Command

from ai_orchestration import config as ai_config
from ai_orchestration.evaluation import replay as _replay
from ai_orchestration.graph.workflow import default_run_config

from . import components as c
from . import data as dsrc

# determination_method is precise but opaque; this is the operator-facing translation.
METHOD_PLAIN = {
    "metric_signature":
        "Telemetry matched a known fault signature.",
    "metric_signature+rag_agreement":
        "Telemetry matched a known fault signature, and retrieved incidents agreed.",
    "metric_signature+rag_disagreement":
        "Telemetry matched a known fault signature. Retrieved incidents disagreed and were "
        "overruled - direct evidence outranks precedent.",
    "rag_only":
        "No signature matched. The closest retrieved incident supplied the answer.",
    "rag_override":
        "ABLATION: retrieval was permitted to overrule the signature.",
    "fallback_unknown":
        "Nothing matched. No fault type could be named, so the symptom service stands as a "
        "placeholder rather than a conclusion.",
}

RISK_TONE = {"low": "success", "medium": "warn", "high": "danger"}
CONFIDENCE_TONE = {"high": "success", "medium": "warn", "low": "neutral"}


# --- pipeline stage derivation -------------------------------------------------------------

def _stage_states(state: Optional[dict]) -> tuple[list[str], list[str]]:
    """Map the graph's real position onto the seven display stages.

    Deliberately conservative: a stage is `done` only when the state proves it ran.
    """
    states = ["idle"] * 7
    caps = [""] * 7
    if not state:
        states[0] = "now"
        return states, caps

    paused = "__interrupt__" in state
    result = state.get("result") or {}
    anomaly = state.get("anomaly_result") or result.get("anomaly_result") or {}

    states[0] = "done"
    caps[0] = (anomaly.get("service") or "")

    if anomaly:
        states[1] = "done"
        score = anomaly.get("anomaly_score")
        caps[1] = f"score {score:.4f}" if isinstance(score, (int, float)) else (anomaly.get("status") or "")
    is_anomaly = bool(anomaly.get("is_anomaly"))

    if not paused and not is_anomaly:
        for i in range(2, 7):
            states[i] = "skip"
        caps[2] = "not reached"
        return states, caps

    rca = state.get("root_cause_analysis") or result.get("root_cause_analysis") or {}
    if rca:
        states[2] = "done"
        caps[2] = rca.get("suspected_fault_type") or "unknown"

    safety = state.get("safety_check") or result.get("safety_check") or {}
    if paused:
        safety = (state["__interrupt__"][0].value or {}).get("safety_check") or safety
    if safety:
        if safety.get("passed"):
            states[3] = "done"
            caps[3] = "passed"
        else:
            states[3] = "fail"
            caps[3] = "rejected"
            for i in range(4, 7):
                states[i] = "skip"
            return states, caps

    if paused:
        states[4] = "now"
        caps[4] = "awaiting decision"
        return states, caps

    decision = result.get("human_decision")
    if decision == "approved":
        states[4] = "done"
        caps[4] = "approved"
    elif decision == "rejected":
        states[4] = "fail"
        caps[4] = "rejected"
        states[5] = states[6] = "skip"
        return states, caps

    execution = result.get("execution_result") or {}
    if execution:
        status = execution.get("status")
        if status in ("executed", "dry_run"):
            states[5] = "done"
            caps[5] = status.replace("_", " ")
        elif status == "skipped_no_op":
            states[5] = "skip"
            caps[5] = "no action"
        else:
            states[5] = "fail"
            caps[5] = status or "failed"

    verification = result.get("recovery_verification") or {}
    if verification:
        if verification.get("verified"):
            states[6] = "done"
            caps[6] = "fault cleared"
        elif verification.get("status") == "not_applicable":
            states[6] = "skip"
            caps[6] = "nothing executed"
        else:
            states[6] = "fail"
            caps[6] = (verification.get("status") or "").replace("_", " ")
    return states, caps


# --- incident selection ----------------------------------------------------------------------


def _provenance_banner() -> None:
    """A replayed incident is a historical record, not the live estate.

    Without this the console shows a fault under investigation while Service Health shows every
    service healthy, and the two look contradictory. They are not: they describe different
    moments. The distinction is stated rather than left for the operator to infer.
    """
    source = st.session_state.get("record_source")
    inc = st.session_state.get("pending_incident") or {}
    if source == "Recorded incident" and inc:
        started = (inc.get("phases") or {}).get("FAULT_ACTIVE", {}).get("start") or             inc.get("start_time") or "an earlier run"
        st.markdown(
            f"<div style='display:flex;align-items:center;gap:10px;padding:9px 14px;"
            f"background:var(--s2);border:1px solid var(--bd);border-left:2px solid var(--info);"
            f"border-radius:var(--r-sm);margin-bottom:14px'>"
            f"{c.pill('replayed record', 'info')}"
            f"<span class='note'>Captured <span class='mono'>{started}</span>. This is a stored "
            f"incident being re-run through the pipeline, not a live event &mdash; the services on "
            f"<b>Service Health</b> show their state <i>now</i>, which is why they can be healthy "
            f"while this investigation shows a fault.</span></div>",
            unsafe_allow_html=True)
    elif source == "Built-in sample":
        st.markdown(
            f"<div style='display:flex;align-items:center;gap:10px;padding:9px 14px;"
            f"background:var(--s2);border:1px solid var(--bd);border-left:2px solid var(--warn);"
            f"border-radius:var(--r-sm);margin-bottom:14px'>"
            f"{c.pill('synthetic sample', 'warn')}"
            f"<span class='note'>A hand-built record for exercising the console. It does not "
            f"correspond to any real or recorded event.</span></div>",
            unsafe_allow_html=True)


def _incident_picker(detected_ids: set[str]) -> Optional[dict]:
    incidents = dsrc.evaluation_incidents()
    if not incidents:
        c.empty_state("Evaluation corpus unavailable",
                      "The Evaluation incident file could not be read. Build the dataset with "
                      "<span class='mono'>python -m dataset_tools.build_dataset</span>.")
        return None

    left, mid, right = st.columns([2, 2, 2], gap="medium")
    faults = ["All fault types"] + sorted({i["fault_type"] for i in incidents})
    fault_filter = left.selectbox("Fault type", faults, label_visibility="collapsed")
    scope = mid.selectbox("Scope", ["All incidents", "Cross-service only", "Same-service only"],
                          label_visibility="collapsed")
    detect_filter = right.selectbox(
        "Detection", ["Detectable only", "All (incl. missed)", "Missed only"],
        label_visibility="collapsed",
        help="Which incidents the currently configured detector actually surfaces.")

    pool = []
    for inc in incidents:
        cross = inc["root_cause_service"] != inc["symptom_service"]
        hit = inc["incident_id"] in detected_ids
        if fault_filter != "All fault types" and inc["fault_type"] != fault_filter:
            continue
        if scope == "Cross-service only" and not cross:
            continue
        if scope == "Same-service only" and cross:
            continue
        if detect_filter == "Detectable only" and not hit:
            continue
        if detect_filter == "Missed only" and hit:
            continue
        pool.append(inc)

    if not pool:
        c.spacer(2)
        c.empty_state("No incidents match these filters",
                      "Widen the fault type, scope or detection filter above.")
        return None

    pool.sort(key=lambda i: (i["incident_id"] not in detected_ids, i["incident_id"]))

    def label(inc: dict) -> str:
        cross = inc["root_cause_service"] != inc["symptom_service"]
        mark = "●" if inc["incident_id"] in detected_ids else "○"
        kind = "cross-service" if cross else "same-service"
        return (f"{mark}  {inc['incident_id']}   ·   {inc['fault_type']}   ·   {kind}"
                f"   ·   cause: {inc['root_cause_service']}")

    chosen = st.selectbox("Incident", pool, format_func=label, label_visibility="collapsed")
    st.markdown(
        f"<div class='note' style='margin-top:-4px'>● surfaced by the current detector "
        f"({len(detected_ids)} of {len(incidents)}) &nbsp;·&nbsp; ○ missed - selecting one "
        f"correctly returns <span class='mono'>normal</span>, which is the detection limitation "
        f"shown rather than hidden. Showing {len(pool)} incident(s).</div>",
        unsafe_allow_html=True)
    return chosen


# --- panels ------------------------------------------------------------------------------------

def _telemetry_inspector(record: dict) -> None:
    metrics = {k: v for k, v in (record.get("metrics") or {}).items() if v is not None}
    with st.expander(f"Telemetry inspector — {len(metrics)} metrics on "
                     f"{record.get('service', 'unknown')}"):
        c.note("The service's own fault flag is withheld from the pipeline "
               "(<span class='mono'>fault: null</span>): for a same-service fault it would be "
               "the answer key.")
        rows = []
        for key in sorted(metrics):
            value = metrics[key]
            shown = f"{value:,.4g}" if isinstance(value, float) else f"{value}"
            rows.append((key, f"<span class='mono'>{shown}</span>"))
        c.kv_rows(rows)


def _diagnosis_panel(rca: dict) -> None:
    cause = rca.get("suspected_root_cause_service") or "unknown"
    symptom = rca.get("symptom_service") or "unknown"
    fault = rca.get("suspected_fault_type") or "not identified"
    confidence = str(rca.get("confidence") or "unknown")
    method = rca.get("determination_method") or ""

    c.panel_open("Diagnosis", c.pill(confidence.title() + " confidence",
                                     CONFIDENCE_TONE.get(confidence, "neutral")))
    c.causal_flow(symptom, cause)
    c.spacer(4)

    c.kv_rows([
        ("Fault type", f"<span class='mono'>{fault}</span>"),
        ("Determination", f"<span class='mono'>{method}</span>"),
    ])

    plain = METHOD_PLAIN.get(method)
    if plain:
        st.markdown(f"<div style='margin-top:12px;font-size:.875rem;line-height:1.6'>{plain}</div>",
                    unsafe_allow_html=True)
    if rca.get("evidence_summary"):
        st.markdown(f"<div class='note' style='margin-top:10px'>{rca['evidence_summary']}</div>",
                    unsafe_allow_html=True)
    c.panel_close()

    anomalies = rca.get("metric_anomalies") or []
    driving = [m for m in anomalies
               if "within documented baseline" not in (m.get("deviation_description") or "")]
    context = [m for m in anomalies if m not in driving]

    if driving:
        c.panel_open(f"Supporting evidence ({len(driving)})")
        st.markdown("".join(
            c.evidence_item(m.get("metric_name", "?"), f"{m.get('observed_value')}",
                            m.get("deviation_description", ""))
            for m in driving), unsafe_allow_html=True)
        if context:
            with st.expander(f"{len(context)} further metric(s), all within baseline"):
                st.markdown("".join(
                    c.evidence_item(m.get("metric_name", "?"), f"{m.get('observed_value')}",
                                    m.get("deviation_description", ""))
                    for m in context), unsafe_allow_html=True)
        c.panel_close()

    if rca.get("llm_reasoning"):
        with st.expander("Language-model narrative — explanatory only, cannot change the conclusion"):
            st.markdown(rca["llm_reasoning"])


def _retrieval_panel(retrieval: Optional[dict]) -> None:
    if not retrieval:
        return
    status = retrieval.get("status")
    incidents = retrieval.get("incidents") or []
    right = c.pill(f"{len(incidents)} retrieved", "info" if incidents else "neutral")
    c.panel_open("Retrieved precedent", right)
    if status != "retrieved" or not incidents:
        c.note(retrieval.get("reason") or "No similar incidents were retrieved.")
    else:
        c.note("Retrieval is advisory. It can raise or lower confidence; it cannot change the "
               "conclusion drawn from telemetry.")
        rows = []
        for inc in incidents:
            ident = inc.get("incident_id", "?")
            ft = inc.get("fault_type", "?")
            root = inc.get("root_cause_service", "?")
            dist = inc.get("distance")
            dtxt = f"  ·  distance {dist:.4f}" if isinstance(dist, (int, float)) else ""
            rows.append((ident, f"<span class='mono'>{ft}</span> → {root}"
                                f"<span class='note'>{dtxt}</span>"))
        c.kv_rows(rows)
    c.panel_close()


def _remediation_panel(proposal: dict, safety: dict) -> None:
    risk = str(proposal.get("risk_level", "")).lower()
    passed = bool(safety.get("passed"))
    c.panel_open("Proposed remediation",
                 c.pill("Safety passed" if passed else "Safety rejected",
                        "success" if passed else "danger"))
    st.markdown(
        f"<div style='font-size:1rem;font-weight:600;line-height:1.45;margin-bottom:14px'>"
        f"{proposal.get('recommended_action', '')}</div>", unsafe_allow_html=True)
    c.kv_rows([
        ("Target service", f"<span class='mono'>{proposal.get('target_service', '-')}</span>"),
        ("Action category", f"<span class='mono'>{proposal.get('action_category', '-')}</span>"),
        ("Risk level", c.pill(risk or "n/a", RISK_TONE.get(risk, "neutral"))),
        ("Expected outcome", proposal.get("expected_outcome", "-")),
    ])
    if not passed:
        c.spacer(3)
        st.error(safety.get("reason", "Rejected by the safety validator."))
        for rule in safety.get("violated_rules", []):
            c.note(f"· {rule}")
    with st.expander("Rationale"):
        st.markdown(proposal.get("rationale", "-"))
    c.panel_close()


def _approval_panel(thread_id: str, graph) -> None:
    """The gate. Rendered as the most prominent element on the page when it is active."""
    c.panel_open("Human approval required",
                 c.pill("Pipeline paused", "warn"))
    st.markdown(
        "<div style='font-size:.875rem;color:var(--fgm);line-height:1.6;margin-bottom:14px'>"
        "The graph is suspended at <span class='mono'>interrupt()</span> and cannot continue "
        "without an explicit decision. Only the literal value <span class='mono'>approved</span> "
        "proceeds to execution; anything else routes to feedback and executes nothing."
        "</div>", unsafe_allow_html=True)

    if ai_config.EXECUTION_DRY_RUN:
        st.info("Dry-run mode is on. Approving resolves and logs the action without carrying it out.")
    else:
        st.warning("Execution is live. Approving will carry this action out against the running "
                   "system.")

    reason = st.text_input("Reason (recorded with the decision)",
                           key=f"reason_{thread_id}", placeholder="Optional")
    confirm = st.checkbox("I have reviewed the diagnosis and the proposed action",
                          key=f"confirm_{thread_id}")

    approve_col, reject_col, _ = st.columns([1, 1, 3])
    cfg = default_run_config(thread_id)
    if approve_col.button("Approve", type="primary", use_container_width=True,
                          disabled=not confirm,
                          help="Confirm the review checkbox first" if not confirm else None):
        with st.spinner("Executing and verifying recovery..."):
            st.session_state.run_state = graph.invoke(
                Command(resume={"decision": "approved", "reason": reason}), cfg)
        st.session_state.last_action = ("approved", reason)
        st.rerun()
    if reject_col.button("Reject", use_container_width=True):
        st.session_state.run_state = graph.invoke(
            Command(resume={"decision": "rejected", "reason": reason}), cfg)
        st.session_state.last_action = ("rejected", reason)
        st.rerun()
    c.panel_close()


def _outcome_panel(result: dict) -> None:
    execution = result.get("execution_result") or {}
    verification = result.get("recovery_verification") or {}
    feedback = result.get("feedback_result") or {}

    c.panel_open("Execution and verification")
    rows = []
    if execution:
        action = execution.get("action") or {}
        status = execution.get("status", "unknown")
        tone = "success" if status in ("executed", "dry_run") else (
            "neutral" if status == "skipped_no_op" else "danger")
        rows.append(("Execution", c.pill(status.replace("_", " "), tone)))
        if action:
            rows.append(("Action performed",
                         f"<span class='mono'>{action.get('kind', '-')}</span> on "
                         f"<span class='mono'>{action.get('target_service', '-')}</span>"))
        rows.append(("Detail", f"<span class='note'>{execution.get('reason', '')}</span>"))
    if verification:
        if verification.get("verified"):
            tone, label = "success", "fault cleared"
        elif verification.get("status") == "fault_still_active":
            tone, label = "danger", "false recovery - fault still active"
        elif verification.get("status") == "not_applicable":
            tone, label = "neutral", "nothing executed"
        else:
            tone, label = "warn", (verification.get("status") or "unverified").replace("_", " ")
        rows.append(("Recovery", c.pill(label, tone)))
        rows.append(("Verified by", f"<span class='note'>{verification.get('reason', '')}</span>"))
        metric_check = (verification.get("detail") or {}).get("metric_check") or {}
        if metric_check.get("status") in ("cleared", "still_elevated"):
            rows.append(("Metric check",
                         f"<span class='mono'>{metric_check.get('metric')}</span> = "
                         f"{metric_check.get('observed')} vs threshold "
                         f"{metric_check.get('threshold')}"))
    if result.get("mttr_seconds") is not None:
        rows.append(("Detection → action complete", f"{result['mttr_seconds']:.1f} s"))
    if result.get("remediation_attempts"):
        rows.append(("Remediation attempts",
                     f"{result['remediation_attempts']} of {ai_config.MAX_REMEDIATION_ATTEMPTS}"))
    if feedback:
        status = feedback.get("status")
        if status == "written":
            rows.append(("Retrieval memory", c.pill("recorded", "success")))
        elif status == "refused_evaluation_incident":
            rows.append(("Retrieval memory", c.pill("refused — leakage guard", "neutral")))
            rows.append(("", f"<span class='note'>{feedback.get('reason', '')}</span>"))
        else:
            rows.append(("Retrieval memory", f"<span class='mono'>{status}</span>"))
    if rows:
        c.kv_rows(rows)
    else:
        c.note("Nothing was executed for this incident.")
    c.panel_close()


# --- page --------------------------------------------------------------------------------------

def render() -> None:
    coverage = dsrc.detection_coverage(ai_config.DETECTION_MODE) or {}
    detected_ids = set(coverage.get("detected") or [])

    arm = st.session_state.arm
    c.page_header(
        "Incident Center",
        "Investigate one telemetry record end to end. Nothing executes without approval.",
        c.pill(f"{arm}", "accent") + "&nbsp;" + c.pill(
            f"detection: {ai_config.DETECTION_MODE}", "info"),
    )

    state = st.session_state.run_state
    states, caps = _stage_states(state)
    c.stepper(states, caps)
    if state is not None:
        _provenance_banner()

    # ---------- input ----------
    if state is None:
        c.panel_open("Select an incident")
        source = st.radio("Source", ["Recorded incident", "Built-in sample", "Paste JSON"],
                          horizontal=True, label_visibility="collapsed")
        record: Optional[dict] = None
        chosen: Optional[dict] = None

        if source == "Recorded incident":
            chosen = _incident_picker(detected_ids)
            if chosen is not None:
                try:
                    record = _replay.incident_to_raw_record(chosen)
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Could not replay {chosen['incident_id']}: {exc}")
        elif source == "Built-in sample":
            presets = st.session_state.sample_presets
            name = st.selectbox("Sample", list(presets), label_visibility="collapsed")
            record = presets[name]
            c.note("Synthetic records for exercising the console. Most score below the detection "
                   "threshold; recorded incidents are the better demonstration.")
        else:
            raw = st.text_area("JSON", height=160, label_visibility="collapsed",
                               placeholder='{"service": "payment-service", "metrics": { ... }}')
            if raw.strip():
                try:
                    record = json.loads(raw)
                except ValueError as exc:
                    st.error(f"Invalid JSON: {exc}")
        c.panel_close()

        if record:
            _telemetry_inspector(record)
            st.session_state.pending_incident = chosen
            st.session_state.record_source = source
            if st.button("Run diagnosis", type="primary"):
                st.session_state.thread_id = str(uuid.uuid4())
                cfg = default_run_config(st.session_state.thread_id)
                with st.spinner("Extracting features, detecting, diagnosing, safety-checking…"):
                    try:
                        st.session_state.run_state = st.session_state.graph.invoke(
                            {"raw_record": record,
                             "incident_id": (chosen or {}).get("incident_id")}, cfg)
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"The pipeline raised: {type(exc).__name__}: {exc}")
                        st.session_state.run_state = None
                        return
                st.rerun()
        return

    # ---------- paused at approval ----------
    if "__interrupt__" in state:
        payload = state["__interrupt__"][0].value
        rca = payload.get("root_cause_analysis") or {}
        proposal = payload["remediation_proposal"]
        safety = payload["safety_check"]

        attempts = len(state.get("failed_actions") or [])
        if attempts:
            st.warning(f"Retry {attempts + 1}. {attempts} earlier attempt(s) did not clear the "
                       f"fault. This is a different action and needs its own approval.")
            for failed in state.get("failed_actions") or []:
                c.note(f"· tried <span class='mono'>{failed.get('action_category')}</span> — "
                       f"{failed.get('reason')}")

        cause = rca.get("suspected_root_cause_service") or "unknown"
        symptom = rca.get("symptom_service") or "unknown"
        cross = cause != symptom
        c.verdict(
            "Root cause identified", cause,
            (f"Symptoms surfaced on <b>{symptom}</b>, which is not the faulty service."
             if cross else "Symptoms and root cause are the same service."),
            "warn")

        _approval_panel(st.session_state.thread_id, st.session_state.graph)

        left, right = st.columns([1.15, 1], gap="large")
        with left:
            _diagnosis_panel(rca)
        with right:
            _remediation_panel(proposal, safety)
            _retrieval_panel(state.get("retrieval"))
        _new_run_button()
        return

    # ---------- finished ----------
    result = state.get("result") or {}
    if result.get("decision") != "anomaly":
        inc = st.session_state.get("pending_incident") or {}
        inc_id = inc.get("incident_id")
        if inc_id and inc_id not in detected_ids:
            c.verdict(
                "Not detected", "No anomaly flagged",
                f"<b>{inc_id}</b> is a genuine <b>{inc.get('fault_type')}</b> fault rooted at "
                f"<b>{inc.get('root_cause_service')}</b>, but the configured detector did not "
                f"surface it, so diagnosis never ran. This is one of "
                f"{coverage.get('total', 0) - len(detected_ids)} such incidents in the corpus.",
                "warn")
        else:
            c.verdict("Outcome", "No anomaly detected",
                      f"{result.get('service', 'the service')} scored within its learned normal "
                      f"range. Diagnosis, safety validation and approval were not reached.", "")
        anomaly = result.get("anomaly_result") or {}
        if anomaly.get("anomaly_score") is not None:
            c.tile_row([
                c.tile("Anomaly score", f"{anomaly['anomaly_score']:.4f}",
                       f"threshold {anomaly.get('threshold')}"),
                c.tile("Service examined", str(result.get("service", "-"))),
                c.tile("Detection mode", ai_config.DETECTION_MODE),
            ])
        if anomaly.get("reason"):
            c.spacer(4)
            c.note(anomaly["reason"])
        _new_run_button()
        return

    rca = result.get("root_cause_analysis") or {}
    cause = rca.get("suspected_root_cause_service") or "unknown"
    symptom = rca.get("symptom_service") or result.get("service", "unknown")
    safety = result.get("safety_check") or {}
    decision = result.get("human_decision")

    if safety and not safety.get("passed"):
        c.verdict("Outcome", "Blocked by safety validation",
                  "The proposal never reached a human. A rule violation should not consume "
                  "operator attention.", "bad")
    elif decision == "approved":
        verification = result.get("recovery_verification") or {}
        if verification.get("verified"):
            c.verdict("Outcome", f"Resolved — {cause}",
                      "The action was approved, executed, and recovery was verified against the "
                      "service's own telemetry.", "ok")
        else:
            c.verdict("Outcome", f"Action applied to {cause}",
                      "Approved and executed. Recovery was not confirmed — see verification below.",
                      "warn")
    elif decision == "rejected":
        c.verdict("Outcome", "Rejected by operator",
                  "Nothing was executed. The decision and its reason are recorded with the incident.",
                  "warn")
    else:
        c.verdict("Outcome", f"Diagnosed — {cause}", "", "")

    anomaly = result.get("anomaly_result") or {}
    cross = cause != symptom
    c.tile_row([
        c.tile("Root cause", cause, "named by the deterministic layer", "danger"),
        c.tile("Symptom service", symptom,
               "errors surfaced here, not at the cause" if cross
               else "symptom and cause are the same service",
               "warn" if cross else None),
        c.tile("Fault type", str(rca.get("suspected_fault_type") or "unknown"),
               str(rca.get("determination_method") or "")),
        c.tile("Anomaly score",
               f"{anomaly.get('anomaly_score'):.4f}" if isinstance(
                   anomaly.get("anomaly_score"), (int, float)) else "n/a",
               f"threshold {anomaly.get('threshold', '-')}"),
    ])
    c.spacer(5)

    left, right = st.columns([1.15, 1], gap="large")
    with left:
        if rca:
            _diagnosis_panel(rca)
    with right:
        proposal = result.get("remediation_proposal")
        if proposal:
            _remediation_panel(proposal, safety or {})
        _outcome_panel(result)
        _retrieval_panel(result.get("retrieval"))

    if result.get("limitations"):
        with st.expander(f"Limitations recorded for this run ({len(result['limitations'])})"):
            for limitation in result["limitations"]:
                st.markdown(f"- {limitation}")
    with st.expander("Full pipeline result (JSON)"):
        st.json(result)
    _new_run_button()


def _new_run_button() -> None:
    c.spacer(2)
    if st.button("Investigate another incident"):
        st.session_state.run_state = None
        st.session_state.thread_id = None
        st.session_state.pending_incident = None
        st.rerun()
