# Rebuilding the Word manuscript

`IEEE-submission-draft.docx` is **generated** from `IEEE-submission-draft.md`. Edit the markdown,
not the Word file, or the two will diverge - which is the exact failure this project has already
been bitten by once, when the paper's prose drifted from the evaluation artefacts.

```bash
node docs/paper/build/build_docx.js \
     docs/paper/IEEE-submission-draft.md \
     docs/paper/IEEE-submission-draft.docx
```

Requires Node with the `docx` package available.

## What the generator does

- US Letter, 1 inch margins, Times New Roman 10 pt, justified body text.
- `##` headings become centred uppercase section headings; `###` become italic subsections, which
  is the IEEE convention.
- Markdown tables become real Word tables with shaded header rows and explicit column widths, so
  they survive a round trip through Google Docs.
- **The HTML figure comments are rendered as visible amber instruction boxes.** They are not
  dropped. An instruction the authors cannot see is an instruction they will not act on; delete the
  boxes once the artwork is placed.
- Figure captions are detected by their `**Fig. N.**` prefix and indented, so they read as captions
  rather than body text.

## Single column, deliberately

IEEE requires its own official template for camera-ready submission, and a two-column Word file
with wide tables is painful to edit. This produces a clean single-column manuscript whose sections
paste cleanly into the official template once the content is final.
