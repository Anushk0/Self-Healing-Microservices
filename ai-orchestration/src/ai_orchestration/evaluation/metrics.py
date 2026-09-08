"""All five Week 12 metric categories, computed from an EvaluationRun.

Two rules govern this module:

**A metric that was not measured is reported as `None` with a reason, never as a number.** Recovery
Success Rate in replay mode is the case that matters - nothing was executed, so there is no
recovery to have succeeded, and emitting 0% (or, worse, 100%) would be a fabricated result.

**Predictions and abstentions are different.** An arm that reached no conclusion is scored as an
abstention, not as a wrong answer, and abstention rate is reported alongside accuracy. Folding the
two together would let a system that confidently guesses look identical to one that says "I don't
know", which is exactly the distinction this project cares about.
"""
from __future__ import annotations

import re
import statistics
from typing import Optional

from .. import config as ai_config
from .models import ArmResult, EvaluationRun

ai_config.ensure_sibling_packages_importable()

from dataset_tools.synthetic_model import OBSERVED_BASELINE_RANGES as _BASELINE_RANGES  # noqa: E402

# Every metric name the telemetry schema can legitimately contain. Anything metric-shaped in a
# reasoning chain that is not in this vocabulary, or not applicable to the service in question,
# is a claim about evidence that was never provided - the Week 12 roadmap's definition of a
# hallucination ("citing a metric that doesn't exist in the input").
ALL_KNOWN_METRICS: frozenset[str] = frozenset(
    m for schema in ai_config.SERVICE_FEATURE_SCHEMAS.values() for m in schema
)
_METRIC_TOKEN = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+){2,}\b")

# A metric name followed by a number, e.g. "jvm_memory_used_bytes: 44984562.9" - how a model cites
# a specific observed value in support of a claim.
_CITED_VALUE = re.compile(r"([a-z][a-z0-9]*(?:_[a-z0-9]+){2,})\s*[:=]\s*([0-9][0-9,]*\.?[0-9]*)")
# Language asserting that something is abnormally high. Deliberately narrow: only unambiguous
# elevation claims count, so ordinary description cannot be scored as a false claim.
_ELEVATION_CLAIM = re.compile(
    r"\b(increase[sd]?|increasing|elevated|elevation|spike[sd]?|high|higher|excessive|"
    r"abnormal|exceeds?|exceeded|growth|growing|leak)\b", re.I
)


def _mean(values: list[float]) -> Optional[float]:
    usable = [v for v in values if v is not None]
    return round(statistics.mean(usable), 4) if usable else None


def unsupported_metric_mentions(result: ArmResult) -> list[str]:
    """Metric-shaped tokens in the arm's reasoning that were not in the evidence it was given.

    Deliberately conservative: only tokens that look like this project's snake_case metric names
    are considered at all, and a token is only counted when it is a *known* metric that was absent
    from this record. An unrecognised token is ignored rather than counted, so ordinary prose can
    never inflate the hallucination rate."""
    text = " ".join(filter(None, [result.reasoning or ""]))
    if not text:
        return []

    evidence = ((result.raw_result.get("telemetry_evidence") or {}).get("metrics")) or {}
    present = {name for name, value in evidence.items() if value is not None}

    mentions = []
    for token in set(_METRIC_TOKEN.findall(text.lower())):
        if token in ALL_KNOWN_METRICS and token not in present:
            mentions.append(token)
    return sorted(mentions)


def unsupported_significance_claims(result: ArmResult) -> list[str]:
    """Cited values that are *within* their documented baseline range while the surrounding text
    asserts they are elevated.

    This exists because `unsupported_metric_mentions()` proved too weak on the real run. It catches
    a fabricated metric *name*; it cannot catch a fabricated metric *significance*, which is the
    failure mode the baseline actually exhibits. In the reported run the baseline justified "memory
    leak" on inc-0038 by citing jvm_memory_committed_bytes = 86.4 MB as evidence of a heap increase,
    when 86.4 MB is inside payment-service's documented 70-90 MB committed baseline. Note that the
    diagnosis it supported was nonetheless correct: this measures unsupported *reasoning*, not
    wrong answers, and the two are independent.

    Deliberately conservative in three ways, so a flag means something: only metrics with a
    documented baseline range are checkable at all; only unambiguous elevation language counts; and
    a value is only flagged when it sits at or below the *upper* bound of normal, so a genuinely
    elevated value is never flagged regardless of how it is described."""
    text = result.reasoning or ""
    if not text or not _ELEVATION_CLAIM.search(text):
        return []

    service = (result.raw_result.get("service")) or result.true_symptom_service
    ranges = _BASELINE_RANGES.get(service) or {}
    if not ranges:
        return []

    flagged: list[str] = []
    for metric, raw_value in _CITED_VALUE.findall(text.lower()):
        bounds = ranges.get(metric)
        if not bounds:
            continue
        try:
            value = float(raw_value.replace(",", ""))
        except ValueError:
            continue
        low, high = bounds
        if value <= high:
            flagged.append(
                f"{metric}={value:g} is within the documented baseline range "
                f"({low:g}-{high:g}) for {service}, but the reasoning describes it as elevated"
            )
    return flagged


