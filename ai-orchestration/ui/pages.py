"""Overview, Service Health, Evaluation, History and Configuration.

Every figure on these pages is either polled live from the running services or read from an
artefact on disk. Where a value cannot be obtained the page says so; nothing is simulated to
make a dashboard look populated.
"""
from __future__ import annotations

from typing import Optional

import streamlit as st

from ai_orchestration import config as ai_config

from . import components as c
from . import data as dsrc

STATUS_TONE = {"healthy": "success", "degraded": "warn", "down": "danger", "unknown": "neutral"}
STATUS_LABEL = {"healthy": "Healthy", "degraded": "Degraded", "down": "Unavailable",
                "unknown": "Unknown"}


# --- overview ------------------------------------------------------------------------------

def overview() -> None:
    c.page_header("Overview", "Live estate state, detector inventory and the evaluation of record.")

    states = dsrc.service_states()
    counts = dsrc.estate_summary(states)
    reachable = len(states) - counts["down"]

    estate_tone = "success" if counts["healthy"] == len(states) else (
        "danger" if counts["down"] else "warn")
    detectors = dsrc.detector_inventory()
    trained = sum(1 for d in detectors if d["trained"])
    corpus = dsrc.corpus_breakdown()
    coverage = dsrc.detection_coverage(ai_config.DETECTION_MODE)

    estate_status = ("All healthy" if counts["healthy"] == len(states)
                     else f"{counts['down']} unavailable" if counts["down"]
                     else f"{counts['degraded']} degraded")
    cov_pct = (100 * len(coverage["detected"]) / coverage["total"]) if coverage else None
    c.tile_row([
        c.tile("Services reachable", f"{reachable} of {len(states)}",
               f"{counts['healthy']} healthy · {counts['degraded']} degraded · "
               f"{counts['down']} unavailable", estate_tone, estate_status),
        c.tile("Detector models", f"{trained} trained",
               f"one per service, {len(detectors)} services covered"),
        c.tile("Evaluation corpus", f"{corpus['total']} incidents",
               f"{corpus['cross_service']} cross-service · "
               f"{corpus['total'] - corpus['cross_service']} same-service"),
        c.tile("Detection coverage",
               f"{cov_pct:.0f}%" if cov_pct is not None else "unknown",
               (f"{len(coverage['detected'])} of {coverage['total']} incidents surfaced · "
                f"mode {ai_config.DETECTION_MODE}") if coverage else "could not be computed",
               "accent"),
    ])
    c.spacer(5)

    left, right = st.columns([1.3, 1], gap="large")

    with left:
        c.panel_open("Service estate", c.pill(f"polled live · {len(states)} services", "info"))
        for s in states:
            label, value = s.headline
            fault_note = ""
            if s.fault_active:
                fault_note = c.pill("fault injected", "warn")
            elif s.error:
                fault_note = f"<span class='note'>{s.error[:60]}</span>"
            st.markdown(
                f"<div style='display:flex;align-items:center;justify-content:space-between;"
                f"gap:12px;padding:10px 0;border-bottom:1px solid var(--bd)'>"
                f"<div style='min-width:0'>"
                f"<div class='mono' style='font-weight:500'>{s.name}</div>"
                f"<div class='note'>:{s.port} &nbsp;·&nbsp; {label}: {value}</div></div>"
                f"<div style='display:flex;gap:8px;align-items:center'>{fault_note}"
                f"{c.pill(STATUS_LABEL[s.status], STATUS_TONE[s.status])}</div></div>",
                unsafe_allow_html=True)
        c.panel_close()

        _recent_activity()

    with right:
        _evaluation_headline()
        _corpus_panel(corpus)


