# Results

> **Authoritative tables:** `docs/evaluation/RESULTS.md`, generated directly by the harness. Raw
> per-incident rows are in `per_incident_results.csv`, the full metric structure in `metrics.json`,
> and the complete run — including every pipeline state — in `raw_run.json`. This section is the
> narrative around those artefacts; it introduces no number that is not in them.

## 5.1 Experimental setup

48 held-out Evaluation incidents (12 per fault type, 24 of them cross-service), each run through
both arms with byte-identical inputs, identical detectors, identical retrieval memory and an
identical simulated-operator policy. Execution ran in dry-run (§6.6 for why replay requires this),
feedback write-back disabled so retrieval memory stayed frozen across the run.

Before any measurement, the harness verified the leakage guard **live**: it attempted a feedback
write-back of a real Evaluation incident (`inc-0014`) and confirmed the refusal, aborting the run
otherwise. The check and its refusal reason are recorded in the results artefact.

Environment: Windows 11, Python 3.12.2, Ollama `llama3.2:3b`, all four detectors trained.

## 5.2 Performance — root cause identification

| Metric | Multi-agent | Single-agent baseline |
|---|---|---|
| Accuracy over all 48 incidents | **43.8%** | 25.0% |
| Accuracy when the system answered | **100%** (21/21) | 57.1% (12/21) |
| Abstention rate | 56.2% | 56.2% |
| Precision / Recall / F1 (macro) | 100% / 100% / 100% | 35.9% / 50.0% / 40.2% |
| **Cross-service accuracy** | **100%** (9/9) | **0%** (0/9) |
| Same-service accuracy | 100% (12/12) | 100% (12/12) |
| Blamed the symptom service on cross-service faults | 0 | **9 of 9** |
| Errored incidents | 0 | 0 |

Two features of this table matter more than the headline.

**The arms are identical on same-service faults and maximally separated on cross-service ones.** The
entire difference between the systems is confined to incidents where the service reporting the
symptom is not the service at fault. Neither system had any advantage on the easier half.

**The single-agent baseline's errors are not scattered.** On every one of the nine cross-service
incidents it answered, it named `payment-service` — the symptom service. A single, systematic
failure mode, not noise.

## 5.3 How the multi-agent arm reached its conclusions

Breaking the 21 answered incidents down by determination method reveals a perfect partition:

| Determination method | Incidents | Correct | Incident class |
|---|---|---|---|
| `metric_signature+rag_agreement` | 12 | 12 | all same-service |
| `metric_signature+rag_disagreement` | 9 | 9 | **all cross-service** |

On every cross-service incident, retrieval **disagreed** with the correct conclusion. Inspecting the
retrieved neighbours shows what it proposed instead — for the `auth-key-error` incidents it returned
`memory-leak` incidents whose root cause was `payment-service`, matching on the surface features of
payment-service telemetry.

The single-agent baseline received the same retrieved incidents and followed them to
`payment-service`. The multi-agent pipeline did not, because its architecture does not permit
retrieval to override a signature match. This is analysed in Discussion §6.1; it is the mechanism
behind the entire result.

## 5.4 System metrics

| Metric | Multi-agent | Single-agent |
|---|---|---|
| Detection latency (detection path only), mean | 0.0115 s | 0.0115 s |
| Detection latency, median | 0.0106 s | 0.0106 s |
| Time to approval pause (incl. diagnosis), mean | 17.6 s | 4.85 s |
| Anomaly detection rate | 43.8% | 43.8% |
| MTTR | **not measurable in replay** | **not measurable in replay** |
| Recovery Success Rate | **not measurable in replay** | **not measurable in replay** |
| False Recovery Rate | **not measurable in replay** | **not measurable in replay** |

Detection is shared by both arms by construction, and the two columns agree to six decimal places —
a divergence would have indicated a harness bug.

Detection latency and time-to-approval-pause are reported separately and deliberately. An earlier
version of the harness conflated them, timing detection by how long the first graph invocation took
— which runs all the way to the approval pause and therefore includes retrieval, RCA and LLM
narration. That reported detection latency as ~18.9 s rather than ~0.01 s, overstating it by three
orders of magnitude and making the two arms appear to differ on a stage they share identically. The
error was found by inspecting the anomalous mean against a near-zero median.

Detection rate by fault type: `memory-leak` 7/12, `db-lock` 5/12, `notification-latency` 5/12,
`auth-key-error` 4/12. Fewer than half of all incidents were surfaced for diagnosis at all, which is
the binding constraint on the system as deployed (§6.3).

