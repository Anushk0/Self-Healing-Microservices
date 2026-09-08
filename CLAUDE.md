# AIOps Self-Healing Microservices Platform — Project Rules

Academic capstone project. This file records the **permanent, standing rules**
for how work on this repository must be done. These rules apply across every
session and every week of the project unless the user explicitly changes them.

## What this project is

An AIOps platform that monitors a microservices payment application, detects
anomalies, performs RAG-augmented root cause analysis with a local LLM,
proposes remediation, validates it with deterministic safety rules, requires
mandatory human approval, executes approved actions against Kubernetes, and
records outcomes for future incident retrieval. A single-agent LLM baseline
is built separately for comparison, and both are evaluated on a held-out
incident set.

## Intended final structure

```
/
├── auth-service/
├── payment-service/
├── ledger-service/
├── notification-service/
├── telemetry/        (added Week 5 - automated telemetry capture)
├── ai-orchestration/
├── dataset-tools/
├── docs/             (added Week 10 - Postman collection; the Weeks 1-2 deliverable)
├── k8s/
├── docker-compose.yml
├── README.md
└── CLAUDE.md
```

Not all components exist yet — they are built incrementally per the weekly
roadmap below.

## Weekly roadmap

1. Auth + Payment services, JWT, Correlation IDs
2. Ledger + Notification, PostgreSQL, full synchronous chain
3. Docker, Docker Compose, Kubernetes, Minikube
4. Actuator, Prometheus, fault injection
5. Automated telemetry capture
6. Incident dataset, Memory/Evaluation split, ChromaDB
7. LangGraph, Isolation Forest, Ollama
8. RCA Agent, RAG, Remediation Agent
9. Safety Validator, mandatory Human Approval, Streamlit
10. Kubernetes execution, feedback write-back, full pipeline
11. Single-Agent baseline
12. Full evaluation
13–15. Paper, results, documentation, demo

## Permanent development rules

1. Implement incrementally.
2. Never implement future weeks unless explicitly requested.
3. Inspect existing code before modifying it.
4. Preserve working functionality.
5. Do not unnecessarily rewrite the architecture.
6. Do not hardcode secrets in source code.
7. Use configuration properties and environment variables.
8. Keep services independently deployable.
9. Use clean, understandable code.
10. Prefer simple solutions over unnecessary abstraction.
11. Add meaningful logging.
12. Do not expose raw stack traces through APIs.
13. Build after significant changes.
14. Run tests when available.
15. Fix errors caused by your changes.
16. Never claim something was tested if it was not actually tested.
17. Clearly distinguish verified behavior from unverified instructions.
18. Fault injection must be disabled by default.
19. Fault injection must be resettable or recoverable.
20. Safety validation must remain deterministic.
21. Human approval must remain mandatory.
22. Never allow Evaluation Set incidents into RAG memory.
23. Maintain reproducibility for academic evaluation.
24. Do not silently change project requirements.
25. Do not automatically push code to GitHub.
26. Do not implement anything outside the currently requested week.

## Correlation ID convention

Every microservice supports `X-Correlation-Id`:

1. Read `X-Correlation-Id` from incoming requests.
2. Generate a UUID if missing.
3. Store it in SLF4J MDC under the key `correlationId`.
4. Include it in logs.
5. Return it in the HTTP response.
6. Propagate it to downstream service calls.
7. Remove it from MDC in a `finally` block.

## Fault injection (implemented from Week 4 onward)

Four controlled faults, each disabled by default and resettable:

1. **Auth key error** — Auth Service JWT key intentionally changed → JWT
   validation fails.
2. **Memory leak** — Payment Service intentionally retains memory → JVM
   memory usage increases, may eventually crash.
3. **DB connection pool exhaustion** — Ledger Service deliberately holds
   DB connections (HikariCP `maximum-pool-size: 10`) → new transactions
   eventually fail/time out.
4. **Notification latency** — Notification Service adds ~6s delay →
   Payment Service (which waits synchronously) experiences latency/timeout.
   Symptom appears in Payment Service; actual root cause is Notification
   Service — the RCA system must eventually identify Notification as root
   cause.

## Dataset rule (critical)

Controlled fault injection generates ~60–80 incidents, split into:

- **Memory set** (~20 incidents) — used for RAG.
- **Evaluation set** (~40–60 incidents) — used only for evaluation.