def _evaluation_headline() -> None:
    m = dsrc.metrics_json()
    c.panel_open("Evaluation of record",
                 c.pill("docs/evaluation", "neutral", dot=False))
    if not m:
        c.note("metrics.json could not be read.")
        c.panel_close()
        return
    try:
        multi = m["per_arm"]["multi_agent"]["performance"]
        single = m["per_arm"]["single_agent"]["performance"]
    except (KeyError, TypeError):
        c.note("The metrics file is present but does not contain both arms.")
        c.panel_close()
        return

    c.comparison_bars("Accuracy when answered", [
        ("Multi-agent", multi["accuracy_when_answered"] * 100, 100.0, "success"),
        ("Single-agent baseline", single["accuracy_when_answered"] * 100, 100.0, "neutral"),
    ], unit="%")
    c.kv_rows([
        ("Incidents evaluated", str(multi.get("incidents_total", "-"))),
        ("Answered / abstained",
         f"{multi.get('answered', '-')} / {multi.get('abstained', '-')}"),
        ("Abstention rate", f"{multi.get('abstention_rate', 0) * 100:.1f}%"),
        ("Model used", f"<span class='mono'>{m.get('environment', {}).get('ollama_model', '-')}</span>"),
        ("Run mode", f"<span class='mono'>{m.get('run', {}).get('mode', '-')}</span>"),
    ])
    c.note("This is the published run, preserved for reproducibility. It was produced with "
           "detection mode <span class='mono'>isolation_forest</span>.")
    c.panel_close()


def _corpus_panel(corpus: dict) -> None:
    c.panel_open("Incident corpus")
    total = corpus["total"] or 1
    rows = [(fault, count, total, "accent") for fault, count in sorted(corpus["by_fault"].items())]
    if rows:
        c.comparison_bars("Incidents by fault type", rows)
    sources = ", ".join(f"{k}: {v}" for k, v in corpus["sources"].items()) or "unknown"
    c.kv_rows([
        ("Cross-service", f"{corpus['cross_service']} of {corpus['total']}"),
        ("Provenance", f"<span class='mono'>{sources}</span>"),
    ])
    c.note("The Memory partition is never opened by this console — only the Evaluation set is "
           "read, which is the same one-way discipline the pipeline enforces (rule 22).")
    c.panel_close()


def _recent_activity() -> None:
    history = st.session_state.get("history") or []
    c.panel_open("Session activity",
                 c.pill(f"{len(history)} run(s)", "neutral", dot=False))
    if not history:
        c.empty_state("No investigations yet",
                      "Runs appear here once you investigate an incident.", compact=True)
        c.panel_close()
        return
    for item in reversed(history[-8:]):
        tone = item["tone"]
        st.markdown(
            f"<div style='display:flex;align-items:center;justify-content:space-between;"
            f"gap:12px;padding:9px 0;border-bottom:1px solid var(--bd)'>"
            f"<div><div class='mono' style='font-size:.8125rem'>{item['incident']}</div>"
            f"<div class='note'>{item['detail']}</div></div>"
            f"{c.pill(item['label'], tone)}</div>", unsafe_allow_html=True)
    c.panel_close()


# --- service health ---------------------------------------------------------------------------

def service_health() -> None:
    head, action = st.columns([5, 1], gap="small")
    with head:
        c.page_header("Service Health",
                      "Polled directly from each service's Actuator endpoints, every 10 seconds.")
    with action:
        c.spacer(3)
        if st.button("Refresh", use_container_width=True):
            dsrc.service_states.clear()
            st.rerun()

    states = dsrc.service_states()
    cols = st.columns(2, gap="medium")
    for i, s in enumerate(states):
        with cols[i % 2]:
            label, value = s.headline
            c.panel_open(s.name, c.pill(STATUS_LABEL[s.status], STATUS_TONE[s.status]))
            rows = [
                ("Endpoint", f"<span class='mono'>{s.base_url}</span>"),
                ("Port", f"<span class='mono'>{s.port}</span>"),
                (label, f"<span class='mono'>{value}</span>"),
            ]
            if s.fault_active is not None:
                rows.append(("Fault injection",
                             c.pill("active", "warn") if s.fault_active
                             else c.pill("inactive", "success")))
            deps = dsrc.DEPENDENCIES.get(s.name) or []
            rows.append(("Calls downstream",
                         ", ".join(f"<span class='mono'>{d}</span>" for d in deps) or
                         "<span class='note'>none</span>"))
            if s.error:
                rows.append(("Error", f"<span class='note'>{s.error}</span>"))
            c.kv_rows(rows)
            if s.metrics:
                with st.expander("All collected metrics"):
                    c.kv_rows([
                        (k, f"<span class='mono'>{v:,.4g}</span>" if isinstance(v, float)
                         else f"<span class='mono'>{v}</span>")
                        for k, v in sorted(s.metrics.items()) if v is not None
                    ])
            c.panel_close()

    _live_graphs()
    _dependency_panel(states)


