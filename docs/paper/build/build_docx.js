// Build an editable IEEE-style .docx from the submission draft.
//
// Single column deliberately: IEEE requires its own official template for camera-ready, and a
// two-column Word file with wide tables is painful to edit. This produces a clean, correctly
// styled manuscript whose sections paste cleanly into the official template.

const fs = require("fs");
const path = require("path");
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  Table, TableRow, TableCell, WidthType, ShadingType, BorderStyle,
  LevelFormat, convertInchesToTwip,
} = require("docx");

const SRC = process.argv[2];
const OUT = process.argv[3];
const md = fs.readFileSync(SRC, "utf8").replace(/\r\n/g, "\n");
const lines = md.split("\n");

const FONT = "Times New Roman";
const PAGE_W = 12240, PAGE_H = 15840; // US Letter, DXA
const MARGIN = convertInchesToTwip(1);
const CONTENT_W = PAGE_W - 2 * MARGIN;

// ---- inline formatting -------------------------------------------------------------------------
// Splits on **bold**, *italic* and `code`, preserving order. Deliberately simple: the source is
// our own markdown, not arbitrary input.
function runs(text, base = {}) {
  const out = [];
  const re = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;
  let last = 0, m;
  const push = (t, extra) => {
    if (!t) return;
    out.push(new TextRun({ text: t, font: FONT, size: 20, ...base, ...extra }));
  };
  while ((m = re.exec(text)) !== null) {
    push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) push(tok.slice(2, -2), { bold: true });
    else if (tok.startsWith("`")) push(tok.slice(1, -1), { font: "Consolas", size: 18 });
    else push(tok.slice(1, -1), { italics: true });
    last = m.index + tok.length;
  }
  push(text.slice(last));
  return out.length ? out : [new TextRun({ text: "", font: FONT, size: 20, ...base })];
}

const body = (text, opts = {}) =>
  new Paragraph({
    children: runs(text),
    alignment: AlignmentType.JUSTIFIED,
    spacing: { after: 120, line: 259 },
    ...opts,
  });

// ---- figure-instruction box --------------------------------------------------------------------
// The HTML comments in the source carry placement instructions the authors must act on. Rendering
// them as a visible shaded box is the point: an instruction they cannot see is an instruction they
// will not follow.
function instructionBox(text) {
  const rows = text.split("\n").map((l) => l.replace(/^\s*/, "")).filter(Boolean);
  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: [CONTENT_W],
    rows: [
      new TableRow({
        children: [
          new TableCell({
            width: { size: CONTENT_W, type: WidthType.DXA },
            shading: { type: ShadingType.CLEAR, fill: "FFF4CE" },
            margins: { top: 120, bottom: 120, left: 160, right: 160 },
            children: rows.map(
              (l, i) =>
                new Paragraph({
                  spacing: { after: i === rows.length - 1 ? 0 : 60 },
                  children: [
                    new TextRun({ text: l, font: FONT, size: 18, italics: true, color: "6B4E00" }),
                  ],
                })
            ),
          }),
        ],
      }),
    ],
  });
}

// ---- tables ------------------------------------------------------------------------------------
function splitRow(line) {
  return line.replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
}

function buildTable(block) {
  const rows = block.filter((l) => !/^\|[\s:|-]+\|$/.test(l)).map(splitRow);
  if (!rows.length) return null;
  const n = Math.max(...rows.map((r) => r.length));
  const colW = Math.floor(CONTENT_W / n);
  const widths = Array(n).fill(colW);
  widths[n - 1] = CONTENT_W - colW * (n - 1);

  return new Table({
    width: { size: CONTENT_W, type: WidthType.DXA },
    columnWidths: widths,
    rows: rows.map(
      (cells, ri) =>
        new TableRow({
          tableHeader: ri === 0,
          children: Array.from({ length: n }, (_, ci) => {
            const raw = cells[ci] ?? "";
            return new TableCell({
              width: { size: widths[ci], type: WidthType.DXA },
              shading:
                ri === 0 ? { type: ShadingType.CLEAR, fill: "E8E8E8" } : undefined,
              margins: { top: 60, bottom: 60, left: 100, right: 100 },
              children: [
                new Paragraph({
                  spacing: { after: 0 },
                  children: runs(raw, ri === 0 ? { bold: true } : {}),
                }),
              ],
            });
          }),
        })
    ),
  });
}

// ---- main walk ---------------------------------------------------------------------------------
const children = [];
let i = 0;
let seenTitle = false;

