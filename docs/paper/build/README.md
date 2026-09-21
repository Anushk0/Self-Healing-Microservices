# The manuscript

**`docs/paper/FINAL_IEEE_PAPER.docx` is the canonical manuscript.** It is the version to submit.
`FINAL_IEEE_PAPER_TRACKED_CHANGES.docx` is the same document with every edit made during review
shown in red, for the authors' and supervisor's reference; strip nothing from it, just don't submit
it.

`IEEE-submission-draft.md` and `IEEE-submission-draft.docx` are the earlier markdown-generated
draft. They are superseded: the final paper was edited in Word after that point (IEEE layout,
restructured abstract, rebuilt tables, new figures), so the markdown no longer matches it. Keep the
markdown for history; do not regenerate the Word file from it.

## Figures

Every figure is generated, not drawn by hand:

```bash
python docs/paper/figures/make_figures.py
```

Figs. 1-3 are diagrams. Fig. 2 mirrors `ai-orchestration/src/ai_orchestration/graph/workflow.py`
node for node, so if the graph changes, update the script. Figs. 4-7 read their values directly from
`docs/evaluation/` (metrics, ablation metrics and the negative control), so no plotted number is
typed. Each figure is written as a 600 dpi PNG, which is what the Word file embeds, and as a vector
PDF, which is what to upload if the venue accepts separate figure files.

## Layout

US Letter, 0.75 in top, 1.0 in bottom and 0.625 in side margins; a single-column title block
followed by two equal 3.5 in columns with a 0.25 in gutter; Times New Roman 10 pt body, 8 pt
captions. All figures are inline (no floating images) and sized to 3.4 in so each fits one column.
