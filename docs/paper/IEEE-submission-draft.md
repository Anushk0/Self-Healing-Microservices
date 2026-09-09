# Evidence, Not Commands: A Deterministic-First Multi-Agent Framework for Cross-Service Root Cause Analysis and Human-Governed Remediation in Financial Microservices

**Anushk Agrawal, Bhoomi Airen, Abhijay Krishnan**
Department of Computer Engineering, Mukesh Patel School of Technology Management & Engineering, NMIMS, Mumbai, India

**Guide:** Prof. Shakila Shaikh, Department of Computer Engineering, MPSTME, NMIMS, Mumbai, India

---

> **Drafting note for the authors (delete before submission).** Every quantitative statement in this
> draft is traceable to a committed artefact under `docs/evaluation/`. Numbers are not to be edited
> by hand: `ai-orchestration/tests/test_paper_claims_match_artefacts.py` re-derives them from
> `metrics.json` and fails if the prose and the artefacts disagree. Placeholders requiring your
> action are marked **[AUTHOR]**.

---

## Abstract

Modern financial platforms are built as networks of interdependent microservices, in which a fault
originating in one service frequently manifests as an observable symptom in a different service.
Automated diagnosis systems that reason primarily from the symptomatic service, or that defer to
historical precedent retrieved by similarity, systematically attribute such faults to the wrong
component. We present a deterministic-first multi-agent framework in which anomaly detection,
root-cause inference, remediation proposal, safety validation, human approval, execution and
recovery verification are separate stages of an explicitly checkpointed state graph. The framework
is organised around one architectural constraint: retrieved historical incidents are treated as
evidence that may adjust confidence, and are never permitted to override a conclusion reached
deterministically from telemetry. Remediation actions are produced by a fault-type lookup with no
model call, safety rules are evaluated without any AI component, and execution is physically
unreachable without an external human approval signal.

We evaluate the framework on 48 held-out incidents spanning four controlled fault types, two of
which are cross-service, against a single-agent large language model baseline that shares every
non-diagnostic stage of the pipeline, so that the isolated variable is agent decomposition rather
than retrieval or downstream governance. On the incidents both arms answered, the multi-agent
pipeline was correct on 10 of 10 against 6 of 10 for the baseline. On cross-service faults
specifically it was correct on 4 of 4 against 0 of 4, with the baseline naming the symptomatic
service on every one (Fisher exact, two-sided, p = 0.0286). The arms were indistinguishable on
same-service faults, so the entire difference is confined to incidents where the service reporting
the symptom is not the service at fault.

Two ablations establish that this difference is architectural rather than incidental. Disabling
retrieval entirely leaves all 48 multi-agent diagnoses unchanged, showing that retrieval contributes
no diagnostic accuracy and that the advantage derives from refusing to defer to it. Permitting
retrieval to override a deterministic match reconstructs the conventional retrieval-first design and
reproduces the single-agent baseline exactly, to four decimal places on every metric.

We report three findings that qualify these results. Detection, not diagnosis, was the binding
constraint: the shared detector surfaced only 10 of 48 incidents, so both arms abstained on 79.2%.
A negative control on healthy telemetry gives a 4.17% false-positive rate on normal-phase records
but 27.08% on post-recovery records, indicating that the system continues to alarm after a fault has
been cleared. Finally, the conventional hallucination metric, defined as citing a metric absent from
the evidence, reports 0% for a baseline that was demonstrably misrepresenting the significance of
metrics that were present; we propose a stronger measure that reports 10.0% on the same responses
and show that even it is a demonstrable lower bound.

**Index Terms** — AIOps, root cause analysis, microservices, multi-agent systems, large language
models, retrieval-augmented generation, human-in-the-loop, self-healing systems, financial systems,
fault injection.

---

## I. Introduction

### A. Motivation

Financial technology platforms have converged on microservice architectures to obtain independent
deployability, fault isolation and horizontal scalability under transaction volumes that monolithic
designs cannot sustain [18], [21]. The same decomposition that delivers these properties also
distributes the evidence required to diagnose a failure. A single fault propagates along synchronous
call chains and surfaces as a symptom in a service that is itself healthy.

Operational reliability in this domain is not a secondary engineering concern. Payment
authorisation, ledger settlement and fraud screening are subject to availability and auditability
obligations, and an outage carries direct financial and regulatory consequence [15], [16], [18].
Where a remediation action can restart a service holding uncommitted transactional state, the cost
of an incorrect automated decision is not merely a prolonged outage but a potential integrity
violation.

### B. The problem with the obvious approach

The intuitive application of large language models to this problem is to present an agent with the
telemetry of the service that raised the alert, together with similar historical incidents retrieved
by embedding similarity, and to ask it to name a root cause and an action. This design fails on
precisely the class of fault that matters most, and it fails systematically rather than randomly.

The failure has two compounding sources. First, the telemetry presented is that of the symptomatic
service, which for a cross-service fault is not the faulty one. Second, retrieval by similarity over
symptom-service telemetry returns incidents that share surface features with the query, which are
disproportionately incidents whose root cause *is* the symptom service. The agent then reasons
correctly from precedent that happens to be wrong.

This is not a hypothetical failure mode. Independent work reports the same pattern from a different
direction: OperAID [4] finds that when large language model agents are denied live observability,
95.3% of their failures are confident diagnoses of plausible but incorrect root causes, and argues
that text-similarity scoring would rate such responses favourably while execution shows complete
failure. The gap between plausibility and functionality is the central evaluation problem in this
area.

### C. Research questions

- **RQ1.** Does decomposing diagnosis into deterministic inference with retrieval as advisory
  evidence improve root-cause identification over a single-agent model given identical evidence, and
  is any improvement confined to cross-service faults?
- **RQ2.** Is the improvement attributable to the architectural constraint on retrieval, or to
  retrieval itself?
- **RQ3.** What are the operational limits of the resulting system in terms of detection coverage,
  false alarms on healthy telemetry, and computational cost?

### D. Contributions

1. **A deterministic-first pipeline in which retrieval is architecturally advisory.** Root cause is
   determined by fault-signature matching against documented thresholds; retrieved incidents may
   raise or lower recorded confidence but cannot change the conclusion. We show this constraint,
   rather than retrieval, is the entire measured difference between the arms (Section VI).
2. **Governance as a structural property rather than a convention.** Safety validation contains no
   AI component and is evaluated before a human is consulted; the workflow graph cannot advance past
   the approval stage without an external resume signal; each remediation retry raises a fresh
   approval rather than inheriting one. Across the reported evaluation, executions without approval
   were zero by measurement, not by assertion.
3. **A controlled architectural ablation.** We reconstruct the conventional retrieval-first design
   inside the same codebase and show it reproduces the single-agent baseline exactly, converting an
   attribution argument into a controlled result.
4. **A critique of hallucination measurement in operational settings,** with a stronger measure, and
   a demonstration that our own stronger measure remains a lower bound for a specific and
   reproducible parsing reason.
