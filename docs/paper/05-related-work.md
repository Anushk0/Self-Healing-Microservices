# Related Work and Differentiation

> **Status:** drafted Week 14. §5.1 contains a finding that materially changes this section and
> should be read first.

## 5.1 The closest prior work has been withdrawn — and its withdrawal reason is directly relevant

The project roadmap identified **arXiv 2511.15755**, *"Multi-Agent LLM Orchestration Achieves
Deterministic, High-Quality Decision Support for Incident Response"* (Philip Drammeh, submitted
19 November 2025), as the closest direct competitor, whose differentiation "cannot be skipped."

**That paper has been withdrawn by its author.** The withdrawal notice on the arXiv abstract page
states:

> "A code audit found the multi-agent arm's action list is a source constant, not model output, and
> the scorer reads only that field. All Decision Quality results, and the zero-variance and
> actionability claims derived from them, are withdrawn."

The withdrawn claims were substantial — a reported 100% actionable-recommendation rate for
multi-agent versus 1.7% for single-agent, a novel Decision Quality metric, and zero variance in
multi-agent outputs, across 348 controlled trials.

This changes this section in two ways.

**First, the differentiation target is gone.** Differentiating from a withdrawn paper's *results* is
not meaningful. What remains meaningful — and more useful — is differentiating from its *method*,
because the withdrawal identifies a specific, reproducible way for a multi-agent AIOps evaluation to
be wrong.

**Second, and more importantly, it defines an obligation this paper must discharge.** The defect was
not a coding slip in isolation; it is the natural failure mode of exactly the architecture this
project also uses. A multi-agent pipeline that deliberately makes some stages deterministic will
contain hardcoded action templates. If the metric being reported reads only such a field, the
evaluation measures a constant and any reported superiority over a single-agent baseline is an
artefact. **Any paper presenting a multi-agent-versus-single-agent AIOps comparison after November
2025 should be expected to demonstrate that this did not happen to it.** §5.2 does so explicitly.

## 5.2 Self-audit: is our scored quantity a constant?

This project's remediation actions **are** deterministic templates, by design and by declaration —
`remediation/proposer.py` performs a fault-type → action lookup with no LLM call. Under the standard
the Drammeh withdrawal establishes, that fact alone requires an audit rather than an assurance.

**What is scored is not the action.** The primary reported metric is identification of
`root_cause_service`, and the secondary is `fault_type`. Neither is a constant: both are produced by
signature matching against observed telemetry.

An empirical audit was run and is reproducible. The multi-agent RCA was given five different
telemetry inputs and its scored output recorded:

| Input (service, distinguishing metric) | `fault_type` | `root_cause_service` | Method |
|---|---|---|---|
| payment-service, `jvm_memory_used_bytes = 5.0e8` | `memory-leak` | payment-service | `metric_signature` |
| payment-service, `http_server_requests_error_count = 25` | `auth-key-error` | **auth-service** | `metric_signature` |
| payment-service, `http_client_requests_avg_duration_ms = 5200` | `notification-latency` | **notification-service** | `metric_signature` |
| ledger-service, `hikaricp_connections_active = 10` | `db-lock` | ledger-service | `metric_signature` |
| payment-service, all metrics at baseline | `None` | payment-service | `fallback_unknown` |

Four distinct root causes are produced across five inputs. Decisively, **rows 1–3 are all
payment-service records** and yield three different root-cause services — so the output is driven by
the metric values, not by the identity of the service the record came from, and not by a constant.
Row 5 shows the system declining to name a fault type when nothing matches, rather than emitting a
default.

**A second-order disclosure, made here rather than left for a reader to discover.** Once a fault type
is determined, `root_cause_service` follows from a fixed domain mapping (`FAULT_SERVICE_MAP`), and
the ground-truth labels are derived from that same mapping. Root-cause accuracy is therefore
arithmetically equal to fault-type accuracy for this system — it is not an additional, independent
capability. The defensible claim is narrower than "the system identifies root causes," and should be
stated as: *the system identifies the fault type from symptom-service telemetry alone, and
consequently does not attribute cross-service faults to the service reporting the symptom.* The
second clause is the non-trivial part and is reported separately in the Results.

