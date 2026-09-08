"""Evaluation category (part 2): metric computation, and above all the honesty rules.

The two properties worth protecting with tests are the ones a reader of the Results section is
entitled to rely on: an unmeasured metric never becomes a number, and an abstention is never scored
as a wrong answer.
"""
from __future__ import annotations

from ai_orchestration.evaluation import metrics as metrics_mod
from ai_orchestration.evaluation.models import ArmResult, EvaluationRun


def _result(arm="multi_agent", incident_id="inc-0001", true_root="payment-service",
            predicted_root="payment-service", true_symptom="payment-service", **overrides) -> ArmResult:
    base = dict(
        arm=arm, incident_id=incident_id,
        true_fault_type="memory-leak", true_root_cause_service=true_root,
        true_symptom_service=true_symptom,
        predicted_fault_type="memory-leak", predicted_root_cause_service=predicted_root,
        decision="anomaly", detected_anomaly=True, safety_passed=True,
        human_decision="approved", total_wall_clock_seconds=1.0,
    )
    base.update(overrides)
    return ArmResult(**base)


# --- Honesty rule 1: an unmeasured metric is never a number ------------------------------------

def test_recovery_metrics_are_none_with_a_reason_in_replay_mode():
    results = [_result(execution_status="dry_run") for _ in range(5)]

    system = metrics_mod.system_metrics(results, mode="replay")

    assert system["recovery_success_rate"] is None
    assert system["false_recovery_rate"] is None
    assert system["mttr_seconds_mean"] is None
    assert "not measurable in replay mode" in system["recovery_success_note"].lower()


def test_recovery_metrics_are_computed_in_live_mode():
    results = [
        _result(execution_status="executed", recovery_verified=True, recovery_status="recovered", mttr_seconds=30.0),
        _result(execution_status="executed", recovery_verified=False, recovery_status="fault_still_active", mttr_seconds=40.0),
    ]

    system = metrics_mod.system_metrics(results, mode="live")

    assert system["recovery_success_rate"] == 0.5
    assert system["false_recovery_rate"] == 0.5
    assert system["mttr_seconds_mean"] == 35.0


# --- Honesty rule 2: an abstention is not a wrong answer ----------------------------------------

def test_abstentions_are_counted_separately_from_wrong_answers():
    results = [
        _result(incident_id="a", predicted_root="payment-service"),                     # correct
        _result(incident_id="b", predicted_root="auth-service"),                        # wrong
        _result(incident_id="c", predicted_root=None, decision="insufficient_data"),    # abstained
        _result(incident_id="d", predicted_root=None, decision="normal"),               # abstained
    ]

    perf = metrics_mod.performance_metrics(results)

    assert perf["answered"] == 2
    assert perf["abstained"] == 2
    assert perf["abstention_rate"] == 0.5
    assert perf["accuracy_when_answered"] == 0.5          # 1 of 2 answered
    assert perf["accuracy_over_all_incidents"] == 0.25    # 1 of 4 overall - the honest headline


def test_unknown_is_treated_as_an_abstention_not_a_prediction():
    """The pipeline's explicit no-conclusion marker must not be scored as a guess."""
    results = [_result(predicted_root=None)]
    perf = metrics_mod.performance_metrics(results)
    assert perf["answered"] == 0
    assert perf["accuracy_when_answered"] is None
    assert perf["per_class"] == {}


def test_cross_service_accuracy_is_reported_separately():
    """The headline capability - an aggregate could hide total failure on exactly these cases."""
    results = [
        # cross-service: correct
        _result(incident_id="x", true_root="notification-service", true_symptom="payment-service",
                predicted_root="notification-service"),
        # cross-service: blamed the symptom service (the failure mode these faults exist to expose)
        _result(incident_id="y", true_root="auth-service", true_symptom="payment-service",
                predicted_root="payment-service"),
        # same-service: correct
        _result(incident_id="z"),
    ]

    perf = metrics_mod.performance_metrics(results)

    assert perf["cross_service_count"] == 2
    assert perf["cross_service_accuracy"] == 0.5
    assert perf["same_service_accuracy"] == 1.0
    assert perf["blamed_symptom_service_on_cross_service_incidents"] == 1


# --- Hallucination proxy ------------------------------------------------------------------------

def test_citing_a_metric_absent_from_the_evidence_is_flagged():
    result = _result(
        reasoning="hikaricp_connections_active is elevated, so the pool is exhausted.",
        raw_result={"telemetry_evidence": {"metrics": {"jvm_memory_used_bytes": 5.0e8,
                                                       "hikaricp_connections_active": None}}},
    )
    assert metrics_mod.unsupported_metric_mentions(result) == ["hikaricp_connections_active"]