**Evaluation incidents must never enter RAG memory.** This is a hard
constraint on every week that touches the dataset or ChromaDB.

## Local LLM

The project uses a self-hosted local LLM via Ollama (initial candidate:
`llama3.1:8b`). Do not design the system around OpenAI/Anthropic APIs.

## Current status

Weeks 15-16 complete (2026-09-08): the roadmap is finished except for two items that require a
person or a cluster, both named below.

**Week 15.** The paper now has an Abstract, Introduction and Conclusion
(`docs/paper/00-...`, `08-...`), including an explicit "What this paper does not claim" section. The
roadmap's actual Week 15 task - "check the novelty claim still matches what the Results show" - was
done as a claim-by-claim table rather than a read-through (`06-novelty-claim-audit.md`), because a
read-through is how an unsupported claim survives. **13 claims held, 2 were weakened, 1 was
removed**, and the edits were applied rather than merely noted: "makes false recovery measurable"
implied a measurement when `executions_attempted` is 0 (it is a capability, not a result in this
run); "an LLM narrates the conclusion" implied a working narration layer when it succeeded on 3 of
21; and unsafe-action prevention was removed from the contributions entirely because the count is
**0** - dry-run execution never failed, so no retry escalated to the action the ledger-service rule
guards, and reporting it as demonstrated would be exactly what that audit exists to catch.

A defence runbook (`07-demo-runbook.md`) gives five beats with timings, anticipated questions mapped
to artefacts, and a failure plan. One correction to the roadmap: it says to rehearse "using your
Week 10 recording" - **no recording exists**; Week 10 was verified in a browser and written up but
never captured. The steps to record one are in §3 and need a cluster.

**Mentor feedback cannot be done for you.** `09-mentor-review-packet.md` is the handover instead: a
15-minute read path and five decisions where a supervisor's judgement should override mine (should
the headline be 100% or 43.8%; submit with the synthetic-corpus caveat or hold for the live run; how
much weight the withdrawn-competitor finding carries; and so on).

**Week 16 - the ablations, which changed the paper's claims.** Two config-flag ablations, both off
by default, both recorded in every run's environment block, both covered by tests asserting they are
off in the shipped configuration.

*Ablation A (retrieval disabled)* produced the most consequential finding of the whole evaluation,
and it is unflattering: **retrieval contributes exactly zero diagnostic accuracy to the multi-agent
pipeline.** With RAG disabled, all 48 diagnoses were identical - 43.8% overall, 100% when answered,
100% cross-service, F1 1.00, to four decimal places. Retrieval moves only the confidence label. This
*sharpens* rather than undermines the Week 12 claim: the advantage came entirely from refusing to
defer to retrieval, not from anything retrieval supplied. Any suggestion that RAG improves diagnosis
here is unsupported and has been removed from the write-up.

For the single-agent baseline the same intervention was dramatic in both directions: same-service
accuracy collapsed 100% -> 0%, cross-service accuracy *improved* 0% -> 33.3%, and symptom-service
blaming fell from 9 of 9 to 2. Retrieval was substituting for reasoning - right on same-service
faults, systematically wrong on cross-service ones. That the same intervention leaves one arm
unchanged and reorganises the other is itself evidence for the architectural claim.

*Ablation B (retrieval permitted to override a signature match)* reconstructs the conventional
RAG-first design this project argues against, to test the §6.1 attribution directly. A real bug was
caught before it ran: `rag_override` was missing from the `DeterminationMethod` literal, which would
have errored every incident.

