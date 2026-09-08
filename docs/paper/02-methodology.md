# Methodology

> **Status:** drafted Week 13 from the implemented system. Every mechanism described here
> corresponds to code in this repository and is cited by module path, so any claim in this section
> can be checked against the implementation rather than taken on trust.

## 3.1 System overview

The platform monitors a four-service microservice payment application, detects anomalies, performs
retrieval-augmented root cause analysis with a self-hosted LLM, proposes remediation, validates that
proposal against deterministic safety rules, requires explicit human approval, executes the approved
action against Kubernetes, verifies whether the fault actually cleared, and records the outcome for
future retrieval.

The pipeline is implemented as a LangGraph state machine
(`ai-orchestration/src/ai_orchestration/graph/workflow.py`) with the following stages:

| # | Stage | Node | Implementation character |
|---|---|---|---|
| 1 | Feature extraction | `extract_features` | Deterministic |
| 2 | Anomaly detection | `detect_anomaly` | Statistical (Isolation Forest) |
| 3 | Anomaly interpretation | `interpret` | LLM (optional, narration only) |
| 4 | Memory retrieval | `retrieve_similar_incidents` | Vector search (ChromaDB) |
| 5 | Root cause analysis | `root_cause_analysis` | Deterministic first, LLM narration second |
| 6 | Remediation proposal | `propose_remediation` | Deterministic (no LLM) |
| 7 | Safety validation | `safety_validator` | Deterministic (no LLM, no I/O) |
| 8 | Human approval | `human_approval` | Blocking interrupt |
| 9 | Execution | `execute_action` | Constrained effectful actions |
| 10 | Recovery verification + feedback | `verify_recovery`, `feedback` | Observation + write-back |

A failure at stage 9, **or** a stage-10 verification showing the fault still active, routes back to
stage 6 rather than stage 5 — the diagnosis is retained and a *different* remediation is proposed.
Every such retry re-enters stages 7 and 8, so no retry inherits a prior approval.

## 3.2 The monitored system and its controlled faults

Four Spring Boot services (`auth-service`, `payment-service`, `ledger-service`,
`notification-service`) implement a payment chain: authenticate → debit → notify. A single
`X-Correlation-Id` propagates through all four via an SLF4J MDC filter, so one request is traceable
across service boundaries.

Four faults are injectable at runtime, each disabled by default and individually resettable:

| Fault | Injected on | Symptom measurable on | Primary metric |
|---|---|---|---|
| `auth-key-error` | auth-service | **payment-service** | `http_server_requests_error_count` |
| `memory-leak` | payment-service | payment-service | `jvm_memory_used_bytes` |
| `db-lock` | ledger-service | ledger-service | `hikaricp_connections_active` |
| `notification-latency` | notification-service | **payment-service** | `http_client_requests_avg_duration_ms` |

**Two of the four faults are deliberately cross-service**, and this is the central design choice of
the evaluation rather than an incidental one. For `auth-key-error`, auth-service's own
`/api/auth/validate` returns HTTP 200 even for an invalid token — reporting validity in the response
body — so auth-service's error metrics never move; the HTTP-level symptom is produced by
payment-service's exception handling. For `notification-latency`, payment-service calls
notification-service *synchronously*, so an injected delay surfaces as a payment-side timeout. In
both cases a system that simply blames whichever service reports the anomaly is wrong, and the
results in Section 5 report accuracy on these cases separately for exactly that reason.

## 3.3 Anomaly detection

One Isolation Forest per service (`anomaly/detector.py`), not one shared model, because the
applicable feature set differs by service: HikariCP pool metrics exist only on ledger-service, and
outbound HTTP client metrics only on payment-service. Training a single model across all four would
treat "permanently absent for this service" as signal.

Training data is restricted to non-faulty telemetry (NORMAL and RECOVERY phases) from incidents in
the Memory set only. This restriction is enforced in code, not by convention:
`feature_extractor.load_memory_eligible_baseline_records()` reads the Memory file and has no
parameter that could point it at the Evaluation file.

Scoring uses `-decision_function(x)`, so higher means more anomalous, compared against a
configurable threshold (`ANOMALY_SCORE_THRESHOLD`, default 0.0).

