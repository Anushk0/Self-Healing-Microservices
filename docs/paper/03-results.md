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
| Accuracy over all 48 incidents | **20.8%** | 12.5% |
| Accuracy when the system answered | **100%** (10/10) | 60.0% (6/10) |
| Abstention rate | 79.2% | 79.2% |
| Precision / Recall / F1 (macro) | 100% / 100% / 100% | 37.5% / 50.0% / 41.7% |
| **Cross-service accuracy** | **100%** (4/4) | **0%** (0/4) |
| Same-service accuracy | 100% (6/6) | 100% (6/6) |
| Blamed the symptom service on cross-service faults | 0 | **4 of 4** |
| Errored incidents | 0 | 0 |

Two features of this table matter more than the headline.

**The arms are identical on same-service faults and maximally separated on cross-service ones.** The
entire difference between the systems is confined to incidents where the service reporting the
symptom is not the service at fault. Neither system had any advantage on the easier half.

**The single-agent baseline's errors are not scattered.** On every one of the four cross-service
incidents it answered, it named `payment-service` — the symptom service. A single, systematic
failure mode, not noise.

## 5.3 How the multi-agent arm reached its conclusions

Breaking the 10 answered incidents down by determination method reveals a perfect partition:

| Determination method | Incidents | Correct | Incident class |
|---|---|---|---|
| `metric_signature+rag_agreement` | 5 | 5 | all same-service |
| `rag_only` | 1 | 1 | same-service (no signature matched) |
| `metric_signature+rag_disagreement` | 4 | 4 | **all cross-service** |

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
| Detection latency (detection path only), mean | 0.0061 s | 0.0061 s |
| Detection latency, median | 0.0052 s | 0.0052 s |
| Time to approval pause (incl. diagnosis), mean | 8.5 s | 2.12 s |
| Anomaly detection rate | 20.8% | 20.8% |
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

Detection rate by fault type: `memory-leak` 4/12, `auth-key-error` 2/12, `db-lock` 2/12,
`notification-latency` 2/12. 10 of 48 incidents - roughly one in five - were surfaced for diagnosis at
all, which is the binding constraint on the system as deployed (§6.3).

The three unmeasured metrics require an active fault to remediate and therefore a live cluster;
they are reported as unmeasurable rather than as the 100% a dry-run would trivially produce.

## 5.5 AI metrics

| Metric | Multi-agent | Single-agent |
|---|---|---|
| LLM calls per incident (mean) | 0.4167 | 0.2083 |
| LLM calls, total | 20 | 10 |
| Wall clock per incident (mean) | 8.5 s | 2.12 s |
| Incidents producing reasoning text | **1 of 10** | 10 of 10 |
| Hallucination — cited a metric absent from the evidence | 0% *(1 sample — not meaningful)* | 0% (over 10) |
| **Hallucination — claimed a baseline-range value was elevated** | 0% | **10.0%** (1 of 10) |
| Response parse-issue rate | 0% | 0% |

Token cost is replaced by inference time and call count, per the self-hosted-model substitution
documented in Methodology §3.11.

**The multi-agent arm's LLM narration failed on 9 of 10 incidents**, exceeding the 30-second
timeout, and its diagnostic accuracy was 100% regardless — an unplanned but direct demonstration
that the deterministic conclusion does not depend on the model (§6.4).

The two hallucination rows measure different things, and the difference is the finding. The
conventional definition — citing a metric that does not exist in the evidence — returns 0% for the
baseline, and that 0% is a real measurement rather than a broken detector (8 genuine metric
citations were detected across the 10 reasoning texts). But on `inc-0038` the baseline justified
"memory leak" by citing `jvm_memory_committed_bytes = 86.4 MB` as evidence of a heap increase, when
86.4 MB sits inside payment-service's documented 70–90 MB committed baseline. The metric was real;
its stated significance was invented. A second measure comparing each cited value against its
documented baseline range reports **10.0%** where the first reports 0%.

Worth stating plainly, because it cuts against a convenient reading: **that diagnosis was
correct.** `inc-0038` genuinely was a payment-service memory leak, and the baseline named it. The
unsupported claim was in the justification, not the conclusion. This is the more troubling case for
an operator, not the less — a right answer supported by a reason that does not hold is
indistinguishable, at review time, from a right answer that is actually grounded.

Both figures remain lower bounds, since most responses cite no numeric value at all. See §6.4.

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
48 answers twice, including the same four wrong ones.

What did vary is the part that does not affect the conclusion, and it varied more than a count
would show. The multi-agent arm's LLM narration succeeded on exactly one incident in each run — but
**not the same incident**: `inc-0010` in the first run, `inc-0065` in the second, with everything
else timing out. And on the single-agent arm, **all ten reasoning texts differed textually between
the runs** while all ten of its extracted conclusions were identical.

So the prose was different on every incident that produced any, in both arms, and not one of the 96
diagnoses moved. That is stronger evidence for the architectural claim than a stable count would
have been: the narrative layer demonstrably varies run to run, and the deterministic conclusion
demonstrably does not depend on it (§3.5). For the baseline it also isolates where the LLM's
non-determinism does and does not reach — it rewrote its justification every time and still
extracted the same service name, including the same four wrong ones.

**Reproduce it:** run `python -m ai_orchestration.evaluation.run_evaluation --out <dir>` twice and
diff `per_incident_results.csv` on `predicted_root_cause_service`. The second run's rows and metrics
are committed at `docs/evaluation/reproducibility/` so the comparison can be checked without
re-running anything.

This should be read narrowly. It establishes that this model, at this temperature, on these prompts,
is stable across two runs; it does not establish that single-agent diagnosis is stable in general,
and two runs cannot characterise a distribution.

## 5.7 Human-in-the-loop

| Metric | Multi-agent | Single-agent |
|---|---|---|
| Incidents reaching human approval | 10 | 10 |
| Approved / Rejected | 10 / 0 | 10 / 0 |
| Approval accuracy | 100% | 60.0% |
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
- The multi-agent design cost ~2× LLM calls and ~4.0× wall clock.
- The system's real-world ceiling in this run was set by **detection** (20.8%), not diagnosis.
- The absolute accuracy figures are bounded by a synthetic-corpus circularity documented in §6.2;
  the comparative findings are not.
