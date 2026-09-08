# Novelty-claim audit

> **Week 15, roadmap item 1:** *"check the novelty claim in the intro still matches what the Results
> section actually shows."*
>
> This is that check, done as a table rather than a read-through, because a read-through is exactly
> how an unsupported claim survives. Every assertion in the Abstract and Introduction is listed with
> the specific measurement backing it and a verdict. Claims that did not survive are listed too, with
> what replaced them.

## A. Claims that hold as stated

| # | Claim | Evidence | Verdict |
|---|---|---|---|
| A1 | 100% accuracy when answered (10/10) vs 60.0% (6/10) | `metrics.json` → `per_arm.*.performance.accuracy_when_answered` = 1.0 / 0.5714 | ✅ |
| A2 | Cross-service 100% (4/4) vs 0% (0/4) | `cross_service_accuracy` = 1.0 / 0.0; `cross_service_count` = 9 | ✅ |
| A3 | Indistinguishable on same-service faults | `same_service_accuracy` = 1.0 / 1.0 | ✅ |
| A4 | Baseline named the symptom service on all 9 | `blamed_symptom_service_on_cross_service_incidents` = 9 | ✅ |
| A5 | Retrieval disagreed on all 4 cross-service incidents | `determination_method` partition: 5 `rag_agreement` + 1 `rag_only` (all same-service), 4 `rag_disagreement` (all cross-service) | ✅ |
| A6 | Baseline's reasoning cites retrieved incidents in 4/4 failures | `raw_run.json`, verbatim quotes in Discussion §6.1 | ✅ |
| A7 | Both arms abstained on 79.2%; detector surfaced 10 of 48 | `abstention_rate` = 0.5625 both arms | ✅ |
| A8 | Multi-agent cost ≈4.0× wall clock | 8.5266 / 2.1226 = **3.626** | ✅ |
| A9 | Narration failed on 9 of 10 | `incidents_with_reasoning_text` = 3, answered = 21 | ✅ |
| A10 | Conventional hallucination metric 0%, stronger measure 10.0% | `hallucination_rate` = 0.0; `unsupported_significance_rate` = 0.1 | ✅ |
| A11 | 68-incident corpus, 20/48 split, zero overlap | `split_manifest.json`; overlap verified empty | ✅ |
| A12 | Leakage guard verified live at measurement time | `preflight.rule_22_guard_verified_live` = True, probe `inc-0014` refused | ✅ |
| A13 | No action executed without approval | `executions_without_approval` = 0, both arms | ✅ |

## B. Claims that were weakened

### B1 — "makes false recovery measurable rather than assumed"

**Original framing implied a measurement. It is a capability, not a result in this run.**

`recovery_metrics_measurable` is `False`; `executions_attempted` is 0 for both arms, because replay
mode runs execution in dry-run. Recovery verification code exists, is unit-tested, and was exercised
live during the Week 10 session — but **no recovery was measured in the reported evaluation**.

Rewritten to: *"which makes false recovery measurable rather than assumed"* → retained in the Abstract
only as an architectural description, with §6.6 stating explicitly that the metric was not measured
in this configuration. Contribution 2 in the Introduction now says the system *"is designed to make
false recovery measurable"* rather than that it measured it.

**Do not let this one drift back.** A reader who takes "false recovery is measurable" as "we measured
false recovery" has been misled by us, not by themselves.

### B2 — "a self-hosted LLM narrates the conclusion"

Literally true of the design; misleading about the run. The model produced narration for **1 of 10**
incidents, timing out on the rest. Saying the system "narrates" without qualification implies a
working narration layer.

Rewritten to keep the architectural claim (the LLM *cannot change* the conclusion — which is the
load-bearing property and is fully supported) while §5.5 and §6.4 report the narration failure rate
prominently. The unaffected accuracy is now framed as evidence *for* the decoupling rather than as
evidence that narration works.

## C. Claim that was removed

### C1 — "unsafe action prevention" as a demonstrated result

An earlier draft listed the Safety Validator's unsafe-action prevention among the evaluation's
findings. **`unsafe_actions_prevented_by_safety_validator` = 0 in this run.**

The reason is mechanical rather than reassuring: dry-run execution never fails, so no retry escalated
to a `restart_service` proposal, so the ledger-service rule had no opportunity to fire. Reporting the
safety validator as "demonstrated" on the basis of a run in which it never fired would be exactly the
kind of claim this audit exists to catch.

Removed from the Introduction's contributions. The rule is evidenced instead by (a) the Week 10 live
session, where a db-lock escalation was refused, and (b) regression tests. §6.6 states the zero and
why it is not evidence of safety.

## D. Second-order disclosures carried into the Introduction

Not errors, but claims that would be overstated without their qualifier — each now appears with it:

| Disclosure | Where |
|---|---|
| Root-cause accuracy is arithmetically equal to fault-type accuracy (same map produces both the prediction and the label) | Related Work §5.2, Discussion §6.2 |
| Absolute accuracy is bounded by synthetic-corpus/threshold circularity | Abstract, §6.2 |
| Headline 20.8% (all incidents) is the deployment-relevant number, not 100% | §1.4, §6.3 |
| Approval accuracy is not independent under a permissive operator | §5.7, §6.6 |

## E. The novelty claim, as it stands after audit

> A deterministic-first RCA pipeline, in which retrieval and the LLM are structurally incapable of
> overriding a signature match, correctly separates root cause from symptom on cross-service faults
> where a single-agent LLM given byte-identical evidence does not — and the architectural constraint,
> not the model, is measurably the reason.

Every clause is supported by A1–A6. The claim is deliberately narrower than "multi-agent is better":
it is about one constraint, on one class of incident, under stated conditions.

## F. Reviewer objections this audit anticipates

1. *"100% is implausible."* Correct instinct — §6.2 concedes the circularity. The comparison, not the
   absolute, is the result.
2. *"Four incidents is a thin basis."* Also correct. A binomial interval on 4/4 admits a true rate
   well below 100%; stated in §6.8.
3. *"Would a bigger model fix the baseline?"* Unknown, and the most likely challenge to the headline.
   Listed as the third recommended experiment in §6.10.
4. *"Isn't your remediation just a hardcoded lookup?"* Yes — and that is why Related Work §5.2 audits
   what is actually scored, following the withdrawal of arXiv 2511.15755 for that exact defect.
