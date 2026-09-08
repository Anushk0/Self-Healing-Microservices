"""Turns computed metrics into the artefacts Week 13-14 writes the paper from: a per-incident CSV
(the raw log), a metrics JSON, and a Markdown results section with the comparison tables.

Unmeasured metrics are rendered as "not measurable (reason)" rather than omitted, so a reader of
the Results section can see what was not established as easily as what was.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Optional

from .models import EvaluationRun

ARM_LABELS = {"multi_agent": "Multi-agent", "single_agent": "Single-agent baseline"}


def _fmt(value, suffix: str = "", note: Optional[str] = None) -> str:
    if value is None:
        return f"not measurable — {note}" if note else "n/a"
    if isinstance(value, float):
        return f"{value:.4g}{suffix}"
    return f"{value}{suffix}"


def _pct(value) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def write_per_incident_csv(run: EvaluationRun, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "arm", "incident_id", "true_fault_type", "true_root_cause_service", "true_symptom_service",
        "predicted_fault_type", "predicted_root_cause_service", "root_cause_correct",
        "fault_type_correct", "confidence", "determination_method", "decision", "detected_anomaly",
        "safety_passed", "human_decision", "proposed_action_category", "proposed_target_service",
        "execution_status", "recovery_status", "recovery_verified", "mttr_seconds",
        "remediation_attempts", "feedback_status", "detection_latency_seconds",
        "time_to_first_pause_seconds", "total_wall_clock_seconds", "llm_calls",
        "unsupported_metric_mentions", "unsupported_significance_claims", "parse_issues", "error",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for r in run.results:
            row = r.to_dict()
            row["root_cause_correct"] = r.root_cause_correct
            row["fault_type_correct"] = r.fault_type_correct
            row["unsupported_metric_mentions"] = "; ".join(r.unsupported_metric_mentions)
            row["unsupported_significance_claims"] = "; ".join(r.unsupported_significance_claims)
            row["parse_issues"] = "; ".join(r.parse_issues)
            writer.writerow(row)
    return path


def write_metrics_json(metrics: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, default=str)
    return path


def build_markdown(metrics: dict, run: EvaluationRun) -> str:
    arms = metrics["run"]["arms"]
    env, ds = metrics["environment"], metrics["dataset"]
    lines: list[str] = []
    add = lines.append

    add("# Week 12 — Evaluation Results\n")
    add(f"Run started `{metrics['run']['started_at']}`, finished `{metrics['run']['finished_at']}`, "
        f"mode `{metrics['run']['mode']}`, operator policy `{metrics['run']['operator_policy']}`.\n")

    add("## What this run can and cannot establish\n")
    add(f"- **Environment:** {env.get('platform')}, Python {env.get('python')}, "
        f"Ollama model `{env.get('ollama_model')}` (reachable: {env.get('ollama_reachable')}).")
    add(f"- **Trained detectors:** {', '.join(env.get('trained_detectors') or []) or 'none'}.")
    add(f"- **Execution:** dry run = `{env.get('execution_dry_run')}`; "
        f"feedback write-back enabled = `{env.get('feedback_enabled')}`.")
    add(f"- **Recovery metrics measurable:** `{env.get('recovery_metrics_measurable')}`. "
        f"{env.get('recovery_metrics_note')}")
    add("")

    add("## Dataset\n")
    add(f"- Evaluation incidents: **{ds.get('evaluation_incident_count')}** "
        f"(held out; never in retrieval memory).")
    add(f"- By fault type: {ds.get('fault_type_counts')}")
    add(f"- By data source: {ds.get('data_source_counts')}")
    add(f"- Cross-service incidents (root cause ≠ symptom service): "
        f"**{ds.get('cross_service_incident_count')}**")
    add("")

    add("## Preflight (leakage guard verified before measuring)\n")
    for key, value in (metrics.get("preflight") or {}).items():
        add(f"- `{key}`: {value}")
    add("")

    add("## 1. Performance — root cause identification\n")
    add("| Metric | " + " | ".join(ARM_LABELS[a] for a in arms) + " |")
    add("|---|" + "---|" * len(arms))
    perf_rows = [
        ("Accuracy (all incidents)", "accuracy_over_all_incidents", _pct),
        ("Accuracy (when it answered)", "accuracy_when_answered", _pct),
        ("Abstention rate", "abstention_rate", _pct),
        ("Precision (macro)", "precision_macro", _pct),
        ("Recall (macro)", "recall_macro", _pct),
        ("F1 (macro)", "f1_macro", _pct),
        ("Fault-type accuracy (when answered)", "fault_type_accuracy_when_answered", _pct),
        ("**Cross-service accuracy**", "cross_service_accuracy", _pct),
        ("Same-service accuracy", "same_service_accuracy", _pct),
        ("Blamed the symptom service (cross-service)", "blamed_symptom_service_on_cross_service_incidents", str),
        ("Errored incidents", "errored", str),
    ]
    for label, key, fmt in perf_rows:
        add(f"| {label} | " + " | ".join(
            fmt(metrics["per_arm"][a]["performance"].get(key)) for a in arms) + " |")
    add("")
    add("*Cross-service accuracy is the headline capability: incidents where the service showing "
        "the symptom is not the service at fault. \"Blamed the symptom service\" counts the "
        "specific failure mode those incidents were designed to expose.*\n")

    add("## 2. System\n")
    add("| Metric | " + " | ".join(ARM_LABELS[a] for a in arms) + " |")
    add("|---|" + "---|" * len(arms))
    for label, key, note_key in [
        ("Detection latency, mean (s) — detection path only", "detection_latency_seconds_mean", None),
        ("Detection latency, median (s)", "detection_latency_seconds_median", None),
        ("Time to approval pause, mean (s) — incl. diagnosis", "time_to_first_pause_seconds_mean", None),
        ("Anomaly detection rate", "anomaly_detection_rate", None),
        ("MTTR, mean (s)", "mttr_seconds_mean", "mttr_note"),
        ("Executions attempted", "executions_attempted", None),
        ("Recovery success rate", "recovery_success_rate", "recovery_success_note"),
        ("False recovery rate", "false_recovery_rate", "false_recovery_note"),
        ("Remediation attempts, mean", "remediation_attempts_mean", None),
    ]:
        cells = []
        for a in arms:
            sysm = metrics["per_arm"][a]["system"]
            value = sysm.get(key)
            if value is None and note_key and sysm.get(note_key):
                cells.append("**not measurable here** (see note)")
            elif key.endswith("_rate") and value is not None:
                cells.append(_pct(value))
            else:
                cells.append(_fmt(value))
        add(f"| {label} | " + " | ".join(cells) + " |")
    add("")

    add("## 3. AI\n")
    add("| Metric | " + " | ".join(ARM_LABELS[a] for a in arms) + " |")
    add("|---|" + "---|" * len(arms))
    for label, key, is_pct in [
        ("Wall clock per incident, mean (s)", "wall_clock_seconds_per_incident_mean", False),
        ("LLM calls per incident, mean", "llm_calls_per_incident_mean", False),
        ("LLM calls, total", "llm_calls_total", False),
        ("Incidents with reasoning text", "incidents_with_reasoning_text", False),
        ("Hallucination: cited a metric absent from the evidence", "hallucination_rate", True),
        ("**Hallucination: claimed a baseline-range value was elevated**", "unsupported_significance_rate", True),
        ("Response parse-issue rate", "parse_issue_rate", True),
    ]:
        add(f"| {label} | " + " | ".join(
            (_pct if is_pct else _fmt)(metrics["per_arm"][a]["ai"].get(key)) for a in arms) + " |")
    add("")
    add(f"*{metrics['per_arm'][arms[0]]['ai']['token_cost_substitution_note']}*\n")

    collab = metrics["collaboration"]
    add("### Agent collaboration time\n")
    add(f"- Multi-agent mean: {_fmt(collab['multi_agent_seconds_mean'], 's')}")
    add(f"- Single-agent mean: {_fmt(collab['single_agent_seconds_mean'], 's')}")
    add(f"- Multi-agent relative overhead: {_pct(collab['multi_agent_relative_overhead'])}")
    add(f"- {collab['note']}\n")

    add("## 4. Infrastructure\n")
    infra = metrics["infrastructure"]
    if infra.get("available"):
        add(f"Collected from Prometheus at `{infra.get('source')}`.\n")
        add("```json")
        add(json.dumps(infra.get("queries", {}), indent=2)[:4000])
        add("```\n")
    else:
        add(f"**Not collected.** {infra.get('reason')}\n")

    add("## 5. Human-in-the-loop\n")
    add("| Metric | " + " | ".join(ARM_LABELS[a] for a in arms) + " |")
    add("|---|" + "---|" * len(arms))
    for label, key, is_pct in [
        ("Incidents reaching human approval", "incidents_reaching_human_approval", False),
        ("Approved", "approved", False),
        ("Rejected", "rejected", False),
        ("Approval accuracy", "approval_accuracy", True),
        ("**Unsafe actions prevented by Safety Validator**", "unsafe_actions_prevented_by_safety_validator", False),
        ("Unsafe action prevention rate", "unsafe_action_prevention_rate", True),
        ("Executions without approval (must be 0)", "executions_without_approval", False),
    ]:
        add(f"| {label} | " + " | ".join(
            (_pct if is_pct else _fmt)(metrics["per_arm"][a]["human"].get(key)) for a in arms) + " |")
    add("")
    add(f"*{metrics['per_arm'][arms[0]]['human']['approval_accuracy_note']}*\n")

    violations = sorted({v for a in arms for v in metrics["per_arm"][a]["human"]["safety_violation_reasons"]})
    if violations:
        add("Safety rules that fired during this run:\n")
        for v in violations:
            add(f"- {v}")
        add("")

    add("## Raw log\n")
    add("Per-incident rows (every field, both arms) are in `per_incident_results.csv`; the full "
        "metric structure including per-class reports is in `metrics.json`. Both are the inputs "
        "Week 13-14's Results section is written from.\n")
    return "\n".join(lines)


def write_all(run: EvaluationRun, metrics: dict, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "csv": write_per_incident_csv(run, out_dir / "per_incident_results.csv"),
        "metrics": write_metrics_json(metrics, out_dir / "metrics.json"),
        "raw_run": write_metrics_json(run.to_dict(), out_dir / "raw_run.json"),
    }

    from . import charts
    chart_paths = charts.generate_all(metrics, out_dir)

    md = build_markdown(metrics, run)
    if "error" not in chart_paths:
        md += (
            "\n## Figures\n\n"
            "![Root cause identification accuracy](fig_accuracy_comparison.png)\n\n"
            "![Outcome distribution](fig_outcome_distribution.png)\n\n"
            "![Cost per incident](fig_cost_per_incident.png)\n"
        )
    else:
        md += f"\n## Figures\n\nNot generated: {chart_paths['error']}\n"

    md_path = out_dir / "RESULTS.md"
    md_path.write_text(md, encoding="utf-8")
    paths["markdown"] = md_path
    return paths
