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

## 5.3 Other related work

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

## 5.4 Position of this work

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