# --- live metric graphs (optional Prometheus/Grafana stack) -------------------------------------

_PANELS = [
    (1, "Memory leak", "payment-service heap"),
    (2, "DB pool exhaustion", "ledger-service HikariCP"),
    (3, "Auth key error", "payment-service error rate"),
    (4, "Notification latency", "payment-service outbound calls"),
    (6, "Throughput", "requests flowing through the chain"),
]


def _live_graphs() -> None:
    """Live metric graphs, shown only on this page.

    Deliberately not shown beside an incident under investigation: the Incident Center replays a
    stored record, so a live graph there would display the estate's state *now* while appearing
    to describe a past event.
    """
    status = dsrc.observability_status()
    if not status["grafana"] or not status["prometheus"]:
        c.panel_open("Live metric graphs", c.pill("not running", "neutral"))
        missing = [n for n, ok in (("Prometheus", status["prometheus"]),
                                   ("Grafana", status["grafana"])) if not ok]
        c.empty_state(
            f"{' and '.join(missing)} unavailable",
            "The observability stack is optional and is not required by the pipeline, which reads "
            "each service's metrics endpoint directly. Start it with "
            "<span class='mono'>docker compose up -d prometheus grafana</span> to watch faults "
            "appear and clear in real time.")
        c.panel_close()
        return

    up, total = status["targets_up"], status["targets_total"]
    right = c.pill(f"{up}/{total} targets up", "success" if up == total else "warn")
    c.panel_open("Live metric graphs", right)
    c.note("Scraped every 5 seconds from the running services. These are live readings, not the "
           "replayed corpus &mdash; inject a fault and the spike appears here within seconds.")
    c.spacer(3)

    window = st.radio("Window", ["5m", "15m", "1h"], horizontal=True, index=1,
                      label_visibility="collapsed")
    minutes = {"5m": 5, "15m": 15, "1h": 60}[window]

    cols = st.columns(2, gap="medium")
    for i, (panel_id, title, subtitle) in enumerate(_PANELS):
        with cols[i % 2]:
            st.markdown(f"<div class='eyebrow' style='margin-bottom:4px'>{title}</div>"
                        f"<div class='note' style='margin-bottom:6px'>{subtitle}</div>",
                        unsafe_allow_html=True)
            st.components.v1.iframe(dsrc.grafana_embed_url(panel_id, minutes), height=240)

    c.spacer(2)
    st.markdown(
        f"<div class='note'>Full dashboard: "
        f"<a href='{dsrc.grafana_embed_url(None, minutes)}' target='_blank'>open in Grafana</a>"
        f" &nbsp;·&nbsp; Prometheus targets: "
        f"<a href='{dsrc.PROMETHEUS_URL}/targets' target='_blank'>{dsrc.PROMETHEUS_URL}/targets</a>"
        f"</div>", unsafe_allow_html=True)
    c.panel_close()


def _dependency_panel(states) -> None:
    by_name = {s.name: s for s in states}
    c.panel_open("Call graph and fault propagation")
    c.note("payment-service calls the other three synchronously. That is what allows a fault "
           "elsewhere to surface as a payment-side symptom — the case this platform exists to "
           "diagnose. A service is never marked unhealthy merely because a dependency is.")
    c.spacer(4)

    caller = by_name.get("payment-service")
    cols = st.columns([1.2, 0.3, 2], gap="small")
    with cols[0]:
        if caller:
            st.markdown(c.service_chip(caller.name, "Entry point", ""), unsafe_allow_html=True)
            st.markdown(
                f"<div style='margin-top:6px'>{c.pill(STATUS_LABEL[caller.status], STATUS_TONE[caller.status])}</div>",
                unsafe_allow_html=True)
    with cols[1]:
        st.markdown("<div class='flow-arrow' style='padding-top:14px'>&#8594;</div>",
                    unsafe_allow_html=True)
    with cols[2]:
        for dep in dsrc.DEPENDENCIES.get("payment-service", []):
            s = by_name.get(dep)
            tone = STATUS_TONE.get(s.status, "neutral") if s else "neutral"
            label = STATUS_LABEL.get(s.status, "Unknown") if s else "Unknown"
            st.markdown(
                f"<div style='display:flex;justify-content:space-between;align-items:center;"
                f"gap:10px;padding:7px 0;border-bottom:1px solid var(--bd)'>"
                f"<span class='mono' style='font-size:.8125rem'>{dep}</span>"
                f"{c.pill(label, tone)}</div>", unsafe_allow_html=True)
    c.panel_close()