5. **A negative control on healthy telemetry,** which surfaces a post-recovery false-alarm rate six
   times higher than the normal-phase rate.

### E. What this paper does not claim

We state the boundaries before the results, because several are consequential.

- **Not that the absolute accuracy generalises.** The reported corpus is synthetic, and the
  generator's fault formulas share constants with the diagnostic thresholds. The comparison between
  arms is unaffected, since both received byte-identical inputs; the absolute figure is not a
  capability claim. LATS-RCA [3] reports 91.3% on an open benchmark against 65.1% on real production
  incidents, and treats that gap as its finding; we expect a comparable gap.
- **Not that the system achieves high coverage.** Measured over all incidents rather than answered
  ones, accuracy is 20.8%, because detection surfaced only 10 of 48.
- **Not that unsafe-action prevention is demonstrated.** The count is zero. Dry-run execution never
  failed, so no retry escalated to the high-risk action the ledger-service rule guards.
- **Not that the local model is adequate for narration.** It exceeded a 30-second timeout on 9 of 10
  incidents. That the diagnostic accuracy was unaffected is a claim about the architecture, not
  about the model.

### F. Organisation

Section II reviews related work. Section III presents the framework. Section IV describes the
dataset, baseline and experimental protocol. Section V reports results, Section VI the ablations,
Section VII discusses implications, Section VIII threats to validity, and Section IX concludes.

---

## II. Related Work

### A. Graph-based root cause localisation

A substantial body of work formulates root cause analysis as inference over service-dependency or
telemetry-correlation graphs. CausalRCA [5] unifies metrics, traces, logs and topology into a
temporal causal candidate graph, adds retrieval-augmented incident reasoning, and reports 86.0%
top-1 and 95.0% top-3 accuracy on DeathStarBench, with an ablation indicating that the
retrieval module and the causal graph contribute complementary strengths across fault categories.
GALR [7] fuses multimodal signals into a service call graph, applies a graph-attention module with
temporal-aware edge attention, and injects LLM-derived anomaly and uncertainty scores into node
representations. TransTVDiag [8] replaces a GraphSAGE encoder with a Graph Transformer using degree
centrality and shortest-path structural encodings, improving hit ratio by 5.3% while reducing
inference time by 83.8%. HEMA-RCL [6] organises hierarchical expert agents under a global
orchestrator, reaching consensus through belief propagation with causal enhancement to reduce hub
misidentification.

These methods establish that precise localisation is achievable without deep application
instrumentation. They also share a prerequisite that our setting does not satisfy: a populated
dependency or correlation graph derived from distributed tracing across many services. Our system
operates on four services and reaches the symptom-to-cause relation through a documented domain
mapping instead. Section VII-E returns to this as a conceded limitation rather than a design
advantage.

### B. Large language model agents for diagnosis and remediation

LATS-RCA [3] formulates root cause analysis as a reflection-guided tree search over hypotheses,
achieving 91.3% on the LO2 benchmark with small variance across frontier models, and reports both a
computational cost analysis and a substantially lower 65.1% on real production incidents from a
commercial web-hosting provider. OperAID [4] evaluates agents through a closed-loop pipeline of
fault injection, diagnosis, remediation and execution-based verification on a live Open5GS
Kubernetes deployment, finding across 900 experiments that tool access raises average success from
10.9% to 61.1% and that model size alone does not predict operational success. Souza and Colaço
Júnior [10] conduct a randomised complete block design experiment over 360 executions in Kubernetes,
reporting a 71.1% success rate for the strongest configuration against a computed random-chance
baseline of 0.91%, and observing that smaller models incurred higher total cost through redundant
reasoning cycles. AgentTrace [9] reconstructs causal graphs from execution logs to localise failures
in deployed multi-agent workflows without requiring model inference at debugging time.

Three methodological practices from this group inform our design directly: execution-based
verification rather than output plausibility [4], explicit comparison against a computed
random-chance baseline [10], and reporting the benchmark-to-production gap as a finding rather than
a caveat [3].

### C. Self-healing architectures and autonomic computing

The MAPE-K reference model remains the organising abstraction for self-healing systems. Aribe and
Oracion [11] implement a modular MAPE-K framework with an AutoFix-inspired recovery mechanism,
evaluated over twenty runtime failure scenarios, reporting a mean fault-detection F1 of 90.7%, a
recovery success rate of 93.2% and a 56.2% reduction in time-to-recovery. Yazdanparast [12] surveys
self-healing methods and their failure-mode taxonomies. Konda [19] and Meyer et al. [20] integrate
machine-learned anomaly detection with recovery, the latter combining detection and root cause
analysis in a single pipeline to reduce mean time to recovery.

Our pipeline maps onto MAPE-K directly: telemetry capture is Monitor, detection and root cause
analysis are Analyse, remediation proposal and safety validation are Plan, and execution with
recovery verification is Execute, with the retrieval memory serving as the shared Knowledge base.
The distinguishing feature is that the transition from Plan to Execute is gated by a mandatory human
decision.

### D. Resilience and self-healing in financial systems

Anderson et al. [21] catalogue fault-tolerance patterns for financial microservices including
circuit breaker, retry, bulkhead, timeout and fallback. Deshpande [18] examines Spring Boot and
Spring Cloud for scaling banking applications under PSD2, GDPR and Basel III obligations. Singh [13]
proposes a machine-learning-driven self-healing framework for FinTech platforms combining supervised
detection, unsupervised pattern discovery and reinforcement-learned recovery policy. Gaddam [14]
presents a four-layer autonomous financial platform comprising observability, intelligence, decision
and execution layers. Singh [15] argues for a prevention-first posture with built-in governance for
high-availability banking infrastructure. Nagaraja et al. [16] couple fraud detection with
autonomous response and infrastructure stabilisation, reporting a 35% reduction in system
instability. Fahmi et al. [17] detect artifact-state anomalies in microservice-based financial
applications at design time.

This literature establishes the domain requirement for auditability and governance but, with the
partial exception of [15], treats human oversight as an operational practice rather than an
architecturally enforced constraint.

### E. Anomaly detection as a precondition for root cause analysis

Soldani and Brogi [1] survey anomaly detection and root cause analysis in multi-service applications
and organise the field by signal modality, noting a recurring trade-off between setup cost, accuracy
and explainability. BARO [2] makes the dependency explicit: because root cause analysis techniques
generally presuppose accurate anomaly detection, and no detector guarantees that accuracy,
inaccurate detection propagates directly into localisation error. BARO addresses this with
multivariate Bayesian online change point detection and a nonparametric hypothesis test that is less
sensitive to detection quality.

This is the published counterpart of our own strongest negative finding. Our diagnostic layer was
correct on every incident it answered, and the system's overall accuracy was nonetheless 20.8%,
because the detector surfaced only a fifth of the corpus (Section VII-A).

### F. Evaluation practice, and an obligation it creates