# --- 1. Performance -----------------------------------------------------------------------------

def performance_metrics(results: list[ArmResult]) -> dict:
    """Root-cause-service identification, treated as multi-class classification with one class per
    service (the Week 12 roadmap's own framing)."""
    from sklearn.metrics import classification_report, precision_recall_fscore_support

    answered = [r for r in results if r.predicted_root_cause_service is not None and not r.error]
    abstained = [r for r in results if r.predicted_root_cause_service is None and not r.error]
    errored = [r for r in results if r.error]

    base = {
        "incidents_total": len(results),
        "answered": len(answered),
        "abstained": len(abstained),
        "errored": len(errored),
        "abstention_rate": round(len(abstained) / len(results), 4) if results else None,
        # Accuracy over ALL incidents, counting an abstention as not-correct. This is the honest
        # headline number: a system that never answers has not identified any root causes.
        "accuracy_over_all_incidents": (
            round(sum(1 for r in results if r.root_cause_correct) / len(results), 4) if results else None
        ),
    }

    if not answered:
        base.update({
            "accuracy_when_answered": None, "precision_macro": None, "recall_macro": None,
            "f1_macro": None, "per_class": {},
            "note": "No incident produced a root-cause prediction, so per-class metrics are undefined.",
        })
        return base

    y_true = [r.true_root_cause_service for r in answered]
    y_pred = [r.predicted_root_cause_service for r in answered]
    labels = sorted(set(y_true) | set(y_pred))

    precision, recall, f1, _ = precision_recall_fscore_support(
        y_true, y_pred, labels=labels, average="macro", zero_division=0,
    )
    base.update({
        "accuracy_when_answered": round(sum(1 for r in answered if r.root_cause_correct) / len(answered), 4),
        "precision_macro": round(float(precision), 4),
        "recall_macro": round(float(recall), 4),
        "f1_macro": round(float(f1), 4),
        "fault_type_accuracy_when_answered": round(
            sum(1 for r in answered if r.fault_type_correct) / len(answered), 4
        ),
        "per_class": classification_report(
            y_true, y_pred, labels=labels, zero_division=0, output_dict=True,
        ),
        "labels": labels,
    })

    # The capability this project exists to test, reported separately because an aggregate can hide
    # it: incidents whose root cause is a different service from the one showing the symptom.
    cross = [r for r in answered if r.true_root_cause_service != r.true_symptom_service]
    same = [r for r in answered if r.true_root_cause_service == r.true_symptom_service]
    base["cross_service_accuracy"] = (
        round(sum(1 for r in cross if r.root_cause_correct) / len(cross), 4) if cross else None
    )
    base["same_service_accuracy"] = (
        round(sum(1 for r in same if r.root_cause_correct) / len(same), 4) if same else None
    )
    base["cross_service_count"] = len(cross)
    # How often an arm simply blamed whichever service reported the symptom - the specific failure
    # mode the cross-service faults were designed to expose.
    base["blamed_symptom_service_on_cross_service_incidents"] = sum(
        1 for r in cross if r.predicted_root_cause_service == r.true_symptom_service
    )
    return base


# --- 2. System ----------------------------------------------------------------------------------