# --- evaluation --------------------------------------------------------------------------------

def evaluation() -> None:
    c.page_header("Evaluation & Benchmarks",
                  "Measured on a held-out synthetic corpus. Not production performance.")

    st.warning("Every figure on this page comes from replayed **synthetic** incidents. The "
               "signature thresholds and the corpus generator share baseline constants, so "
               "absolute accuracy is an upper bound under favourable conditions, not a "
               "capability claim. The comparison between arms is unaffected — both arms receive "
               "byte-identical inputs.")

    tab_modes, tab_arms, tab_neg, tab_sweep, tab_ablate = st.tabs(
        ["Detection modes", "Arm comparison", "Negative control", "Threshold sweep", "Ablations"])

    with tab_modes:
        _detection_modes()
    with tab_arms:
        _arm_comparison()
    with tab_neg:
        _negative_control()
    with tab_sweep:
        _threshold_sweep()
    with tab_ablate:
        _ablations()


def _detection_modes() -> None:
    c.note("Computed live by running the real detection node over the Evaluation corpus with "
           "each mode, so these figures track the models currently on disk.")
    c.spacer(3)
    results = []
    for mode in ("isolation_forest", "signature", "either"):
        cov = dsrc.detection_coverage(mode)
        if cov:
            results.append(cov)
    if not results:
        c.empty_state("Coverage could not be computed",
                      "The detectors or the Evaluation corpus could not be loaded.")
        return

    c.tile_row([
        c.tile(f"{r['mode']}", f"{len(r['detected'])} / {r['total']}",
               f"cross-service {r['cross_hit']} / {r['cross_total']}",
               "accent" if r["mode"] == ai_config.DETECTION_MODE else None)
        for r in results
    ])
    c.spacer(5)

    cols = st.columns(len(results), gap="medium")
    for col, r in zip(cols, results):
        with col:
            c.panel_open(r["mode"],
                         c.pill("active", "accent") if r["mode"] == ai_config.DETECTION_MODE else "")
            c.comparison_bars("Detected by fault type", [
                (fault, seen, total, "success" if seen == total else "warn")
                for fault, (seen, total) in sorted(r["by_fault"].items())
            ])
            c.panel_close()


def _arm_comparison() -> None:
    m = dsrc.metrics_json()
    if not m or "per_arm" not in m:
        c.empty_state("No evaluation run on disk",
                      "Run <span class='mono'>python -m ai_orchestration.evaluation.run_evaluation"
                      "</span> to produce metrics.json.")
        return
    multi = m["per_arm"]["multi_agent"]["performance"]
    single = m["per_arm"]["single_agent"]["performance"]
    rows = dsrc.per_incident_rows()

    def subset(arm: str, cross: bool) -> tuple[int, int]:
        hits = tot = 0
        for r in rows:
            if r["arm"] != arm or not r["predicted_root_cause_service"]:
                continue
            is_cross = r["true_root_cause_service"] != r["true_symptom_service"]
            if is_cross != cross:
                continue
            tot += 1
            hits += r["root_cause_correct"] == "True"
        return hits, tot

    mc, mct = subset("multi_agent", True)
    sc, sct = subset("single_agent", True)

    c.tile_row([
        c.tile("Multi-agent, answered",
               f"{multi['accuracy_when_answered'] * 100:.0f}%",
               f"{multi['answered']} of {multi['incidents_total']} incidents", "success"),
        c.tile("Baseline, answered",
               f"{single['accuracy_when_answered'] * 100:.0f}%",
               f"{single['answered']} of {single['incidents_total']} incidents"),
        c.tile("Cross-service, multi-agent", f"{mc} / {mct}" if mct else "n/a",
               "where symptom and cause differ", "accent"),
        c.tile("Cross-service, baseline", f"{sc} / {sct}" if sct else "n/a",
               "same inputs, same retrieved incidents"),
    ])
    c.spacer(5)

    left, right = st.columns(2, gap="large")
    with left:
        c.panel_open("Performance")
        c.comparison_bars("", [
            ("Multi-agent — accuracy over all incidents",
             multi["accuracy_over_all_incidents"] * 100, 100.0, "success"),
            ("Baseline — accuracy over all incidents",
             single["accuracy_over_all_incidents"] * 100, 100.0, "neutral"),
            ("Multi-agent — macro F1", multi["f1_macro"] * 100, 100.0, "success"),
            ("Baseline — macro F1", single["f1_macro"] * 100, 100.0, "neutral"),
        ], unit="%")
        c.note("Macro F1 is computed over the answered subset only, where each class carries a "
               "support of two to four. At that support it conveys little.")
        c.panel_close()
    with right:
        c.panel_open("Cost of decomposition")
        col = m.get("collaboration") or {}
        c.kv_rows([
            ("Multi-agent, mean", f"{col.get('multi_agent_seconds_mean', '-')} s"),
            ("Baseline, mean", f"{col.get('single_agent_seconds_mean', '-')} s"),
            ("Relative overhead", f"{col.get('multi_agent_relative_overhead', '-')}×"),
        ])
        if col.get("note"):
            c.note(col["note"])
        c.panel_close()