A paper proposing a multi-agent versus single-agent comparison in this area was withdrawn by its
author in 2025 after a code audit found that the multi-agent arm's action list was a source constant
rather than model output, and that the scorer read only that field **[AUTHOR: verify the current
state of arXiv 2511.15755 on the abstract page and cite it exactly as it stands at submission
time]**. The reported superiority was therefore an artefact of measuring a constant.

This is the natural failure mode of any deliberately deterministic multi-agent pipeline, and our
system is one: its remediation proposer *is* a hardcoded template lookup. We therefore treat the
demonstration that our scored quantity varies with input telemetry as an obligation rather than an
assurance, and discharge it in Section IV-G with a permanent regression test.

### G. Research gap

Across the reviewed work, three gaps are consistent. No system treats human approval as an
architecturally enforced precondition for execution. Retrieval is used to inform or to determine the
conclusion, but its authority relative to deterministic evidence is not treated as an experimental
variable. And hallucination, where measured at all, is measured as reference to non-existent
entities rather than as misrepresentation of real ones.

---

## III. Proposed Framework

### A. Design principles

Four principles govern the design, each of which is falsifiable and is tested later in this paper.

1. **Determinism where a rule suffices.** Any stage expressible as a threshold or a lookup is
   implemented as such. Model inference is confined to narration.
2. **Retrieved incidents are evidence, not commands.** Retrieval may adjust recorded confidence and
   supplies provenance; it cannot change a conclusion reached from telemetry.
3. **Governance is structural.** Safety validation precedes human consultation and contains no model
   call; the graph cannot advance past approval without an external signal.
4. **Refusal over fabrication.** An unmeasurable quantity is reported as unmeasurable, and an
   abstention is scored separately from a wrong answer.

### B. Monitored system and fault taxonomy

The testbed is a four-service transactional application: an authentication service issuing JWTs, a
payment orchestration service, a ledger service holding account balances in PostgreSQL behind
pessimistic row locking, and a notification service. The payment service calls the others
synchronously, which is what allows a fault elsewhere to surface as a payment-side symptom.

Four faults are injectable, each disabled by default and independently resettable through an
endpoint the service itself exposes. Two are same-service, where the symptom and the cause coincide,
and two are cross-service.

| Fault | Injected at | Symptom visible at | Class |
|---|---|---|---|
| `memory-leak` | payment-service | payment-service | same-service |
| `db-lock` | ledger-service | ledger-service | same-service |
| `auth-key-error` | auth-service | payment-service | cross-service |
| `notification-latency` | notification-service | payment-service | cross-service |

The two cross-service faults are the experimental object of the paper. A system that reasons only
from the symptomatic service will attribute both to payment-service.

### C. Telemetry capture

A collector polls each service's health endpoint, a curated subset of its Prometheus metrics and its
fault-status endpoint on a fixed interval, tolerating any single service being unreachable by
recording a collection error rather than terminating. Metrics are extracted selectively rather than
scraped wholesale: JVM heap used, committed and maximum; HikariCP pool state for the ledger service;
HTTP server and client request counts, error counts and mean durations; and process and system CPU
utilisation. A synthetic login-and-payment probe runs each cycle, because the authentication and
latency faults manifest only on a real request path. The probe deliberately caches its token across
cycles, so that a token obtained before an authentication fault continues to be presented and begins
to fail, reproducing the experience of an already-authenticated user.

### D. Anomaly detection

One Isolation Forest is trained per service rather than one shared model, because the curated
feature set is service-specific: HikariCP fields apply only to the ledger service and HTTP client
fields only to the payment service. Training uses 100 estimators, a contamination parameter of 0.1
and a fixed random seed of 42.

Training data is restricted to normal-phase and recovery-phase telemetry drawn from incidents in the
Memory partition. This restriction is enforced in code rather than by convention: the loader reads
only the Memory file and exposes no parameter that could redirect it at the Evaluation file.

### E. Retrieval memory

Each Memory-partition incident is rendered as a short template-structured postmortem narrative and
embedded into a persistent local ChromaDB collection using the bundled default embedding model, so
that no cloud service is involved. Queries retrieve the three nearest incidents by cosine
similarity.

### F. Root cause analysis

Root cause is determined in three ordered steps.

1. **Signature matching.** Observed metrics are compared against documented per-service baseline
   ranges and fault-specific thresholds. A match yields a fault type.
2. **Symptom-to-cause resolution.** The fault type is mapped to its root-cause service through a
   documented domain mapping, which for the two cross-service faults names a service other than the
   one whose telemetry was examined.
3. **Retrieval adjudication.** Retrieved incidents are compared with the conclusion. Agreement
   raises recorded confidence to high; disagreement lowers it to medium and is recorded explicitly
   as `metric_signature+rag_disagreement`. Neither changes the conclusion. Where no signature
   matches, and only then, the nearest retrieved incident may supply the answer, recorded as
   `rag_only`. Where neither applies, the system abstains.

An optional model-generated narrative explains the conclusion already reached. Its prompt contains
no recommendation field, and its failure is non-fatal.

### G. Remediation proposal

Remediation is a deterministic fault-type-to-action lookup with no model call. Each proposal carries
an action category, a target service, a risk level and a type-level guarantee that human approval is
required. The proposer walks a fixed escalation ladder on failure: a targeted fault reset, then a
service restart, then escalation to a human, never re-proposing a category that has already failed.

### H. Safety validation

The validator contains no AI, no model call and no network access. It independently re-checks every
field of a proposal against a whitelist of permitted action categories and target services, and
implements a domain rule: any action with risk level *high* targeting the ledger service is
rejected, because that service holds uncommitted transactional state behind a pessimistic locking
boundary. A rejected proposal is routed to finalisation and never reaches a human, on the principle
that a rule violation should not consume operator attention.

### I. Mandatory human approval

Approval is implemented as a graph-level interrupt. The workflow is compiled with a checkpointer and
physically cannot advance past the approval node without an external resume signal carrying an
explicit decision. This is a structural property of the execution model, not a documented
convention. Three further properties hold: a human approval cannot override a safety rejection,
because safety is evaluated first and independently; the decision and its free-text reason are
recorded verbatim in the final result; and each retry after a failed remediation raises a *fresh*
interrupt, so an approval granted for one action is never inherited by a different one.

### J. Execution, verification and feedback

Execution is the only component that mutates the running system, and it re-checks all three gates
immediately before acting rather than trusting graph routing. It can perform exactly one of three
things: POST a service's own fault-reset endpoint, delete a pod so its deployment recreates it, or
nothing. There is no generic command path, and the Kubernetes client refuses any service outside the
four in this system.

Verification re-observes the target's fault-status and health endpoints rather than trusting the
remediation call's own success response. This is what makes a false recovery detectable at all.
Completed runs are written back into retrieval memory in the same postmortem format, including
failures and rejections rather than rewriting them as successes.

---

## IV. Experimental Setup