while (i < lines.length) {
  const line = lines[i];
  const t = line.trim();

  // HTML comment -> visible instruction box
  if (t.startsWith("<!--")) {
    const buf = [];
    while (i < lines.length && !lines[i].includes("-->")) {
      buf.push(lines[i].replace(/<!--/, "").replace(/=+/g, "").trim());
      i++;
    }
    if (i < lines.length) {
      buf.push(lines[i].replace("-->", "").replace(/=+/g, "").trim());
      i++;
    }
    children.push(instructionBox(buf.filter(Boolean).join("\n")));
    children.push(new Paragraph({ spacing: { after: 120 }, children: [] }));
    continue;
  }

  if (!t) { i++; continue; }

  if (t === "---") {
    children.push(
      new Paragraph({
        spacing: { before: 120, after: 120 },
        border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: "AAAAAA", space: 1 } },
        children: [],
      })
    );
    i++; continue;
  }

  // Tables
  if (t.startsWith("|")) {
    const block = [];
    while (i < lines.length && lines[i].trim().startsWith("|")) { block.push(lines[i].trim()); i++; }
    const tbl = buildTable(block);
    if (tbl) {
      children.push(tbl);
      children.push(new Paragraph({ spacing: { after: 160 }, children: [] }));
    }
    continue;
  }

  // Headings
  if (t.startsWith("#")) {
    const level = (t.match(/^#+/) || ["#"])[0].length;
    const text = t.replace(/^#+\s*/, "");
    if (level === 1 && !seenTitle) {
      seenTitle = true;
      children.push(
        new Paragraph({
          alignment: AlignmentType.CENTER,
          spacing: { after: 240 },
          children: [new TextRun({ text, font: FONT, size: 48, bold: true })],
        })
      );
    } else {
      children.push(
        new Paragraph({
          heading: level <= 2 ? HeadingLevel.HEADING_1 : HeadingLevel.HEADING_2,
          spacing: { before: 240, after: 120 },
          alignment: level <= 2 ? AlignmentType.CENTER : AlignmentType.LEFT,
          children: [
            new TextRun({
              text: level <= 2 ? text.toUpperCase() : text,
              font: FONT,
              size: level <= 2 ? 22 : 21,
              bold: level <= 2,
              italics: level > 2,
            }),
          ],
        })
      );
    }
    i++; continue;
  }

  // Blockquote (the drafting note)
  if (t.startsWith(">")) {
    const buf = [];
    while (i < lines.length && lines[i].trim().startsWith(">")) {
      buf.push(lines[i].trim().replace(/^>\s?/, "")); i++;
    }
    // A quote block may itself contain a table (the figure inventory).
    let j = 0;
    while (j < buf.length) {
      if (buf[j].startsWith("|")) {
        const blk = [];
        while (j < buf.length && buf[j].startsWith("|")) { blk.push(buf[j]); j++; }
        const tbl = buildTable(blk);
        if (tbl) { children.push(tbl); children.push(new Paragraph({ spacing: { after: 120 }, children: [] })); }
        continue;
      }
      if (buf[j]) {
        children.push(
          new Paragraph({
            spacing: { after: 80 },
            indent: { left: 360 },
            border: { left: { style: BorderStyle.SINGLE, size: 12, color: "C00000", space: 8 } },
            children: runs(buf[j], { color: "7A0000" }),
          })
        );
      }
      j++;
    }
    continue;
  }

  // Bullet list
  if (/^[-*]\s+/.test(t)) {
    const text = t.replace(/^[-*]\s+/, "");
    const cont = [];
    i++;
    while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*[-*]\s/.test(lines[i])) {
      cont.push(lines[i].trim()); i++;
    }
    children.push(
      new Paragraph({
        bullet: { level: 0 },
        spacing: { after: 80 },
        alignment: AlignmentType.JUSTIFIED,
        children: runs([text, ...cont].join(" ")),
      })
    );
    continue;
  }

  // Numbered list
  if (/^\d+\.\s+/.test(t)) {
    const text = t.replace(/^\d+\.\s+/, "");
    const cont = [];
    i++;
    while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*\d+\.\s/.test(lines[i])) {
      cont.push(lines[i].trim()); i++;
    }
    children.push(
      new Paragraph({
        numbering: { reference: "num", level: 0 },
        spacing: { after: 80 },
        alignment: AlignmentType.JUSTIFIED,
        children: runs([text, ...cont].join(" ")),
      })
    );
    continue;
  }

  // Ordinary paragraph: gather wrapped lines
  const buf = [t];
  i++;
  while (
    i < lines.length &&
    lines[i].trim() &&
    !lines[i].trim().startsWith("#") &&
    !lines[i].trim().startsWith("|") &&
    !lines[i].trim().startsWith(">") &&
    !lines[i].trim().startsWith("<!--") &&
    lines[i].trim() !== "---" &&
    !/^[-*]\s+/.test(lines[i].trim()) &&
    !/^\d+\.\s+/.test(lines[i].trim())
  ) { buf.push(lines[i].trim()); i++; }

  const text = buf.join(" ");
  const isCaption = /^\*\*Fig\. \d+\.\*\*/.test(text);
  children.push(
    new Paragraph({
      children: runs(text),
      alignment: isCaption ? AlignmentType.LEFT : AlignmentType.JUSTIFIED,
      spacing: { after: isCaption ? 240 : 120, line: 259 },
      ...(isCaption ? { indent: { left: 180 } } : {}),
    })
  );
}

const doc = new Document({
  creator: "Anushk Agrawal, Bhoomi Airen, Abhijay Krishnan",
  title: "Evidence, Not Commands",
  description: "Deterministic-first multi-agent RCA and human-governed remediation",
  numbering: {
    config: [
      {
        reference: "num",
        levels: [
          {
            level: 0,
            format: LevelFormat.DECIMAL,
            text: "%1.",
            alignment: AlignmentType.START,
            style: { paragraph: { indent: { left: 720, hanging: 360 } } },
          },
        ],
      },
    ],
  },
  styles: {
    default: {
      document: { run: { font: FONT, size: 20 } },
    },
  },
  sections: [
    {
      properties: {
        page: { size: { width: PAGE_W, height: PAGE_H }, margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN } },
      },
      children,
    },
  ],
});

Packer.toBuffer(doc).then((buf) => {
  fs.writeFileSync(OUT, buf);
  console.log("wrote", OUT, (buf.length / 1024).toFixed(0) + " KB", "|", children.length, "blocks");
});
