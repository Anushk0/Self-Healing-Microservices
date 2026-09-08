"""Every headline figure printed in `docs/paper/` must equal the value in the artefacts it cites.

This exists because it failed. A dataset regeneration changed every measured number, the harness
artefacts (`docs/evaluation/`) were rewritten correctly, and the hand-written narrative in
`docs/paper/` was not fully updated with them - so the paper carried figures from a superseded run,
including a claim-audit table whose Evidence column contradicted its own Claim column while marked
verified.

A prose review is exactly how that survives: the numbers are individually plausible and spread
across six files. So the check is mechanical. Each case below renders the figure *from
metrics.json* and asserts the resulting string appears in the document, which means a future re-run
either updates the prose or fails here naming the line to fix.

Deliberately checks the rendered substring rather than parsing tables: it is the printed characters
a reader sees that must be right, and this catches a stale figure wherever in the prose it appears.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PAPER = REPO / "docs" / "paper"
EVAL = REPO / "docs" / "evaluation"


def _json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def metrics() -> dict:
    return _json(EVAL / "metrics.json")


@pytest.fixture(scope="module")
def docs() -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8") for p in PAPER.glob("*.md")}


def _pct(value: float, places: int = 1) -> str:
    return f"{value * 100:.{places}f}"


def test_headline_accuracy_figures_appear_verbatim(metrics, docs):
    multi = metrics["per_arm"]["multi_agent"]["performance"]
    single = metrics["per_arm"]["single_agent"]["performance"]

    answered_m, answered_s = multi["answered"], single["answered"]
    correct_s = round(single["accuracy_when_answered"] * answered_s)

    assert f"({answered_m}/{answered_m})" in docs["03-results.md"]
    assert f"({correct_s}/{answered_s})" in docs["03-results.md"]
    assert f"{_pct(single['accuracy_when_answered'])}%" in docs["03-results.md"]

    n_cross = multi["cross_service_count"]
    assert f"({n_cross}/{n_cross})" in docs["03-results.md"]
    assert f"(0/{n_cross})" in docs["03-results.md"]
    assert single["cross_service_accuracy"] == 0.0
    assert multi["cross_service_accuracy"] == 1.0


def test_same_service_counts_are_not_confused_with_total_answered(metrics, docs):
    """The stale value here was `12/12` - a pre-regeneration count - in two files at once."""
    multi = metrics["per_arm"]["multi_agent"]["performance"]
    n_same = multi["answered"] - multi["cross_service_count"]
    row = f"| Same-service accuracy | 100% ({n_same}/{n_same}) | 100% ({n_same}/{n_same}) |"
    assert row in docs["03-results.md"]
    assert row in docs["04-discussion.md"]


def test_macro_prf_row_matches_metrics(metrics, docs):
    single = metrics["per_arm"]["single_agent"]["performance"]
    rendered = (
        f"| Precision / Recall / F1 (macro) | 100% / 100% / 100% | "
        f"{_pct(single['precision_macro'])}% / {_pct(single['recall_macro'])}% / "
        f"{_pct(single['f1_macro'])}% |"
    )
    assert rendered in docs["03-results.md"]


def test_llm_call_totals_match(metrics, docs):
    m = metrics["per_arm"]["multi_agent"]["ai"]["llm_calls_total"]
    s = metrics["per_arm"]["single_agent"]["ai"]["llm_calls_total"]
    assert f"| LLM calls, total | {m} | {s} |" in docs["03-results.md"]


def test_reasoning_text_counts_match(metrics, docs):
    """Narration counts carry the 'the conclusion does not depend on the model' argument."""
    m_ai = metrics["per_arm"]["multi_agent"]["ai"]
    s_ai = metrics["per_arm"]["single_agent"]["ai"]
    answered_m = metrics["per_arm"]["multi_agent"]["performance"]["answered"]
    answered_s = metrics["per_arm"]["single_agent"]["performance"]["answered"]

    assert (
        f"| Incidents producing reasoning text | **{m_ai['incidents_with_reasoning_text']} of "
        f"{answered_m}** | {s_ai['incidents_with_reasoning_text']} of {answered_s} |"
    ) in docs["03-results.md"]

    failed = answered_m - m_ai["incidents_with_reasoning_text"]
    assert f"narration failed on {failed} of {answered_m}" in docs["04-discussion.md"].lower()


def test_human_approval_counts_match(metrics, docs):
    for arm in ("multi_agent", "single_agent"):
        assert metrics["per_arm"][arm]["human"]["rejected"] == 0
    n = metrics["per_arm"]["multi_agent"]["human"]["incidents_reaching_human_approval"]
    assert f"| Incidents reaching human approval | {n} | {n} |" in docs["03-results.md"]
    assert f"| Approved / Rejected | {n} / 0 | {n} / 0 |" in docs["03-results.md"]


def test_detection_figures_match(metrics, docs):
    sysm = metrics["per_arm"]["multi_agent"]["system"]
    mean = f"{sysm['detection_latency_seconds_mean']:.4f}"
    median = f"{sysm['detection_latency_seconds_median']:.4f}"
    assert f"| Detection latency (detection path only), mean | {mean} s | {mean} s |" in docs["03-results.md"]
    assert f"| Detection latency, median | {median} s | {median} s |" in docs["03-results.md"]

    total = metrics["dataset"]["evaluation_incident_count"]
    detected = round(sysm["anomaly_detection_rate"] * total)
    assert f"{detected} of {total}" in docs["03-results.md"]


def test_claim_audit_evidence_column_matches_the_metrics_it_cites(metrics, docs):
    """The audit table is where a wrong number does the most damage: it is presented as
    verification. Its Evidence column previously disagreed with its own Claim column."""
    audit = docs["06-novelty-claim-audit.md"]
    multi = metrics["per_arm"]["multi_agent"]["performance"]
    single = metrics["per_arm"]["single_agent"]["performance"]

    assert f"= 1.0 / {single['accuracy_when_answered']}" in audit
    assert f"`cross_service_count` = {multi['cross_service_count']}" in audit
    assert (
        f"`blamed_symptom_service_on_cross_service_incidents` = "
        f"{single['blamed_symptom_service_on_cross_service_incidents']}"
    ) in audit
    assert f"`abstention_rate` = {multi['abstention_rate']}" in audit
    assert (
        f"`incidents_with_reasoning_text` = "
        f"{metrics['per_arm']['multi_agent']['ai']['incidents_with_reasoning_text']}, "
        f"answered = {multi['answered']}"
    ) in audit


def test_claim_audit_cost_ratio_arithmetic_is_correct(metrics, docs):
    """A8 previously stated a ratio that its own two operands do not produce."""
    m = metrics["per_arm"]["multi_agent"]["ai"]["wall_clock_seconds_per_incident_mean"]
    s = metrics["per_arm"]["single_agent"]["ai"]["wall_clock_seconds_per_incident_mean"]
    assert f"{m} / {s} = **{m / s:.3f}**" in docs["06-novelty-claim-audit.md"]


@pytest.mark.parametrize("ablation", ["no_retrieval", "rag_override"])
def test_ablation_tables_match_their_own_artefacts(ablation, docs):
    m = _json(EVAL / "ablations" / ablation / "metrics.json")
    text = docs["10-ablations.md"]
    for arm, per in m["per_arm"].items():
        answered = per["performance"]["answered"]
        assert f"| Incidents answered | {answered} |" in text, (
            f"{ablation}/{arm}: answered={answered} is not what the ablation table prints"
        )


def test_ablation_a_single_agent_figures_are_stated_correctly(docs):
    m = _json(EVAL / "ablations" / "no_retrieval" / "metrics.json")
    perf = m["per_arm"]["single_agent"]["performance"]
    text = docs["10-ablations.md"]
    n_same = perf["answered"] - perf["cross_service_count"]
    same_correct = round(perf["same_service_accuracy"] * n_same)
    cross_correct = round(perf["cross_service_accuracy"] * perf["cross_service_count"])

    assert f"to {_pct(perf['same_service_accuracy'])}%** ({same_correct} of {n_same})" in text
    assert (
        f"to {_pct(perf['cross_service_accuracy'])}%** "
        f"({cross_correct} of {perf['cross_service_count']})"
    ) in text
    assert f"to {_pct(perf['accuracy_when_answered'])}% when answered" in text


def test_discussion_quotes_are_present_in_the_run_it_cites():
    """Discussion 6.1 quotes the baseline verbatim and attributes each quote to an incident id.

    Those quotes previously cited incident ids that were not in the run at all - a regeneration had
    changed which incidents were answered. A quote attributed to the wrong incident is a fabricated
    citation, so it is checked against raw_run.json rather than trusted.
    """
    raw = _json(EVAL / "raw_run.json")
    reasoning = {
        (r["arm"], r["incident_id"]): (r.get("reasoning") or "") for r in raw["results"]
    }
    discussion = (PAPER / "04-discussion.md").read_text(encoding="utf-8")

    quoted = [
        ("inc-0011", "Historical records (inc-0042, inc-0044, inc-0037) also suggest a memory-leak"),
        ("inc-0010", "Historical past incidents (inc-0042, inc-0044, inc-0037) also report memory-leak"),
        ("inc-0067", "This is consistent with the pattern of memory-leak faults seen in past incidents"),
    ]
    for incident_id, fragment in quoted:
        assert fragment in discussion, f"{incident_id}: quote missing from Discussion 6.1"
        assert fragment in reasoning[("single_agent", incident_id)], (
            f"Discussion 6.1 attributes a quote to {incident_id} that does not appear in that "
            f"incident's reasoning in raw_run.json"
        )


def test_cited_within_baseline_heap_values_are_really_within_baseline():
    """6.1 claims three cited heap figures sit inside payment-service's documented normal range.

    Checked against the range the metrics module actually uses, so the prose cannot drift from the
    constant it describes.
    """
    from ai_orchestration.evaluation.metrics import _BASELINE_RANGES

    low, high = _BASELINE_RANGES["payment-service"]["jvm_memory_used_bytes"]
    raw = _json(EVAL / "raw_run.json")
    by_id = {r["incident_id"]: r for r in raw["results"] if r["arm"] == "single_agent"}
    discussion = (PAPER / "04-discussion.md").read_text(encoding="utf-8")

    for incident_id, stated_mb in (("inc-0011", 30.7), ("inc-0067", 30.5), ("inc-0065", 35.0)):
        text = by_id[incident_id]["reasoning"]
        value = next(
            float(tok)
            for tok in text.replace("(", " ").replace(")", " ").split()
            if tok.replace(".", "", 1).isdigit() and abs(float(tok) / 1e6 - stated_mb) < 0.1
        )
        assert low <= value <= high, f"{incident_id}: {value} is not inside the documented baseline"
        assert f"{stated_mb} MB" in discussion


def test_determination_method_partition_matches_the_csv(docs):
    """5.3's partition table is the mechanism claim; it must match the per-incident rows."""
    with open(EVAL / "per_incident_results.csv", encoding="utf-8") as f:
        rows = [
            r for r in csv.DictReader(f)
            if r["arm"] == "multi_agent" and r["predicted_root_cause_service"]
        ]
    counts = Counter(r["determination_method"] for r in rows)
    text = docs["03-results.md"]
    assert f"| `metric_signature+rag_agreement` | {counts['metric_signature+rag_agreement']} |" in text
    assert f"| `rag_only` | {counts['rag_only']} |" in text
    assert f"| `metric_signature+rag_disagreement` | {counts['metric_signature+rag_disagreement']} |" in text