def _negative_control() -> None:
    nc = dsrc.negative_control()
    if not nc:
        c.empty_state("No negative control on disk",
                      "Run <span class='mono'>python -m ai_orchestration.evaluation."
                      "negative_control</span>.")
        return
    c.note(nc.get("description", ""))
    c.spacer(3)
    tiles = []
    for phase, body in (nc.get("per_phase") or {}).items():
        rate = body.get("false_positive_rate", 0) * 100
        tone = "success" if rate < 10 else ("warn" if rate < 25 else "danger")
        tiles.append(c.tile(
            f"{phase} phase", f"{rate:.2f}%",
            f"{body.get('false_positives')} of {body.get('records_scored')} · 95% CI "
            f"[{body.get('ci95_lower', 0) * 100:.1f}, {body.get('ci95_upper', 0) * 100:.1f}]",
            tone))
    c.tile_row(tiles)
    c.spacer(4)
    c.panel_open("Why the recovery phase is worse")
    c.note("The corpus generator assigns a memory-leak incident a recovery-phase heap of "
           "baseline plus the <i>full</i> retained allocation, while the fault window receives "
           "baseline plus half of it. Recovery is therefore further from baseline than the fault "
           "that preceded it, so a substantial part of this figure is the detector correctly "
           "flagging genuinely elevated memory rather than raising a false alarm.")
    c.panel_close()


def _threshold_sweep() -> None:
    sweep = dsrc.threshold_sweep()
    if not sweep or "sweep" not in sweep:
        c.empty_state("No sweep on disk",
                      "Run <span class='mono'>python -m ai_orchestration.evaluation."
                      "threshold_sweep</span>.")
        return
    c.note("Detection coverage against negative-control false positives, across the full range of "
           "observed anomaly scores. Coverage can only be bought by degrading the negative control.")
    c.spacer(3)
    header = ("<div style='display:grid;grid-template-columns:1fr 1fr 1fr 1fr 1fr;gap:8px;"
              "padding:8px 0;border-bottom:1px solid var(--bds)'>"
              + "".join(f"<div class='tile-label'>{h}</div>" for h in
                        ["Threshold", "Detected", "Cross-service", "FP normal", "FP recovery"])
              + "</div>")
    rows = []
    for entry in sweep["sweep"]:
        threshold, det, cross, _answered, _correct, fpn, fpr = entry
        active = abs(threshold - ai_config.ANOMALY_SCORE_THRESHOLD) < 1e-9
        style = ("background:var(--accent-s);border-radius:var(--r-sm)" if active else "")
        rows.append(
            f"<div style='display:grid;grid-template-columns:1fr 1fr 1fr 1fr 1fr;gap:8px;"
            f"padding:7px 4px;border-bottom:1px solid var(--bd);{style}'>"
            f"<div class='mono'>{threshold:+.4f}</div><div class='mono'>{det}/48</div>"
            f"<div class='mono'>{cross}/24</div><div class='mono'>{fpn}/48</div>"
            f"<div class='mono'>{fpr}/48</div></div>")
    st.markdown(header + "".join(rows), unsafe_allow_html=True)
    c.spacer(4)
    c.note("Cross-service coverage never exceeds 5 of 24 at any threshold. The ranking produced "
           "by the Isolation Forest for cross-service faults is statistically indistinguishable "
           "from chance (AUC 0.474, 95% CI [0.346, 0.602]), so no operating point recovers them.")