### A. Dataset construction

Incidents were reconstructed from raw telemetry captured during controlled fault-injection
experiments, and augmented with deterministically generated synthetic incidents whose fault-phase
formulas mirror each real fault mechanism and whose baseline ranges are anchored to values observed
live. Every synthetic incident is labelled as such and never presented as real. The reported run
uses a corpus of 68 incidents.

Each incident records its phase boundaries, per-service metric averages for the normal, fault-active
and recovery phases, metric deltas, observed errors, correlation identifiers and a generated
postmortem narrative.

### B. Memory and Evaluation partitioning

The corpus is split deterministically under seed 42, stratified by fault type, into a 20-incident
Memory partition used exclusively for retrieval and detector training, and a 48-incident Evaluation
partition used exclusively for measurement. The Evaluation partition contains 12 incidents of each
fault type, of which 24 are cross-service.

### C. Leakage prevention

Separation is enforced in code at three independent layers. The collection builder never opens the
Evaluation file; it checks for identifier overlap before writing anything and re-verifies by direct
lookup afterwards. The feedback writer refuses any incident belonging to the Evaluation partition,
and fails closed when the Evaluation file cannot be read, so that "cannot check" is never silently
treated as "nothing to check". Finally, the evaluation harness verifies the guard *live* before any
measurement is recorded: it attempts a write-back of a genuine Evaluation incident and aborts the
entire run unless the refusal occurs. The probe identifier and refusal reason are recorded in the
results artefact.

### D. Single-agent baseline

The baseline is a genuinely separate implementation: one prompt, one model call producing root cause
and action together, importing nothing from the root-cause, remediation or interpretation modules.
Every stage that is *not* the diagnostic step is shared by importing the identical node objects, and
a test asserts node identity so the arms cannot drift apart. The isolated variable is therefore
agent decomposition, not retrieval, not detection and not downstream governance. The baseline
receives the same retrieved incidents. Its failure modes are recorded rather than repaired: a
hallucinated service or invented action becomes a countable parse issue, and an invented action is
coerced to something non-executable. It does not retry, so the comparison uses one attempt per
incident for both arms.

### E. Replay protocol

Each Evaluation incident is converted into the single raw telemetry record a monitor would have
seen. Three decisions carry methodological weight.

1. **The symptom service's record is replayed**, because a real monitor cannot know in advance that
   the cause lies elsewhere.
2. **The fault-active phase averages are used**, unmodified.
3. **The self-reported fault flag is withheld.** Each service exposes an endpoint stating whether
   its own fault injection is active, which for a same-service fault is effectively an answer key. A
   genuine unknown incident does not arrive with such a flag, so neither does a replayed one.

The simulated operator approves everything reaching it, deliberately. Gating execution on ground
truth would make recovery metrics measure how well remediation works *when the diagnosis is already
known to be correct*.

### F. Metrics

We report five categories. Performance covers accuracy over all incidents, accuracy when answered,
abstention rate, macro precision, recall and F1, and cross- and same-service accuracy reported
separately. System covers detection latency, time to first approval pause and anomaly detection
rate. AI covers model calls, wall-clock time, reasoning availability and two hallucination measures.
Human covers incidents reaching approval, decisions recorded, unsafe actions prevented and, as the
safety invariant, executions without approval. Infrastructure metrics were not collected.

Two conventions distinguish this protocol. An unmeasurable quantity is reported as unmeasurable with
a stated reason, never as a number. And an abstention is scored separately from a wrong answer,
because conflating them rewards a system that guesses.

Our system emits a single root-cause service rather than a ranked list, so the Top-k and mean
reciprocal rank metrics prevalent in graph-based localisation work [5], [7], [8] are not applicable;
our accuracy figures correspond to Top-1.

### G. Verifying that the scored quantity is not a constant

Following the obligation set out in Section II-F, we audit empirically whether the scored output
varies with input. The root-cause analysis was given five telemetry inputs and its output recorded.

| Input (service, distinguishing metric) | Fault type | Root cause | Method |
|---|---|---|---|
| payment-service, heap used 5.0e8 | `memory-leak` | payment-service | signature |
| payment-service, server error count 25 | `auth-key-error` | **auth-service** | signature |
| payment-service, client duration 5200 ms | `notification-latency` | **notification-service** | signature |
| ledger-service, HikariCP active 10 | `db-lock` | ledger-service | signature |
| payment-service, all metrics at baseline | none | payment-service | fallback |

Rows one to three are all payment-service records and yield three different root-cause services, so
the output is driven by metric values rather than by the identity of the reporting service. Row five
shows abstention from naming a fault type rather than emission of a default. This audit is enforced
by a permanent regression test.

**A second-order disclosure.** Once a fault type is determined, the root-cause service follows from
a fixed domain mapping that the ground-truth labels also use. Root-cause accuracy is therefore
arithmetically equal to fault-type accuracy for this system, and is not an additional independent
capability. The defensible claim is narrower than "the system identifies root causes" and should be
read as: *the system identifies the fault type from symptom-service telemetry alone, and
consequently does not attribute cross-service faults to the service reporting the symptom.*

### H. Implementation configuration

| Component | Configuration |
|---|---|
| Anomaly detection | Isolation Forest, 100 estimators, contamination 0.1, seed 42 |
| Features | Per-service curated schema (JVM memory, HikariCP, HTTP server/client, CPU) |
| Training data | Normal and recovery phases, Memory partition only |
| Vector store | ChromaDB, persistent, local |
| Embedding | ChromaDB bundled default model (local ONNX) |
| Retrieval depth | 3 nearest incidents, cosine similarity |
| Language model | `llama3.2:3b`, self-hosted via Ollama, 30 s timeout |
| Orchestration | LangGraph checkpointed state graph, 12 nodes |
| Execution mode | Dry run (replay) |
| Feedback | Disabled during measurement |

### I. Environment and reproducibility

The reported run executed on Windows 11 with Python 3.12.2 in replay mode with execution in dry run
and feedback disabled. Determinism is enforced by fixed seeds and by deriving all pseudo-random
offsets from a specified stable checksum rather than from the interpreter's randomised string hash,
a defect found and corrected during this work. A regression test executes the generator in a
*separate process*, because a same-process test cannot detect per-process hash randomisation. The
software carries 316 automated tests across three Python packages and 47 across four Java services,
all passing.

---

## V. Results

### A. Root cause identification

| Metric | Multi-agent | Single-agent baseline |
|---|---|---|
| Accuracy over all 48 incidents | **20.8%** | 12.5% |
| Accuracy when answered | **100%** (10/10) | 60.0% (6/10) |
| Abstention rate | 79.2% | 79.2% |
| Precision / Recall / F1 (macro) | 100% / 100% / 100% | 37.5% / 50.0% / 41.7% |
| **Cross-service accuracy** | **100%** (4/4) | **0%** (0/4) |
| Same-service accuracy | 100% (6/6) | 100% (6/6) |
| Blamed the symptom service on cross-service faults | 0 | **4 of 4** |
| Errored incidents | 0 | 0 |

