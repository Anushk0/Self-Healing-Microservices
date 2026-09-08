# Discussion

> Written from the Week 12 evaluation run in `docs/evaluation/`. Every figure cited here appears in
> `metrics.json` or `per_incident_results.csv`.

## 6.1 The result, and the mechanism behind it

The headline comparison is stark and, unusually, has a single legible cause.

| | Multi-agent | Single-agent baseline |
|---|---|---|
| Accuracy when it answered | **100%** (10/10) | 60.0% (6/10) |
| **Cross-service accuracy** | **100%** (4/4) | **0%** (0/4) |
| Same-service accuracy | 100% (12/12) | 100% (12/12) |
| Blamed the symptom service on cross-service faults | 0 | **4 of 4** |

The two arms are **identical on same-service faults** and **maximally divergent on cross-service
ones**. The entire performance gap is attributable to the cases where the service reporting the
symptom is not the service at fault — which is exactly the capability the dataset was constructed to
isolate, and it did so cleanly.

The mechanism is not a matter of inference. Breaking the multi-agent results down by how the
conclusion was reached:

| Determination method | Incidents | Correct | Incident class |
|---|---|---|---|
| `metric_signature+rag_agreement` | 5 | 5 | all same-service |
| `rag_only` | 1 | 1 | same-service (no signature matched) |
| `metric_signature+rag_disagreement` | 4 | 4 | **all cross-service** |

The partition is perfect. On every cross-service incident, **retrieval disagreed with the correct
answer**, and inspection of the retrieved neighbours shows what it proposed instead: for the
`auth-key-error` incidents, retrieval returned `memory-leak` incidents whose root cause was
`payment-service` — the symptom service. Retrieval was not merely unhelpful; it pointed confidently
at the wrong service, because it was matching on the surface features of payment-service telemetry.

The single-agent baseline received **the same retrieved incidents** and followed them: on all four
cross-service incidents it answered, it predicted `payment-service`. Not a scatter of errors — the
same wrong answer, four times out of four, and the same wrong answer retrieval suggested.

This is not an inference from aggregate statistics. **On 4 of 4 cross-service failures, the model's
own reasoning explicitly cites the retrieved incidents as its justification.** Verbatim, from
`raw_run.json` (all three are `auth-key-error` incidents whose true root cause is `auth-service`):

> *"Historical past incidents (inc-0044, inc-0042, inc-0037) report the same root cause of a
> memory-leak fault in the payment-service, which is consistent with the symptom of a JVM heap usage
> increase."* — `inc-0005`

> *"Past incidents (inc-0044, inc-0042, and inc-0037) also indicate a memory-leak fault type in the
> payment-service, with similar symptoms."* — `inc-0002`

> *"Historical incident records (inc-0042, inc-0044, and inc-0037) show that the root cause of these
> similar incidents was a memory-leak in the payment-service, which retained byte[] chunks without
> releasing them."* — `inc-0015`

The model is not reasoning poorly from the telemetry; it is reasoning *correctly from the retrieved
precedent*, which happens to be wrong. Note also that it cites the same three Memory incidents every
time — retrieval returned the same neighbours repeatedly, so a single retrieval weakness propagated
into every cross-service diagnosis.

**A compounding failure, and evidence that the reported hallucination rate understates the problem.**
Two of those responses justify "memory leak" by citing specific heap values —
`jvm_memory_used_bytes: 44984562.9` (45.0 MB) and `42760427.6` (42.8 MB). Payment-service's
documented baseline range is **30–45 MB**. Both values are *within normal*. The model constructed an
anomaly narrative around unremarkable readings in order to fit the retrieved precedent. That is a
genuine hallucination, and the automated proxy did not catch it — because the proxy checks whether a
cited metric *exists in the evidence*, and these metrics did exist; what was fabricated was their
*significance*. See §6.4.

**So the design decision that "retrieved incidents are evidence, not commands" is not a stylistic
preference in this system — it is the entire measured difference between the two arms.** Had the
multi-agent pipeline been built the conventional way, letting retrieval inform or override the
conclusion, it would have been led to `payment-service` on precisely the cases that matter and would
have scored 0% cross-service, identical to the baseline. The deterministic layer's refusal to defer
is what produced 4/4.

This also reframes what "RAG improves RCA" means, and the ablations in §6.9 sharpen the reframing
further than this section originally claimed. For the multi-agent pipeline retrieval turned out to be
**diagnostically inert** — removing it changed none of the 48 diagnoses — so the advantage came
entirely from the refusal to defer, not from anything retrieval supplied. For the single-agent
baseline retrieval was simultaneously *load-bearing* (it collapses to 0% same-service accuracy
without it) and *harmful* (cross-service accuracy improves from 0% to 33% when it is removed). A
system architected to trust retrieval inherits its blind spots on exactly the incidents where
diagnosis is non-trivial.

