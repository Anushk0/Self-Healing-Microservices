"""Runs every Evaluation-set incident through both arms and records everything that happened.

Fairness is enforced structurally rather than by care: both arms are handed the *same*
`raw_record` object built once per incident, the same detectors, the same Ollama client, and the
same simulated operator policy. The only difference between the two runs is which graph they go
through.

Two preconditions are asserted, not assumed, before any measurement starts (`preflight()`), because
either one silently wrong would invalidate the entire run while making the numbers look better:

- **Feedback write-back must be off.** Otherwise an earlier evaluation incident would change what a
  later one can retrieve, and the run would not be reproducible - and the two arms would not even
  see the same retrieval memory as each other.
- **Rule 22 must actually refuse an Evaluation incident.** The guard is proven live, against the
  real Evaluation set, with feedback temporarily enabled for the check, rather than trusted from
  unit tests.

Execution is forced into dry-run for replay mode, and the reason is methodological rather than
cautious: a replayed incident is a historical record, so "resetting" its fault would target a fault
that is not currently active, trivially succeed, and let recovery verification report success for
every incident. Recovery Success Rate and False Recovery Rate are therefore **not measurable by
replay at all** - they need `mode="live"`, where a fault is genuinely injected first. That is
recorded honestly in the run's environment block rather than papered over with a meaningless 100%.
"""
from __future__ import annotations

import logging
import platform
import sys
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from .. import config as ai_config
from ..baseline.graph import build_baseline_graph
from ..graph.workflow import build_graph, default_run_config, load_all_detectors
from ..llm.ollama_client import OllamaClient
from . import replay
from .models import Arm, ArmResult, EvaluationRun
from .operator import SimulatedOperator

ai_config.ensure_sibling_packages_importable()

from dataset_tools import config as dt_config  # noqa: E402

log = logging.getLogger("ai_orchestration.evaluation.harness")

MAX_RESUMES = 8  # a routing bug must surface as a failed incident, never as an infinite loop


class PreflightError(RuntimeError):
    """A precondition that would invalidate the run was not met."""


def preflight(evaluation_incidents: list[dict]) -> dict:
    """Verifies the two things that must be true before any number is recorded."""
    checks: dict = {}

    if ai_config.FEEDBACK_ENABLED:
        raise PreflightError(
            "FEEDBACK_ENABLED is true. An evaluation run must not write incidents back into "
            "retrieval memory, or earlier incidents would change what later ones retrieve and the "
            "run would not be reproducible. Set AI_ORCHESTRATION_FEEDBACK_ENABLED=false."
        )
    checks["feedback_disabled"] = True

    # Rule 22, proven live rather than assumed: temporarily enable feedback and confirm the writer
    # refuses a real Evaluation incident. Nothing is written either way.
    from ..feedback import writer as feedback_writer

    sample_id = evaluation_incidents[0]["incident_id"]
    original = ai_config.FEEDBACK_ENABLED
    try:
        ai_config.FEEDBACK_ENABLED = True
        result = feedback_writer.write_back(
            {"decision": "anomaly", "root_cause_analysis": {"suspected_fault_type": "memory-leak"}},
            source_incident_id=sample_id,
        )
    finally:
        ai_config.FEEDBACK_ENABLED = original

    if result.status != "refused_evaluation_incident":
        raise PreflightError(
            f"CLAUDE.md rule 22 guard did not refuse Evaluation incident {sample_id!r} - it "
            f"returned {result.status!r}. Refusing to run an evaluation that could contaminate "
            f"retrieval memory."
        )
    checks["rule_22_guard_verified_live"] = True
    checks["rule_22_probe_incident_id"] = sample_id
    checks["rule_22_refusal_reason"] = result.reason

    return checks


def _environment_block(mode: str, detectors: dict, ollama_client: OllamaClient) -> dict:
    ollama_available = ollama_client.is_available()
    return {
        "mode": mode,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "ollama_model": ollama_client.model,
        "ollama_reachable": ollama_available,
        "trained_detectors": sorted(detectors),
        "execution_dry_run": ai_config.EXECUTION_DRY_RUN,
        "feedback_enabled": ai_config.FEEDBACK_ENABLED,
        "anomaly_score_threshold": ai_config.ANOMALY_SCORE_THRESHOLD,
        "max_remediation_attempts": ai_config.MAX_REMEDIATION_ATTEMPTS,
        "recovery_metrics_measurable": mode == "live",
        "recovery_metrics_note": (
            "Recovery Success Rate and False Recovery Rate require a genuinely active fault to "
            "remediate. In replay mode nothing is executed (dry run), so they are not measurable "
            "and are reported as such rather than as a meaningless 100%."
            if mode != "live" else
            "Live mode: a real fault was injected before each run, so recovery metrics reflect "
            "real remediation outcomes."
        ),
    }


