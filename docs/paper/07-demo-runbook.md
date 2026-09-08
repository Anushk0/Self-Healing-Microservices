# Defence demo — runbook and narration

> **Week 15, roadmap item 3.** The roadmap says to rehearse "using your Week 10 recording as the
> backbone". **No recording exists.** The Week 10 live session was verified through a browser and
> written up in `ai-orchestration/README.md`, but no screen capture was produced, and there is none
> in this repository. Record one from §3 below before the defence — or plan to demo live and accept
> the risk in §5.

## 1. What the demo has to prove

One thing, and it should be said in the first fifteen seconds:

> **The service that reports the problem is not the service that caused it — and this system gets
> that right where a single LLM gets it wrong every single time.**

Everything else in the demo is supporting detail. If the audience remembers one sentence, that is
the sentence.

## 2. The narration, with the beats in order

### Beat 1 — Set the trap (30 s)

> "payment-service is timing out. Every alarm is on payment-service. Its own error metrics are
> climbing. The obvious move is to restart payment-service."
>
> "payment-service is fine. notification-service has a six-second delay injected into it, and
> payment-service calls it synchronously, so the caller is what shows the symptom. Restarting
> payment-service would achieve nothing, and we'd do it again in ten minutes."

### Beat 2 — Show the baseline failing (45 s)

Run the single-agent arm on that incident. It answers `payment-service`.

> "This is a single LLM with the same telemetry and the same retrieved history the full pipeline
> gets. It says payment-service. It said payment-service on all four cross-service incidents in the
> evaluation — nine out of nine, the same wrong answer every time."

Then show *why*, from the model's own words (`docs/evaluation/raw_run.json`):

> *"Historical past incidents (inc-0044, inc-0042, inc-0037) report the same root cause of a
> memory-leak fault in the payment-service…"*

> "It isn't reasoning badly. It's reasoning correctly from precedent that happens to be wrong.
> Retrieval matched on payment-service's telemetry, so it returned payment-service incidents."

**This beat is the demo.** Do not rush it.

### Beat 3 — Show the pipeline getting it right (45 s)

Same incident, multi-agent arm. Root cause: `notification-service`.

> "Same evidence, same retrieval — and note the determination method: `rag_disagreement`. The
> retrieved incidents disagreed with this answer, and the system kept its own conclusion anyway,
> because a signature match against measured thresholds outranks precedent. That single constraint
> is the entire difference between the two systems in our results."

### Beat 4 — Safety and the human gate (45 s)

Let it reach the approval pause in the Streamlit console.

> "It has a fix. It cannot apply it. This is a LangGraph interrupt — the graph is halted, and there
> is no code path that continues without an explicit resume. Not a confidence threshold, not a
> policy: the process is stopped."

Then the ledger-service refusal, which is the strongest safety moment:

> "If a db-lock remediation fails, the system escalates to restarting ledger-service — and the
> safety validator refuses, because ledger-service holds financial state behind a locked
> transaction. That rule is deterministic Python. No model is consulted, and no human is asked,
> because a rule violation isn't a judgement call."

### Beat 5 — Close on the honest limits (30 s)

Pre-empt the examiner rather than being caught by them:

> "Two things I'd flag myself. Detection is the weak link — the Isolation Forest surfaced only 21 of
> 48 incidents, and I can show you exactly why: it saturates outside its training range, so a 2 GB
> heap scores identically to 50 MB. And the 100% figure is on a synthetic corpus whose generator
> shares constants with my thresholds, so I'd treat the comparison as the result and the absolute
> number as bounded."

Volunteering this converts the two most likely attacks into evidence of rigour.

## 3. Recording the backbone (do this before the defence)

Requires a live cluster — so on the Mac, not the Windows machine.

```bash
# 1. Bring the stack up and confirm all four faults are OFF
kubectl apply -f k8s/ && kubectl get pods
for s in 8081 8082 8083 8084; do curl -s localhost:$s/fault-status; echo; done

# 2. Start the console
cd ai-orchestration && streamlit run app.py

# 3. Inject the headline fault
curl -X POST localhost:8084/inject-latency \
     -H 'Content-Type: application/json' -d '{"delayMs":6000}'

# 4. Drive a payment so the symptom appears on payment-service
#    (see docs/postman/ collection, "Payment (full chain)")

# 5. In the console: single-agent arm first (it fails), then multi-agent (it succeeds),
#    then Approve and let execution + recovery verification run.

# 6. RESET — do not leave a fault injected
curl -X POST localhost:8084/reset-latency
curl -s localhost:8084/fault-status
```

Record beats 2–4 in one take if possible; cuts invite "what happened in between?".

## 4. Anticipated questions, with answers grounded in artefacts

| Question | Answer | Where it's evidenced |
|---|---|---|
| "Isn't your remediation just a hardcoded lookup?" | Yes, and deliberately. But what's *scored* is the diagnosis, which varies with telemetry — three payment-service records with different metrics give three different root causes. There's a regression test asserting it. | Related Work §5.2; `test_scored_output_is_not_constant.py` |
| "Why not let a confident model act alone?" | Confidence here is a deterministic label, not a calibrated probability — and the highest-confidence case (db-lock) escalates to the most dangerous action (restarting the ledger). | Methodology §3.8 |
| "100% looks too good." | It is. Synthetic corpus, thresholds and generator share constants. The comparison holds; the absolute doesn't. | §6.2 |
| "Does RAG actually help?" | Measured, not assumed — see the ablation results. On cross-service faults retrieval was actively harmful. | §6.9 / `docs/evaluation/ablations/` |
| "What if the baseline had a bigger model?" | Unknown, and the strongest objection. It's the third experiment I'd run. | §6.10 |
| "Did anything execute without approval?" | Zero, both arms, all 48 incidents — reported as a metric, not asserted. | §5.7 |

## 5. Failure plan

Live demos fail. Decide in advance:

- **Ollama times out** (it did on 9 of 10 incidents in the evaluation) — this is *fine* and on-message: the deterministic conclusion is unaffected. Say so and continue.
- **A pod isn't ready** — have the recording ready to cut to.
- **Detection doesn't fire** on your chosen incident — known and expected (20.8% rate). Have a
  pre-selected incident ID that you have already confirmed detects.
- **Everything fails** — the artefacts stand alone: `docs/evaluation/RESULTS.md` and the three
  figures make the argument without a live system.

## 6. Timing

| Beat | Target |
|---|---|
| 1. Set the trap | 0:30 |
| 2. Baseline fails | 1:15 |
| 3. Pipeline succeeds | 2:00 |
| 4. Safety + human gate | 2:45 |
| 5. Honest limits | 3:15 |

Leave the rest for questions — the audit trail in §4 is deep enough to answer from.