Note also the asymmetry between arms, which is a property of the designs rather than a flaw in the
comparison: the multi-agent arm reaches `root_cause_service` through a deterministic mapping, whereas
the single-agent baseline emits the service name directly as model output. Both systems output a
service name and are scored identically on whether it is correct; the mechanisms differ, and that
difference is the subject of the comparison rather than a confound in it.

## 5.3 Papers the roadmap named

> **⚠ Requires your verification.** The searches below were run in September 2026. Everything in
> §5.1–§5.2 is verified against primary sources (the arXiv abstract page itself). The items in this
> subsection were surfaced by search and their relevance is assessed from abstracts only — read them
> before citing.

**"VeriOps-Agent" could not be located.** The roadmap names it as the second closest competitor, but
no paper of that title was found. Either the name is inexact, or it is not indexed where the search
reached. Please confirm the correct title/venue — it cannot be cited as-is.

Two papers surfaced that are directly relevant to this project's safety posture and are worth
reading before submission:

- **"When AIOps Become 'AI Oops': Subverting LLM-driven IT Operations via Telemetry Manipulation"**
  (arXiv 2508.06394). Examines AIOps systems that let autonomous LLM agents interpret telemetry and
  take corrective action with minimal human intervention. This is the adversarial case *for* the
  design choices in §3.7–§3.8: if telemetry can be manipulated to steer an LLM, then a pipeline
  whose remediation is deterministic, whose safety rules are evaluated without AI, and whose
  execution cannot proceed without human approval has a materially smaller attack surface than one
  where the model chooses and performs the action. Worth citing as motivation rather than contrast.
- **"Large Language Models for Agentic NetOps and AIOps: Architectures, Evaluation, and Safety"**
  (arXiv 2605.12729). A survey-style treatment of agentic investigation workflows — maintaining
  competing hypotheses and tying action proposals to evidence. Useful for positioning this project's
  "deterministic conclusion, LLM narration" split within a broader taxonomy.

For dataset methodology, RCAEval, Cloud-OpsBench and MicroServo are cited in Dataset Construction
§4.1 as precedent for synthetic fault-injection corpora; those citation slots are also marked for
verification there.

## 5.4 Contemporary LLM-agent RCA systems

> Week 16 lit-review pass. Each entry below was located and its abstract read; claims are attributed
> to the papers rather than asserted. I have **not** read them in full — where a comparison depends
> on methodological detail I could not verify from an abstract, I say so.

### Flow-of-Action (arXiv 2502.08224, WWW '25 Companion — Chinese Academy of Sciences / ByteDance)

An SOP-enhanced LLM multi-agent RCA system. Its motivating observation is close to this project's:
ReAct-style agents suffer hallucinations that "lead to irrelevant actions and directly affect
subsequent results", so the paper imposes Standard Operating Procedures to constrain the LLM at
crucial junctures.

**This is the closest philosophical relative of this work, and the comparison is a convergence rather
than a contrast.** Both conclude that an unconstrained LLM is the wrong thing to put at the centre of
RCA, and both address it by removing the model's authority at the decisive moment. The difference is
what supplies the constraint: Flow-of-Action encodes *expert procedure* (SOPs distilled from SREs),
whereas this system encodes *measurable evidence* (signature thresholds traceable to observed
baselines). SOPs generalise to incidents nobody has thresholds for; thresholds do not depend on
anyone having written the procedure down. A system combining both — SOP-guided exploration with a
deterministic evidential veto — is a natural direction neither paper takes.

### LATS-RCA (arXiv 2605.03505)

Formulates RCA as a reflection-guided tree search (Language Agent Tree Search), with separate log and
metric agents coordinated by a supervisor. Reports **91.3% accuracy on the LO2 benchmark**, and —
importantly — **60–70% in production**, at a cost of roughly **75 API calls, 220K tokens and 13
minutes per incident**.