**Lit review (Week 16 item 3):** all four named papers located and verified. Flow-of-Action
(2502.08224, WWW '25) reaches this project's core insight by a different route - constraining the LLM
at crucial junctures via SOPs where this uses thresholds; written up as a convergence, not a
contrast. LATS-RCA (2605.03505) supplies a hard cost comparison (~75 API calls and 13 min per
incident vs 0.875 calls and 17.6 s here) and independently corroborates the benchmark-vs-production
caution (91.3% -> 60-70%). GALA (2508.12472 / 2608.08968) prompted a conceded limitation: a
dependency graph could *derive* the symptom-to-cause relation that `FAULT_SERVICE_MAP` hardcodes.

**Week 16 item 2 (fifth service / Istio) was deliberately not attempted** - it needs a cluster, and
the evidence points elsewhere: detection surfaced only 43.8% of incidents, so adding services would
add scope without addressing the measured bottleneck. Recorded as a decision in
`10-ablations.md` §10.5, not silently skipped.

**Still outstanding, and neither is something I can close:** the three dataset-methodology citations
(RCAEval, Cloud-OpsBench, MicroServo) are left as marked slots because I have not read them and will
not invent summaries; and "VeriOps-Agent" could not be located at all. `docs/paper/README.md` lists
all seven pre-submission items.

Weeks 12-14 complete (2026-09-08): the evaluation ran, and the paper sections are drafted from it.

**Week 12.** `ai-orchestration/src/ai_orchestration/evaluation/` runs every Evaluation incident
through both arms with byte-identical inputs and computes all five metric categories. Three design
choices carry the methodological weight. The replay adapter feeds the pipeline the *symptom*
service's fault-window telemetry and **withholds the self-reported fault flag** - `/fault-status` is
effectively an answer key for same-service faults, so a replayed incident does not come with one.
The simulated operator **approves everything that reaches it**, deliberately: gating execution on
ground truth would make recovery metrics measure "how well does remediation work when the diagnosis
is already known to be right". And before any number is recorded the harness proves the rule-22
guard **live** against the real Evaluation file, aborting the run unless a write-back of a genuine
Evaluation incident is refused. Metrics refuse to fabricate: an unmeasured quantity is `None` with a
reason, never a number, and an abstention is scored separately from a wrong answer.

**The result.** On 48 held-out incidents: multi-agent **100% accuracy when it answered (21/21)**
versus 57.1% for the single-agent baseline, and on the cross-service faults the project was built
around, **100% (9/9) versus 0% (0/9)**. The two arms were *identical* on same-service faults, so the
entire difference is confined to incidents where the symptom service is not the faulty one.

**The mechanism, which matters more than the number.** Breaking the multi-agent results down by
determination method gives a perfect partition: all 12 same-service incidents were
`metric_signature+rag_agreement`, and all 9 cross-service incidents were
`metric_signature+rag_disagreement` - and all 9 were still correct. On every cross-service incident
retrieval *disagreed with the right answer*, returning `memory-leak` incidents rooted at
`payment-service` (the symptom service). The baseline received the same retrieved incidents and
followed them, naming `payment-service` on all 9. So the design decision that "retrieved incidents
are evidence, not commands" is not stylistic - it is the entire measured difference between the
arms. Built the conventional way, the multi-agent arm would have scored 0% cross-service too.

**Caveats recorded as prominently as the results.** The absolute 100% is inflated by a circularity:
this run's corpus is fully synthetic (no cluster on this machine), and the synthetic generator's
fault formulas and the RCA signature thresholds both derive from the same `OBSERVED_BASELINE_RANGES`
constants, so a threshold matcher was evaluated on data generated to exceed those thresholds. The
*comparison* is unaffected (both arms got identical inputs) and the baseline's failure is not an
artefact, but the absolute figure is not a capability claim. Detection - not diagnosis - was the
binding constraint: both arms abstained on 56.2% of incidents because the shared Isolation Forest
surfaced only 21 of 48, which is the saturation limitation from Week 10 showing up as a system
outcome. MTTR, Recovery Success Rate and False Recovery Rate are **not measurable by replay** and
are reported as such. And the multi-agent arm's LLM narration timed out on 20 of 21 incidents while
diagnostic accuracy stayed at 100% - an unplanned but direct demonstration that the deterministic
conclusion does not depend on the model.

**A metric bug was found and fixed mid-week**: "detection latency" was measuring time-to-first-pause
(including retrieval, RCA and LLM narration), overstating it by orders of magnitude. Detection is
now timed on the detection path alone, outside the graph, and time-to-first-pause is reported
separately.

**Weeks 13-14.** `docs/paper/` contains Dataset Construction, Methodology, Results, Discussion,
Related Work and five Mermaid figures, plus `docs/evaluation/` (RESULTS.md, metrics.json,
per-incident CSV, raw run, three PNG charts).

**A finding that changes the Related Work section.** arXiv 2511.15755 (Drammeh), which this roadmap
named as the closest direct competitor whose differentiation "cannot be skipped", has been
**withdrawn by its author** - verified on the arXiv abstract page. The withdrawal notice states that
"a code audit found the multi-agent arm's action list is a source constant, not model output, and
the scorer reads only that field." That is the natural failure mode of a deliberately-deterministic
multi-agent pipeline, which this project is - `remediation/proposer.py` *is* a hardcoded template
lookup. So §5.2 of the related-work section carries an empirical self-audit showing the scored
quantity varies with input telemetry (three payment-service records differing only in metrics yield
three different root causes), backed by a permanent regression test
(`tests/test_scored_output_is_not_constant.py`). A second-order disclosure is made there too: once
the fault type is known, `root_cause_service` follows from a fixed map that the ground-truth labels
also use, so root-cause accuracy is arithmetically equal to fault-type accuracy - one capability
reported two ways. Separately, **"VeriOps-Agent" could not be located**; two substitute papers were
found and are flagged for reading before citation.

67 new tests (283 across the three Python packages, 0 failures). Docker Desktop would not start on
this machine, so there is still no cluster: the dataset was regenerated here as 68 synthetic
incidents (20 Memory / 48 Evaluation, seed 42, zero overlap), detectors were trained from Memory-set
NORMAL/RECOVERY phase averages via `evaluation/prepare.py` (which never opens the Evaluation file),
and the evaluation ran in replay mode. Running `--mode live` on a machine with a cluster fills in
the three unmeasured metrics with no code change. See `docs/paper/README.md` for the five items
still needing your input before submission. Waiting for explicit instruction to begin Week 15.

Weeks 10-11 complete (2026-09-08): the pipeline now closes the loop and has a comparison arm.

**Week 10.** `execution/` is the only code in this project that changes the running system, and
every gate is re-checked immediately before acting rather than trusted from graph routing:
`human_decision` must be exactly `"approved"`, `safety_check.passed` must be True (a human
approval can never override a safety rejection), and the incident must not have exhausted
`MAX_REMEDIATION_ATTEMPTS`. It can only ever do one of three things - POST a service's own fault
reset (path read from Week 5's `telemetry.experiment.FAULTS`, so it cannot drift from what the
Java services expose), delete a pod so its Deployment recreates it, or nothing - there is no
generic command path, and the Kubernetes client refuses any service outside this project's four.
`execution/verify.py` then re-observes the target's `/fault-status` and `/actuator/health` instead
of trusting the remediation call's own 200, which is what makes Week 12's Recovery Success Rate
measurable and makes False Recovery (`fault_still_active`) detectable at all. The failed-fix retry
loop the Week 8 roadmap specified but could not build is now wired: it routes back to
`propose_remediation` (not RCA), the proposer walks a deterministic ladder (targeted reset ->
restart_service -> human escalation) that never re-proposes a category that already failed, a
false recovery triggers a retry exactly like an outright failure, and **every retry re-enters the
Safety Validator and raises a fresh interrupt - a new action never inherits the approval given for
a different one**. This also made Week 9's ledger-service rule reachable in the real pipeline for
the first time: a db-lock incident whose reset fails escalates to "restart ledger-service", which
is high-risk on ledger-service, and is refused before any human is asked. `feedback/` writes the
finished run back into ChromaDB in Week 6's postmortem prose format - honestly, including failures
and rejections rather than rewriting them as successes. CLAUDE.md rule 22 is enforced twice
independently (the writer refuses Evaluation incidents before building anything; `chroma_store`
refuses again by source ID, refuses any non-`run-`-prefixed ID, and **fails closed when the
Evaluation file cannot be read**, since `read_jsonl` returns `[]` for a missing file and would
otherwise turn "cannot check" into "nothing to check"). MTTR is measured from detection to
execution completion, with a negative interval dropped rather than recorded.

**Week 11.** `baseline/` is a genuinely separate single-agent implementation - one prompt, one LLM
call producing root cause and action together, importing nothing from `rca/`, `remediation/`, or
`llm/interpret.py`. Everything that is not the diagnostic step is shared by importing the
*identical node objects*, so the isolated variable is agent decomposition rather than retrieval or
downstream safety; a test asserts node identity so the arms cannot drift apart later. Its failure
modes are recorded rather than repaired (a hallucinated service or invented action becomes a
countable `parse_issues` entry, and an invented action is coerced to something non-executable), it
is subject to identical downstream rules, it deliberately has no deterministic fallback when Ollama
is unavailable, and it does not retry - so Week 12 must compare one attempt per incident.

**Gaps closed from earlier weeks:** the Postman collection specified in Weeks 1-2 but never created
(now at `docs/postman/`, 31 requests covering the happy path, error paths, Actuator and all four
faults), and the per-service Kubernetes/run READMEs specified in Week 3 item 7 (now present for all
four Java services). `app.py` was also brought up to date - approving now genuinely executes, so
the page says so explicitly, labels retries as retries, and shows execution, recovery verification,
MTTR and feedback outcomes; it also gained a multi-agent/baseline arm selector.

**A real finding, measured not assumed:** Isolation Forest **saturates** outside its training
range. A swept measurement showed a 2 GB heap scoring identically to a 50 MB one (-0.0279 for every
value above the training range), because every learned split threshold for that feature lies inside
the observed range, so any value beyond it takes the same branch everywhere. This is a property of
the algorithm plus this feature schema, so it affects the real per-service detectors too - it is a
concrete mechanism for Week 7's empirically-documented "detection is inconsistent" limitation, and
it sharpens it: extreme deviations are as invisible as moderate ones, for the same reason. It was
characterised rather than tuned away; the response was to surface the already-documented,
intentionally-configurable `ANOMALY_SCORE_THRESHOLD` knob in the console with the raw score always
displayed. Important mitigation already in place: Week 8's deterministic signature matching does
not share this weakness, so the *diagnosis* stays correct wherever detection fires - the
deterministic layer compensating for the ML layer's blind spot is a result in favour of the
multi-agent design and belongs in the paper's Discussion.

81 new tests (182 in ai-orchestration, 249 across all three Python packages, 0 failures). Live
verification again ran through a real `streamlit run app.py` browser session rather than a cluster
(no Minikube on this machine) - which made for a *stronger* Week 10 test than a healthy cluster
would have: with nothing listening on port 8082 and no cluster, every remediation genuinely failed,
so the entire escalation ladder ran under real failure conditions. One live memory-leak incident
produced three real execution attempts, three separate interrupt pauses and three separate Approve
clicks, ending at the terminal escalation which correctly executed nothing; MTTR 53.4s. The
feedback loop demonstrably closed - a later run retrieved incidents that earlier runs of the same
pipeline had learned - and `validate_no_leakage()` passes against the real on-disk collection.
Java tests were not re-run (no Maven on this machine). See `ai-orchestration/README.md`'s "Week 10
architecture", "Week 11 architecture", "Live verification performed" and "Known limitations" for
full detail. Waiting for explicit instruction to begin Week 12 (full evaluation run, all five
metric categories).

Week 9 complete (2026-09-02): `ai-orchestration/` adds a deterministic Safety Validator
(`safety/validator.py`, zero AI/LLM/network calls) and a genuinely mandatory human-approval
gate built on LangGraph's `interrupt()`/`Command(resume=...)` - the graph physically cannot
proceed past `human_approval` without an external resume signal, per CLAUDE.md rule 21, not
merely a documented convention. The Safety Validator runs first and independently re-checks
every field of a `RemediationProposal` (not trusting Pydantic's own validation alone): a known
whitelist of `action_category`/`target_service` values, and - implementing the roadmap's example
rule directly - a hard rejection of any `risk_level="high"` proposal targeting `ledger-service`,
since it holds financial transaction state behind Week 2's pessimistic-locking `@Transactional`
boundary. A rejected proposal routes straight to `finalize` and never reaches a human at all
(rule 20: "a rule violation shouldn't even reach the human"); a passed proposal pauses at
`human_approval` until `app.py` (a new Streamlit console) or a test resumes it with an
approve/reject decision, which is recorded verbatim in the final result and its `limitations`.
`build_graph()` now always compiles with a checkpointer (`MemorySaver` by default, swappable),
which meant every existing test call site needed a `thread_id` config - a real, mechanical
consequence of making approval actually non-bypassable rather than just typed as mandatory.
11 new tests pass (101 total in ai-orchestration, up from 90), including two new interrupt-cycle
tests and one safety-rejection test (the latter monkeypatches the proposer, since the real Week 8
templates never assign `risk_level="high"` today - a disclosed, not hidden, limitation). Fixing
these tests also surfaced a real latent bug: two Week 8 test mocks left `ollama_client.model`
unset, so a raw `MagicMock` silently flowed into checkpointed state - harmless before Week 9 (no
serialization happened), a genuine `TypeError` once `MemorySaver` started really serializing
state; fixed by setting the mock attributes explicitly. This machine has no live
Kubernetes/Minikube deployment (unlike the Weeks 5-8 verification environment), so this week's
live verification ran `streamlit run app.py` end-to-end in a real browser instead: trained a
synthetic, clearly-labeled, in-memory-only demo detector from the UI, ran a memory-leak sample
through to a real pause-and-Approve cycle (the typed approval reason round-tripped through
`Command(resume=...)` and appeared verbatim in the final result), and a separate db-lock sample
through a pause-and-Reject cycle. That db-lock run caught a real bug live: the demo detector's
synthetic training data initially generated `ledger-service`'s HikariCP fields as fixed constants
instead of jittered values, giving Isolation Forest a zero-variance feature it could never learn
to split on, so the detector failed to flag an obvious real deviation - fixed in `app.py` (not
the production package, which has always trained on naturally-varying real telemetry) and
re-verified live. The safety-rejection branch was not exercised live (same reason as the test
above) - disclosed explicitly rather than implied. See `ai-orchestration/README.md`'s "Week 9
architecture" and "Live verification performed" for full detail. Waiting for explicit instruction
to begin Week 10 (Kubernetes execution client, feedback write-back, full pipeline wiring).

Week 8 complete (2026-08-28): `ai-orchestration/` extends Week 7's
LangGraph workflow with an RCA agent, RAG retrieval, and a deterministic
remediation-proposal generator (`rca/`, `rag/`, `remediation/`
subpackages). Root cause is determined by deterministic fault-signature
matching FIRST (`rca/signatures.py`, thresholds traceable to Week 6's
own documented values, e.g. `OBSERVED_BASELINE_RANGES`), correctly
distinguishing the symptom-visible service from the actual root-cause
service for the two cross-service faults (auth-key-error,
notification-latency) by reusing `dataset_tools.config.FAULT_SERVICE_MAP`
rather than re-deriving it. RAG-retrieved incidents (from Week 6's
existing `incident_memory` ChromaDB collection, reused directly, not
duplicated) can only raise or lower confidence in that deterministic
conclusion, never override it - "historical incidents are evidence, not
commands." An optional local-LLM narrative (same `llama3.2:1b`) explains
the already-reached conclusion but cannot change it; its prompt has no
"recommendation" field at all, because remediation proposals are 100%
deterministic Python (`remediation/proposer.py`, no LLM call whatsoever)
- consistent with "prefer deterministic structured data over free-form
AI output." Every `RemediationProposal.requires_human_approval` is
`Literal[True]`, a type-level guarantee per CLAUDE.md rule 21, and
nothing in the module can execute anything (verified: it exposes no
`execute`/`run`/`apply`-named attribute). 46 new tests pass (90 total in
ai-orchestration), and all 19 Week 5 + 42 Week 6 Python tests and all 47
Java tests still pass unmodified. Live verification triggered two brand-
new experiments this week: a notification-latency run that - reported
honestly rather than cherry-picked - surfaced a real limitation (Week
7's already-documented cumulative-counter behavior caused the RCA to
misattribute this run to auth-key-error, since a stale elevated error
count from earlier in the session outweighed the actual, but
below-threshold, notification-latency signal; RAG still correctly
refused to blindly follow retrieval's disagreeing suggestion); and a
db-lock run that produced a fully correct end-to-end result (root cause,
RAG agreement, and remediation proposal all correct, confidence "high").
Every retrieved incident ID across both live runs was verified present
in the Memory set and absent from the Evaluation set. No remediation
action was executed at any point; all four faults were confirmed
inactive both before and after testing, and the full payment flow was
verified working live. See `ai-orchestration/README.md` for full
architecture and live-verification detail. Waiting for explicit
instruction to begin Week 9 (Safety Validator, mandatory Human Approval,
Streamlit).

Week 7 complete (2026-08-28): `ai-orchestration/` (Python) adds the AI
intelligence foundation - anomaly detection, local LLM interpretation,
and a LangGraph workflow - that Week 8's RCA/remediation agents build on.
One `IsolationForest` per service (not one shared model) is trained on
`config.SERVICE_FEATURE_SCHEMAS`, since some curated metrics only apply
to specific services (`hikaricp_*` to ledger-service only, `http_client_*`
to payment-service only - verified against real telemetry). Training data
is restricted to NORMAL/RECOVERY-phase telemetry from real experiments
whose incident is in the Memory set - enforced in code the same way
`dataset-tools/chroma_store.py` enforces ChromaDB's Memory-only
constraint: `feature_extractor.load_memory_eligible_baseline_records()`
only ever reads `dataset-tools/data/memory/memory_incidents.jsonl`, has
no parameter that could point it at the Evaluation file, and synthetic
incidents (having no raw telemetry behind them) contribute nothing.
This currently yields 42-45 real training samples per service (4 real
experiments landed in Memory out of 16 total). A local Ollama integration
(`llama3.2:1b`, chosen over CLAUDE.md's originally-named `llama3.1:8b`
because this machine had only ~8.8GB free disk - documented, not silent,
and switching back is a one-line env var change) produces a structured
JSON anomaly interpretation from real evidence only, degrading
gracefully (never raising) whenever Ollama is unavailable, times out, or
returns malformed output - it never executes any action. A real
LangGraph workflow (`extract_features -> detect_anomaly -> normal |
anomaly -> interpret -> finalize`) ties it together with explicit typed
state; the LLM is only ever invoked on the anomaly branch (verified by a
test asserting the mock client is never called on the normal path).
44 new Python tests pass, and all 19 Week 5 + 42 Week 6 Python tests and
all 47 Java tests still pass unmodified. Live verification: trained all
4 real models; installed Ollama and pulled a real model; ran a full
`graph.invoke()` against a live Ollama server (7.3s round-trip, correct
structured interpretation); triggered a brand-new live memory-leak
experiment against the running Kubernetes deployment this week, collected
fresh telemetry, and confirmed anomaly scores trend upward with
`jvm_memory_used_bytes` through the fault window; confirmed the full
payment flow still works and all four faults are inactive afterward.
Known, disclosed limitation: with only 42-45 real training samples,
detection is reliable for extreme deviations (verified) but inconsistent
for some moderate real fault instances (also verified, not hidden) -
partly because `http_server_requests_count`/`error_count` are cumulative
Prometheus counters rather than rates, a real behavior discovered and
documented this week (an error counter was observed to reset to 0
between experiments despite the request counter continuing to climb and
zero Kubernetes pod restarts - most likely per-tag Micrometer meter
eviction after inactivity). See `ai-orchestration/README.md` for full
architecture, feature schema, and live-verification detail. Waiting for
explicit instruction to begin Week 8 (RCA Agent, RAG, Remediation Agent).

Week 6 complete (2026-08-28): `dataset-tools/` (Python) reconstructs
labeled incidents from Week 5's raw telemetry and builds the ChromaDB
retrieval memory. 16 real incidents (4 per fault type) were reconstructed
from live experiments against the running Kubernetes deployment (4 from
Week 5 plus 12 additional experiments run this week via
`telemetry.experiment.run_experiment()`, extended with an optional
`inject_body` parameter to vary notification-latency's delay). 52
deterministic synthetic incidents (13 per fault type) were generated by a
model whose fault-phase formulas mirror each real Java fault mechanism
(cited by class/method name in `synthetic_model.py`) and whose baseline
ranges are anchored to values actually observed live — every synthetic
incident is clearly labeled `data_source: "synthetic"`, never presented
as real. Two real bugs were found and fixed during reconstruction: (1) a
phase-labeling bug where the symptom service's own self-reported fault
flag (always false for the two cross-service faults, auth-key-error and
notification-latency) caused its fault-window telemetry to be silently
mislabeled and dropped out of `fault_metrics` — fixed by only trusting
the self-reported flag for the actual fault-injection target service,
falling back to the same time-window rule already used for payment
probes for every other service; (2) a Python operator-precedence bug in
postmortem-text generation (adjacent string-literal concatenation binds
tighter than a trailing `if/else`) that discarded all but the closing
sentence for same-service faults (memory-leak, db-lock) — fixed, with
regression tests for both. The 68-incident dataset was split
deterministically (seed 42, stratified by fault type) into a 20-incident
Memory set and a 48-incident Evaluation set (exactly 5/12 per fault
type). The Memory/Evaluation separation is enforced in code, not just
documented: `chroma_store.build_collection()` never opens the Evaluation
file, checks for incident-ID overlap between the Memory and Evaluation
files before writing anything to the collection, and re-verifies by
direct ID lookup against the built collection afterward — proven live
(evaluation IDs are absent from the collection by ID-set equality,
intersection, direct lookup, and postmortem-text comparison, all checked
against the real built collection, not just asserted in tests). ChromaDB
runs fully locally (persistent client at `dataset-tools/chroma/`,
bundled ONNX embedding model downloaded once and cached, no cloud
dependency) and correctly retrieves fault-type-relevant Memory incidents
for representative queries across all four fault types. 42 new Python
tests pass (dataset-tools, covering all 14 required test categories), all
19 Week 5 telemetry tests still pass unmodified, and all 47 Java tests
still pass unmodified. Live verification was performed against the real
Kubernetes deployment (Colima + Minikube): all four services healthy,
all four faults confirmed inactive both before and after testing, and
the full payment chain (login → payment → ledger debit → notification)
verified working end-to-end via a live HTTP call. See
`dataset-tools/README.md` for full architecture, schema, real-vs-
synthetic provenance, and exact reproduction commands. Waiting for
explicit instruction to begin Week 7 (LangGraph, Isolation Forest,
Ollama).

Week 5 complete (2026-08-28): automated telemetry capture, implemented as a
standalone Python tool (`telemetry/`) rather than a fifth Spring Boot
service — Weeks 6-12 are all Python (ChromaDB, LangGraph, Isolation
Forest, Ollama, RAG), so writing the telemetry format in Python now avoids
a double translation later, and a periodic HTTP-polling loop doesn't need
a JVM's startup cost or another Dockerfile/Kubernetes Deployment.

The one Java-side change: each of the four existing fault controllers
(`AuthFaultController`, `MemoryLeakFaultController`, `DbLockFaultController`,
`LatencyFaultController`) gained a read-only `GET /fault-status` endpoint,
reusing each fault service's existing getters — no fault behavior changed,
no secrets exposed, and all 47 pre-existing tests still pass unmodified.

`telemetry/collector.py` polls all four services' `/actuator/health`,
`/actuator/prometheus` (curated extraction — jvm memory, HikariCP pool
state, http server/client request counts+durations+errors, CPU — not a
raw dump), and `/fault-status` on a configurable interval, tolerating any
single service being down (recorded as `collection_error`, never crashes).
It also runs a synthetic login+payment probe every cycle
(`telemetry/payment_probe.py`) — necessary because the auth-key-error and
notification-latency faults only manifest on a real request, not passive
polling. The probe deliberately *caches* its JWT across cycles rather than
re-logging in each time, so a token obtained before an auth-key-error
injection keeps being reused (and starts failing) exactly like a real
already-logged-in user would experience it. Three JSONL streams, one file
per UTC day per stream (bounded growth): `telemetry_<date>.jsonl` (per-
service), `payment_probes_<date>.jsonl`, and `events_<date>.jsonl` (fault
inject/reset markers, written only by the experiment orchestrator).

`telemetry/experiment.py` runs a full NORMAL → FAULT → RECOVERY experiment
against any of the four faults, reusing the same `Collector` loop. All
four were run live this week with clean, textbook results: notification-
latency showed probe duration jump from ~15ms baseline to ~5020ms during
the fault (payment-service's existing 5s timeout) back to ~15ms after
reset, with `fault.faultActive` flipping in exact sync with the event
timestamps; memory-leak showed `jvm_memory_used_bytes` climb from ~33MB to
~153MB during the fault; db-lock showed `hikaricp_connections_active` go
9/10 during the fault and back to 0/10 after; auth-key-error showed the
cached-token probe start failing with 401 immediately on injection and
recover immediately on reset (via an explicit `force_new_token()` call
after `/reset-auth-key`, since a real client would also get a fresh token
after re-authenticating).

Verified against native processes, Docker Compose, and Kubernetes (via
`kubectl port-forward` + env var overrides — no code changes needed for
any of the three). 19 new Python tests (pytest + `responses` for HTTP
mocking) plus all 47 existing Java tests still pass. Full schema/design
in `telemetry/README.md`. Waiting for explicit instruction to begin Week 6
(incident dataset, Memory/Evaluation split, ChromaDB).
