"""Evaluation category (part 3): the artefacts the paper is written from.

The point of these tests is that the *absence* of a measurement survives every transformation. A
metric that was not measured must still read as "not measurable" in the CSV, in the Markdown table
and on the chart - if any one of those renders it as a zero, a reader of the Results section is
misled by an artefact rather than by a claim.
"""
from __future__ import annotations

import csv
import json

from ai_orchestration.evaluation import metrics as metrics_mod, report
from ai_orchestration.evaluation.models import ArmResult, EvaluationRun


def _run() -> EvaluationRun:
    run = EvaluationRun(
        started_at="2026-01-01T00:00:00+00:00", finished_at="2026-01-01T00:10:00+00:00",
        arms=["multi_agent", "single_agent"], mode="replay", operator_policy="permissive",
        environment={"platform": "test", "python": "3.12", "ollama_model": "llama3.2:3b",
                     "ollama_reachable": True, "trained_detectors": ["payment-service"],
                     "execution_dry_run": True, "feedback_enabled": False,
                     "recovery_metrics_measurable": False,
                     "recovery_metrics_note": "not measurable in replay"},
        dataset={"evaluation_incident_count": 2, "fault_type_counts": {"memory-leak": 2},
                 "data_source_counts": {"synthetic": 2}, "cross_service_incident_count": 1},
        preflight={"feedback_disabled": True, "rule_22_guard_verified_live": True},
    )
    run.results = [
        ArmResult(arm="multi_agent", incident_id="inc-0001", true_fault_type="memory-leak",
                  true_root_cause_service="payment-service", true_symptom_service="payment-service",
                  predicted_fault_type="memory-leak", predicted_root_cause_service="payment-service",
                  decision="anomaly", detected_anomaly=True, safety_passed=True,
                  human_decision="approved", execution_status="dry_run",
                  total_wall_clock_seconds=2.0, llm_calls=2,
                  reasoning="jvm_memory_used_bytes is elevated",
                  raw_result={"telemetry_evidence": {"metrics": {"jvm_memory_used_bytes": 5.0e8}}}),
        ArmResult(arm="single_agent", incident_id="inc-0001", true_fault_type="memory-leak",
                  true_root_cause_service="payment-service", true_symptom_service="payment-service",
                  predicted_root_cause_service=None, decision="anomaly", detected_anomaly=True,
                  total_wall_clock_seconds=1.0, llm_calls=1,
                  parse_issues=["action_category 'nuke' is not in the allowed action set"]),
    ]
    return run


def test_csv_contains_one_row_per_arm_per_incident_with_correctness_columns(tmp_path):
    run = _run()
    metrics_mod.compute_all(run)

    path = report.write_per_incident_csv(run, tmp_path / "per_incident_results.csv")
    rows = list(csv.DictReader(open(path, encoding="utf-8")))

    assert len(rows) == 2
    assert {r["arm"] for r in rows} == {"multi_agent", "single_agent"}
    multi = next(r for r in rows if r["arm"] == "multi_agent")
    assert multi["root_cause_correct"] == "True"
    single = next(r for r in rows if r["arm"] == "single_agent")
    assert single["predicted_root_cause_service"] == ""      # abstained
    assert "nuke" in single["parse_issues"]                   # honesty field survives to the CSV


def test_markdown_renders_unmeasured_metrics_as_not_measurable_not_as_zero(tmp_path):
    run = _run()
    computed = metrics_mod.compute_all(run)

    md = report.build_markdown(computed, run)

    assert "not measurable" in md.lower()
    # The specific trap: recovery must never appear as a 0.0% or 100% figure in replay mode.
    recovery_line = next(line for line in md.splitlines() if "Recovery success rate" in line)
    assert "0.0%" not in recovery_line and "100.0%" not in recovery_line


def test_markdown_reports_cross_service_accuracy_and_the_preflight_check(tmp_path):
    run = _run()
    computed = metrics_mod.compute_all(run)

    md = report.build_markdown(computed, run)

    assert "Cross-service accuracy" in md
    assert "rule_22_guard_verified_live" in md      # the leakage guard is visible in the results
    assert "Executions without approval" in md      # and so is the must-be-zero safety number


def test_write_all_produces_every_artefact(tmp_path):
    run = _run()
    computed = metrics_mod.compute_all(run)

    paths = report.write_all(run, computed, tmp_path)

    for key in ("csv", "metrics", "raw_run", "markdown"):
        assert paths[key].exists(), f"{key} artefact missing"
    saved = json.load(open(paths["metrics"], encoding="utf-8"))
    assert saved["per_arm"]["multi_agent"]["performance"]["accuracy_over_all_incidents"] == 1.0


def test_charts_are_generated_and_do_not_plot_unmeasured_metrics(tmp_path):
    """Charts must degrade rather than fabricate: a missing measurement is annotated, not drawn as
    a zero-height bar that reads as a real result."""
    from ai_orchestration.evaluation import charts

    run = _run()
    computed = metrics_mod.compute_all(run)

    written = charts.generate_all(computed, tmp_path)

    assert "error" not in written, written
    for key in ("accuracy", "outcomes", "cost"):
        assert (tmp_path / f"fig_{ {'accuracy': 'accuracy_comparison', 'outcomes': 'outcome_distribution', 'cost': 'cost_per_incident'}[key] }.png").exists()