def measure_detection_latency(raw_record: dict, detectors: dict) -> Optional[float]:
    """Times the detection path only - feature extraction plus scoring - and nothing downstream.

    This is measured separately, outside the graph, rather than by timing the first `invoke()`.
    The first invoke runs all the way to the approval pause, so it includes retrieval, RCA and any
    LLM narration; reporting that as "detection latency" would overstate it by orders of magnitude
    and would make the two arms look different on a metric they share identically."""
    from ..anomaly import feature_extractor
    from ..anomaly.models import FeatureVector  # noqa: F401 - imported for symmetry/readability

    started = time.perf_counter()
    fv = feature_extractor.extract_features(raw_record)
    if fv is None:
        return None
    detector = detectors.get(fv.service)
    if detector is None or not detector.is_trained:
        return None
    detector.score(fv)
    return round(time.perf_counter() - started, 6)


def run_one(
    arm: Arm,
    graph,
    raw_record: dict,
    incident: dict,
    truth: dict,
    operator: SimulatedOperator,
    llm_call_counter: Optional[Callable[[], int]] = None,
    detection_latency_seconds: Optional[float] = None,
) -> ArmResult:
    """Runs one incident through one arm, resuming past every approval pause the graph raises."""
    incident_id = incident["incident_id"]
    result = ArmResult(
        arm=arm, incident_id=incident_id,
        true_fault_type=truth["fault_type"],
        true_root_cause_service=truth["root_cause_service"],
        true_symptom_service=truth["symptom_service"],
        detection_latency_seconds=detection_latency_seconds,
    )

    calls_before = llm_call_counter() if llm_call_counter else 0
    config = default_run_config(f"eval-{arm}-{incident_id}")
    started = time.perf_counter()

    try:
        state = graph.invoke({"raw_record": raw_record, "incident_id": incident_id}, config)
        # Time to the first approval pause: detection + retrieval + diagnosis. Distinct from
        # detection latency above, and reported under its own name so the two are not conflated.
        result.time_to_first_pause_seconds = round(time.perf_counter() - started, 4)

        resumes = 0
        while "__interrupt__" in state and resumes < MAX_RESUMES:
            payload = state["__interrupt__"][0].value
            state = graph.invoke(Command(resume=operator.decide(payload, incident_id)), config)
            resumes += 1

        if "__interrupt__" in state:
            result.error = f"still interrupting after {MAX_RESUMES} resumes"
            return result

        final = state.get("result") or {}
        result.raw_result = final
        _populate_from_result(result, final)

    except Exception as exc:  # noqa: BLE001 - one bad incident must not abort a 96-run evaluation
        log.exception("Incident %s failed on arm %s", incident_id, arm)
        result.error = f"{type(exc).__name__}: {exc}"

    result.total_wall_clock_seconds = round(time.perf_counter() - started, 4)
    if llm_call_counter:
        result.llm_calls = llm_call_counter() - calls_before
    return result


def _populate_from_result(result: ArmResult, final: dict) -> None:
    rca = final.get("root_cause_analysis") or {}
    proposal = final.get("remediation_proposal") or {}
    safety = final.get("safety_check") or {}
    execution = final.get("execution_result") or {}
    verification = final.get("recovery_verification") or {}
    diagnosis = final.get("single_agent_diagnosis") or {}
    anomaly = final.get("anomaly_result") or {}

    result.decision = final.get("decision", "unknown")
    result.detected_anomaly = bool(anomaly.get("is_anomaly"))
    result.predicted_fault_type = rca.get("suspected_fault_type")
    # "unknown" is the pipeline's explicit no-conclusion marker; treating it as a prediction would
    # score a refusal to guess as a wrong answer.
    predicted_root = rca.get("suspected_root_cause_service")
    result.predicted_root_cause_service = None if predicted_root in (None, "unknown") else predicted_root
    result.confidence = rca.get("confidence")
    result.determination_method = rca.get("determination_method")
    result.reasoning = rca.get("llm_reasoning")

    result.safety_passed = safety.get("passed")
    result.safety_violations = list(safety.get("violated_rules") or [])
    result.human_decision = final.get("human_decision")
    result.proposed_action_category = proposal.get("action_category")
    result.proposed_target_service = proposal.get("target_service")
    result.execution_status = execution.get("status")
    result.recovery_status = verification.get("status")
    result.recovery_verified = verification.get("verified")
    result.mttr_seconds = final.get("mttr_seconds")
    result.remediation_attempts = final.get("remediation_attempts") or 0
    result.feedback_status = (final.get("feedback_result") or {}).get("status")
    result.limitations = list(final.get("limitations") or [])
    result.parse_issues = list(diagnosis.get("parse_issues") or [])


