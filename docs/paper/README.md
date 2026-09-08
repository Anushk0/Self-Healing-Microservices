# Paper sections (Weeks 13–16)

Drafted from the implemented system and the Week 12 evaluation. Every quantitative claim traces to a
file in `docs/evaluation/`, and every mechanism claim cites a module path — both checkable rather
than trusted.

| File | Roadmap item | Status |
|---|---|---|
| `00-abstract-and-introduction.md` | Week 15 item 1 | Abstract, intro, contributions, and an explicit *"what this does not claim"* |
| `01-dataset-construction.md` | Week 13 item 1 | Drafted — **3 citation slots need verification** |
| `02-methodology.md` | Week 13 item 2 | Drafted — fully grounded in code |
| `03-results.md` | Week 13 item 3 / Week 14 item 1 | Generated from the evaluation run |
| `04-discussion.md` | Week 14 item 1 | Drafted from measured results, incl. ablations |
| `05-related-work.md` | Week 14 item 3 / Week 16 item 3 | **Contains a finding that changes the section** |
| `06-novelty-claim-audit.md` | Week 15 item 1 | Claim-by-claim audit; 2 claims weakened, 1 removed |
| `07-demo-runbook.md` | Week 15 item 3 | Defence narration, timings, failure plan |
| `08-conclusion.md` | — | Conclusion and future work |
| `09-mentor-review-packet.md` | Week 15 item 2 | **Needs your supervisor** — decisions only they can make |
| `10-ablations.md` | Week 16 item 1 | Both ablations run — **changed the paper's claims** |
| `diagrams.md` | Week 14 item 2 | 5 figures, Mermaid source |

Evidence lives in `docs/evaluation/`: `RESULTS.md`, `metrics.json`, `per_incident_results.csv`,
`raw_run.json`, three PNG figures, and `ablations/` (Week 16).

## Read this first

**The paper the roadmap named as the closest direct competitor has been withdrawn.** arXiv 2511.15755
(Drammeh) was retracted by its author after a code audit found its multi-agent arm's action list was
a source constant rather than model output, and that its scorer read only that field.

Two consequences, both handled in `05-related-work.md`:

1. The differentiation target changed — from its *results* (which no longer stand) to its *method*,
   which is the more useful comparison anyway.
2. It creates an obligation. That defect is the natural failure mode of a deliberately-deterministic
   multi-agent pipeline, which this project is. §5.2 therefore contains an empirical self-audit
   showing the scored quantity varies with input telemetry, backed by a permanent regression test
   (`ai-orchestration/tests/test_scored_output_is_not_constant.py`).

## The ablations changed the claims — read `10-ablations.md`

Two ablations were run in Week 16, and they moved the paper's central argument from an inference to a
controlled result — while also removing a claim the earlier draft made.

| | Cross-service accuracy |
|---|---|
| Shipped configuration | **100%** |
| Retrieval removed entirely | **100%** — unchanged |
| Retrieval permitted to override a signature match | **0%** — reproduces the baseline exactly |

**Retrieval contributes zero diagnostic accuracy.** Disabling it left all 48 multi-agent diagnoses
identical. Any suggestion that RAG improves diagnosis here is unsupported and has been removed.

**The constraint on retrieval contributes everything.** Allowing retrieval to override reproduced the
single-agent baseline's numbers to four decimal places — 12.5% overall, 60.0% when answered, F1
0.4167, the same 4 symptom-service attributions.

So the value is not in the retrieval, and not in the multi-agent decomposition, but specifically in
the refusal to let retrieval decide.

## Outstanding items before submission

Flagged inline and collected here so none is missed:

1. **Verify the three dataset citations** — RCAEval, Cloud-OpsBench, MicroServo (`01`, §4.1). Not
   read; deliberately left as marked slots rather than invented.
2. **Resolve "VeriOps-Agent"** (`05`, §5.3). No paper of that title was found. Confirm or drop.
3. **Read before citing** the four Week 16 papers (`05`, §5.4) — located and abstracts read, but not
   read in full: Flow-of-Action (2502.08224), LATS-RCA (2605.03505), GALA (2508.12472 / 2608.08968),
   plus 2508.06394 and 2605.12729 from the earlier pass.
4. **State the dataset composition honestly in the body** (`01`, §4.5) — the reported run used a
   fully synthetic corpus.
5. **Decide how to present unmeasured metrics.** Recovery Success Rate, False Recovery Rate and MTTR
   are unmeasurable by replay. `--mode live` on a machine with a cluster fills them in.
6. **Take the five decisions in `09-mentor-review-packet.md` §2** to your supervisor.
7. **Record the demo** (`07`, §3) — no recording exists.

## Regenerating everything

```bash
cd ai-orchestration

# main evaluation
python -m ai_orchestration.evaluation.run_evaluation --out ../docs/evaluation

# Week 16 ablations
python -m ai_orchestration.evaluation.run_evaluation --ablation no_retrieval --out ../docs/evaluation/ablations/no_retrieval
python -m ai_orchestration.evaluation.run_evaluation --ablation rag_override --arms multi_agent --out ../docs/evaluation/ablations/rag_override

# recompute metrics/figures from a saved run without re-executing the pipeline
python -m ai_orchestration.evaluation.run_evaluation --from-run ../docs/evaluation/raw_run.json --out ../docs/evaluation
```

Add `--prometheus-url http://localhost:9090` for infrastructure metrics, and
`--mode live --allow-live-execution` on a machine with a cluster to make recovery metrics measurable.
