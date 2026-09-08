# Ablation Experiments

> **Week 16, roadmap item 1** (the memory-retrieval ablation), plus a second ablation added because
> the Week 12 results attributed the entire multi-agent/single-agent margin to one architectural
> constraint, and an attribution of that weight should be tested rather than asserted.
>
> Both are implemented as config flags that default off, are recorded in every run's environment
> block, and are covered by tests asserting they are off in the shipped configuration
> (`tests/test_ablation_switches.py`). Artefacts: `docs/evaluation/ablations/`.

## 10.1 What was ablated, and why

| Ablation | Question | Switch |
|---|---|---|
| **A: no retrieval** | Does RAG contribute anything? | `RETRIEVAL_ENABLED=false` |
| **B: RAG override** | What would this system score if built the conventional way — retrieval permitted to override a signature match? | `RAG_MAY_OVERRIDE_SIGNATURE=true` |

Ablation B reconstructs the design this project argues against. It is never the shipped behaviour,
and any result carrying `determination_method="rag_override"` came from an ablation run.

## 10.2 Ablation A — removing retrieval entirely

| Metric | Multi-agent | Multi-agent *no RAG* | Single-agent | Single-agent *no RAG* |
|---|---|---|---|---|
| Accuracy (all incidents) | 43.8% | **43.8%** | 25.0% | **6.3%** |
| Accuracy (when answered) | 100% | **100%** | 57.1% | **15.8%** |
| **Cross-service accuracy** | 100% | **100%** | 0% | **33.3%** |
| **Same-service accuracy** | 100% | **100%** | 100% | **0%** |
| Blamed the symptom service | 0 | **0** | 9 | **2** |
| F1 (macro) | 1.00 | **1.00** | 0.40 | **0.09** |
| Incidents answered | 21 | 21 | 21 | 19 |

### Finding A1 — retrieval is diagnostically inert for the multi-agent pipeline

**Every performance metric is identical with and without retrieval.** Not similar — identical, to
four decimal places, on accuracy, cross-service accuracy, same-service accuracy and F1.

Retrieval changes only the `confidence` label (`high` vs `medium`) and the recorded
`determination_method`. It does not change a single diagnosis across 48 incidents.

This is worth stating plainly because it is unflattering to the architecture as advertised: **the
RAG component — the most fashionable part of the design — contributes nothing to diagnostic accuracy
here.** The signature matcher reaches the same conclusion alone. The honest characterisation of
retrieval's role in this system is *corroboration and provenance*, not diagnosis: it tells an
operator "three past incidents agree", which is useful for trust and for the audit trail, and it
tells the RCA whether to say `medium` or `high`. Those are real but modest benefits, and the paper
should not imply more.

A caveat in the other direction: retrieval cannot help a signature matcher that is already at 100%.
On a live corpus where signature matching is imperfect (§6.2), retrieval has room to contribute that
it does not have here — and the `rag_only` path exists precisely for incidents no signature matches.
This ablation shows retrieval is inert *under these conditions*, not that it is useless in principle.

### Finding A2 — for the single-agent baseline, retrieval is load-bearing *and* harmful

The baseline's numbers move dramatically, and in opposite directions:

- **Same-service accuracy collapses from 100% to 0%.**
- **Cross-service accuracy improves from 0% to 33.3%.**
- Symptom-service blaming falls from 9 of 9 to 2.
- Overall accuracy falls from 57.1% to 15.8% when answered.