class _CountingOllamaClient:
    """Wraps the real client to count `generate()` calls, so "how much LLM work did this arm do"
    is measured rather than assumed from the architecture diagram. Delegates everything else."""

    def __init__(self, inner: OllamaClient):
        self._inner = inner
        self.calls = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def generate(self, prompt: str, expect_json: bool = True) -> str:
        self.calls += 1
        return self._inner.generate(prompt, expect_json=expect_json)


def run_evaluation(
    arms: Optional[list[Arm]] = None,
    operator_policy: str = "permissive",
    limit: Optional[int] = None,
    mode: str = "replay",
) -> EvaluationRun:
    arms = arms or ["multi_agent", "single_agent"]
    incidents = replay.load_evaluation_incidents()
    if limit:
        incidents = incidents[:limit]

    if mode == "replay" and not ai_config.EXECUTION_DRY_RUN:
        raise PreflightError(
            "Replay mode requires EXECUTION_DRY_RUN. Replaying a historical incident and then "
            "'resetting' its fault would target a fault that is not active, trivially succeed, and "
            "make every incident look recovered. Set AI_ORCHESTRATION_EXECUTION_DRY_RUN=true."
        )

    checks = preflight(incidents)

    detectors = load_all_detectors()
    if not detectors:
        raise PreflightError(
            "No trained detectors found. Without them every incident scores 'no_model' and no "
            "diagnosis is produced. Train them first (see evaluation/prepare.py)."
        )

    ollama = _CountingOllamaClient(OllamaClient())
    operator = SimulatedOperator(operator_policy)  # type: ignore[arg-type]

    run = EvaluationRun(
        started_at=datetime.now(timezone.utc).isoformat(),
        arms=arms, mode=mode, operator_policy=operator_policy,
        environment=_environment_block(mode, detectors, ollama),  # type: ignore[arg-type]
        dataset={
            "evaluation_file": str(dt_config.EVALUATION_INCIDENTS_FILE),
            "evaluation_incident_count": len(incidents),
            "fault_type_counts": _counts(incidents, "fault_type"),
            "data_source_counts": _counts(incidents, "data_source"),
            "cross_service_incident_count": sum(
                1 for i in incidents if i.get("root_cause_service") != i.get("symptom_service")
            ),
        },
        preflight=checks,
    )

    builders = {"multi_agent": build_graph, "single_agent": build_baseline_graph}
    for index, incident in enumerate(incidents, start=1):
        try:
            raw_record = replay.incident_to_raw_record(incident)
            truth = replay.ground_truth(incident)
        except replay.ReplayError as exc:
            log.error("Skipping %s: %s", incident.get("incident_id"), exc)
            continue

        # Measured once per incident, outside the graph: it is the same shared detection path for
        # both arms, so measuring it per-arm would report the same work twice.
        detection_latency = measure_detection_latency(raw_record, detectors)

        for arm in arms:
            # A fresh checkpointer per (incident, arm) so no run can ever resume into another's
            # paused state.
            graph = builders[arm](detectors, ollama, MemorySaver())  # type: ignore[arg-type]
            result = run_one(arm, graph, raw_record, incident, truth, operator,
                             lambda: ollama.calls, detection_latency_seconds=detection_latency)
            run.results.append(result)

        log.info("[%d/%d] %s done", index, len(incidents), incident["incident_id"])

    run.finished_at = datetime.now(timezone.utc).isoformat()
    run.environment["operator_decisions"] = operator.decisions
    return run


def _counts(incidents: list[dict], key: str) -> dict:
    out: dict[str, int] = {}
    for inc in incidents:
        out[str(inc.get(key))] = out.get(str(inc.get(key)), 0) + 1
    return dict(sorted(out.items()))