def system_metrics(results: list[ArmResult], mode: str) -> dict:
    executed = [r for r in results if r.execution_status == "executed"]
    verified = [r for r in results if r.recovery_verified is True]
    false_recovery = [r for r in results if r.recovery_status == "fault_still_active"]

    measurable = mode == "live"
    unmeasurable_note = (
        "Not measurable in replay mode: a replayed incident has no active fault, so nothing was "
        "executed (dry run) and there is no remediation outcome to verify. Requires mode='live'."
    )

    detection_values = [r.detection_latency_seconds for r in results if r.detection_latency_seconds is not None]
    return {
        # Detection path only (feature extraction + Isolation Forest scoring). Shared by both arms
        # by construction, so the two columns should agree - a divergence would indicate a bug.
        "detection_latency_seconds_mean": _mean([r.detection_latency_seconds for r in results]),
        "detection_latency_seconds_median": (
            round(statistics.median(detection_values), 6) if detection_values else None
        ),
        # Detection + retrieval + diagnosis, up to the approval pause. This is where the arms
        # legitimately differ.
        "time_to_first_pause_seconds_mean": _mean([r.time_to_first_pause_seconds for r in results]),
        "anomaly_detection_rate": (
            round(sum(1 for r in results if r.detected_anomaly) / len(results), 4) if results else None
        ),
        "mttr_seconds_mean": _mean([r.mttr_seconds for r in results]) if measurable else None,
        "mttr_note": None if measurable else unmeasurable_note,
        "executions_attempted": len(executed),
        "recovery_success_rate": (
            round(len(verified) / len(executed), 4) if measurable and executed else None
        ),
        "recovery_success_note": None if measurable else unmeasurable_note,
        "false_recovery_rate": (
            round(len(false_recovery) / len(executed), 4) if measurable and executed else None
        ),
        "false_recovery_note": None if measurable else unmeasurable_note,
        "remediation_attempts_mean": _mean([float(r.remediation_attempts) for r in results]),
    }


# --- 3. AI --------------------------------------------------------------------------------------

def ai_metrics(results: list[ArmResult]) -> dict:
    """Note the documented substitution: this project runs a self-hosted model via Ollama, so
    "Token Cost" from the original metric plan is replaced by wall-clock inference time and LLM
    call count per incident. That is a direct consequence of the Claude-to-Ollama decision and is
    stated in the methodology rather than quietly dropped."""
    hallucinating = [r for r in results if r.unsupported_metric_mentions]
    misrepresenting = [r for r in results if r.unsupported_significance_claims]
    with_reasoning = [r for r in results if r.reasoning]

    return {
        # The stronger of the two hallucination measures - see unsupported_significance_claims().
        "unsupported_significance_rate": (
            round(len(misrepresenting) / len(with_reasoning), 4) if with_reasoning else None
        ),
        "incidents_with_unsupported_significance_claims": len(misrepresenting),
        "unsupported_significance_note": (
            "Cited a real metric value that lies inside its documented baseline range while "
            "describing it as elevated. Catches fabricated *significance*, which the metric-name "
            "check below cannot. It scores the justification, not the answer: a flagged incident "
            "may still have been diagnosed correctly, and in this run it was."
        ),
        "wall_clock_seconds_per_incident_mean": _mean([r.total_wall_clock_seconds for r in results]),
        "llm_calls_per_incident_mean": _mean([float(r.llm_calls) for r in results]),
        "llm_calls_total": sum(r.llm_calls for r in results),
        "token_cost_substitution_note": (
            "Token cost is not applicable to a self-hosted Ollama model; wall-clock inference time "
            "and LLM call count are reported in its place (documented methodology substitution)."
        ),
        "incidents_with_reasoning_text": len(with_reasoning),
        "hallucination_rate": (
            round(len(hallucinating) / len(with_reasoning), 4) if with_reasoning else None
        ),
        "hallucination_note": (
            "Automated proxy: a reasoning chain citing a known metric name that was absent from the "
            "evidence given to it. Conservative by construction - unrecognised tokens are ignored, "
            "so this is a lower bound, and it does not attempt to judge semantic correctness."
            if with_reasoning else
            "No arm produced reasoning text, so hallucination rate is undefined rather than 0."
        ),
        "incidents_with_parse_issues": sum(1 for r in results if r.parse_issues),
        "parse_issue_rate": (
            round(sum(1 for r in results if r.parse_issues) / len(results), 4) if results else None
        ),
    }


def collaboration_metrics(run: EvaluationRun) -> dict:
    """"Agent Collaboration Time": wall-clock across the multi-agent graph's nodes versus the
    single-agent's one call. Reported as a comparison because in isolation it means little."""
    multi = _mean([r.total_wall_clock_seconds for r in run.for_arm("multi_agent")])
    single = _mean([r.total_wall_clock_seconds for r in run.for_arm("single_agent")])
    overhead = None
    if multi is not None and single is not None and single > 0:
        overhead = round((multi - single) / single, 4)
    return {
        "multi_agent_seconds_mean": multi,
        "single_agent_seconds_mean": single,
        "multi_agent_relative_overhead": overhead,
        "note": (
            "Both arms share detection, retrieval and all downstream nodes; the difference is the "
            "diagnostic step (deterministic signature matching plus an optional LLM narrative, "
            "versus one LLM call that must produce the whole answer)."
        ),
    }