## 6.2 What the 100% does and does not mean

The multi-agent arm answered 10 incidents and got 10 right. That number must be qualified, and the
qualification is more important than the number.

**The absolute figure is inflated by a circularity between the dataset and the diagnostic
thresholds.** The evaluation corpus for this run is entirely synthetic (§4.5). The synthetic
generator's fault-phase formulas and the RCA signature thresholds are both derived from the same
`OBSERVED_BASELINE_RANGES` constants: the memory-leak threshold is 1.3× the observed baseline upper
bound, and the generator produces fault-window values above that bound; the db-lock and
auth-key-error thresholds sit just above an empirically-verified baseline of zero, and the generator
emits non-zero values by construction. A threshold matcher evaluated on data generated to exceed
those thresholds will match. **100% here measures agreement between a generator and a matcher, not
diagnostic capability on live faults.**

Three things survive that caveat intact, and they are what the paper should claim:

1. **The comparison is unaffected.** Both arms received byte-identical inputs. The synthetic data was
   equally favourable to both, and the baseline still scored 0% on cross-service faults. Favourable
   inputs do not explain why one arm exploited them and the other did not.
2. **The baseline's failure is not an artefact.** An LLM following misleading retrieval to the
   symptom service is a real behaviour, reproduced four times out of four. Nothing about synthetic
   generation caused it.
3. **The mechanism (§6.1) is structural.** The agreement/disagreement partition would hold on real
   incidents too, because it follows from what retrieval matches on, not from how the data was made.

The honest claim is therefore: *under conditions favourable to signature matching, a
deterministic-first pipeline separates root cause from symptom on cross-service faults where a
single-agent LLM given identical evidence does not.* Establishing the absolute accuracy of the
deterministic layer requires the live-fault corpus (§6.6).

A second, narrower disclosure, also made in §5.2: once the fault type is identified,
`root_cause_service` follows by a fixed domain mapping, and the ground-truth labels use that same
mapping. Root-cause accuracy is therefore arithmetically equal to fault-type accuracy for this
system — one capability, reported two ways, not two capabilities.

## 6.3 Detection, not diagnosis, is the binding constraint

Both arms abstained on 79.2% of incidents — **identically**, because abstention is decided upstream
of the diagnostic step they differ in. Only 10 of 48 incidents produced any diagnosis at all.

Detection rate by fault type: `memory-leak` 4/12, `auth-key-error` 2/12, `db-lock` 2/12,
`notification-latency` 2/12.

This is the Isolation Forest saturation limitation (§3.3) showing up as a system-level outcome
rather than a footnote. A pipeline whose diagnosis is perfect on what it sees still resolves fewer
than half its incidents if the detector does not surface them. Measured over *all* incidents rather
than answered ones, the multi-agent arm's accuracy is 20.8% — and that figure, not the 100%, is the
one that describes the system as deployed.

The practical implication is a re-prioritisation. The diagnostic layer is not the bottleneck and
does not need more sophistication; the detection layer is, and needs a different approach — feature
normalisation so out-of-range values stay informative, deviation-from-baseline features rather than
raw values, or a rule-based pre-filter running alongside the model. That is a more useful conclusion
than an incremental accuracy improvement would have been.

## 6.4 The local model underperformed, and the architecture absorbed it

This is where the Claude→Ollama substitution shows its cost, and it should be stated plainly rather
than minimised.

**The multi-agent arm's LLM narration failed on 9 of 10 incidents** — `llama3.2:3b` exceeded the
30-second timeout on nearly every RCA narrative request. Only three incidents received the
LLM-authored reasoning chain the design intends.

And the diagnostic accuracy was 100% regardless.

Across two independent runs of the full evaluation, that narration count varied (1 of 21, then 3 of
21) while **every one of the 96 diagnoses was identical** (§5.6). The component that failed
inconsistently is precisely the component that changes nothing — which is what "the LLM narrates a
conclusion it cannot alter" means operationally, demonstrated rather than asserted.

That is the "deterministic conclusion, LLM narration" split (§3.5) working exactly as designed,
demonstrated by accident rather than by construction: the model that was supposed to explain the
conclusion was unavailable for 95% of incidents, and not one conclusion changed. A system that had
delegated diagnosis to that model would have produced nothing for those incidents.

Two consequences follow honestly:

