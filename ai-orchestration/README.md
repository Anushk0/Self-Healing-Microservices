# Weeks 7-11 — AI Intelligence Foundation, RCA, RAG, Remediation, Safety, Approval, Execution, Baseline

Week 7: anomaly detection (Isolation Forest), a local LLM interpretation step (Ollama), and a
LangGraph workflow. Week 8 extends that same workflow with an RCA agent, RAG retrieval against
Week 6's ChromaDB Memory collection, and a deterministic remediation-proposal generator - Week 8
**never executes anything**; see "Week 8 architecture" below. Week 9 adds a deterministic Safety
Validator and a genuinely **mandatory** human-approval gate (LangGraph's `interrupt()` - the graph
physically cannot proceed past it without an external `Command(resume=...)`), plus `app.py`, a
Streamlit console for exercising that gate; see "Week 9 architecture" below. Week 10 completes the
pipeline - an approved action is actually executed against the running system, the result is
verified by re-observing the service, a failed fix escalates through a deterministic ladder (each
rung needing its own approval), and the finished incident is written back into retrieval memory with
Evaluation-set incidents hard-refused. Week 11 adds a separate single-agent baseline that shares
every node except the diagnostic step, for the Week 12 comparison.

## Architecture

```
ai-orchestration/
├── src/ai_orchestration/
│   ├── config.py              paths, env vars, per-service feature schema, IF hyperparameters
│   ├── anomaly/                (Week 7)
│   │   ├── models.py           FeatureVector, ModelMetadata, AnomalyResult dataclasses
│   │   ├── feature_extractor.py  raw telemetry -> FeatureVector; Memory-eligible baseline selection
│   │   ├── detector.py          AnomalyDetector: train/save/load/score, one per service
│   │   └── train.py             CLI: trains + persists all 4 services' detectors
│   ├── llm/                    (Week 7, extended Week 8)
│   │   ├── models.py            AnomalyInterpretation dataclass
│   │   ├── ollama_client.py      thin REST client (no SDK), availability check, timeouts
│   │   ├── prompts.py            interpretation prompt (Week 7) + RCA narrative prompt (Week 8)
│   │   └── interpret.py           orchestrates client+prompt, graceful degradation
│   ├── rag/                    (Week 8) - RAG retrieval against Week 6's ChromaDB
│   │   ├── models.py            RetrievedIncident, RetrievalResult (Pydantic)
│   │   └── retriever.py          query construction + dataset_tools.chroma_store.query_similar()
│   ├── rca/                    (Week 8) - root cause analysis
│   │   ├── models.py            MetricAnomaly, RCAEvidence, RootCauseAnalysis (Pydantic)
│   │   ├── signatures.py         deterministic fault-signature matching (documented thresholds)
│   │   └── analyzer.py           ties signatures + RAG + optional LLM narrative together
│   ├── remediation/            (Week 8) - proposals only, never executes
│   │   ├── models.py            RemediationProposal (Pydantic)
│   │   └── proposer.py           deterministic fault_type -> action mapping, no LLM involved
│   ├── safety/                 (Week 9) - zero-AI deterministic gate, checked before any human sees a proposal
│   │   ├── models.py            SafetyCheckResult (Pydantic)
│   │   └── validator.py          plain-Python hard rules - see "Week 9 architecture" below
│   ├── execution/              (Week 10) - the ONLY code that changes the running system
│   │   ├── models.py            ExecutableAction, ExecutionResult, RecoveryVerification
│   │   ├── actions.py           proposal -> one of exactly three mechanisms; no generic command path
│   │   ├── kubernetes_client.py  deliberately tiny: list/delete this project's own pods only
│   │   ├── executor.py           three fail-closed gates, then act
│   │   └── verify.py             re-observe /fault-status + /actuator/health; False Recovery detection
│   ├── feedback/               (Week 10) - the "learning" write-back
│   │   ├── models.py            FeedbackResult
│   │   └── writer.py             postmortem text + rule 22 enforcement (layer 1 of 2)
│   ├── baseline/               (Week 11) - the single-agent comparison arm
│   │   ├── models.py            SingleAgentDiagnosis (incl. parse_issues for hallucination counting)
│   │   ├── prompts.py           one prompt: root cause AND action together
│   │   ├── single_agent.py       one LLM call; imports nothing from rca/ or remediation/
│   │   └── graph.py              shares every non-diagnostic node with the multi-agent graph
│   └── graph/
│       ├── state.py              WorkflowState (explicit TypedDict)
│       └── workflow.py            the LangGraph itself - see "The graph" below
├── tests/                      182 pytest tests (see "Testing")
├── models/                     trained IsolationForest + metadata per service (gitignored, generated)
├── app.py                      (Week 9) Streamlit human-approval console - see "Running the Streamlit console"
├── requirements.txt
└── pyproject.toml
```

Reuses Week 5/6 code directly (`telemetry`, `dataset_tools`) via the same `sys.path` insertion
pattern `dataset-tools/src/dataset_tools/generate.py` already established - no duplication of
JSONL loading, phase-reconstruction, or ChromaDB setup logic. `rag/retriever.py` calls
`dataset_tools.chroma_store.query_similar()` directly rather than opening a second collection.

## Feature schema