With 95% Wilson intervals, multi-agent accuracy when answered is 1.000 [0.722, 1.000] and
cross-service accuracy is 1.000 [0.510, 1.000]; the baseline's cross-service accuracy is 0.000
[0.000, 0.490]. The intervals are wide, and deliberately reported as such.

Against a random-chance baseline computed as one of four services, the multi-agent arm's 10 of 10 is
significant at p = 9.5 × 10⁻⁷; against joint identification of service and fault type, one of
sixteen, at p = 9.1 × 10⁻¹³. The baseline is also above chance (p = 0.020 and p = 1.0 × 10⁻⁵
respectively), so the comparison between arms, not the comparison against chance, is the meaningful
one.

On the cross-service subset the difference between arms is significant by Fisher's exact test, two
sided, p = 0.0286. Over all answered incidents it is not (p = 0.0867). Treating the design as paired
— both arms saw the same ten incidents, with four discordant pairs, all favouring the multi-agent
arm — an exact sign test gives p = 0.125. **We report the paired test as the conservative statistic
and do not claim significance over all answered incidents.** The defensible statistical claim is
confined to the cross-service subset.

Two features of the table matter more than the headline. The arms are identical on same-service
faults and maximally separated on cross-service ones, so the entire difference is confined to
incidents where the reporting service is not the faulty one. And the baseline's errors are not
scattered: on every one of the four cross-service incidents it answered, it named payment-service,
the symptom service.

### B. Mechanism

Breaking the ten answered multi-agent incidents down by determination method gives a clean
partition.

| Determination method | Incidents | Correct | Class |
|---|---|---|---|
| `metric_signature+rag_agreement` | 5 | 5 | all same-service |
| `rag_only` | 1 | 1 | same-service, no signature matched |
| `metric_signature+rag_disagreement` | 4 | 4 | **all cross-service** |

On every cross-service incident, retrieval disagreed with the correct conclusion, returning
memory-leak incidents rooted at payment-service that match on the surface features of payment-service
telemetry. The baseline received the same retrieved incidents and followed them.

This is not an inference from aggregates. On four of four cross-service failures the baseline's own
reasoning cites the retrieved incidents as its justification, three of them naming the retrieved
identifiers outright. That the same three Memory incidents are cited across *both* cross-service
fault types, whose telemetry signatures are entirely different, indicates the failure is driven by
retrieved precedent rather than by anything in the incident itself.

### C. System metrics

| Metric | Multi-agent | Single-agent |
|---|---|---|
| Detection latency (detection path only), mean | 0.0061 s | 0.0061 s |
| Detection latency, median | 0.0052 s | 0.0052 s |
| Time to approval pause (incl. diagnosis), mean | 8.5 s | 2.12 s |
| Anomaly detection rate | 20.8% | 20.8% |
| MTTR | not measurable in replay | not measurable in replay |
| Recovery success rate | not measurable in replay | not measurable in replay |
| False recovery rate | not measurable in replay | not measurable in replay |

Detection is shared by construction and the two columns agree to six decimal places; divergence
would have indicated a harness defect. Detection latency and time-to-approval-pause are reported
separately and deliberately: an earlier harness conflated them by timing the first graph invocation,
which runs to the approval pause and therefore includes retrieval, diagnosis and narration,
overstating detection latency by roughly three orders of magnitude. The error was found by
inspecting an anomalous mean against a near-zero median.

Detection by fault type: memory-leak 4 of 12, auth-key-error 2 of 12, db-lock 2 of 12,
notification-latency 2 of 12. The three unmeasured metrics require an active fault to remediate, and
therefore a live cluster; they are reported as unmeasurable rather than as the 100% a dry run would
trivially produce.

### D. Negative control on healthy telemetry

Following the negative-control design of [10], we replayed healthy telemetry through the detection
path and measured the false-positive rate. Every Evaluation incident contributes two healthy
records: its normal-phase averages and its recovery-phase averages, for the same symptom service.

| Condition | False positives | Rate | 95% Wilson CI |
|---|---|---|---|
| Normal phase | 2 / 48 | 4.17% | [1.15%, 13.98%] |
| **Recovery phase** | **13 / 48** | **27.08%** | [16.57%, 41.00%] |
| Combined | 15 / 96 | 15.62% | [9.70%, 24.19%] |

The normal-phase rate is acceptable. The recovery-phase rate is more than six times higher and is an
operationally significant defect: after a fault has been cleared, metrics have not yet returned to
baseline, and the system continues to raise anomalies on a service that has just been successfully
repaired. In a closed loop with automated remediation, this is the precondition for a remediation
storm, in which a successful fix is followed by repeated re-detection and further action.

We report this as a limitation of the detector configuration rather than of the architecture, and
note that it is invisible to any evaluation that replays only fault-window telemetry, which is what
our own main experiment does.

### E. AI metrics

| Metric | Multi-agent | Single-agent |
|---|---|---|
| Model calls per incident (mean) | 0.4167 | 0.2083 |
| Model calls, total | 20 | 10 |
| Wall clock per incident (mean) | 8.5 s | 2.12 s |
| Incidents producing reasoning text | **1 of 10** | 10 of 10 |
| Hallucination: cited a metric absent from evidence | 0% *(1 sample)* | 0% (over 10) |
| **Hallucination: claimed a baseline-range value was elevated** | 0% | **10.0%** (1 of 10) |
| Response parse-issue rate | 0% | 0% |

Token cost is replaced by inference time and call count, following the self-hosted-model
substitution documented in Section IV-H.

**The multi-agent arm's narration failed on 9 of 10 incidents**, exceeding the 30-second timeout,
and its diagnostic accuracy was 100% regardless.

The two hallucination rows measure different things, and the difference is the finding. The
conventional definition returns 0% for the baseline, and that 0% is a real measurement rather than a
broken detector: eight genuine metric citations were detected across the ten reasoning texts, all
present in the evidence. But on one incident the baseline justified "memory leak" by citing a
committed-heap value of 86.4 MB as evidence of a heap increase, when that value lies inside the
documented 70–90 MB range for the service. The metric was real; its stated significance was
invented.

Worth stating plainly, because it cuts against a convenient reading: **that diagnosis was correct.**
The incident genuinely was a payment-service memory leak. The unsupported claim was in the
justification, not the conclusion. For an operator reviewing a recommendation this is the more
troubling case, not the less, because a right answer supported by a reason that does not hold is
indistinguishable at review time from a right answer that is grounded.

**Both measures are lower bounds, in a specific and demonstrable way.** Three of the four
cross-service failures also cite a heap figure inside the documented 30–45 MB range while describing
it as elevated: 30.7 MB, 30.5 MB and 35.0 MB. Neither measure catches them. The metric-name proxy
misses them because the metrics are real. The stronger measure misses them because it parses only
values written adjacent to their metric name, and the model wrote these as bare parentheticals. So
at least four unsupported significance claims exist in this run and one is counted.