- **The hallucination rate reported for the multi-agent arm (0%) is meaningless**, computed over the
  single incident that produced reasoning text.
- **The single-agent figure (0% over 10 incidents) is measurable but understates the problem, and
  the metric's definition is the reason.** The proxy was validated rather than assumed: across the
  21 reasoning texts it detected 11 metric-name citations, all of which were genuinely present in
  the evidence — so 0% is a real measurement, not an artefact of a detector that finds nothing.
  But §6.1 shows the model twice citing *real* heap values (45.0 MB, 42.8 MB) that sit inside
  payment-service's documented 30–45 MB baseline and describing them as a "JVM heap usage increase"
  indicating a memory leak. The metric was real; its stated significance was invented. **The proxy
  detects fabricated metric _names_, not fabricated metric _interpretations_**, and the latter is
  the more consequential failure because it is what produced the wrong diagnosis.
- **So a second measure was implemented and applied**
  (`metrics.unsupported_significance_claims`): it compares each cited value against its documented
  baseline range and flags claims of elevation the value does not support. Deliberately
  conservative — only metrics with a documented range are checkable, only unambiguous elevation
  language counts, and a value is flagged only if it sits at or below the upper bound of normal.

  | Measure | Multi-agent | Single-agent |
  |---|---|---|
  | Cited a metric absent from the evidence | 0% | 0% (over 21) |
  | **Claimed a baseline-range value was elevated** | 0% *(1 sample)* | **10.0%** (2 of 21) |

  The stronger measure finds a non-zero rate where the conventional one reports zero. Both figures
  remain lower bounds: only 11 metric citations appear across 21 reasoning texts, so most responses
  offer no numeric claim to check at all. The lesson generalises beyond this project — a
  hallucination metric defined as "cited something that does not exist" will report near-zero for a
  model whose actual failure is misrepresenting things that do exist.
- **The narration quality of a small local model is not demonstrated by this run.** What is
  demonstrated is that the pipeline does not depend on it.

## 6.5 Cost of the multi-agent design

| | Multi-agent | Single-agent |
|---|---|---|
| LLM calls per incident (mean) | 0.4167 | 0.2083 |
| Wall clock per incident (mean) | 8.5 s | 2.12 s |

The multi-agent arm makes twice the LLM calls and takes roughly 4.0× the wall clock. Almost all of
that overhead is the narration step that mostly timed out — i.e. **the overhead was paid for output
that was not produced and was not needed for the result.** Making narration asynchronous, or
skipping it when a signature match is unambiguous, would remove most of the cost difference without
touching accuracy. Detection latency proper is negligible for both (~11 ms, identical to six
decimal places since they share the stage).

## 6.6 What this evaluation could not measure

Stated as prominently as the results, because a reader is entitled to know the shape of the gap:

- **Recovery Success Rate, False Recovery Rate and MTTR are not measured.** Replaying a historical
  incident leaves no active fault to remediate, so execution ran in dry-run and there was no
  remediation outcome to verify. Reporting these as 100% — which a naive harness would have — would
  have been fabrication. They require `--mode live` against a running cluster.
- **Infrastructure metrics are not collected**, for the same absence of a deployed monitoring stack.
- **Unsafe Action Prevention is 0 in this run**, and that is a consequence of dry-run rather than
  evidence of safety. With nothing executing, nothing failed, so no retry escalated to
  `restart_service`, so the ledger-service rule never had an opportunity to fire. The rule is
  demonstrated instead by the Week 10 live session and by regression tests.
- **Approval Accuracy is not an independent measurement** under the permissive operator policy
  (§3.8, `evaluation/operator.py`); it equals diagnostic accuracy by definition.
- **The corpus is synthetic** (§6.2).

## 6.7 Where the multi-agent design did *not* win

Worth stating, because a paper in which the proposed system wins on everything invites suspicion:

- **Same-service faults: no advantage whatsoever.** 100% for both arms. For the two faults whose
  symptom and cause coincide, the single LLM call was entirely sufficient, and the additional
  machinery bought nothing.
- **Detection: no advantage**, by construction — the stage is shared.
- **Cost: a clear loss.** ~4.0× wall clock, 2× LLM calls.
- **Abstention: no advantage.** Both arms declined equally often.

The multi-agent design's benefit is therefore narrow and specific rather than general: it is
confined to incidents where the causal service differs from the symptomatic one. That is a real and
important class — it is arguably the class where automated RCA has the most to offer, since it is
also the class humans find hardest — but it is not "multi-agent is better at incident response".

## 6.8 Threats to validity