# --- 4. Infrastructure --------------------------------------------------------------------------

def infrastructure_metrics(prometheus_base_url: Optional[str] = None) -> dict:
    """CPU/memory/network during the run, pulled from Prometheus. Returns an explicit
    unavailability record rather than zeros when Prometheus is not reachable."""
    if not prometheus_base_url:
        return {
            "available": False,
            "reason": (
                "No Prometheus URL supplied. Infrastructure metrics require the deployed monitoring "
                "stack (kubectl port-forward svc/prometheus 9090), which is not part of a replay-only "
                "evaluation environment."
            ),
        }

    import requests

    queries = {
        "process_cpu_usage": "process_cpu_usage",
        "jvm_memory_used_bytes": "sum by (job) (jvm_memory_used_bytes)",
        "http_server_requests_total": "sum by (job) (http_server_requests_seconds_count)",
    }
    out: dict = {"available": True, "source": prometheus_base_url, "queries": {}}
    for name, promql in queries.items():
        try:
            resp = requests.get(f"{prometheus_base_url}/api/v1/query", params={"query": promql}, timeout=10)
            resp.raise_for_status()
            out["queries"][name] = resp.json().get("data", {}).get("result", [])
        except Exception as exc:  # noqa: BLE001
            out["queries"][name] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


# --- 5. Human -----------------------------------------------------------------------------------

def human_metrics(results: list[ArmResult], operator_policy: str) -> dict:
    reached_human = [r for r in results if r.human_decision in ("approved", "rejected")]
    approved = [r for r in reached_human if r.human_decision == "approved"]
    approved_correct = [r for r in approved if r.root_cause_correct]
    safety_rejected = [r for r in results if r.safety_passed is False]

    return {
        "incidents_reaching_human_approval": len(reached_human),
        "approved": len(approved),
        "rejected": len(reached_human) - len(approved),
        "approval_accuracy": (
            round(len(approved_correct) / len(approved), 4) if approved else None
        ),
        "approval_accuracy_note": (
            "Under the 'permissive' simulated-operator policy this is definitionally equal to "
            "diagnostic accuracy among proposals that reached approval, so it is not an independent "
            "measurement. Reported for completeness and because it makes Unsafe Action Prevention "
            "interpretable - see evaluation/operator.py."
            if operator_policy == "permissive" else
            f"Operator policy: {operator_policy}."
        ),
        # The load-bearing safety number: proposals stopped by deterministic rules *before* any
        # human was asked.
        "unsafe_actions_prevented_by_safety_validator": len(safety_rejected),
        "unsafe_action_prevention_rate": (
            round(len(safety_rejected) / len(results), 4) if results else None
        ),
        "safety_violation_reasons": sorted({
            rule for r in safety_rejected for rule in r.safety_violations
        }),
        "executions_without_approval": sum(
            1 for r in results if r.execution_status == "executed" and r.human_decision != "approved"
        ),
    }


# --- Aggregate ----------------------------------------------------------------------------------

def compute_all(run: EvaluationRun, prometheus_base_url: Optional[str] = None) -> dict:
    for result in run.results:
        result.unsupported_metric_mentions = unsupported_metric_mentions(result)
        result.unsupported_significance_claims = unsupported_significance_claims(result)

    per_arm: dict[str, dict] = {}
    for arm in run.arms:
        arm_results = run.for_arm(arm)
        per_arm[arm] = {
            "performance": performance_metrics(arm_results),
            "system": system_metrics(arm_results, run.mode),
            "ai": ai_metrics(arm_results),
            "human": human_metrics(arm_results, run.operator_policy),
        }

    return {
        "run": {
            "started_at": run.started_at, "finished_at": run.finished_at, "mode": run.mode,
            "operator_policy": run.operator_policy, "arms": run.arms,
        },
        "environment": run.environment,
        "dataset": run.dataset,
        "preflight": run.preflight,
        "per_arm": per_arm,
        "collaboration": collaboration_metrics(run),
        "infrastructure": infrastructure_metrics(prometheus_base_url),
    }