### F. Run-to-run reproducibility

The evaluation was executed twice independently on the same corpus.

| Arm | Identical root-cause predictions across runs |
|---|---|
| Multi-agent | **48 / 48** |
| Single-agent baseline | **48 / 48** |

The multi-agent result is reproducible by construction. That the single-agent result is equally
reproducible was not guaranteed and is worth stating: a model-driven diagnosis produced the same 48
answers twice, including the same four wrong ones.

What varied is the part that does not affect the conclusion, and it varied more than a count would
show. Narration succeeded on exactly one incident in each run, but **not the same incident**. And on
the baseline, **all ten reasoning texts differed textually between runs** while all ten extracted
conclusions were identical. The prose was different on every incident that produced any, in both
arms, and not one of the 96 diagnoses moved. This is stronger evidence for the architectural claim
than a stable count would have been.

### G. Human-in-the-loop

| Metric | Multi-agent | Single-agent |
|---|---|---|
| Incidents reaching human approval | 10 | 10 |
| Approved / Rejected | 10 / 0 | 10 / 0 |
| Unsafe actions prevented by safety validator | 0 | 0 |
| **Executions without approval** | **0** | **0** |

The last row is the safety invariant, reported as a measured quantity rather than asserted. Unsafe
action prevention is zero because dry-run execution never failed, so no retry escalated to the
high-risk action the ledger-service rule guards; that rule is evidenced instead by regression tests
and by a live session, and is **not** claimed as a demonstrated result of this evaluation.

---

## VI. Ablation Studies

### A. Design

Two ablations, both disabled by default in the shipped configuration, both recorded in every run's
environment block, and both covered by tests asserting they are off by default.

- **Ablation A** removes retrieval entirely. *Does retrieval contribute anything?*
- **Ablation B** permits retrieved incidents to override a deterministic signature match. *What
  would this system score if built the conventional way?*

### B. Ablation A — removing retrieval

| Metric | Multi (shipped) | Multi **no retrieval** | Baseline (shipped) | Baseline **no retrieval** |
|---|---|---|---|---|
| Accuracy (all incidents) | 20.8% | **20.8%** | 12.5% | **6.3%** |
| Accuracy (when answered) | 100% | **100%** | 60.0% | **30.0%** |
| **Cross-service accuracy** | 100% | **100%** | 0% | **50.0%** |
| **Same-service accuracy** | 100% | **100%** | 100% | **16.7%** |
| Blamed the symptom service | 0 | **0** | 4 | **0** |
| F1 (macro) | 1.00 | **1.00** | 0.42 | **0.18** |
| Incidents answered | 10 | 10 | 10 | 10 |

**Finding A1: retrieval is diagnostically inert for the multi-agent pipeline.** Every performance
metric is identical with and without retrieval, to four decimal places. Retrieval changes only the
recorded confidence label and the determination method. It does not change a single diagnosis across
48 incidents.

This is unflattering to the architecture as advertised, and we state it plainly: the
retrieval-augmented component contributes nothing to diagnostic accuracy here. The honest
characterisation of its role is corroboration and provenance, not diagnosis. A caveat in the other
direction: retrieval cannot help a signature matcher already at 100%, and on a live corpus where
signature matching is imperfect it has room to contribute that it does not have here.

**Finding A2: for the single-agent baseline, retrieval is load-bearing *and* harmful.** Same-service
accuracy collapses from 100% to 16.7%; cross-service accuracy *improves* from 0% to 50.0%;
symptom-service blaming falls from 4 of 4 to 0 of 4. The coherent reading is that retrieval was
substituting for reasoning. With precedent available the model pattern-matches to it, which is
exactly right on same-service faults and exactly wrong on cross-service ones. Remove the precedent
and it loses the easy cases but stops being systematically dragged toward the symptom service.

**Finding A3: the same intervention leaves one arm unchanged and reorganises the other.** That
asymmetry is itself evidence for the architectural claim: in the multi-agent pipeline retrieval is
advisory by construction, so removing it changes confidence labels; in the baseline it is causal, so
removing it changes answers.

**Finding A0, reported first among the caveats.** All 48 predictions are identical with and without
retrieval, but the *paths* differ on ten of them, and one is not a coincidence-free match. On one
incident no signature matched, so with retrieval the answer came from the nearest retrieved incident
and without it the pipeline fell back to naming the symptom service. Both produced the right answer,
but only because that is a same-service fault where the fallback's default happens to be correct. A1
is a claim about scores, not about reasoning being unchanged.

### C. Ablation B — letting retrieval override

The prediction registered before this run: because retrieval disagreed with the correct answer on
all four cross-service incidents and proposed payment-service each time, allowing it to win those
disagreements should collapse cross-service accuracy toward 0% while leaving the six same-service
incidents untouched.

| Metric | Multi (shipped) | Multi **+ override** | Single-agent baseline |
|---|---|---|---|
| Accuracy (all incidents) | 20.8% | **12.5%** | **12.5%** |
| Accuracy (when answered) | 100% | **60.0%** | **60.0%** |
| **Cross-service accuracy** | 100% | **0%** | **0%** |
| Same-service accuracy | 100% | 100% | 100% |
| Blamed the symptom service | 0 | **4** | **4** |
| F1 (macro) | 1.00 | **0.4167** | **0.4167** |
| Incidents answered | 10 | 10 | 10 |

**Finding B1: removing the constraint reproduces the baseline exactly.** Not approximately. Every
metric in the override column equals the baseline's value to four decimal places, including the
macro F1 and the count of symptom-service attributions. The determination-method breakdown shows
why: five agreements unchanged, one retrieval-only, and **four overrides** — precisely the four
cross-service incidents, each flipped to retrieval's answer.

Under this ablation the pipeline retains everything else that distinguishes it: per-service
detectors, signature matching, a deterministic proposer, safety validation and staged approval. All
of that apparatus is worth exactly zero once retrieval is allowed to override the conclusion. The
system collapses to its own baseline.

**Finding B2.** This converts the Section V-B attribution from an interpretation into a controlled
result. The claim "the advantage comes from refusing to defer to retrieval" is no longer an
explanation of a correlation; it is the measured consequence of toggling exactly that constraint
with everything else held fixed.

---

## VII. Discussion

### A. Detection, not diagnosis, is the binding constraint

The diagnostic layer was correct on every incident it answered, and the system's accuracy over all
incidents was 20.8%, because the shared detector surfaced only 10 of 48. This is the system's real
operating characteristic, and it is a detection problem.

The mechanism is specific. Isolation Forest **saturates** outside its training range: every learned
split threshold for a feature lies inside the observed range, so any value beyond it takes the same
branch at every node. A swept measurement confirmed a 2 GB heap scoring identically to a 50 MB one.
Extreme deviations are therefore as invisible as moderate ones, for the same reason. We characterise
this rather than tune it away.