- **Synthetic corpus and threshold circularity** (§6.2) — the most serious threat, bounding the
  absolute accuracy claims but not the comparative ones.
- **Small evaluation set, and smaller than it looks.** 48 incidents, of which only **10** produced a
  diagnosis at all and only **4** of those were cross-service. The 4/4 versus 0/4 split is perfectly
  separated, but four incidents is a very thin basis for a proportion: an exact binomial 95%
  interval on 4/4 is approximately [40%, 100%] and on 0/4 approximately [0%, 60%]. Those intervals
  do not overlap, so the *direction* of the effect survives; the magnitudes should not be quoted as
  precise. This is the weakest part of the evidence base and the strongest argument for the live
  re-run in §6.10.
- **One model, one configuration.** `llama3.2:3b` at a 30-second timeout. A larger model, or a
  longer timeout, might have let the baseline reason past the misleading retrieval — that is an
  ablation worth running, and it is the single most likely challenge to §6.1.
- **The permissive operator** removes human judgement as a variable; a real operator might have
  rejected the baseline's payment-service proposals on inspection.
- **Detection and diagnosis are entangled in the headline number.** Accuracy over all incidents
  (20.8%) reflects a weak detector as much as a strong diagnostician.

## 6.9 What the ablations established

Full detail in `10-ablations.md`; the two findings that change claims in this Discussion:

**Retrieval contributes zero diagnostic accuracy to the multi-agent pipeline.** Running the full
evaluation with retrieval disabled produced *identical* results on every performance metric — 20.8%
overall, 100% when answered, 100% cross-service, F1 1.00. Not one of 48 diagnoses changed. Retrieval
affects only the confidence label and the recorded determination method.

This is unflattering and belongs in the paper anyway. §6.1 argued that the *constraint on* retrieval
was the source of the multi-agent advantage; the ablation sharpens that to something stronger and
more awkward: **retrieval itself was not contributing, so what mattered was entirely the refusal to
defer to it.** Any claim that RAG improves diagnosis in this system is unsupported. Its defensible
roles here are confidence calibration, operator-facing provenance, and a fallback for incidents no
signature matches.

**For the single-agent baseline, retrieval was load-bearing and harmful at once.** Removing it
collapsed same-service accuracy from 100% to 0% while *improving* cross-service accuracy from 0% to
33.3%, and symptom-service blaming fell from 4 of 4 to 2. The reading is that retrieval substitutes
for reasoning: the model pattern-matches to precedent, which is right on same-service faults and
wrong on cross-service ones. Remove precedent and it cannot diagnose at all, but it also stops being
systematically dragged to the symptom service.

The same intervention leaving one arm unchanged and reorganising the other is itself evidence for the
architectural claim — advisory in one design, causal in the other.

**Ablation B closes the argument.** Letting retrieval override a signature match — i.e. rebuilding
the conventional RAG-first design — collapsed cross-service accuracy from 100% to **0%** and
reproduced the single-agent baseline's numbers *exactly*: 12.5% overall, 60.0% when answered, F1
0.4167, and the same 4 symptom-service attributions. Every metric identical to four decimal places.
The determination-method breakdown confirms the mechanism directly: 5 `rag_agreement` unchanged, and
**4 `rag_override`** — precisely the four cross-service incidents, each flipped to retrieval's answer.

Taken together the two ablations make the attribution in §6.1 a controlled result rather than an
interpretation of a correlation:

| | Cross-service accuracy |
|---|---|
| Shipped configuration | **100%** |
| Retrieval removed entirely (A) | **100%** — unchanged |
| Retrieval permitted to override (B) | **0%** — baseline reproduced |

The uncomfortable precision of this is worth stating: **the value is not in the retrieval, and not in
the multi-agent decomposition, but specifically in the refusal to let retrieval decide.** Under
ablation B the pipeline keeps its per-service detectors, its signature matching, its deterministic
proposer, its safety validation and its staged approval — and all of it is worth nothing. One `if`
branch accounts for the entire margin.

## 6.10 Recommended next experiments

In priority order, from what this run actually showed:

1. **Re-run against live faults** (`--mode live`). This is the single highest-value follow-up: it
   removes the circularity of §6.2 and fills in the three unmeasured system metrics at once.
2. ~~Ablate the RAG-override policy.~~ **Done** — see §6.9 and `10-ablations.md`.
3. **Vary the baseline's model.** Establish whether the baseline's cross-service failure is a
   capability limit of a 3B model or a property of single-agent framing. This is the strongest
   available objection to the headline result and should be pre-empted.
4. **Fix detection before improving diagnosis** (§6.3).