def test_citing_a_metric_that_was_present_is_not_flagged():
    result = _result(
        reasoning="jvm_memory_used_bytes is far above baseline.",
        raw_result={"telemetry_evidence": {"metrics": {"jvm_memory_used_bytes": 5.0e8}}},
    )
    assert metrics_mod.unsupported_metric_mentions(result) == []


def test_ordinary_prose_cannot_inflate_the_hallucination_rate():
    """Only known metric names count; unrecognised snake_case tokens are ignored by design, which
    makes the reported rate a lower bound rather than a noisy over-estimate."""
    result = _result(
        reasoning="the payment_service_was_slow and some_other_thing_happened repeatedly.",
        raw_result={"telemetry_evidence": {"metrics": {"jvm_memory_used_bytes": 1.0}}},
    )
    assert metrics_mod.unsupported_metric_mentions(result) == []


def test_claiming_a_baseline_range_value_is_elevated_is_flagged():
    """The stronger hallucination measure, added after the metric-name check proved too weak on the
    real run: the baseline justified "memory leak" by citing heap values that sit inside
    payment-service's documented 30-45 MB normal range. The metric was real; its significance was
    invented, and the weaker check cannot see that.

    The value below is a fixture, not a quotation from the reported run - see
    test_paper_claims_match_artefacts.py for the checks tied to the actual run artefacts."""
    result = _result(
        reasoning="The JVM heap usage increase (jvm_memory_used_bytes: 44984562.9) suggests a memory leak.",
        raw_result={"service": "payment-service",
                    "telemetry_evidence": {"metrics": {"jvm_memory_used_bytes": 44984562.9}}},
    )

    claims = metrics_mod.unsupported_significance_claims(result)

    assert len(claims) == 1
    assert "within the documented baseline range" in claims[0]
    # ...and the weaker check sees nothing wrong, which is exactly why this one exists.
    assert metrics_mod.unsupported_metric_mentions(result) == []


def test_a_genuinely_elevated_value_is_never_flagged():
    """Conservative by construction: a real deviation described as a deviation is correct."""
    result = _result(
        reasoning="jvm_memory_used_bytes: 500000000.0 shows a large increase above baseline.",
        raw_result={"service": "payment-service",
                    "telemetry_evidence": {"metrics": {"jvm_memory_used_bytes": 5.0e8}}},
    )
    assert metrics_mod.unsupported_significance_claims(result) == []


def test_reporting_a_value_without_claiming_elevation_is_not_flagged():
    """Merely reporting a value is not a false claim about it."""
    result = _result(
        reasoning="Observed jvm_memory_used_bytes: 44984562.9 for this service.",
        raw_result={"service": "payment-service",
                    "telemetry_evidence": {"metrics": {"jvm_memory_used_bytes": 44984562.9}}},
    )
    assert metrics_mod.unsupported_significance_claims(result) == []


def test_hallucination_rate_is_none_when_no_arm_produced_reasoning():
    ai = metrics_mod.ai_metrics([_result(reasoning=None)])
    assert ai["hallucination_rate"] is None
    assert "undefined rather than 0" in ai["hallucination_note"]


# --- Human metrics ------------------------------------------------------------------------------

def test_safety_rejections_are_counted_as_prevented_unsafe_actions():
    results = [
        _result(safety_passed=False, human_decision=None,
                safety_violations=["High-risk actions are never allowed against 'ledger-service'"]),
        _result(safety_passed=True),
    ]

    human = metrics_mod.human_metrics(results, operator_policy="permissive")

    assert human["unsafe_actions_prevented_by_safety_validator"] == 1
    assert human["incidents_reaching_human_approval"] == 1  # the rejected one never reached a human
    assert any("ledger-service" in r for r in human["safety_violation_reasons"])


def test_executions_without_approval_are_surfaced_as_their_own_number():
    """This must always be zero; making it a reported metric means a regression shows up in the
    results table rather than only in a test."""
    results = [_result(execution_status="executed", human_decision="approved")]
    assert metrics_mod.human_metrics(results, "permissive")["executions_without_approval"] == 0


def test_compute_all_produces_every_metric_category():
    run = EvaluationRun(started_at="t0", arms=["multi_agent", "single_agent"], mode="replay")
    run.results = [_result(arm="multi_agent"), _result(arm="single_agent")]

    computed = metrics_mod.compute_all(run)

    for arm in ("multi_agent", "single_agent"):
        assert set(computed["per_arm"][arm]) == {"performance", "system", "ai", "human"}
    assert "collaboration" in computed
    assert computed["infrastructure"]["available"] is False  # no Prometheus URL supplied