BARO [2] makes the general form of this argument and addresses it with change-point detection that
does not presuppose accurate anomaly flags. That is the most promising direction for this system's
principal weakness.

An important mitigation is already present: the deterministic signature matcher does not share this
weakness, so the *diagnosis* stays correct wherever detection fires. The deterministic layer
compensating for the machine-learned layer's blind spot is itself a result in favour of the
decomposed design.

### B. The architecture absorbed a failing model

The narration layer was unavailable for 90% of the incidents the multi-agent arm answered, and not
one conclusion changed. A system that had delegated diagnosis to that model would have produced
nothing for those incidents. This is the "deterministic conclusion, model narration" split working
exactly as designed, demonstrated by accident rather than by construction.

The reproducibility comparison sharpens it: across two runs the prose differed on every incident
that produced any, in both arms, while all 96 diagnoses were identical. The component that behaves
inconsistently is precisely the component that changes nothing.

This has a direct engineering implication. Where a diagnosis must be auditable, the model should
narrate a conclusion it cannot alter. Where the model determines the conclusion, as in the baseline,
its non-determinism becomes the system's non-determinism.

### C. Rethinking hallucination measurement

The conventional operational definition of hallucination — citing a metric absent from the evidence
— returns near-zero for a model whose actual failure is misrepresenting the significance of metrics
that are present. Our data shows this concretely: 0% under the conventional measure, 10.0% under a
measure comparing each cited value against its documented baseline range, and at least four such
claims present in a run where one is counted.

The lesson generalises beyond this system. A hallucination metric defined as reference to a
non-existent entity will read as near-zero for any model whose failure mode is misdescribing real
entities. In operational settings, where every quantity the model cites is drawn from real
telemetry, that is the failure mode that actually occurs.

We also note what our stronger measure does *not* do: it scores the justification, not the answer. A
flagged incident may still be diagnosed correctly, and in this run it was.

### D. Cost of the decomposed design

The multi-agent arm used twice the model calls and 4.017 times the wall clock of the baseline. The
benefit is confined entirely to cross-service diagnosis; on same-service faults the two are
indistinguishable and the decomposition is pure overhead.

This mirrors a trade-off reported elsewhere in the literature. LATS-RCA [3] reports roughly 75 API
calls and 13 minutes per incident; OperAID [4] finds a 2.9-fold cost increase buying a 5.5 point
accuracy gain. Our overhead is small in absolute terms because most stages are deterministic Python
and the sole model call is optional narration.

### E. Where the design does not win, and what it concedes

Three concessions.

The two arms are *identical* on same-service faults. The contribution is narrow and should be stated
narrowly.

The symptom-to-cause relation is supplied by a documented domain mapping. Graph-based approaches
[5], [6], [7], [8] *derive* this relation from observed dependencies, which generalises to systems
whose topology is not known in advance. Ours does not. On a four-service system with a known
synchronous call chain the mapping is defensible; on a system of a hundred services it is not, and a
learned or traced dependency graph would be required.

Unsafe-action prevention is zero, and we do not claim it.

### F. Deployment and regulatory implications

Three properties matter for a regulated financial deployment. Every executed action is preceded by a
recorded human decision with a free-text reason, giving a complete audit trail. Every retry requires
its own approval, so an operator cannot be deemed to have consented to an action they never saw. And
the safety rule protecting the transactional service is evaluated deterministically and before human
consultation, so an operator cannot approve past it.

The negative-control finding in Section V-D is the counterweight and should be addressed before any
closed-loop deployment: a 27% false-positive rate on post-recovery telemetry, combined with
automated remediation, is a remediation-storm risk.

---

## VIII. Threats to Validity

**Internal validity.** The two arms share every non-diagnostic node by importing identical objects,
and a test asserts node identity, so drift between arms is excluded by construction. Both receive
byte-identical replayed records. The simulated operator approves uniformly, which removes operator
judgement as a confound but also means approval accuracy is definitionally equal to diagnostic
accuracy and is not an independent measurement. The self-reported fault flag is withheld to prevent
an answer-key leak on same-service faults.

**External validity.** The corpus is synthetic and the testbed has four services and four fault
types. Real incidents exhibit multi-factor causes, incomplete observability and cascading failures
that this corpus does not contain. LATS-RCA [3] reports 91.3% on a benchmark and 65.1% on production
incidents; we expect a comparable degradation and have not measured it. Results are from one
application, one model and one machine.

**Construct validity.** The most serious threat is a circularity: the synthetic generator's fault
formulas and the diagnostic signature thresholds derive from the same documented baseline constants,
so a threshold matcher was evaluated on data generated to exceed those thresholds. The comparison
between arms is unaffected, since both received identical inputs and the baseline's failure is not
an artefact of this, but the absolute accuracy is not a capability claim. Separately, root-cause
accuracy equals fault-type accuracy by construction (Section IV-G), so the two are one capability
reported twice. Our hallucination measures are lower bounds by acknowledged parsing limits.

**Statistical validity.** The answered subset is 10 incidents and the cross-service subset is 4.
Confidence intervals are correspondingly wide and are reported. The cross-service difference is
significant by Fisher's exact test (p = 0.0286) but the conservative paired sign test over four
discordant pairs is not (p = 0.125), and we do not claim significance over all answered incidents (p
= 0.0867). Two runs cannot characterise a distribution; they establish stability, not its
parameters.

---

## IX. Conclusion and Future Work

### A. Conclusion

We presented a deterministic-first multi-agent framework for root cause analysis and human-governed
remediation in financial microservices, organised around the constraint that retrieved incidents are
evidence rather than commands.

On 48 held-out incidents the framework was correct on 4 of 4 cross-service faults where a
single-agent model given byte-identical evidence was correct on 0 of 4, naming the symptom service
every time (p = 0.0286). The arms were indistinguishable on same-service faults, so the contribution
is confined to, and defined by, cross-service attribution.

Two ablations establish the mechanism. Retrieval contributes zero diagnostic accuracy to the
pipeline, and permitting it to override the deterministic conclusion reproduces the baseline
exactly. The advantage is therefore attributable to the architectural constraint, not to the
retrieval component, and this is a controlled result rather than an interpretation.

We further report that detection, not diagnosis, bounded the system at 20.8% coverage; that a
negative control reveals a 27% false-positive rate on post-recovery telemetry; and that the
conventional hallucination metric measures the wrong thing in operational settings.

### B. Future work

1. **Replace the detector.** Change-point detection in the manner of BARO [2] directly targets the
   binding constraint and the saturation behaviour documented in Section VII-A.
2. **Fix the post-recovery false-alarm rate**, through a stabilisation window after a verified
   recovery or a detector trained explicitly on recovery-phase telemetry.
3. **Derive the dependency relation rather than declare it**, using distributed tracing, removing
   the concession in Section VII-E and generalising beyond four services.
