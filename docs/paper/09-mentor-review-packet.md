# Mentor review packet

> **Week 15, roadmap item 2** is *"Get Shakila Shaikh's feedback, incorporate it."* That requires
> your supervisor and cannot be done for you. What follows is the packet to hand over: what to read,
> in what order, and — more usefully — the specific decisions where a supervisor's judgement changes
> the paper and mine cannot substitute.

## 1. If they have 15 minutes

Read in this order:

1. `docs/evaluation/RESULTS.md` — the generated tables. Nothing is hand-written.
2. `00-abstract-and-introduction.md` §1.4, *"What this paper does not claim"*.
3. `05-related-work.md` §5.1–§5.2 — the withdrawn competitor and the self-audit it forced.

Those three establish what was measured, what is claimed, and why the claim is bounded.

## 2. The decisions where your judgement is needed

These are genuine forks where I made a defensible call that a supervisor might reasonably overturn.
Each is flagged in the text, but they are collected here so none is discovered late.

### 2.1 Should the headline be 100% or 20.8%?

Both are true and both are reported. Accuracy *when the system answered* is 100%; accuracy over
*all* incidents — counting the 79.2% where the detector never surfaced anything — is 20.8%.

I led with the comparison (100% vs 60.0%, and 4/4 vs 0/4) and put 20.8% in the abstract and §1.4.
An examiner focused on deployment realism may want 20.8% foregrounded instead. **This is a framing
call about which honest number leads, not a question of what to report.**

### 2.2 How much weight should the withdrawn-competitor finding carry?

arXiv 2511.15755 was the roadmap's designated closest competitor and has been retracted for scoring
a source constant. I treated this as *raising the evidentiary bar* for this paper and added a
self-audit plus a regression test (§5.2).

Alternative view: this is a distraction from your own contribution and belongs in a footnote. I
think it belongs in the body — the defect is the natural failure mode of your architecture, and
pre-empting it is worth more than the space it costs — but this is a judgement about positioning
that a supervisor is better placed to make than I am.

### 2.3 Is the synthetic corpus acceptable, or must the live run happen first?

The reported evaluation uses 68 synthetic incidents because no cluster was available on the run
machine. The circularity this introduces is documented (§6.2), and the comparative findings survive
it. But **§6.10 lists "re-run against live faults" as the highest-value next experiment**, and if the
timeline permits it before submission, it would remove the single largest caveat in the paper.

Ask them directly: *submit with the caveat, or hold for the live run?*

### 2.4 Should the ablations be a section or a subsection?

The RAG ablations (§6.9) convert the paper's central mechanism claim from an observation into a
controlled result. That arguably deserves its own numbered section rather than living inside the
Discussion.

### 2.5 Citation slots I deliberately left empty

RCAEval, Cloud-OpsBench and MicroServo are referenced in Dataset Construction §4.1 as precedent for
synthetic fault-injection corpora. **I have not read them and did not invent summaries.** They are
marked `[VERIFY]`. Either fill them from the papers or cut the comparison — do not submit as-is.

Similarly, **"VeriOps-Agent" could not be located.** If your supervisor knows the correct title, it
goes in §5.3; otherwise it should be dropped rather than cited vaguely.

## 3. Questions worth asking them explicitly

1. Is the narrowness of the claim (§1.4, §8.5) a strength or does it read as under-selling?
2. Does the cross-service framing need more justification as a *realistic* incident class, or is it
   self-evidently so to this audience?
3. Is the cost comparison against LATS-RCA (75 API calls vs 0.4167) fair to include given the
   datasets differ, or does it invite an apples-to-oranges objection?
4. For the defence: lead with the demo or with the results?

## 4. What I could not check

Stated so the review is not falsely reassured:

- **Nobody has read the three dataset-methodology citations.** See §2.5.
- **The Java test suite is fully verified in this environment.** Maven, a Temurin JDK 17 and a
  throwaway PostgreSQL instance were set up for the final audit: auth-service (12),
  notification-service (4), payment-service (13) and ledger-service (18) — **all 47 pass**. The
  procedure for the database-backed ones is in `ledger-service/README.md`.
- **The services do not build on JDK 23** — a Lombok/JDK incompatibility, documented in the root
  README's prerequisites. The code is correct; the toolchain is the constraint.
- **No screen recording of the demo exists** (`07-demo-runbook.md` §3 has the steps to make one).
- **The paper has not been read end-to-end as a single document by a human.** I performed a
  claim-by-claim audit (`06-novelty-claim-audit.md`), which catches unsupported assertions but not
  prose that reads badly across section boundaries.