The three unmeasured metrics require an active fault to remediate and therefore a live cluster;
they are reported as unmeasurable rather than as the 100% a dry-run would trivially produce.

## 5.5 AI metrics

| Metric | Multi-agent | Single-agent |
|---|---|---|
| LLM calls per incident (mean) | 0.875 | 0.4375 |
| LLM calls, total | 42 | 21 |
| Wall clock per incident (mean) | 17.6 s | 4.85 s |
| Incidents producing reasoning text | **3 of 21** | 21 of 21 |
| Hallucination — cited a metric absent from the evidence | 0% *(3 samples — not meaningful)* | 0% (over 21) |
| **Hallucination — claimed a baseline-range value was elevated** | 0% | **9.5%** (2 of 21) |
| Response parse-issue rate | 0% | 0% |

Token cost is replaced by inference time and call count, per the self-hosted-model substitution
documented in Methodology §3.11.

**The multi-agent arm's LLM narration failed on 18 of 21 incidents**, exceeding the 30-second
timeout, and its diagnostic accuracy was 100% regardless — an unplanned but direct demonstration
that the deterministic conclusion does not depend on the model (§6.4).

The two hallucination rows measure different things, and the difference is the finding. The
conventional definition — citing a metric that does not exist in the evidence — returns 0% for the
baseline, and that 0% is a real measurement rather than a broken detector (11 genuine metric
citations were detected across the 21 reasoning texts). But the baseline twice justified "memory
leak" using heap values of 45.0 MB and 42.8 MB, both inside payment-service's documented 30–45 MB
baseline. The metrics were real; their stated significance was invented. A second measure comparing
each cited value against its documented baseline range reports **9.5%** where the first reports 0%.
Both remain lower bounds, since most responses cite no numeric value at all. See §6.4.

## 5.6 Run-to-run reproducibility

The evaluation was executed twice, independently, on the same corpus. Comparing the two runs
incident by incident:

| Arm | Identical root-cause predictions across runs |
|---|---|
| Multi-agent | **48 / 48** |
| Single-agent baseline | **48 / 48** |

Every headline metric is therefore identical between runs. The multi-agent result is reproducible by
construction — its conclusion is reached deterministically. **The single-agent result being equally
reproducible was not guaranteed** and is worth stating: an LLM-driven diagnosis produced the same
48 answers twice, including the same nine wrong ones.

What did vary is the part that does not affect the conclusion: the multi-agent arm's LLM narration
succeeded on 1 of 21 incidents in the first run and 3 of 21 in the second, the rest timing out. That
narration variance changed no diagnosis — which is direct evidence for the architectural claim that
the narrative layer cannot influence the deterministic conclusion (§3.5).

This should be read narrowly. It establishes that this model, at this temperature, on these prompts,
is stable across two runs; it does not establish that single-agent diagnosis is stable in general,
and two runs cannot characterise a distribution.

## 5.7 Human-in-the-loop

| Metric | Multi-agent | Single-agent |
|---|---|---|
| Incidents reaching human approval | 21 | 21 |
| Approved / Rejected | 21 / 0 | 21 / 0 |
| Approval accuracy | 100% | 57.1% |
| Unsafe actions prevented by Safety Validator | 0 | 0 |
| **Executions without approval** | **0** | **0** |

The last row is the safety invariant, reported as a measured quantity rather than asserted: no
action executed without an explicit approval, on either arm, on any incident.

Approval accuracy is not an independent measurement under the permissive operator policy — it equals
diagnostic accuracy by construction (§3.8). Unsafe Action Prevention is 0 because dry-run execution
never failed, so no retry escalated to a `restart_service` proposal and the ledger-service rule had
no opportunity to fire; that rule is evidenced instead by the Week 10 live session and by regression
tests.

## 5.8 Infrastructure metrics

Not collected. Prometheus was not deployed in the replay environment; the harness records this as an
explicit unavailability rather than as zeros.

## 5.9 Summary of what was established

- A deterministic-first pipeline identified the root cause on **100% of the cross-service incidents
  it answered**, where a single-agent LLM given identical evidence identified **0%**, failing
  systematically to the symptom service.
- The two systems were **indistinguishable on same-service faults**.
- The mechanism is identified and quantified (§5.3): retrieval was misleading on precisely the hard
  cases, and the architectural refusal to defer to it is what preserved accuracy.
- The multi-agent design cost ~2× LLM calls and ~3.6× wall clock.
- The system's real-world ceiling in this run was set by **detection** (43.8%), not diagnosis.
- The absolute accuracy figures are bounded by a synthetic-corpus circularity documented in §6.2;
  the comparative findings are not.