# --- The two-run reproducibility claim (5.6, 6.4) -----------------------------------------------
#
# 5.6 states that a second independent run produced identical diagnoses while the LLM prose varied.
# The second run's artefacts are committed under docs/evaluation/reproducibility/ precisely so that
# claim is checkable rather than taken on trust - these tests do the checking.

REPRO = EVAL / "reproducibility"


def _predictions(csv_path: Path) -> dict[tuple[str, str], str]:
    with open(csv_path, encoding="utf-8") as f:
        return {
            (r["arm"], r["incident_id"]): r["predicted_root_cause_service"]
            for r in csv.DictReader(f)
        }


def test_second_run_reproduces_every_diagnosis():
    run1 = _predictions(EVAL / "per_incident_results.csv")
    run2 = _predictions(REPRO / "run2_per_incident_results.csv")

    assert set(run1) == set(run2), "the two runs did not cover the same incidents"
    differing = sorted(k for k in run1 if run1[k] != run2[k])
    assert not differing, f"5.6 claims 48/48 identical per arm; these differ: {differing}"
    assert len(run1) == 96


def test_narration_landed_on_a_different_incident_in_each_run():
    """5.6's sharpened claim: the count was stable at one, but not the same one - which is why the
    passage argues from *which* incident rather than from the count."""
    run1 = _json(EVAL / "raw_run.json")["results"]
    run2 = _json(REPRO / "run2_raw_run.json")["results"]

    def narrated(results: list[dict]) -> list[str]:
        return sorted(
            r["incident_id"]
            for r in results
            if r["arm"] == "multi_agent" and (r.get("reasoning") or "").strip()
        )

    first, second = narrated(run1), narrated(run2)
    assert first == ["inc-0010"], first
    assert second == ["inc-0065"], second
    assert first != second, "5.6 claims narration landed on a different incident each run"

    discussion = (PAPER / "04-discussion.md").read_text(encoding="utf-8")
    assert "`inc-0010`, then `inc-0065`" in discussion


def test_baseline_prose_varied_while_its_conclusions_did_not():
    """The claim that carries 6.4: every single-agent reasoning text differed between runs, yet no
    extracted conclusion did."""
    by_key = lambda results: {  # noqa: E731 - local, and a def here would read worse
        (r["arm"], r["incident_id"]): r for r in results
    }
    run1 = by_key(_json(EVAL / "raw_run.json")["results"])
    run2 = by_key(_json(REPRO / "run2_raw_run.json")["results"])

    single = [k for k in run1 if k[0] == "single_agent" and (run1[k].get("reasoning") or "").strip()]
    assert len(single) == 10

    identical_text = [k[1] for k in single if run1[k]["reasoning"] == run2[k]["reasoning"]]
    assert not identical_text, f"expected all prose to differ; identical on {identical_text}"

    moved = [
        k[1] for k in single
        if run1[k]["predicted_root_cause_service"] != run2[k]["predicted_root_cause_service"]
    ]
    assert not moved, f"conclusions must be stable while prose varies; moved on {moved}"
