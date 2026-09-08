# Week 12 — Evaluation Results

Run started `2026-09-08T05:18:58.698891+00:00`, finished `2026-09-08T06:09:20.642023+00:00`, mode `replay`, operator policy `permissive`.

## What this run can and cannot establish

- **Environment:** Windows-11-10.0.26200-SP0, Python 3.12.2, Ollama model `llama3.2:3b` (reachable: True).
- **Trained detectors:** auth-service, ledger-service, notification-service, payment-service.
- **Execution:** dry run = `True`; feedback write-back enabled = `False`.
- **Recovery metrics measurable:** `False`. Recovery Success Rate and False Recovery Rate require a genuinely active fault to remediate. In replay mode nothing is executed (dry run), so they are not measurable and are reported as such rather than as a meaningless 100%.

## Dataset

- Evaluation incidents: **48** (held out; never in retrieval memory).
- By fault type: {'auth-key-error': 12, 'db-lock': 12, 'memory-leak': 12, 'notification-latency': 12}
- By data source: {'synthetic': 48}
- Cross-service incidents (root cause ≠ symptom service): **24**

## Preflight (leakage guard verified before measuring)

- `feedback_disabled`: True
- `rule_22_guard_verified_live`: True
- `rule_22_probe_incident_id`: inc-0014
- `rule_22_refusal_reason`: Incident 'inc-0014' belongs to the Evaluation set. Evaluation incidents must never enter RAG memory (CLAUDE.md rule 22), so this run was not written back.

## 1. Performance — root cause identification

| Metric | Multi-agent | Single-agent baseline |
|---|---|---|
| Accuracy (all incidents) | 43.8% | 6.2% |
| Accuracy (when it answered) | 100.0% | 15.8% |
| Abstention rate | 56.2% | 60.4% |
| Precision (macro) | 100.0% | 5.8% |
| Recall (macro) | 100.0% | 18.8% |
| F1 (macro) | 100.0% | 8.8% |
| Fault-type accuracy (when answered) | 100.0% | 26.3% |
| **Cross-service accuracy** | 100.0% | 33.3% |
| Same-service accuracy | 100.0% | 0.0% |
| Blamed the symptom service (cross-service) | 0 | 2 |
| Errored incidents | 0 | 0 |

*Cross-service accuracy is the headline capability: incidents where the service showing the symptom is not the service at fault. "Blamed the symptom service" counts the specific failure mode those incidents were designed to expose.*

## 2. System

| Metric | Multi-agent | Single-agent baseline |
|---|---|---|
| Detection latency, mean (s) — detection path only | 0.011 | 0.011 |
| Detection latency, median (s) | 0.01001 | 0.01001 |
| Time to approval pause, mean (s) — incl. diagnosis | 58.3 | 4.535 |
| Anomaly detection rate | 43.8% | 43.8% |
| MTTR, mean (s) | **not measurable here** (see note) | **not measurable here** (see note) |
| Executions attempted | 0 | 0 |
| Recovery success rate | **not measurable here** (see note) | **not measurable here** (see note) |
| False recovery rate | **not measurable here** (see note) | **not measurable here** (see note) |
| Remediation attempts, mean | 0.4375 | 0.2708 |

## 3. AI

| Metric | Multi-agent | Single-agent baseline |
|---|---|---|
| Wall clock per incident, mean (s) | 58.3 | 4.539 |
| LLM calls per incident, mean | 0.875 | 0.4375 |
| LLM calls, total | 42 | 21 |
| Incidents with reasoning text | 20 | 21 |
| Hallucination: cited a metric absent from the evidence | 0.0% | 0.0% |
| **Hallucination: claimed a baseline-range value was elevated** | 0.0% | 4.8% |
| Response parse-issue rate | 0.0% | 4.2% |

*Token cost is not applicable to a self-hosted Ollama model; wall-clock inference time and LLM call count are reported in its place (documented methodology substitution).*

### Agent collaboration time

- Multi-agent mean: 58.3s
- Single-agent mean: 4.539s
- Multi-agent relative overhead: 1184.6%
- Both arms share detection, retrieval and all downstream nodes; the difference is the diagnostic step (deterministic signature matching plus an optional LLM narrative, versus one LLM call that must produce the whole answer).

## 4. Infrastructure

**Not collected.** No Prometheus URL supplied. Infrastructure metrics require the deployed monitoring stack (kubectl port-forward svc/prometheus 9090), which is not part of a replay-only evaluation environment.

## 5. Human-in-the-loop

| Metric | Multi-agent | Single-agent baseline |
|---|---|---|
| Incidents reaching human approval | 21 | 20 |
| Approved | 21 | 20 |
| Rejected | 0 | 0 |
| Approval accuracy | 100.0% | 15.0% |
| **Unsafe actions prevented by Safety Validator** | 0 | 1 |
| Unsafe action prevention rate | 0.0% | 2.1% |
| Executions without approval (must be 0) | 0 | 0 |

*Under the 'permissive' simulated-operator policy this is definitionally equal to diagnostic accuracy among proposals that reached approval, so it is not an independent measurement. Reported for completeness and because it makes Unsafe Action Prevention interpretable - see evaluation/operator.py.*

Safety rules that fired during this run:

- High-risk actions are never allowed against 'ledger-service' - it holds financial transaction state behind a transactional, pessimistic-locking boundary. Escalate to manual investigation instead.

## Raw log

Per-incident rows (every field, both arms) are in `per_incident_results.csv`; the full metric structure including per-class reports is in `metrics.json`. Both are the inputs Week 13-14's Results section is written from.

## Figures

![Root cause identification accuracy](fig_accuracy_comparison.png)

![Outcome distribution](fig_outcome_distribution.png)

![Cost per incident](fig_cost_per_incident.png)