One ordered feature list **per service**, not one shared list - some curated telemetry metrics are
only meaningful for specific services (verified against real telemetry, same standard
`dataset-tools/config.py`'s `FAULT_SERVICE_MAP` uses):

| feature | auth-service | payment-service | ledger-service | notification-service |
|---|:---:|:---:|:---:|:---:|
| jvm_memory_used/committed/max_bytes | ✓ | ✓ | ✓ | ✓ |
| http_server_requests_count/error_count/avg_duration_ms | ✓ | ✓ | ✓ | ✓ |
| process_cpu_usage / system_cpu_usage | ✓ | ✓ | ✓ | ✓ |
| http_client_requests_count/avg_duration_ms | | ✓ | | |
| hikaricp_connections_active/idle/pending/acquire_seconds_avg | | | ✓ | |

`http_client_*` only applies to payment-service (the only service that makes outbound calls);
`hikaricp_*` only applies to ledger-service (the only service with a DB connection pool) - see
`telemetry/collector.py`'s `parse_metrics()` comments. Training one Isolation Forest per service
on only its own applicable features avoids treating "always None for this service" as a
signal, and lets each service have its own baseline distribution (JVM baseline ranges differ
significantly by service).

`extract_features(record)` returns `None` (never a fabricated/imputed vector) when the service is
unknown, metrics collection failed that cycle, or any of that service's *own applicable* metrics
is missing - handled identically at training and inference time, so the two can never disagree
about what counts as valid.

## Training data: Memory-eligible baseline telemetry only

**Never trains on Evaluation-set incidents; the separation is enforced in code, not just
documented** (same shape as `dataset-tools/chroma_store.py`'s ChromaDB leakage prevention):

- `feature_extractor.load_memory_eligible_baseline_records()` reads incidents from **only**
  `dataset-tools/data/memory/memory_incidents.jsonl` - the Evaluation file is never opened by
  anything that feeds training, and there is no `evaluation_incidents` parameter on this function
  at all (impossible to point it there by construction).
- Only **real** incidents' `experiment_id` are used to find eligible raw experiments (synthetic
  incidents have no raw telemetry behind them - they're generated dicts, see
  `dataset-tools/synthetic_model.py` - so they can never contribute a training row).
- Within an eligible experiment, only **NORMAL and RECOVERY** phase telemetry is used (both
  represent non-faulty system behavior; RECOVERY = fault already reset).
- See `tests/test_baseline_selection.py` for the regression tests proving this (only
  Memory-associated experiments contribute records; synthetic incidents contribute none;
  experiments absent from Memory contribute nothing; FAULT_ACTIVE-phase telemetry is excluded
  even from otherwise-eligible experiments).

Right now this yields **42-45 training samples per service**, from the 4 real experiments that
happen to have landed in the Memory set (out of 16 real experiments total, 20/68 incidents in
Memory) - see "Known limitations" below for what this small sample size means in practice.

## Model training and persistence

```bash
cd ai-orchestration
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .

python -m ai_orchestration.anomaly.train
```

One `IsolationForest` per service, saved to `models/<service>/model.joblib` +
`models/<service>/metadata.json`. Metadata records: feature schema version, ordered feature names,
every hyperparameter used, training timestamp, sample count, and the exact list of source
experiment IDs (an audit trail - see `ModelMetadata`).

**Reproducible configuration** - all env-overridable, defaults in `config.py`:

| variable | default | meaning |
|---|---|---|
| `AI_ORCHESTRATION_IF_N_ESTIMATORS` | `100` | Isolation Forest tree count |
| `AI_ORCHESTRATION_IF_CONTAMINATION` | `0.1` | expected anomaly fraction (see below for why not `"auto"`) |
| `AI_ORCHESTRATION_IF_RANDOM_STATE` | `42` | reproducibility, matches the project's existing seed convention |
| `AI_ORCHESTRATION_IF_MAX_SAMPLES` | `auto` | sklearn's per-tree subsample size |
| `AI_ORCHESTRATION_ANOMALY_THRESHOLD` | `0.0` | configurable decision boundary on top of the trained model, without retraining |

**Feature-schema compatibility is checked, not assumed**: `AnomalyDetector.load()` compares the
persisted model's `feature_schema_version` + `feature_names` against the currently configured
schema and raises `FeatureSchemaMismatchError` if they differ - a schema change (e.g. a new metric
added to a service in a future week) makes old models refuse to load rather than silently
misaligning feature columns and producing a meaningless prediction.

## Inference

`AnomalyDetector.score(feature_vector)` returns an `AnomalyResult` with (at minimum, per Week 7's
spec): `timestamp`, `service`, `anomaly_score`, `is_anomaly`, `relevant_feature_values` (the actual
metric values that produced the score), and `model_version`/`feature_schema_version`. `status` is
one of `"scored"`, `"insufficient_data"` (no valid feature vector could be built), or `"no_model"`
(no trained detector for that service) - the caller always gets a structured result, never an
exception, for these expected non-error conditions.

`anomaly_score = -decision_function(x)` (sklearn's own convention is inverted so *higher* here
means *more anomalous*, matching the intuitive meaning of the field name); `is_anomaly = score >
threshold`, threshold configurable per above.

## Ollama integration

No cloud API, no SDK - `llm/ollama_client.py` talks to Ollama's local REST API
(`http://localhost:11434` by default) directly via `requests`.

| variable | default |
|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` |
| `OLLAMA_MODEL` | `llama3.2:1b` (see below) |
| `OLLAMA_TIMEOUT_SECONDS` | `30` |

**Model choice**: CLAUDE.md names `llama3.1:8b` as the project's initial candidate local model.
This development machine had only ~8.8GB free disk at the start of Week 7, and `llama3.1:8b`'s
model blob alone is ~4.7GB - risky to pull outright. `llama3.2:1b` (1.3GB) was installed and used
for all live verification instead, and is the configured default. To switch to the originally
named model once disk space allows:
```bash
ollama pull llama3.1:8b
export OLLAMA_MODEL=llama3.1:8b
```
No other code change is needed - the model name is the only thing that changes.

**Graceful degradation, not a hard dependency**: `OllamaClient.is_available()` (GET `/api/tags`,
3s timeout) is checked before every interpretation attempt; `generate()` raises
`OllamaUnavailableError` on any connection error or timeout, which `interpret.py` catches and
turns into a structured `AnomalyInterpretation(status="llm_unavailable", ...)` - never an
exception that would crash the graph. Malformed/incomplete JSON from the model is handled the same
way (`status="llm_error"`). **The LLM never executes anything** - Week 7 only ever produces a
structured interpretation; no remediation action is taken based on it (per the explicit Week 7
scope boundary - that's Week 8+).

**Only real evidence goes into the prompt** (`llm/prompts.py`): the exact `service`, `timestamp`,
`anomaly_score`, `threshold`, and `relevant_feature_values` from the `AnomalyResult` that already
went through the Isolation Forest - nothing invented, and the prompt explicitly instructs the
model not to reference any metric not listed.

### Setup used for live verification

```bash
brew install ollama
ollama serve &                 # or: brew services start ollama
ollama pull llama3.2:1b
```

## The graph

```
raw telemetry record
        |
  extract_features
        |
  detect_anomaly
        |
    (decision)
   /          \
normal       anomaly
  |             |
  |        interpret (Ollama)
  |             |
  |     retrieve_similar_incidents (ChromaDB, Memory-set only)
  |             |
  |     root_cause_analysis (deterministic signature match + RAG cross-check + optional LLM narrative)
  |             |
  |     propose_remediation (deterministic - no LLM, never executes)
   \           /
     finalize
        |
       END
```

Built with real LangGraph (`langgraph.graph.StateGraph`), explicit typed state
(`graph/state.py`'s `WorkflowState`), and a real conditional edge
(`add_conditional_edges` on `_route_after_detection`) - not a fake linear stand-in. **The LLM is
only ever invoked on the anomaly branch, and RAG/RCA/remediation only ever run on the anomaly
branch too** - a normal reading never calls Ollama, never queries ChromaDB, and never produces an
RCA or remediation proposal (verified in `tests/test_workflow.py::test_workflow_normal_path_never_calls_llm`,
which asserts the mock LLM client is never called *and* that a `retrieve_similar_incidents` spy
that raises on any call is never triggered).

`build_graph(detectors, ollama_client)` takes both dependencies as parameters rather than
constructing them internally - models are loaded once (`load_all_detectors()`) and reused across
every `graph.invoke()` call, and tests can inject fakes/mocks for both.

The final `result` dict (present on every path, not just the anomaly one) contains:
`correlation_id`, `incident_id`, `service`, `timestamp`, `decision`, `telemetry_evidence` (the raw
metrics/fault/collection_error), the full `anomaly_result`, `llm_interpretation` (Week 7),
`retrieval`, `root_cause_analysis`, `remediation_proposal` (Week 8 - all three `None` on the
normal/insufficient_data/no_model paths), and `limitations` (plain-text notes for any degraded
condition across the whole pipeline).

## Week 8 architecture: RCA, RAG, and remediation proposals

### RCA design (`rca/`)

`RootCauseAnalysis` (Pydantic, `rca/models.py`) is never solely reliant on free-form LLM text.
Precedence, deliberately in this order:

1. **Deterministic fault-signature matching** (`rca/signatures.py`) always runs first and always
   wins when it matches. Each of the four known faults has one documented "primary metric" and
   threshold (reusing `dataset_tools.incidents._primary_metric_key` - Week 6's own mapping, not
   re-derived), with every threshold traceable to a real, documented value (`OBSERVED_BASELINE_RANGES`
   for JVM memory, empirically-verified real baselines of 0 for HikariCP/error counts, a value well
   below the real `client_timeout_ms` for notification latency). This is exactly what distinguishes
   symptom from root cause for the two cross-service faults: `auth-key-error`'s and
   `notification-latency`'s signatures are both keyed to **payment-service's own telemetry**
   (where the symptom is measured) but resolve to **auth-service**/**notification-service** as
   `suspected_root_cause_service` via `dataset_tools.config.FAULT_SERVICE_MAP` (reused, not
   duplicated) - see `tests/test_signatures.py` and `tests/test_rca_analyzer.py` for the explicit
   symptom-vs-root-cause assertions.
2. **RAG-retrieved incidents can only raise/lower confidence, never override step 1.** If
   retrieved incidents' `fault_type` metadata agrees with the signature match, confidence rises to
   `"high"`. If they disagree, the signature's conclusion is kept and the disagreement is recorded
   in `evidence_summary` (`determination_method="metric_signature+rag_disagreement"`) - "the agent
   must not blindly trust retrieved incidents" per the Week 8 spec. When no signature matched at
   all, the single closest retrieved incident is used as a *low-confidence* suggestion instead
   (`"rag_only"`); with neither a signature match nor useful retrieval, `suspected_fault_type` is
   `None` and the root cause defaults to the symptom-visible service itself
   (`"fallback_unknown"`) - never a fabricated guess.
3. **The LLM only narrates the conclusion already reached by 1-2** - it is never asked to
   determine the root cause, and its response schema has no field for one (see
   `llm/prompts.py::build_rca_reasoning_prompt`, which explicitly instructs "you are NOT being
   asked to determine the root cause yourself"). Its two-key JSON response
   (`observed_evidence`/`inference`) implements the "OBSERVED EVIDENCE / INFERENCE" split the spec
   asks for; there is no `recommendation` key at all, because remediation is never LLM-authored
   (see below). Degrades identically to Week 7's `interpret_anomaly()` - unavailable/timeout/
   malformed JSON all just leave `llm_reasoning=None`, never an exception, and never change the
   deterministic conclusion (verified: `test_llm_reasoning_is_none_when_ollama_unavailable`
   explicitly asserts `suspected_root_cause_service` is unaffected).

### RAG design (`rag/`)

`rag/retriever.py` calls `dataset_tools.chroma_store.query_similar()` directly - no second
ChromaDB collection, no re-embedding. `build_query_text()` builds a natural-language-style query
(deliberately similar in shape to the postmortem prose actually indexed) from the anomaly's
service, its most notable metric values, and Week 7's `llm_interpretation.abnormal_summary` when
available. Results become typed `RetrievedIncident`s (incident ID, distance, fault type, root
cause/symptom service, severity, data source, and a postmortem excerpt) inside a `RetrievalResult`
with an explicit `status` (`"retrieved"` / `"empty"` / `"collection_unavailable"`) - never a bare
exception reaching the graph.

### Remediation design (`remediation/`)

`remediation/proposer.py` is **100% deterministic Python - no LLM call at all**. This is
deliberate: the project's own rules already say to prefer deterministic structured data over
free-form AI output, and remediation is exactly the place where that matters most. A
`RemediationProposal` (Pydantic, `remediation/models.py`) is returned for every anomaly, with
`requires_human_approval: Literal[True] = True` - a type-level guarantee (Pydantic rejects any
attempt to construct one with `False`), citing CLAUDE.md's permanent rule 21. **Nothing in this
module calls Kubernetes, a fault-injection endpoint, or any other side-effecting API** - a
`RemediationProposal` is inert data (`tests/test_remediation_proposer.py::test_proposal_has_no_executable_side_effects`
checks it exposes no `execute`/`run`/`apply`/`invoke_*`-named attribute at all). Fault-name lookup
templates exist for the four known faults, but are only used when the RCA's own `confidence` is at
least `"medium"` - a `"low"`-confidence RCA (regardless of which `fault_type` was merely suspected)
always falls back to a generic, low-risk `"investigate_manually"` proposal instead, per "do not
hardcode the final answer solely based on fault names."

## Week 9 architecture: Safety Validator and mandatory Human Approval

### Safety Validator design (`safety/`)

`safety/validator.py`'s `validate_safety()` is **plain Python - zero AI, zero LLM calls, zero
network calls** (CLAUDE.md rule 20: "Safety validation must remain deterministic"). It re-checks
every field of a `RemediationProposal` independently rather than trusting `RemediationProposal`'s
own Pydantic validation - defense in depth, so a future bug in that model (or a hand-constructed
dict bypassing it) can't silently skip safety review:

1. `action_category` must be in a fixed whitelist re-declared in this module (mirrors, but doesn't
   import, `remediation/models.py`'s `ActionCategory` Literal).
2. `target_service` must be one of the project's four known services.
3. **A `risk_level="high"` proposal targeting `ledger-service` is rejected outright.**
   `ledger-service` holds financial transaction state behind a pessimistic-locking, multi-step
   `@Transactional` boundary (`LedgerService.debit()`/`credit()`, Week 2) - exactly what the Week 2
   roadmap flagged as "what makes 'just restart the pod' dangerous later (mid-transaction state)".
   This directly implements the Week 9 roadmap's example rule, "never allow a destructive action on
   ledger-service."
4. `requires_human_approval` must be `True` (re-checked here even though it's already
   `Literal[True]` at the type level - the same defense-in-depth reasoning as rule 1).

A violation is collected (all violations, not just the first) into a `SafetyCheckResult` with
`passed=False` and a human-readable `reason` + `violated_rules` list. **A failed check routes
straight to `finalize` - `human_approval` is never even reached** (Week 9 roadmap: "a rule
violation shouldn't even reach the human"; see `_route_after_safety_check` in `graph/workflow.py`).

Honest limitation, disclosed rather than hidden: the real `remediation/proposer.py` templates
(Week 8) only ever assign `risk_level="medium"` or `"low"`, never `"high"` - so under today's
actual pipeline, rule 3 above is a safety net for a future, more aggressive proposer (or a
different LLM-driven one) rather than something that fires in the current demo. It is exercised
directly in `tests/test_safety_validator.py` (hand-built proposals) and, at the graph level, in
`tests/test_workflow.py::test_workflow_safety_rejection_skips_human_approval_entirely` (which
monkeypatches `propose_remediation` to return a deliberately unsafe proposal, since the real
proposer can't produce one today) - see "Live verification performed" for why this specific branch
was **not** exercised live through the Streamlit UI, unlike everything else in this section.

### Mandatory Human Approval design (`graph/workflow.py`)

A proposal that passes the Safety Validator reaches `human_approval`, which calls LangGraph's
`interrupt()` with a payload of `{remediation_proposal, root_cause_analysis, safety_check}`. This
is what makes approval **mandatory** rather than merely documented: `interrupt()` genuinely halts
graph execution mid-run (raises internally, caught by LangGraph's runtime) and returns control to
the caller with `"__interrupt__"` in the result - there is no code path that lets the graph
continue past this node on its own. Resuming requires an explicit
`graph.invoke(Command(resume={"decision": "approved" | "rejected", "reason": ...}), config)` call
using the *same* `thread_id` the paused run started with.

This requires a checkpointer to be compiled into the graph (LangGraph's own constraint - `interrupt()`
needs somewhere to persist the paused state). `build_graph()` now always compiles with one
(`MemorySaver` by default, injectable for a future durable backend - see "Known limitations"),
which means **every** `.invoke()` call - even ones that never reach `human_approval` - must now
pass a `thread_id` via `config={"configurable": {"thread_id": ...}}`; this was a real, mechanical
change to every existing test call site in `test_workflow.py` (see `_run_to_completion()`/
`_thread_config()` helpers there), not just new Week 9 tests.

`human_decision`/`human_decision_reason` land in `WorkflowState` and flow into `finalize_node`'s
`result` exactly like every other field; a rejection is recorded as a `limitations` entry
("A human operator rejected the proposed remediation...") rather than silently dropped, and -
because nothing downstream of `finalize` exists yet (Week 10 is where execution happens) - a
rejection today simply means the run ends with no action taken, which is the correct behavior for
a step that has no executor yet to hand a rejection to.

## Week 10 architecture: execution, recovery verification, retry, feedback

### Execution (`execution/`)

`execution/executor.py` is **the only code in this project that changes the running system**. Three
gates are re-checked immediately before acting, rather than trusted from graph routing - "the graph
shouldn't have called us" is not a safety property; refusing when the preconditions are not literally
present in the handed-in state is:

1. `human_decision` must be exactly `"approved"` (not `"approve"`, not `"APPROVED"` - tested).
2. `safety_check["passed"]` must be True. **A human approval can never override a safety rejection.**
3. The incident must not have exhausted `config.MAX_REMEDIATION_ATTEMPTS`.

A refusal is an `ExecutionResult(status="refused")`, never an exception and never a silent success.
`execution/actions.py` resolves a proposal into one of exactly three mechanisms - `http_fault_reset`
(POST to the service's own reset endpoint, path read from Week 5's `telemetry.experiment.FAULTS` so
it can never drift from what the Java services expose), `restart_pod` (delete the pod; the
Deployment recreates it), or `no_op`. There is no generic "run a command" path anywhere.
`execution/kubernetes_client.py` is deliberately tiny and refuses any service outside this project's
four, so it cannot touch Postgres or Prometheus even if called incorrectly.

`AI_ORCHESTRATION_EXECUTION_DRY_RUN=true` resolves and logs the action without performing it.

### Recovery verification (`execution/verify.py`)

Re-observes the target service's own `/fault-status` and `/actuator/health` (via Week 5's
`telemetry.collector` functions) rather than treating the remediation call's own 200 as proof. This
is what makes Week 12's Recovery Success Rate measurable, and `status="fault_still_active"` **is**
Week 12's False Recovery case. It retries with an interval, because a restarted pod needs time to
come back - concluding failure instantly would measure the delay, not the outcome.

### The failed-fix retry loop (the Week 8 roadmap item, deferred here)

Week 8 specified this loop but could not build it before an executor existed. It routes back to
`propose_remediation`, **not** to RCA, so a retry tries a different fix rather than re-diagnosing.
The escalation ladder in `remediation/proposer.py` is deterministic and strictly ordered:

1. the targeted fault-configuration reset,
2. `restart_service` (always `risk_level="high"` - restarting drops in-flight requests),
3. `investigate_manually` - a terminal escalation to a human, deliberately *not* an ever-more
   destructive third action.

`failed_actions` is passed into the proposer, so a category that already failed is never proposed
again. A retry is triggered by execution failure **or** by a verified-false recovery (an action that
"succeeded" while the fault is still active) - treating only the former as failure would let a reset
that changed nothing look like a fix. Crucially, **each retry re-enters the Safety Validator and
raises a fresh `interrupt()`**: a new action never inherits the approval given for a different one.

This also made Week 9's ledger-service rule reachable in the real pipeline for the first time. A
db-lock incident whose connection-release reset fails escalates to "restart ledger-service", which
is `risk_level="high"` on `ledger-service` - exactly the case the Safety Validator refuses, before
any human is asked. Week 9 documented that rule as a safety net nothing could then trigger; it is
now exercised by the system's own escalation path
(`test_db_lock_escalation_produces_the_proposal_the_safety_validator_must_refuse`).

### Feedback write-back (`feedback/`) and CLAUDE.md rule 22

The finished run is turned into the same kind of postmortem prose Week 6 indexed and written into
the same collection, so later incidents retrieve from things this system actually did. Runs are
written **honestly**: a failed remediation, a human rejection, or an unverified recovery is recorded
as such, never rewritten into a success - "this was tried here and did not work" is at least as
useful to a future retrieval.

**Rule 22 ("Never allow Evaluation Set incidents into RAG memory") is enforced twice, independently**,
because a silent leak here would invalidate every number in Week 12 *and be invisible* - the pipeline
would look better, not worse:

- `feedback/writer.py` refuses to build a write for any run whose `incident_id` is in the Evaluation
  set, before any text is built or embedded.
- `dataset_tools.chroma_store.add_pipeline_run_incident()` refuses again by source ID, refuses any
  ID not carrying the `run-` prefix, and **fails closed if the Evaluation file cannot be read** -
  because `ingest.read_jsonl` returns `[]` for a missing file, which would otherwise silently turn
  "I cannot check" into "nothing to check".

All collection writes go through `chroma_store`, so leakage enforcement lives in exactly one module.
`validate_no_leakage()` now distinguishes a learned `run-` incident from an unexplained ID, without
weakening the Evaluation check. Set `AI_ORCHESTRATION_FEEDBACK_ENABLED=false` for Week 12 so
retrieval memory stays frozen and identical for both arms.

### MTTR

Measured from `detected_at` (stamped the moment an anomaly is flagged, so time spent waiting for a
human is inside MTTR - it genuinely is part of time-to-repair) to execution completion, per the
Week 10 roadmap. Whether that execution *worked* is recorded separately in
`recovery_verification.verified`, so Week 12 can compute both a raw MTTR and a recovery-only MTTR
without either being baked in here. A negative interval is dropped and logged rather than recorded.

## Week 11 architecture: the single-agent baseline

`baseline/` is a genuinely separate implementation - `baseline/single_agent.py` imports nothing from
`rca/`, `remediation/`, or `llm/interpret.py`. One prompt, one LLM call, producing root cause *and*
action together, where the multi-agent arm uses an interpretation step, deterministic signature
matching, a RAG cross-check that cannot be overridden, and a separate deterministic proposer.

Everything that is **not** the diagnostic step is shared by importing the identical node objects
(`baseline/graph.py`): detection, retrieval, Safety Validator, human approval, execution,
verification, feedback. `test_both_arms_share_every_non_diagnostic_node` asserts node *identity*, so
the two arms cannot drift apart as later weeks change downstream code.

**The isolated variable is therefore agent decomposition, not retrieval** - both arms get the same
RAG-retrieved incidents. Whether RAG helps is a separate question (the roadmap's Week 16 memory
ablation), deliberately not confounded with this one.

Three properties worth naming for the paper:

- **The baseline's failure modes are recorded, not repaired.** A hallucinated service, an invented
  action category, or an out-of-set fault type each produce a `parse_issues` entry, so Week 12 can
  count them. An invented action is coerced to `investigate_manually` so it cannot execute, and the
  coercion itself is recorded.
- **It is subject to identical downstream rules.** Its proposals are still `requires_human_approval:
  Literal[True]`, still validated by the Safety Validator, and a baseline `restart_service` proposal
  is still `risk_level="high"` - so a baseline proposal to restart ledger-service is refused exactly
  like a multi-agent one.
- **It has no deterministic fallback, by design.** With Ollama unavailable the multi-agent pipeline
  still reaches a correct deterministic conclusion; the baseline reaches none
  (`status="llm_unavailable"`). That asymmetry is a real property of single-agent designs, so it is
  reported rather than hidden behind a backstop that would make the comparison meaningless.
- **It does not retry.** Re-asking the same model the same question is not comparable to the
  multi-agent deterministic ladder, so Week 12 must compare a single attempt per incident for both
  arms to stay fair (`_route_after_verification_no_retry`).

## Week 12 architecture: the evaluation harness

`evaluation/` runs every Evaluation-set incident through both arms and records everything. Four
design decisions carry the methodological weight:

### Replaying an incident without leaking the answer (`evaluation/replay.py`)

A stored incident becomes the one raw telemetry record a monitor would have seen: the **symptom**
service's **fault-window** metrics. Two consequences are deliberate. The pipeline is handed
payment-service telemetry for `auth-key-error` and `notification-latency` and must still name a
different service - the capability under test. And **the self-reported fault flag is withheld**
(`fault: None`): every service exposes `/fault-status`, which for a same-service fault is
effectively the answer key, so including it would let either arm "diagnose" by reading a debug
endpoint. Ground-truth labels are cross-checked against `FAULT_SERVICE_MAP`, so a mislabelled
incident fails loudly rather than silently scoring every arm wrong.

### The simulated operator (`evaluation/operator.py`)

Approval stays mandatory - the graph still interrupts and still cannot proceed without a resume.
What is simulated is who supplies the decision. The default policy **approves everything that
reaches it**, and the alternative was rejected on purpose: approving only correct diagnoses would
gate execution on ground truth, so recovery metrics would measure "how well does remediation work
when the diagnosis is already known to be right". The cost is stated rather than hidden - under a
permissive policy Approval Accuracy is definitionally equal to diagnostic accuracy and is not an
independent measurement.

### Preflight: the leakage guard is proven at measurement time (`evaluation/harness.py`)

Before any number is recorded, the harness asserts feedback write-back is disabled *and* attempts a
real write-back of an actual Evaluation incident, aborting the run unless it is refused. Unit tests
prove the guard existed when they were written; this proves it works against the real Evaluation
file at the moment of measuring. The result is recorded in the run artefact.

Replay mode also forces dry-run execution, for a methodological reason rather than caution: a
replayed incident has no active fault, so "resetting" it would trivially succeed and every incident
would look recovered. **Recovery Success Rate and False Recovery Rate are therefore not measurable
by replay** and are reported as unmeasurable with a reason - never as a meaningless 100%.

### Metrics that refuse to fabricate (`evaluation/metrics.py`)

Two rules: an unmeasured metric is `None` with a reason, never a number; and an abstention is scored
separately from a wrong answer, so a system that declines to guess is not conflated with one that
guesses badly. Accuracy is reported both over all incidents (the honest headline) and over answered
incidents only, alongside the abstention rate. **Cross-service accuracy is reported separately**,
because an aggregate can hide total failure on exactly the cases the dataset was built around, and
"blamed the symptom service" counts that specific failure mode directly.

Hallucination rate uses an automated, deliberately conservative proxy: a reasoning chain citing a
*known* metric name that was absent from the evidence it was given. Unrecognised tokens are ignored,
so ordinary prose cannot inflate it and the reported figure is a lower bound.

### `evaluation/prepare.py` - training where there is no captured telemetry

Week 7's normal path trains on raw per-cycle telemetry from live experiments. On a machine with no
cluster there is none, so this module trains the same models from Memory-set incidents' own
**NORMAL and RECOVERY phase metrics**. The Memory/Evaluation boundary is preserved exactly - the
Evaluation file is not imported, not opened, and there is no parameter that could redirect it - and
no fault-window data is used, so a detector still only ever learns what normal looks like. The
honest difference: these are per-incident phase averages rather than raw observations, so the
variance structure is smoother than live telemetry's.

## An audit the literature now requires

arXiv 2511.15755 (Drammeh, Nov 2025), which this project's roadmap named as its closest competitor,
was **withdrawn by its author**: "a code audit found the multi-agent arm's action list is a source
constant, not model output, and the scorer reads only that field."

That is the natural failure mode of a deliberately-deterministic multi-agent pipeline - which this
is. `remediation/proposer.py` *is* a hardcoded template lookup, by design and by declaration. So the
property is asserted rather than argued: `tests/test_scored_output_is_not_constant.py` shows that
three payment-service records differing only in their metrics produce three *different* root-cause
services, that the conclusion is reached with no LLM call at all, and that the system abstains
rather than emitting a default when nothing matches. See `docs/paper/05-related-work.md` §5.2,
including the second-order disclosure that root-cause accuracy is arithmetically equal to fault-type
accuracy for this system.

## How Weeks 10-11 feed Week 12

- Both arms are compiled the same way (`build_graph()` / `build_baseline_graph()`, identical
  signatures) and produce an **identical result shape**, asserted by
  `test_baseline_result_is_shape_compatible_with_the_multi_agent_result` - so the evaluation
  harness can loop over both without special-casing either. `result["single_agent_diagnosis"]` is
  non-None for exactly one of them, which is the unambiguous marker of which arm produced a result.
- Every Week 12 metric now has a field behind it: `root_cause_analysis.suspected_root_cause_service`
  vs. the ground-truth label (Accuracy/Precision/Recall/F1), `mttr_seconds` (MTTR),
  `recovery_verification.verified` (Recovery Success Rate), `recovery_verification.status ==
  "fault_still_active"` (False Recovery Rate), `detected_at` (Detection Latency),
  `safety_check.violated_rules` (Unsafe Action Prevention), `human_decision` (Approval Accuracy),
  and `single_agent_diagnosis.parse_issues` (a countable component of Hallucination Rate).
- **Set `AI_ORCHESTRATION_FEEDBACK_ENABLED=false` for the evaluation run.** Otherwise earlier
  evaluation incidents would change what later ones can retrieve, and the comparison would not be
  reproducible. The rule 22 guard would still prevent Evaluation incidents from being written, but
  freezing memory is what makes the two arms see *identical* retrieval.
- Compare **one attempt per incident** for both arms - the baseline does not retry by design (see
  "Week 11 architecture"), so including the multi-agent retry ladder in an aggregate would compare
  different numbers of attempts.
- `MemorySaver` is in-process only (see "Known limitations"); a long-running evaluation harness in
  one process is fine, but a durable checkpointer (`SqliteSaver`/`PostgresSaver`) is a drop-in swap
  via the `checkpointer` parameter if runs need to survive a restart.

## Running the Streamlit console

```bash
cd ai-orchestration && pip install -r requirements.txt && streamlit run app.py
```

Loads any real trained detectors from `models/` (likely none on a machine with no captured
telemetry yet - see Week 5/6's gitignored generated artifacts) and lets you train a small,
clearly-labeled **synthetic, in-memory-only** demo detector per service directly from the sidebar,
purely so the mandatory-approval flow can be exercised without a live Kubernetes deployment. Pick a
built-in sample (or paste a raw telemetry JSON record), run the pipeline, and - if it reaches
`human_approval` - the page shows the RCA hypothesis, its reasoning chain, the retrieved evidence,
the Safety Validator's verdict, and the proposed action, with Approve/Reject buttons that resume
the paused graph via `Command(resume=...)`. Nothing in `app.py` executes a remediation action -
see its module docstring.

## Running a local investigation

Requires: a trained model per service (`python -m ai_orchestration.anomaly.train`), a built
ChromaDB Memory collection (`cd dataset-tools && python -m dataset_tools.chroma_store`), and
optionally a running Ollama server (`ollama serve`, model pulled) - the pipeline still produces a
full, deterministic result without Ollama, just with `llm_interpretation`/`llm_reasoning` set to
`None`/`"llm_unavailable"`.

```python
from ai_orchestration.graph.workflow import build_graph, load_all_detectors
from ai_orchestration.llm.ollama_client import OllamaClient

graph = build_graph(load_all_detectors(), OllamaClient())

raw_record = {...}  # one record exactly as telemetry/collector.py's collect_service() produces
final_state = graph.invoke({"raw_record": raw_record, "incident_id": None})
print(final_state["result"])  # decision, anomaly_result, retrieval, root_cause_analysis, remediation_proposal
```

## Testing

```bash
cd ai-orchestration && source .venv/bin/activate && python -m pytest tests/ -v
```

182 tests (101 through Week 9 + 81 new across Weeks 10-11), mocking Ollama (`responses` for the HTTP client,
`unittest.mock.MagicMock` for higher-level tests) and using a real, isolated local ChromaDB
(`tmp_path`-scoped, same pattern as `dataset-tools/tests/test_chroma_store.py`) for RAG tests -
never the project's real Memory collection, and requiring no running Kubernetes cluster, payment
system, or Ollama server:

- `test_feature_extractor.py` (8), `test_baseline_selection.py` (4), `test_detector.py` (10),
  `test_ollama_client.py` (10), `test_interpret.py` (7) - unchanged from Week 7.
- `test_signatures.py` (9, new) - each of the four faults' signature match, including the two
  symptom-vs-root-cause cross-service cases, ranking multiple simultaneous matches, and documented
  baseline references.
- `test_rca_analyzer.py` (12, new) - all four faults' end-to-end RCA identification, RAG
  agreement/disagreement behavior, `rag_only`/`fallback_unknown` fallback, and LLM
  narrative graceful degradation.
- `test_rag_retriever.py` (9, new) - real isolated-ChromaDB retrieval, Evaluation-incident
  exclusion (both "never built into the collection" and "even if a match somehow returned one,
  filtered before reaching RCA"), empty/no-result, and malformed-match handling.
- `test_remediation_proposer.py` (13, new) - a proposal for each of the four faults, the
  low-confidence fallback, Pydantic validation of risk/action-category/`requires_human_approval`,
  and the no-executable-attributes check.
- `test_workflow.py` (10, 3 new + 7 extended) - normal path still reaches none of Week 8's new
  nodes; anomaly path reaches RCA, retrieval, and remediation; Ollama-unavailable degrades the
  whole extended pipeline gracefully (RCA/remediation still populate, just without a narrative);
  RAG agreement raises RCA confidence to `"high"` end-to-end through the compiled graph. Extended
  this week: every existing call site now passes a `thread_id` config (required once a checkpointer
  is always compiled in - see "Week 9 architecture"), and the two anomaly-path tests now resume
  past the mandatory approval interrupt via `_run_to_completion()`.
- `test_safety_validator.py` (8, new) - a valid proposal passes; each of the four known services
  passes at `risk_level="medium"`; a `risk_level="high"` proposal is rejected specifically for
  `ledger-service` but *not* for any other service (proving the rule is service-specific, not a
  blanket high-risk ban); unknown `target_service`/`action_category` and a tampered
  `requires_human_approval=False` are each caught independently; multiple simultaneous violations
  are all collected, not just the first.
- `test_workflow.py`'s three new interrupt-specific tests - `human_approval` pauses with exactly
  the expected payload and resumes correctly on `Command(resume={"decision": "approved", ...})`;
  a rejection is recorded verbatim (`human_decision`, `human_decision_reason`, and a matching
  `limitations` entry); a safety-rejected proposal (monkeypatched, since the real proposer can't
  produce one - see "Week 9 architecture") never pauses at all, reaching `finalize` in one
  `.invoke()` call with `"__interrupt__"` absent from the returned state.

A real, latent bug was caught (not by these tests directly, but by them requiring `MemorySaver`'s
real msgpack serialization for the first time in this project): two existing Week 8 test mocks
never set `ollama_client.model`/`.base_url` to strings, so a raw `unittest.mock.MagicMock` object
was silently flowing into `AnomalyInterpretation.model` and, from there, into checkpointed
`WorkflowState` - harmless before Week 9 (state was never serialized), but a genuine
`TypeError: Type is not msgpack serializable: MagicMock` once it was. Fixed by setting both
attributes explicitly on those mocks, matching the pattern the third, already-correct test used.

Weeks 10-11 added (all run, all passing):

- `test_execution_actions.py` (10) - each fault resolves to its real reset endpoint (paths read from
  Week 5's `telemetry.experiment.FAULTS`), restart resolves to a pod restart, escalation categories
  resolve to a no-op, and a proposal naming a different service than the fault's real owner is
  **refused rather than guessed**.
- `test_execution_executor.py` (17) - the three gates, each tested by handing the executor a state
  upstream routing should never produce: six near-miss approval strings (`"approve"`, `"APPROVED"`,
  `""`, ...) all refuse; a human approval cannot override a safety rejection; the attempt budget
  refuses. Plus real mechanisms with mocked HTTP/Kubernetes, dry-run sending nothing, and an
  unresolvable action reported as a failure rather than a silent skip.
- `test_recovery_verification.py` (9) - recovered / **fault_still_active (the False Recovery case)** /
  unhealthy / unreachable / not-applicable, and that it retries before concluding failure.
- `test_feedback_writer.py` (11) - the rule 22 guard from both directions (writer refuses; store
  refuses independently), the fail-closed missing-Evaluation-file case, the non-prefixed-ID refusal,
  metadata shape-compatibility with Week 6 seed incidents, and that a *failed* remediation is still
  learned from honestly rather than rewritten as a success.
- `test_remediation_proposer.py` (+7, now 22) - the escalation ladder: escalates after a failure,
  never re-proposes a failed category, terminates at human escalation, and the coherence check that
  a db-lock escalation produces exactly the proposal the Safety Validator must refuse.
- `test_workflow.py` (+8, now 18) - approved actions reach execution/verification/feedback; a human
  rejection never reaches execution; a safety rejection reaches feedback but never execution; the
  retry loop proposes a *different* action and demands a *fresh* approval; a false recovery also
  retries and stops at the attempt cap; an Evaluation incident is refused write-back through the
  graph; MTTR is recorded; and the normal path still reaches none of Week 10's nodes.
- `test_baseline_single_agent.py` (12) - one call in/structured answer out, prompt-fairness (the
  baseline sees the same evidence and retrieved incidents), and that hallucinated services, invented
  actions and unknown fault types are each **recorded in `parse_issues`, not silently accepted**.
- `test_baseline_graph.py` (8) - node *identity* with the multi-agent arm for every non-diagnostic
  step, absence of the three multi-agent diagnostic nodes, a spy proving the baseline never calls
  multi-agent RCA/proposer code, result shape-compatibility between the two arms, and the no-retry
  property.
- `dataset-tools/tests/test_chroma_store.py` (+6, now 48) - the new pipeline-run write path, and
  that `validate_no_leakage()` accepts a learned `run-` incident while still rejecting both
  Evaluation IDs and arbitrary unexplained IDs.

Also re-run as part of Weeks 10-11 verification, freshly rather than from recollection: all 19
Week 5 telemetry tests and all 48 Week 6 dataset-tools tests - **249 tests total across the three
Python packages, 0 failures**. Java tests were again not independently re-run (no Maven installed on
this machine - see "Live verification performed" for what could and could not be verified where).

## Live verification performed

Against the real Kubernetes deployment (Colima + Minikube) and a real local Ollama server.

**Week 7** (repeated this week to confirm no regression): trained all 4 services' Isolation
Forests (`python -m ai_orchestration.anomaly.train`); confirmed Ollama + `llama3.2:1b` available;
ran a full `graph.invoke()` against a deliberately extreme record with a live Ollama server
(anomaly correctly flagged, LLM interpretation returned); triggered a live memory-leak experiment
and confirmed anomaly scores tracked real memory growth.

**Week 8, this week:**

1. Confirmed Kubernetes healthy (6/6 pods) and all four faults inactive before starting.
2. Ran a **brand-new live `notification-latency` experiment**. Among its FAULT_ACTIVE-phase
   telemetry, one record (`http_client_requests_avg_duration_ms=74.5ms`) was flagged anomalous by
   the trained detector; running it through the full graph correctly reached `interpret` →
   `retrieve_similar_incidents` → `root_cause_analysis` → `propose_remediation`. **This run
   surfaced a genuine, honestly-reported finding**: the RCA's signature match picked
   `"auth-key-error"` instead of `"notification-latency"`, because `http_server_requests_error_count`
   had accumulated to `22` over this long-running session (the same cumulative-counter behavior
   Week 7 already documented) - `22` cleared the auth-key-error threshold by a much larger margin
   (44x) than this record's `http_client_requests_avg_duration_ms=74.5ms` cleared
   notification-latency's threshold (it didn't clear it at all - 74.5 is below the 500ms
   threshold). RAG retrieval correctly found real/synthetic `memory-leak` Memory incidents
   (semantically close given the JVM metrics in the query), and the RCA's own logic correctly
   recognized the disagreement and did *not* blindly follow retrieval
   (`determination_method="metric_signature+rag_disagreement"`) - but the underlying signature
   match itself was still wrong, for the reason above. See "Known limitations" for what this means
   and the reasonable fix direction (not implemented this week).
3. Ran a **brand-new live `db-lock` experiment** (a clean resource-fault case, since
   `hikaricp_connections_active` is a gauge, not a cumulative counter). A FAULT_ACTIVE record
   (`hikaricp_connections_active=9.0`) was flagged anomalous; the full pipeline produced a
   **correct, clean result end-to-end**: `suspected_fault_type="db-lock"`,
   `suspected_root_cause_service="ledger-service"` (matches `symptom_service` - both correctly
   ledger-service, a same-service fault), 3/3 retrieved incidents agreed
   (`determination_method="metric_signature+rag_agreement"`, `confidence="high"`), and the
   remediation proposal correctly targeted `ledger-service` with `action_category="release_resources"`,
   `requires_human_approval=True`. The LLM's narrative text was imperfect in places (a 1B-parameter
   model summarizing "9.0" as "5 raw JDBC connections" in one sentence) - an honest limitation of
   using a very small local model for narration, not a pipeline defect, since the narrative field
   never feeds back into the deterministic conclusion.
4. Verified (both experiments): every retrieved incident ID (`inc-0037`, `inc-0042`, `inc-0044`,
   `inc-0022`, `inc-0032`, `inc-0029`) is present in `memory_incidents.jsonl` and absent from
   `evaluation_incidents.jsonl` - no Evaluation incident was ever retrieved or used as context.
5. Confirmed no remediation action was executed: `RemediationProposal` objects were only ever
   constructed and printed/inspected; the only fault-injection API calls made during this
   verification were the experiment orchestrator's own scripted inject/reset calls (Week 5's
   `telemetry.experiment`), not anything triggered by a proposal.
6. Confirmed all four faults inactive after testing, and the full payment flow (login → payment →
   ledger debit → notification) still works end-to-end via a live HTTP call.

**Week 9, this week:** this development machine has no live Kubernetes/Minikube deployment (Docker
and a local Ollama server are present; Minikube is not) - so, unlike Weeks 5-8, this week's live
verification is against a **real running `streamlit run app.py` process, driven end-to-end through
an actual browser**, not a real cluster. This is disclosed explicitly, not blurred: the Safety
Validator and human-approval mechanics below are genuinely exercised live; the underlying
telemetry is synthetic (see "Week 9 architecture" > "Running the Streamlit console").

1. Started `app.py` under Streamlit and confirmed `ollama_client.is_available()` correctly reports
   the real local Ollama server as reachable (a Docker+Ollama install exists here, though the
   project's configured `llama3.2:1b` model is not pulled on this machine - see point 4).
2. Trained a demo detector for `payment-service` from the sidebar, selected the built-in
   memory-leak sample, and ran the pipeline. It correctly reached `human_approval` and **paused**:
   the page showed `suspected_fault_type="memory-leak"`, `suspected_root_cause_service=
   "payment-service"`, the exact anomalous-metric evidence, the matching remediation proposal
   (`action_category="reset_fault_configuration"`, `risk_level="medium"`), and "Safety validator:
   PASSED". Typed a reason and clicked **Approve** - the graph correctly resumed past the
   interrupt, and the final page showed `"Approved by a human operator: <the exact typed reason>"`,
   confirming `Command(resume=...)` round-trips real user input through the paused graph correctly.
3. **A real bug was found and fixed live**, not just in a unit test: the first version of this
   page's demo-detector trainer (`app.py::_base_metrics`) generated `ledger-service`'s
   `hikaricp_connections_active`/`idle`/`pending` as **fixed constants** (`0.0`/`10.0`/`0.0`), not
   jittered like every other field. A zero-variance feature gives Isolation Forest no split point
   to ever learn from, so a trained demo detector for `ledger-service` failed to flag a live
   `db-lock` sample (`hikaricp_connections_active=10.0`) as anomalous at all
   (`anomaly_score=-0.0107`, `is_anomaly=false`) - confirmed live in the browser, not predicted.
   Fixed by adding small realistic jitter to those fields (matching the pattern
   `tests/conftest.py`'s `ledger_record_factory` already uses); retrained live, re-ran the same
   `db-lock` sample, and it was correctly flagged this time
   (`anomaly_score=0.052887`), producing a correct RCA (`suspected_fault_type="db-lock"`,
   root cause = symptom = `ledger-service`) and a matching remediation proposal
   (`action_category="release_resources"`). This fix is scoped entirely to `app.py`'s demo-only
   helper - it does not touch the production `ai_orchestration` package, whose real training path
   (Week 7) has always used naturally-varying real captured telemetry.
4. Clicked **Reject** on that `db-lock` run with a typed reason - the page correctly showed
   `"Rejected by a human operator: <reason>"` and the exact same text appeared verbatim in the
   `limitations` list (`"A human operator rejected the proposed remediation: <reason>. No
   remediation was executed."`), confirming the rejection path is recorded, not silently dropped.
5. Confirmed graceful degradation live, not just mocked: `llama3.2:1b` (this project's configured
   default) is not pulled on this machine, so every real `client.generate()` call returned a real
   HTTP 404 from Ollama - `interpret_anomaly()`/`rca_analyzer.analyze()` both caught it and
   degraded to `llm_unavailable`/`llm_reasoning=None` exactly as designed, visible in the page's
   "Limitations / caveats" section with the real error text, never a crash. (One transient
   `OllamaUnavailableError` traceback *did* surface uncaught in the browser once, immediately after
   using Streamlit's own "Rerun after file change" hot-reload mid-click; reproducing the exact same
   call directly in a plain Python REPL, outside Streamlit, confirmed the production code degrades
   correctly - this was a Streamlit hot-reload artifact of the dev workflow, not a real code path,
   and disappeared on a clean retry with no concurrent file-change rerun in flight.)
6. **Not exercised live**: the safety-rejection branch (`RiskLevel="high"` on `ledger-service`)
   never fired through the UI, because the real `remediation/proposer.py` templates never assign
   `risk_level="high"` - this is the same, already-disclosed limitation noted in "Week 9
   architecture". It is covered by a real, passing automated test
   (`test_workflow_safety_rejection_skips_human_approval_entirely`) instead.
7. Ran the complete Python test suite fresh after all `app.py` changes (`ai-orchestration` +
   `dataset-tools` + `telemetry`, 162 tests total) - all still pass.

**Weeks 10-11, this week:** same constraint as Week 9 (Docker and a local Ollama server are present
on this machine; Minikube is not), so verification was again driven through a real
`streamlit run app.py` process in a browser. What that made possible, though, is arguably a
*better* test of Week 10 than a healthy cluster would have been: with no service on port 8082 and
no cluster, every remediation genuinely failed, which exercised the entire escalation ladder under
real failure conditions rather than mocked ones.

1. **The full escalation ladder ran live, end to end**, on one memory-leak incident:
   - RCA reached `suspected_fault_type="memory-leak"`, `suspected_root_cause_service=
     "payment-service"`, `confidence="high"` (`metric_signature+rag_agreement`).
   - Attempt 1, `reset_fault_configuration`, was approved and **really executed** - a real HTTP POST
     to `localhost:8082/reset-memory-leak` - and failed with a real
     `ConnectionError: ... target machine actively refused it`.
   - The graph routed back to `propose_remediation`, recorded the failed action, proposed
     `restart_service` instead, and **paused again**. The page labelled it "Retry 2 ... this is not
     the action you approved before" and showed the previous failure verbatim.
   - Attempt 2 was approved, really attempted, and failed (no cluster).
   - Attempt 3 was the terminal `investigate_manually` escalation, which correctly executed nothing
     (`status="skipped_no_op"`), and recovery verification correctly reported `not_applicable`
     rather than crediting itself with a service it never touched.
   - Final state: `remediation_attempts=2`, MTTR 53.4s, and the run written back to memory.
2. **Every retry required its own approval.** Three separate `interrupt()` pauses and three separate
   Approve clicks for one incident - no action inherited a previous approval, confirming the
   property the automated tests assert.
3. **The feedback loop demonstrably closed.** Inspecting the real ChromaDB collection afterwards
   showed three `run-`-prefixed pipeline-run incidents, and a later run's RCA reported
   `metric_signature+rag_agreement` with "1/2 retrieved similar incidents agree" - i.e. it retrieved
   incidents that *earlier runs of this same pipeline had learned*, not just Week 6 seeds.
4. **Rule 22 verified against the real collection**, not only in tests: all three written incidents
   carry `data_source="pipeline_run"` and the `run-` prefix, none is an Evaluation ID, and
   `chroma_store.validate_no_leakage()` returns True on the real on-disk collection.
5. **A real finding was measured, not assumed**: the Isolation Forest saturation described under
   "Known limitations" was discovered when the demo detector scored a 500 MB heap as normal, and
   was then characterised properly with a value sweep rather than worked around by tuning. The
   response was to surface the *already documented, intentionally configurable*
   `ANOMALY_SCORE_THRESHOLD` knob in the console (with the raw score always displayed), rather than
   quietly retune the model until the demo passed.
6. Full Python suite re-run after every change: **249 tests** (182 ai-orchestration + 48
   dataset-tools + 19 telemetry), all passing. Java tests were again not re-run (no Maven on this
   machine).

## Known limitations

- **Training sample size is small** (42-45 real samples per service, from only the 4 real
  experiments currently in the Memory set) - this is a direct, disclosed consequence of Week 6's
  dataset still being early-stage (68 total incidents, 16 real). Live verification against the
  real dataset showed detection is **reliable for clearly extreme/large deviations** (verified in
  both the automated tests and the live Ollama end-to-end check) but **inconsistent for some real,
  moderate fault instances** - e.g. scoring the actual recorded FAULT_ACTIVE windows of the
  Evaluation-set memory-leak and db-lock experiments did not always separate cleanly from NORMAL
  windows at the default threshold. This should improve as more real experiments are run in future
  weeks and the Memory-eligible training set grows.
- **`http_server_requests_count`/`error_count` are cumulative Prometheus counters**, not
  per-interval rates - their absolute value reflects the whole service session's history, not just
  the current window, which makes them a weaker anomaly-detection feature than a delta/rate would
  be (live investigation during this week directly confirmed this: an error counter observed at
  value 5 in one experiment was later found at value 0 in a subsequent experiment despite the
  request counter continuing to climb, indicating the underlying Prometheus/Micrometer counter
  series had reset independently of the request counter - most likely due to per-tag meter
  eviction after inactivity, not a service restart, since Kubernetes reported 0 pod restarts
  throughout). Converting these to rate-of-change features is a reasonable direction for a future
  week, not implemented here (out of Week 7's scope).
- `ISOLATION_FOREST_CONTAMINATION`'s default was changed from sklearn's `"auto"` to an explicit
  `0.1` after live testing showed `"auto"`'s fixed `-0.5` score offset - tuned for much larger
  datasets - flagged nearly all real telemetry (both normal and fault) as anomalous on this small
  dataset. `0.1` is a more conventional, defensible default for a small-sample setting, not a
  value tuned to make any specific fault example pass.
- The graph currently processes one telemetry record at a time (matches how Week 8 calls it - once
  per anomaly candidate) rather than a batch/stream - batching wasn't needed for this week's scope.
- **RCA signature matching inherits Week 7's cumulative-counter limitation, and this week's live
  verification directly demonstrated the consequence**: `auth-key-error`'s and `db-lock`-adjacent
  signature checks against `http_server_requests_error_count` compare an absolute cumulative value
  to a fixed threshold, so a long-running session with several unrelated faults tested earlier can
  leave that counter elevated long after the fault that caused it is over, making a *different*,
  currently-active fault's RCA misattribute to `auth-key-error` (observed live this week - see
  "Live verification performed"). The gauge-based signatures (`hikaricp_connections_active`,
  `jvm_memory_used_bytes`) don't have this problem, since gauges reflect current state, not a
  lifetime total. Reasonable fix direction for a future week: compute error-count and
  request-count signatures from a windowed delta (e.g. the difference between this record and the
  same service's telemetry a fixed number of cycles earlier) rather than the raw cumulative value
  - not implemented this week, consistent with Week 7's decision to leave the same underlying
  counter-vs-rate issue as documented future work rather than redesigning the metric schema
  mid-week.
- The RCA's optional LLM narrative uses the same small `llama3.2:1b` model as Week 7's
  interpretation step; live verification showed it can summarize numeric evidence imprecisely
  (e.g. restating "9.0" as "5" in one sentence of a db-lock narrative). This never affects the
  deterministic conclusion (`suspected_root_cause_service`, `confidence`,
  `determination_method`), only the free-text `llm_reasoning` field, and is an expected tradeoff
  of using a 1.2B-parameter model rather than a larger one (see "Ollama integration" above for why
  this model was chosen for this machine).
- Remediation proposal templates exist for the four already-known controlled faults; a genuinely
  novel anomaly (one that matches no signature and finds no useful retrieval evidence) correctly
  falls back to a generic, low-risk `"investigate_manually"` proposal rather than a specific
  action - this is intentional, not a gap to fill by guessing.
- **`build_graph()`'s default checkpointer (`MemorySaver`) is in-process only** - paused runs are
  lost if the process restarts, and nothing is shared across multiple processes/workers. Acceptable
  for this week's single-process Streamlit console and test suite; a real deployment would swap in
  a durable checkpointer (`SqliteSaver`/`PostgresSaver`) via the same `checkpointer` parameter - not
  implemented this week, since nothing yet needs paused state to survive a restart (see "How Week 9
  feeds Week 10").
- **The Safety Validator's `ledger-service` + `risk_level="high"` rejection rule is currently
  unreachable by the real pipeline** - `remediation/proposer.py`'s Week 8 templates never assign
  `risk_level="high"` to anything. It is real, tested code (`test_safety_validator.py`,
  `test_workflow_safety_rejection_skips_human_approval_entirely`), just not something today's demo
  can trigger without deliberately monkeypatching the proposer - disclosed here rather than
  presented as fully exercised end-to-end.
- **Isolation Forest saturates outside its training range - newly characterised in Week 10, and it
  affects the production detectors, not just the demo one.** Measured directly (sweeping one
  feature while holding the rest at baseline, against a detector trained on payment-service
  baseline telemetry):

  | jvm_memory_used_bytes | anomaly_score |
  |---|---|
  | 40 MB (in range) | -0.0637 |
  | 50 MB | -0.0279 |
  | 153 MB (the real observed fault peak) | -0.0279 |
  | 500 MB | -0.0279 |
  | 2000 MB | **-0.0279** |

  A 2 GB heap scores *identically* to a 50 MB one. The mechanism: every split threshold the trees
  learned for that feature lies inside the observed training range, so any value above that range
  takes the same branch at every such split and terminates at the same depth. The score therefore
  cannot increase past the range boundary no matter how extreme the value gets, and whether it is
  flagged at all depends entirely on where `contamination` placed the offset - not on severity.

  This is a property of Isolation Forest plus this feature schema, not of the synthetic demo data,
  so it applies to the real per-service detectors too. It is a concrete mechanism for the
  "detection is inconsistent for some real fault instances" limitation Week 7 documented
  empirically, and it sharpens it: the failure is not confined to *moderate* deviations - a
  sufficiently extreme deviation is equally invisible, for the same reason.

  Not fixed here: changing the detector is a Week 7 research change that would need re-validation
  against real captured telemetry, which this machine does not have. Reasonable directions for a
  future week (none implemented): normalising or log-scaling features so out-of-range values stay
  informative; adding an explicit range-based pre-filter alongside the model; or scoring on
  deviation-from-baseline rather than raw values. **Important mitigation already in place:** the
  Week 8 deterministic RCA signature matching does *not* share this weakness - it compares against
  documented thresholds directly - so the pipeline's *diagnosis* stays correct wherever detection
  fires at all. That the deterministic layer compensates for the ML layer's blind spot is a
  finding in favour of the multi-agent design, and belongs in the paper's Discussion.

- **This week's live verification ran against a real Streamlit process, not a real Kubernetes
  deployment** - this development machine has Docker and a local Ollama server but no
  Minikube/cluster. The mandatory-approval mechanics (pause, resume, approve, reject) were verified
  against real LangGraph execution and a real browser session; the underlying telemetry driving
  those runs was synthetic (built-in samples / a demo-trained detector), not a live fault injection
  against real pods, unlike Weeks 5-8's verification standard. See "Live verification performed" >
  "Week 9" for the exact scope.