def _ablations() -> None:
    names = [("no_retrieval", "Retrieval disabled"),
             ("rag_override", "Retrieval permitted to override")]
    any_found = False
    for key, title in names:
        m = dsrc.ablation(key)
        if not m:
            continue
        any_found = True
        c.panel_open(title, c.pill(key, "neutral", dot=False))
        try:
            multi = m["per_arm"]["multi_agent"]["performance"]
            c.kv_rows([
                ("Accuracy when answered", f"{multi['accuracy_when_answered'] * 100:.1f}%"),
                ("Accuracy over all incidents", f"{multi['accuracy_over_all_incidents'] * 100:.1f}%"),
                ("Macro F1", f"{multi['f1_macro']:.4f}"),
                ("Answered", f"{multi['answered']} of {multi['incidents_total']}"),
            ])
            single = m["per_arm"].get("single_agent", {}).get("performance")
            if single:
                c.note(f"Baseline under the same ablation: "
                       f"{single['accuracy_when_answered'] * 100:.1f}% when answered.")
        except (KeyError, TypeError):
            c.note("Metrics present but incomplete.")
        c.panel_close()
    if not any_found:
        c.empty_state("No ablation artefacts on disk",
                      "Run the evaluation with <span class='mono'>--ablation no_retrieval</span> "
                      "or <span class='mono'>--ablation rag_override</span>.")


# --- history -----------------------------------------------------------------------------------

def history() -> None:
    items = dsrc.load_history()
    c.page_header("Incident History",
                  f"{len(items)} investigation(s) recorded by this console.",
                  c.pill("persisted to disk", "neutral", dot=False) if items else "")
    if not items:
        c.empty_state("No investigations yet",
                      "Every run you perform in the <b>Incident Center</b> is appended to "
                      "<span class='mono'>docs/evaluation/console_history.jsonl</span> and listed "
                      "here, including across restarts. This log records operator activity and is "
                      "separate from the pipeline's own write-back to retrieval memory.")
        return

    search = st.text_input("Filter", placeholder="Filter by incident, service or outcome",
                           label_visibility="collapsed")
    rows = [i for i in items
            if not search or search.lower() in
            f"{i['incident']} {i['detail']} {i['label']}".lower()]
    if not rows:
        c.empty_state("Nothing matches", "No run in this session matches that filter.")
        return

    head_cols = st.columns([4, 1])
    with head_cols[1]:
        if st.button("Clear log", use_container_width=True):
            dsrc.clear_history()
            st.session_state.history = []
            st.rerun()
    c.panel_open(f"{len(rows)} investigation(s)")
    for item in reversed(rows):
        st.markdown(
            f"<div style='display:flex;align-items:center;justify-content:space-between;"
            f"gap:12px;padding:11px 0;border-bottom:1px solid var(--bd)'>"
            f"<div style='min-width:0'>"
            f"<div class='mono' style='font-size:.8125rem;font-weight:500'>{item['incident']}</div>"
            f"<div class='note'>{item['detail']}</div></div>"
            f"<div style='display:flex;gap:8px;align-items:center'>"
            f"<span class='note'>{item['when']}</span>"
            f"<span class='note'>{item.get('arm', '')}</span>"
            f"{c.pill(item['label'], item['tone'])}"
            f"</div></div>", unsafe_allow_html=True)
    c.panel_close()


# --- configuration -----------------------------------------------------------------------------