Two things in that make it the most useful comparator here.

First, the benchmark-to-production drop (91.3% → 60–70%) is independent corroboration of the caution
in §6.2 of the Discussion. A high number on a constructed corpus is not a deployment number, and a
system reporting only the former is reporting the easier question.

Second, the cost comparison is stark and worth stating plainly:

| | LATS-RCA (reported) | This system (measured) |
|---|---|---|
| LLM calls per incident | ~75 | **0.4167** |
| Tokens per incident | ~220K | n/a (self-hosted; ~8.5 s wall clock) |
| Time per incident | ~13 min | **8.5 s** |

The comparison is not like-for-like — different datasets, different fault taxonomies, different
telemetry, and LATS-RCA is doing genuinely more exploratory work — so it must not be presented as
"85× cheaper and therefore better". What it does support is a narrower claim worth making: **for the
specific capability of separating root cause from symptom, a deterministic-first design reaches the
answer without tree search, and the LLM budget is not where that capability comes from.** In this
system the diagnosis is unchanged when the LLM fails entirely (§6.4), which is not a property a
tree-search agent can have.

### GALA / GALA+ (arXiv 2508.12472; arXiv 2608.08968, ASE '26)

Graph-augmented agentic RCA: service dependency graphs bound the agent's exploration, with
trace- and graph-structure-aware scoring for hypothesis generation, producing ranked diagnoses and
"stratified action recommendations".

The dependency-graph approach is a direct alternative solution to *this paper's core problem*. A
service graph knows that payment-service calls notification-service, so a graph-aware agent could in
principle traverse from symptom to cause structurally rather than needing a signature that names the
root cause service. **That is arguably a more general solution than the fixed `FAULT_SERVICE_MAP`
used here**, which encodes the symptom→cause relationship per known fault type and cannot generalise
to an unseen one. This should be conceded in the paper rather than left for a reviewer: the
cross-service capability demonstrated here is real but is achieved by domain encoding, where a graph
method could derive it.

The honest counter is that a graph tells you *what could have caused this*, not *what did* — it
narrows candidates rather than selecting among them — so the two are complementary. Verifying that
characterisation requires reading the papers in full.

### OpsAgent and the multi-agent RCA family

The broader pattern — specialised agents per signal type (logs, metrics, traces, deploy history)
coordinated by a supervisor that correlates across modalities — is well established, including
Microsoft Research's work on LLM agents for RCA (FSE '24 Companion).

This project sits deliberately at the opposite end of that design space. Rather than more agents over
more modalities, it uses **one** telemetry modality and makes the diagnostic step *less* agentic. The
Week 12 comparison suggests that on cross-service faults the binding constraint was not the number of
signals or agents but whether retrieval was permitted to override direct evidence. That is a claim
about one fault class on one dataset, not a general argument against multi-modal agents.

## 5.5 Position of this work

Stated narrowly, so that it is defensible:

1. **A closed loop, not a diagnostic benchmark.** Most comparable work evaluates diagnosis. This
   system additionally validates, gates, executes, and then *verifies by re-observation* whether the
   fault actually cleared — which is what makes false recovery measurable rather than assumed.
2. **Deterministic-first, LLM-narrating.** The conclusion is reached before the model is consulted,
   and the model's prompt has no field capable of changing it. This inverts the common arrangement
   and is what allows the system to degrade to a correct answer when the LLM is unavailable.
3. **Non-bypassable human approval as an architectural property.** Approval is a blocking graph
   interrupt, not a policy — and confidence-gated autonomy was explicitly rejected, with reasons
   (§3.8).
4. **Leakage prevention enforced in code and verified at measurement time**, including a live
   preflight that aborts the evaluation if the guard fails to refuse a real Evaluation incident.
5. **An audited comparison.** Following §5.1, the claim that the multi-agent arm's scored output is
   data-derived rather than constant is demonstrated (§5.2) rather than asserted.