The coherent reading is that **retrieval was substituting for reasoning.** With precedent available,
the model pattern-matches to it: that is exactly right on same-service faults (where the retrieved
incident's root cause *is* the symptom service) and exactly wrong on cross-service ones (where it
isn't). Remove the precedent and the model has no reliable basis at all — it loses the easy cases
entirely — but it also stops being systematically dragged toward the symptom service.

This is a more interesting result than either "RAG helps" or "RAG hurts". For an LLM-first design,
**retrieval determines the answer**, so retrieval's blind spots become the system's blind spots.
Net-positive on aggregate accuracy, net-negative precisely where diagnosis is hard.

### Finding A3 — the two arms are affected in completely different ways

The same intervention leaves one system exactly unchanged and reorganises the other. That asymmetry
is itself evidence for the architectural claim: in the multi-agent pipeline retrieval is *advisory*
by construction, so removing it changes confidence labels; in the single-agent baseline it is
*causal*, so removing it changes the answers.

## 10.3 Ablation B — letting retrieval override the signature match

The prediction registered before this run: because retrieval disagreed with the correct answer on all
nine cross-service incidents and proposed `payment-service` each time, allowing it to win those
disagreements should collapse cross-service accuracy from 100% toward 0%, while leaving the twelve
same-service incidents untouched (retrieval already agrees there, so there is nothing to override).

**The result matches the prediction exactly, and then goes further than it.**

| Metric | Multi-agent (shipped) | Multi-agent **+ override** | Single-agent baseline |
|---|---|---|---|
| Accuracy (all incidents) | 43.8% | **25.0%** | **25.0%** |
| Accuracy (when answered) | 100% | **57.1%** | **57.1%** |
| **Cross-service accuracy** | 100% | **0%** | **0%** |
| Same-service accuracy | 100% | 100% | 100% |
| Blamed the symptom service | 0 | **9** | **9** |
| F1 (macro) | 1.00 | **0.4022** | **0.4022** |
| Incidents answered | 21 | 21 | 21 |

### Finding B1 — removing the constraint reproduces the baseline *exactly*

Not approximately. **Every metric in the override column equals the single-agent baseline's value**,
to four decimal places, including the F1 macro (0.4022) and the count of symptom-service
attributions (9). The determination-method breakdown shows why: 12 `rag_agreement` (unchanged) and
**9 `rag_override`** — precisely the nine cross-service incidents, each flipped to retrieval's
answer.

The multi-agent pipeline retains, under this ablation, everything that distinguishes it: per-service
Isolation Forests, signature matching against documented thresholds, a separate deterministic
remediation proposer, safety validation, staged approval. **All of that apparatus is worth exactly
zero once retrieval is allowed to override the conclusion.** The system collapses to its own
baseline.

### Finding B2 — the attribution in §6.1 is now a controlled result

Discussion §6.1 argued from a correlation: the multi-agent arm's cross-service wins coincided with
`rag_disagreement`. Ablations A and B together close that argument from both ends:

- **A:** retrieval contributes zero accuracy — remove it and nothing changes (100% → 100%).
- **B:** the *constraint on* retrieval contributes everything — remove it and the margin vanishes
  entirely (100% → 0%).

The conjunction is what makes the claim precise, and it is a slightly uncomfortable formulation:
**the value is not in the retrieval, and not in the multi-agent decomposition, but specifically in
the refusal to let retrieval decide.** A single architectural constraint — cheap, requiring no model
capability, expressible in one `if` branch — accounts for the entire measured difference between a
system that is right on every cross-service incident and one that is wrong on all of them.

### Caveats on B

- The 9/9-to-0/9 flip is clean but rests on nine incidents; §6.8's binomial caution applies equally
  here.
- The override policy implemented is majority-vote-by-`fault_type` among retrieved neighbours. A
  more sophisticated fusion (distance weighting, confidence-aware blending) might not lose all nine.
  What this ablation establishes is that the *naive* form of "let retrieval inform the conclusion" —
  the common one — is catastrophic on cross-service faults, not that every possible fusion is.
- Both ablations inherit the synthetic-corpus circularity of §6.2. The mechanism should transfer,
  because it follows from what retrieval matches on, but the magnitudes should not be assumed to.

## 10.4 What the ablations change in the paper's claims

1. **Strengthened, and promoted from inference to controlled result.** The claim that the
   *constraint* rather than the *retrieval* produces the multi-agent arm's cross-service accuracy is
   supported from both directions: removing retrieval changes nothing (A1: 100% → 100%), and letting
   it override changes everything (B1: 100% → 0%, reproducing the baseline exactly).
2. **Weakened, and this should be conceded rather than buried.** Any suggestion that RAG improves
   diagnosis in this system is not supported. It contributed zero accuracy across 48 incidents. The
   defensible claims for retrieval here are confidence calibration, operator-facing provenance, and
   a fallback path for unmatched incidents.
3. **Reframed.** "Retrieved incidents are evidence, not commands" reads like a safety property. A1
   and A2 together suggest it is better understood as a *capability* property: it is what stops
   retrieval's failure modes from becoming the system's failure modes, which is exactly what happens
   to the baseline.

## 10.5 Ablations not run, and why

- **A fifth service / Istio traffic shifting** (roadmap Week 16 item 2). Not attempted. It requires
  a live cluster, which this machine does not have, and — more importantly — the evidence points
  elsewhere: detection surfaced only 43.8% of incidents (§6.3), so adding services would add scope
  without addressing the measured bottleneck. Recorded as a decision, not an omission.
- **Varying the baseline's model.** The most valuable remaining experiment (§6.10 item 3) and the
  strongest objection to the headline result. Not run here because it needs a larger model than this
  machine can serve at acceptable latency — `llama3.2:3b` already timed out on 18 of 21 narration
  requests at 30 s.