def configuration() -> None:
    c.page_header("Configuration", "Live pipeline settings. Changes apply to this session only.")

    left, right = st.columns([1, 1], gap="large")

    with left:
        c.panel_open("Detection")
        modes = ["signature", "isolation_forest", "either"]
        current = ai_config.DETECTION_MODE
        chosen = st.selectbox(
            "Detection mode", modes, index=modes.index(current) if current in modes else 0,
            help="Which component decides a record is anomalous.")
        if chosen != ai_config.DETECTION_MODE:
            ai_config.DETECTION_MODE = chosen
            dsrc.detection_coverage.clear()
            st.rerun()
        c.note({
            "signature": "Deterministic fault-signature matching decides, falling back to the "
                         "Isolation Forest for services no signature covers.",
            "isolation_forest": "The per-service Isolation Forest decides. This reproduces the "
                                "published evaluation.",
            "either": "Anomalous if either fires. Higher coverage, roughly double the false "
                      "positives.",
        }[chosen])

        c.spacer(4)
        threshold = st.number_input("Anomaly score threshold",
                                    value=float(ai_config.ANOMALY_SCORE_THRESHOLD),
                                    step=0.005, format="%.4f")
        if threshold != ai_config.ANOMALY_SCORE_THRESHOLD:
            ai_config.ANOMALY_SCORE_THRESHOLD = threshold
            dsrc.detection_coverage.clear()
            st.rerun()
        if threshold > 0.025:
            st.warning("No fault window in the corpus scores above 0.0247, so nothing can be "
                       "detected at this threshold.")
        c.panel_close()

        c.panel_open("Safety and execution")
        c.kv_rows([
            ("Execution mode", c.pill("dry run", "neutral") if ai_config.EXECUTION_DRY_RUN
             else c.pill("live", "danger")),
            ("Max remediation attempts", str(ai_config.MAX_REMEDIATION_ATTEMPTS)),
            ("Recovery verification attempts", str(ai_config.RECOVERY_VERIFICATION_ATTEMPTS)),
            ("Verify via /fault-status",
             c.pill("yes", "neutral") if ai_config.VERIFY_USING_FAULT_STATUS
             else c.pill("no — metric based", "success")),
            ("Feedback write-back", c.pill("enabled", "success") if ai_config.FEEDBACK_ENABLED
             else c.pill("disabled", "neutral")),
        ])
        c.note("Human approval is mandatory and is not configurable. It is enforced by the graph "
               "itself, not by this console.")
        c.panel_close()

    with right:
        c.panel_open("Models and retrieval")
        c.kv_rows([
            ("Isolation Forest", f"{ai_config.ISOLATION_FOREST_N_ESTIMATORS} estimators, "
                                 f"contamination {ai_config.ISOLATION_FOREST_CONTAMINATION}, "
                                 f"seed {ai_config.ISOLATION_FOREST_RANDOM_STATE}"),
            ("Feature schema", f"<span class='mono'>{ai_config.FEATURE_SCHEMA_VERSION}</span>"),
            ("Training phases",
             ", ".join(f"<span class='mono'>{p}</span>" for p in ai_config.TRAINING_PHASES)),
            ("Retrieval", c.pill("enabled", "success") if ai_config.RETRIEVAL_ENABLED
             else c.pill("disabled (ablation)", "warn")),
            ("RAG may override signature",
             c.pill("no", "success") if not ai_config.RAG_MAY_OVERRIDE_SIGNATURE
             else c.pill("yes (ablation)", "danger")),
            ("Language model", f"<span class='mono'>{ai_config.OLLAMA_MODEL}</span> "
                               f"({ai_config.OLLAMA_TIMEOUT_SECONDS:.0f}s timeout)"),
        ])
        c.panel_close()

        c.panel_open("Detector inventory")
        for d in dsrc.detector_inventory():
            tone = "success" if d["trained"] else "warn"
            label = "trained" if d["trained"] else "not trained"
            samples = f"{d['samples']} samples" if d["samples"] else "unknown sample count"
            st.markdown(
                f"<div style='display:flex;align-items:center;justify-content:space-between;"
                f"gap:12px;padding:9px 0;border-bottom:1px solid var(--bd)'>"
                f"<div><div class='mono' style='font-size:.8125rem'>{d['service']}</div>"
                f"<div class='note'>{d['features']} features · {samples}</div></div>"
                f"{c.pill(label, tone)}</div>", unsafe_allow_html=True)
        c.panel_close()