4. **Run the live evaluation.** Recovery success rate, false recovery rate and MTTR require an
   active fault, and become measurable with no code change under a live-mode run on a cluster.
5. **Validate on real incidents**, to quantify the benchmark-to-production gap [3] for this design.
6. **Study operator behaviour at the approval gate**, including whether approval fatigue erodes the
   governance property this architecture provides.

---

## Data and Code Availability

**[AUTHOR: insert the repository URL and, if the venue permits, an archived DOI.]** The
implementation, the incident corpus with its Memory and Evaluation partitions, the evaluation
harness, both ablation configurations, per-incident results, computed metrics, the raw run records
of both independent runs, and the figures reproduced here are available in the project repository.
The full evaluation is reproduced with a single command, and metrics may be recomputed from a saved
run without re-executing the pipeline.

---

## References

[1] J. Soldani and A. Brogi, "Anomaly detection and failure root cause analysis in (micro)
service-based cloud applications: A survey," *ACM Computing Surveys*, vol. 55, no. 3, art. 59, 2022.

[2] L. Pham, H. Ha, and H. Zhang, "BARO: Robust root cause analysis for microservices via
multivariate Bayesian online change point detection," 2024, doi: 10.1145/3660805.

[3] A. Naakka, Y. Wang, and M. V. Mäntylä, "LATS-RCA: Language agent tree search for root cause
analysis in microservices," arXiv:2605.03505, 2026.

[4] A. Góes de Castro, K. Vandikas, S. Ferlin, M. Chiesa, and C. Esteve Rothenberg, "OperAID:
Benchmarking LLM agents for autonomous Kubernetes fault remediation," in *Proc. IEEE NetSoft*, 2026,
doi: 10.1109/NETSOFT70012.2026.11603505.

[5] J. Kou, Z. Qing, R. Wei, F. Xue, W. Huang, and H. Zhuang, "CausalRCA: Causal graph-augmented
retrieval for root cause analysis in microservice systems," in *Proc. IEEE EIBDCT*, 2026, doi:
10.1109/EIBDCT69742.2026.11566729.

[6] C. Qiu, "Hierarchical expert multi-agent framework for causal root cause localization in
cloud-native microservices," in *Proc. IEEE EIECT*, 2025, doi: 10.1109/EIECT68017.2025.11331781.

[7] W. Zhang, Z. Yang, F. Peng, L. Zhang, Y. Chen, and R. Chen, "GALR: Graph-based root cause
localization and LLM-assisted recovery for microservice systems," *Electronics*, vol. 15, no. 243,
2026.

[8] Á. Kiss and K. Nehéz, "A graph transformer-based framework for multi-modal failure diagnosis in
microservice systems," *Int. J. Cloud Applications and Computing*, vol. 16, no. 1, 2026.

[9] Z. G. Wang, "AgentTrace: Causal graph tracing for root cause analysis in deployed multi-agent
systems," in *ICLR Workshop on Agents in the Wild*, 2026.

[10] R. G. dos Santos Souza and M. Colaço Júnior, "Cloud microservices: An empirical study of
MCP-orchestrated LLM agents for incident triage," in *Proc. CSBC/SEMISH*, 2026.

[11] S. G. Aribe Jr. and R. J. G. Oracion, "When web apps heal themselves: A MAPE-K based approach
to fault tolerance and adaptive recovery," *Int. J. Informatics and Communication Technology*, vol.
15, no. 2, pp. 729–740, 2026, doi: 10.11591/ijict.v15i2.pp729-740.

[12] Z. Yazdanparast, "A survey on self-healing software system," arXiv:2403.00455, 2024.

[13] P. Singh, "Self-healing architecture: Using ML to enhance system resilience in FinTech
platforms," *J. Artificial Intelligence, Machine Learning and Data Science*, vol. 2, no. 1, 2024,
doi: 10.51219/JAIMLD/Prashant-singh/573.

[14] K. R. Gaddam, "Towards autonomous financial platforms: Leveraging AI agents and microservices
for self-healing infrastructure," *World J. Advanced Research and Reviews*, vol. 26, no. 2, 2025,
doi: 10.30574/wjarr.2025.26.2.1912.

[15] T. Singh, "Self-healing infrastructure: Predictive automation for high-availability banking
systems," *Int. J. AI, Big Data, Computational and Management Studies*, vol. 7, no. 1, 2026, doi:
10.63282/3050-9416.ijaibdcms-v7i1p120.

[16] M. Nagaraja, D. R. Palavali, A. Jain, and R. Y. Hosamani, "Self-healing security orchestration
for financial fraud systems: Autonomous incident response and infrastructure stabilization," in
*Proc. IEEE SoutheastCon*, 2026, doi: 10.1109/SoutheastCon63549.2026.11476294.

[17] F. Fahmi, P.-S. Huang, and F.-J. Wang, "Detecting artifact anomalies in microservice-based
financial applications," in *Proc. IEEE Int. Conf. Services Computing*, 2020, doi:
10.1109/SCC49832.2020.00061.

[18] R. A. Deshpande, "Application of Spring Boot microservice architecture for scaling banking
applications," *The American J. Engineering and Technology*, vol. 7, no. 9, 2025, doi:
10.37547/tajet/volume07issue09-09.

[19] R. Konda, "Machine learning-based self-healing systems: Automated failure detection and
recovery in microservices," *Int. J. for Multidisciplinary Research*, vol. 3, no. 5, 2021, doi:
10.36948/ijfmr.2021.v03i05.43946.

[20] O. Meyer, E. Johnson, and J. Brown, "A unified framework for anomaly detection and root cause
analysis in microservice systems," *Computer Life*, vol. 13, no. 2, 2025, doi: 10.54097/1gw77589.

[21] M. Anderson, J. Thompson, R. Williams, E. Carter, D. Martinez, and C. Srinivas, "Resilient
architecture design for financial microservices using fault tolerance patterns," *Int. J. Scientific
Research in Science, Engineering and Technology*, 2020, doi: 10.32628/ijsrset2072905.

[22] S. Mali, S. Gavale, D. Ganage, S. Bhalekar, and S. Jadhav, "Self-healing microservices and
AI-based anomaly detection system," *Int. Research J. Modernization in Engineering Technology and
Science*, vol. 7, no. 11, 2025.

[23] F. T. Liu, K. M. Ting, and Z.-H. Zhou, "Isolation forest," in *Proc. IEEE Int. Conf. Data
Mining*, 2008, pp. 413–422.

[24] S. Yao, J. Zhao, D. Yu, N. Du, I. Shafran, K. Narasimhan, and Y. Cao, "ReAct: Synergizing
reasoning and acting in language models," in *Proc. ICLR*, 2023.

[25] P. Lewis et al., "Retrieval-augmented generation for knowledge-intensive NLP tasks," in *Proc.
NeurIPS*, 2020.

[26] **[AUTHOR]** Withdrawn preprint, arXiv:2511.15755 — cite as it stands at submission time; see
Section II-F.