**A documented limitation of this choice**, characterised in Week 10 and reported here because it
bounds what the detection numbers can mean: Isolation Forest *saturates* outside its training range.
Every split threshold the trees learn for a feature lies within the observed training range, so any
value beyond that range follows the same path through every tree and receives an identical score. A
measured sweep on a payment-service detector showed heap values of 50 MB, 153 MB, 500 MB and 2000 MB
all scoring exactly −0.0279. Detection sensitivity therefore does not increase with fault severity
once the value leaves the training range. This affects the statistical layer only; the deterministic
signature matching in Section 3.5 compares against documented thresholds directly and is unaffected.

## 3.4 Retrieval memory

Postmortem prose for each Memory-set incident is embedded into a local ChromaDB collection using
ChromaDB's bundled ONNX embedding model — local, cached after first use, no cloud dependency and no
second model runtime alongside Ollama.

**Evaluation incidents are never indexed.** This is enforced structurally rather than documented:
`chroma_store.build_collection()` never opens the Evaluation file; it additionally loads Evaluation
IDs read-only, checks for overlap *before* writing anything, and re-verifies by direct ID lookup
afterwards. Section 3.9 describes the second enforcement point added for feedback write-back.

## 3.5 Root cause analysis

RCA is deliberately **not** an LLM-first design. The precedence is fixed:

1. **Deterministic fault-signature matching** (`rca/signatures.py`) runs first and wins when it
   matches. Each fault has one documented primary metric and threshold, every threshold traceable to
   an empirically observed value. Signatures for the two cross-service faults are keyed to
   payment-service's telemetry (where the symptom is measurable) but resolve to auth-service /
   notification-service as the root cause via the same `FAULT_SERVICE_MAP` used to build the
   dataset — reused rather than re-derived, so the diagnostic mapping and the ground-truth labels
   cannot drift apart.
2. **Retrieved incidents adjust confidence but can never override the conclusion.** Agreement raises
   confidence to `high`; disagreement retains the signature's conclusion and records the
   disagreement (`determination_method="metric_signature+rag_disagreement"`). Where no signature
   matches, the closest retrieved incident becomes a low-confidence suggestion; where neither is
   available, the fault type is `None` rather than a guess.
3. **The LLM narrates a conclusion already reached.** Its prompt states explicitly that it is not
   being asked to determine the root cause, and its response schema contains no field for one. If
   Ollama is unavailable, times out, or returns malformed JSON, `llm_reasoning` is `None` and the
   deterministic conclusion is unchanged.

## 3.6 Remediation proposal

Remediation is **100% deterministic Python with no LLM call** (`remediation/proposer.py`). A
fault-type → action template is used only when RCA confidence is at least `medium`; a low-confidence
diagnosis always falls back to a non-executable `investigate_manually` proposal regardless of which
fault type was suspected.

When a previous action for the same incident has failed, the proposer walks a fixed escalation
ladder and never re-proposes a category that already failed:

1. targeted fault-configuration reset,
2. `restart_service` (always `risk_level="high"`, because a restart drops in-flight requests),
3. `investigate_manually` — terminal escalation to a human.

Every proposal carries `requires_human_approval: Literal[True]` — a type-level guarantee that the
field cannot be constructed as `False`.

## 3.7 Safety validation

`safety/validator.py` is plain Python with no AI, no network calls and no I/O. It re-checks every
field independently rather than trusting the Pydantic model that produced it, so a bug in that model
cannot bypass review. Rules:

1. `action_category` must be in a whitelist re-declared in the validator.
2. `target_service` must be one of the four known services.
3. **Any `risk_level="high"` action targeting `ledger-service` is rejected outright.**
4. `requires_human_approval` must be `True`.

Rule 3 encodes a domain constraint rather than a generic caution: ledger-service holds financial
transaction state behind a `@Transactional` boundary with pessimistic row locking
(`SELECT ... FOR UPDATE`), so restarting it mid-transaction is materially dangerous in a way that
restarting a stateless service is not. The rule is service-specific by design — high-risk actions on
the other three services are permitted to reach a human, who may approve or reject them.

A proposal failing safety validation is routed to termination and **never reaches a human at all**.
The rationale is that a human approval prompt for an action the system already knows violates a hard
rule invites rubber-stamping; a rule violation is not a judgement call.

## 3.8 Mandatory human approval, and why confidence-gated bypass was rejected

Human approval is implemented with LangGraph's `interrupt()`, which halts graph execution and
returns control to the caller. Resuming requires an explicit `Command(resume=...)` on the same
thread. There is no code path that continues past this node without one.

This is an architectural choice, not a policy statement. A common alternative — auto-approving
actions above some confidence threshold — was explicitly rejected, for three reasons:

1. **Confidence is not calibrated.** The RCA's `confidence` is a deterministic label derived from
   signature match quality and retrieval agreement. It has not been validated as a probability, so
   thresholding it would grant autonomy on the basis of a number whose meaning has not been
   established.
2. **The highest-confidence cases are not the safest ones.** A clean signature match on db-lock
   yields `high` confidence, and its escalation path is a ledger-service restart — the single most
   dangerous action in the system.
3. **It would make the safety property untestable.** "Approval is mandatory" is checkable by
   asserting that no execution occurred without `human_decision == "approved"`; "approval is
   mandatory below a threshold" is not a property that can be asserted at all.

The executor re-checks approval immediately before acting rather than trusting graph routing, and
refuses any decision value other than exactly `"approved"`.

## 3.9 Execution, verification, and feedback

Execution (`execution/`) is the only component that changes the running system. It can perform
exactly three action kinds — an HTTP fault reset, a pod deletion (the Deployment recreates it), or
nothing. There is no general command-execution path, and the Kubernetes client refuses any service
outside the four under study. Three gates are re-checked immediately before acting: approval,
safety, and remediation-attempt budget.

Recovery is **verified by re-observation**, not inferred from the remediation call's own success:
the target service's `/fault-status` and `/actuator/health` are polled after the action. A service
that reports the fault still active after a "successful" remediation is recorded as a *false
recovery*, which is what makes that metric measurable at all.

Completed incidents are written back into retrieval memory in the same postmortem format used for
seed incidents, including failures and rejections — a record that a remediation was tried and did
not work is as useful to future retrieval as a success. Rule 22 (no Evaluation incident may enter
retrieval memory) is enforced at two independent layers, and the store layer **fails closed** if the
Evaluation file cannot be read, because the JSONL loader returns an empty list for a missing file
and would otherwise convert "cannot verify" into "nothing to verify".

## 3.10 The single-agent baseline

The comparison system (`baseline/`) is a separate implementation, not the multi-agent graph with
stages disabled. It imports nothing from the RCA, remediation, or interpretation modules; it issues
one LLM call whose prompt asks for root cause *and* remediation action together.

Everything that is not the diagnostic step is shared by importing the identical node objects:
feature extraction, anomaly detection, retrieval, safety validation, human approval, execution,
verification and feedback. A test asserts node *identity* rather than equivalent behaviour, so the
two arms cannot drift apart as the codebase changes.

**The isolated variable is therefore agent decomposition, not retrieval.** Both arms receive the
same retrieved incidents. Whether retrieval helps is a separate question requiring a memory-ablation
experiment, and is deliberately not confounded with this one.

Two asymmetries are inherent to the designs rather than artefacts of the harness, and are reported
rather than corrected:

- The baseline has **no deterministic fallback**. With the LLM unavailable, the multi-agent pipeline
  still reaches a correct deterministic conclusion; the baseline reaches none.
- The baseline **does not retry**. Re-asking the same model the same question is not comparable to a
  deterministic escalation ladder, so the evaluation compares one attempt per incident for both arms.

## 3.11 Local LLM substitution

The original design named a hosted Claude/OpenAI API. The implemented system uses a self-hosted
model via Ollama. The reasons are rate limits during high-volume evaluation, and data locality —
telemetry and incident records never leave the machine.

Two consequences are carried through the rest of this paper:

1. **"Token cost" is not an applicable metric** for a self-hosted model. Wall-clock inference time
   and LLM call count per incident are reported in its place. This substitution is stated in the
   metric definitions rather than silently applied.
2. **Model capability is a stated variable, not a constant.** The configured model is an environment
   variable; the exact model used for the reported run is recorded in the results artefact's
   environment block. A small local model narrating numeric evidence has been observed to restate
   values imprecisely — which affects only the free-text `llm_reasoning` field and never the
   deterministic conclusion, by construction.

## 3.12 Reproducibility

- Dataset construction, splitting and synthetic generation are seeded (`SPLIT_SEED = 42`) and
  produce identical output from identical input.
- Isolation Forest hyperparameters are fixed and environment-overridable, with `random_state=42`.
- Incident IDs are assigned by a deterministic sort, so the same input set always yields the same
  IDs and therefore the same Memory/Evaluation split.
- The evaluation harness records its full environment — platform, Python version, model, threshold,
  detector inventory, and whether execution was live or dry-run — into the results artefact, so a
  results table can never be read without the conditions that produced it.
