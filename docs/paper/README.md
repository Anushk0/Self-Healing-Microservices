# Paper sections (Weeks 13–14)

Drafted from the implemented system and from the Week 12 evaluation artefacts. Every quantitative
claim traces to a file in `docs/evaluation/`, and every mechanism claim cites a module path, so both
can be checked rather than trusted.

| File | Roadmap item | Status |
|---|---|---|
| `01-dataset-construction.md` | Week 13 item 1 | Drafted — **3 citation slots need verification** |
| `02-methodology.md` | Week 13 item 2 | Drafted — fully grounded in code |
| `03-results.md` | Week 13 item 3 / Week 14 item 1 | Generated from the evaluation run |
| `04-discussion.md` | Week 14 item 1 | Drafted from the measured results |
| `05-related-work.md` | Week 14 item 3 | Drafted — **contains a finding that changes the section** |
| `diagrams.md` | Week 14 item 2 | 5 figures, Mermaid source |

## Read this first

**The paper the roadmap named as the closest direct competitor has been withdrawn.** arXiv 2511.15755
(Drammeh) was retracted by its author after a code audit found that its multi-agent arm's action
list was a source constant rather than model output, and that its scorer read only that field.

This has two consequences, both handled in `05-related-work.md`:

1. The differentiation target changed — from differentiating against its *results* (which no longer
   stand) to differentiating against its *method*, which is the more useful comparison anyway.
2. It creates an obligation for this paper. The withdrawn defect is the natural failure mode of a
   deliberately-deterministic multi-agent pipeline, which is exactly what this project is. §5.2
   therefore contains an empirical self-audit demonstrating that the scored quantity here varies
   with input telemetry, backed by a permanent regression test
   (`ai-orchestration/tests/test_scored_output_is_not_constant.py`) so the property cannot silently
   regress.

## Outstanding items before submission

These are flagged inline in the sections and repeated here so none is missed:

1. **Verify the three dataset citations** — RCAEval, Cloud-OpsBench, MicroServo (`01`, §4.1). I have
   not read these and deliberately left structural slots rather than inventing summaries.
2. **Resolve "VeriOps-Agent"** (`05`, §5.3). No paper of that title could be found; confirm the
   correct title or drop it.
3. **Read the two substitute papers** surfaced during the search (arXiv 2508.06394 on telemetry
   manipulation, arXiv 2605.12729 on agentic AIOps safety) before citing them — relevance was
   assessed from abstracts only.
4. **State the dataset composition honestly in the paper body** (`01`, §4.5). The reported run used a
   fully synthetic corpus because no cluster was available on the run machine; the same tooling
   produces a real/synthetic mix where live services exist.
5. **Decide how to present unmeasured metrics.** Recovery Success Rate, False Recovery Rate and MTTR
   are not measurable by replay and are reported as such. If you can run the harness against a live
   cluster (`--mode live`), those cells fill in with real numbers and the Results section should be
   regenerated.

## Regenerating the results

```bash
cd ai-orchestration
python -m ai_orchestration.evaluation.run_evaluation --out ../docs/evaluation
```

Add `--prometheus-url http://localhost:9090` to populate infrastructure metrics, and `--mode live
--allow-live-execution` on a machine with a running cluster to make the recovery metrics measurable.
