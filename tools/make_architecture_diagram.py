"""Render architecture.png -- the pipeline and its verification gates.

Kept as a script rather than a checked-in binary alone so the diagram can be
regenerated when the pipeline changes:

    python tools/make_architecture_diagram.py
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "architecture.png"
FONTS = ROOT / "assets" / "fonts"


def shaped_devanagari(word: str, pt: float = 34.0):
    """Render `word` through MuPDF/HarfBuzz and return it as an RGB array.

    matplotlib cannot shape Devanagari -- it would draw the codepoints
    one-for-one, which is precisely the failure the diagram claims we avoid.
    So the sample is rendered by the same engine the pipeline typesets with.
    """
    import fitz

    # The url is resolved against the Archive below, so it must stay a bare
    # filename -- a Windows path here gets its backslashes eaten by the CSS parser.
    css = (
        "@font-face { font-family: D; src: url(NotoSerifDevanagari-Regular.ttf); }"
        f"* {{ font-family: D; font-size: {pt:.0f}px; color: #1f6b3c; }}"
    )
    arch = fitz.Archive(str(FONTS))
    doc = fitz.open()
    pg = doc.new_page(width=420, height=90)
    pg.insert_htmlbox(fitz.Rect(4, 4, 416, 86), word, css=css, archive=arch, scale_low=1)
    pix = pg.get_pixmap(dpi=340, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    doc.close()

    mask = (arr < 250).any(axis=2)            # tight crop to the inked pixels
    rows, cols = np.where(mask)
    if len(rows) == 0:
        return arr
    pad = 6
    r0, r1 = max(rows.min() - pad, 0), min(rows.max() + pad + 1, arr.shape[0])
    c0, c1 = max(cols.min() - pad, 0), min(cols.max() + pad + 1, arr.shape[1])
    return arr[r0:r1, c0:c1]


# Palette: one hue per role so the eye can separate flow from checks.
INK = "#12161c"
MUTED = "#5b6673"
STAGE_FACE = "#e8eefb"
STAGE_EDGE = "#3d6fd4"
LLM_FACE = "#fdf0dc"
LLM_EDGE = "#c8851c"
CHECK_FACE = "#e4f4e9"
CHECK_EDGE = "#2f8f52"
CHECK_INK = "#1f6b3c"
STORE_FACE = "#eceef1"
STORE_EDGE = "#7b8493"
IO_FACE = "#f3e9f7"
IO_EDGE = "#8b52ab"
FAIL_FACE = "#fbe9e9"
FAIL_EDGE = "#c0392b"

fig, ax = plt.subplots(figsize=(19, 12.5), dpi=200)
ax.set_xlim(0, 190)
ax.set_ylim(0, 125)
ax.axis("off")
fig.patch.set_facecolor("white")


def box(x, y, w, h, face, edge, *, radius=1.2, lw=1.6, z=2, dashed=False):
    ax.add_patch(FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        facecolor=face, edgecolor=edge, linewidth=lw, zorder=z,
        linestyle=(0, (5, 3)) if dashed else "solid",
    ))


def text(x, y, s, *, size=9, color=INK, weight="normal", ha="left", va="center", z=4):
    return ax.text(x, y, s, fontsize=size, color=color, fontweight=weight, ha=ha,
                   va=va, zorder=z, linespacing=1.5)


def arrow(x1, y1, x2, y2, *, color=STAGE_EDGE, lw=1.8, style="-|>", z=3,
          dashed=False, mut=13):
    ax.add_patch(FancyArrowPatch(
        (x1, y1), (x2, y2), arrowstyle=style, mutation_scale=mut,
        color=color, linewidth=lw, zorder=z,
        linestyle=(0, (4, 3)) if dashed else "solid",
        shrinkA=0, shrinkB=0,
    ))


# ---------------------------------------------------------------- title
text(6, 120.3, "AI Book Translator", size=23, weight="bold")
text(6, 116.0, "English → Marathi, page-faithful PDF  ·  agentic pipeline and verification gates",
     size=11.5, color=MUTED)
ax.plot([6, 184], [113.2, 113.2], color="#c9d0da", lw=1.2, zorder=1)

text(4, 111.1, "ARTIFACTS  &  STATE", size=9, weight="bold", color=MUTED)
text(47, 111.1, "PIPELINE  (run.py)", size=9, weight="bold", color=MUTED)
text(116, 111.1, "VERIFICATION  —  what proves the stage worked", size=9,
     weight="bold", color=MUTED)

# ---------------------------------------------------------------- geometry
SX, SW = 47, 62          # pipeline column
VX, VW = 116, 68         # verification column
AX_, AW = 4, 36          # artifact / state column
H = 9.4                  # stage height
YS = [100.5, 89.5, 78.5, 67.5, 56.5, 45.5, 34.5]     # stage TOP edges
LOOP_X = 44.0            # the qa revise loop runs down this line

STAGES = [
    ("0", "preflight", LLM_FACE, LLM_EDGE,
     "probe model capabilities · fetch fonts\nassert Devanagari shaping before any spend"),
    ("1", "extract", STAGE_FACE, STAGE_EDGE,
     "get_text(\"dict\") → blocks + bbox + emphasis\nextract_image(xref) → original bytes, no re-encode"),
    ("2", "glossary", LLM_FACE, LLM_EDGE,
     "31 sections × ~40k chars → 413 candidates → 75 terms\nappend-only; \"locked\" entries survive rebuilds"),
    ("3", "translate", LLM_FACE, LLM_EDGE,
     "one call per page, 6 concurrent · chapter summary +\nneighbour pages · strict JSON schema · cached prefix"),
    ("4", "review", LLM_FACE, LLM_EDGE,
     "English source + Marathi draft → revised text + notes\na second opinion, not a rubber stamp"),
    ("5", "render", STAGE_FACE, STAGE_EDGE,
     "copy PDF · strip only text · insert_htmlbox\nde-overlap boxes · continuation pages · remap TOC"),
    ("6", "qa", CHECK_FACE, CHECK_EDGE,
     "automated integrity rules + vision inspection of\nevery rendered page, continuation pages included"),
]

CHECKS = [
    "• model caps probed, never assumed: max_completion_tokens,\n"
    "   json_schema ✓, vision ✓, temperature rejected\n"
    "• shaping asserted before a rupee is spent:",
    "• 448,818 / 448,818 alphanumeric chars captured = 100.00%\n"
    "• 0 double spaces, 0 unexpanded ligatures, 0 dangling hyphens\n"
    "• 577 blocks kept their italic / bold emphasis",
    "• every term is injected into every later prompt\n"
    "• compliance re-checked per page in stage 6\n"
    "• append-only, so hand-edits are never overwritten",
    "• schema-constrained output cannot fail to parse\n"
    "• finish_reason == \"length\" raises rather than shipping\n"
    "   a silently truncated page",
    "• revision rate tracked; warns loudly below 1%\n"
    "• measured on the full book: 76.0% of blocks revised\n"
    "   (the previous pipeline silently revised 0%)",
    "• SHA-256 of every image stream re-asserted: 12/12 identical\n"
    "• apply_redactions(images=0, graphics=0, text=0)\n"
    "   — no image is ever decoded, resampled or re-encoded",
    "• blocks not dropped or invented · numbers preserved\n"
    "• glossary compliance · Latin proper nouns · tag balance\n"
    "• vision: clipped text, overlap, broken conjuncts",
]

for (num, name, face, edge, body), ytop, chk in zip(STAGES, YS, CHECKS):
    y = ytop - H
    box(SX, y, SW, H, face, edge)
    box(SX + 1.7, y + H - 6.3, 6.0, 4.8, edge, edge, radius=0.9, lw=0)
    text(SX + 4.7, y + H - 3.95, num, size=12, weight="bold", color="white", ha="center")
    text(SX + 10, y + H - 3.95, name, size=13.5, weight="bold")
    text(SX + 10, y + 2.7, body, size=8.1, color=MUTED)

    box(VX, y, VW, H, "#ffffff", CHECK_EDGE, lw=1.3, dashed=True)
    text(VX + 2.8, y + H - 1.2, chk, size=8.0, color=CHECK_INK, va="top")
    arrow(SX + SW, y + H / 2, VX, y + H / 2, color=CHECK_EDGE, lw=1.3, mut=10, dashed=True)

# The shaped sample is drawn by MuPDF, not matplotlib, so the conjunct really forms.
_s = shaped_devanagari("क्षत्रिय")
_h = 2.3
_w = _h * (_s.shape[1] / _s.shape[0])
_x, _y = VX + 3.4, YS[0] - H + 0.9
ax.imshow(_s, extent=(_x, _x + _w, _y, _y + _h), aspect="auto", zorder=5,
          interpolation="lanczos")
text(_x + _w + 2.0, _y + _h / 2, "8 codepoints  →  4 glyphs", size=8.0, color=CHECK_INK)

for ytop in YS[:-1]:                                   # flow down the column
    arrow(SX + SW / 2, ytop - H, SX + SW / 2, ytop - H - 1.6)

# ---------------------------------------------------------------- input / output
box(SX, 102.5, SW, 6.5, IO_FACE, IO_EDGE)
text(SX + SW / 2, 106.8, "source PDF  ·  296 pp, embedded text layer", size=9.6,
     weight="bold", ha="center")
text(SX + SW / 2, 104.1, "no OCR branch — a scanned PDF is rejected at preflight",
     size=7.8, color=MUTED, ha="center")
arrow(SX + SW / 2, 102.5, SX + SW / 2, 100.5)

box(SX, 15.8, SW, 7.0, IO_FACE, IO_EDGE)
text(SX + SW / 2, 20.4, "Marathi PDF", size=11, weight="bold", ha="center")
text(SX + SW / 2, 17.6, "same page geometry · byte-identical images · selectable text",
     size=7.8, color=MUTED, ha="center")
arrow(SX + SW / 2, YS[-1] - H, SX + SW / 2, 22.8)

# ---------------------------------------------------------------- state spine
SP_BOT = YS[-1] - H
box(AX_, SP_BOT, AW, YS[0] - SP_BOT, STORE_FACE, STORE_EDGE, radius=1.4, lw=1.4)
text(AX_ + AW / 2, 96.6, "workdir/state.db", size=11.5, weight="bold", ha="center")
text(AX_ + AW / 2, 93.4, "SQLite · content-addressed", size=8.2, color=MUTED, ha="center")
ax.plot([AX_ + 3, AX_ + AW - 3], [91.2, 91.2], color=STORE_EDGE, lw=1.0, alpha=0.55)

text(AX_ + 2.6, 88.6, "block_id = sha256(\n        page | index | source_text )[:32]",
     size=7.6, color=INK, va="top")
text(AX_ + 2.6, 82.5,
     "Re-running after an edit re-does only\nwhat actually changed. A positional key\n"
     "would silently desynchronise every\nrow after the edit.",
     size=7.7, color=MUTED, va="top")

for label, sub, yy in [
    ("pages", "geometry, chapter, status", 70.5),
    ("blocks", "bbox, style, draft, final, notes", 64.5),
    ("images", "sha256, original bytes, rects", 58.5),
    ("qa", "per-page findings, attempts", 52.5),
    ("run_log", "stage-level audit trail", 46.5),
]:
    box(AX_ + 2.6, yy - 2.5, AW - 5.2, 5.0, "#ffffff", STORE_EDGE, radius=0.8, lw=1.0)
    text(AX_ + 4.8, yy + 0.7, label, size=8.6, weight="bold")
    text(AX_ + 4.8, yy - 1.4, sub, size=7.2, color=MUTED)

for label, yy in [("glossary.json", 39.5), ("page_map.json", 35.0), ("qa_report.json", 30.5)]:
    text(AX_ + 4.8, yy, "▪   " + label, size=8.0, color=MUTED)

for ytop in YS:                                        # every stage reads/writes state
    arrow(SX, ytop - H / 2, AX_ + AW, ytop - H / 2,
          color=STORE_EDGE, lw=0.9, style="<|-|>", mut=7, z=1)

# ---------------------------------------------------------------- qa revise loop
y6, y3 = YS[6] - H / 2, YS[3] - H / 2
for seg in [((SX, y6), (LOOP_X, y6)), ((LOOP_X, y6), (LOOP_X, y3))]:
    ax.add_patch(FancyArrowPatch(*seg, arrowstyle="-", color=FAIL_EDGE, lw=1.6, zorder=3))
arrow(LOOP_X, y3, SX, y3, color=FAIL_EDGE, lw=1.6, mut=11)
lbl = text(LOOP_X - 1.2, (y3 + y6) / 2, "failed page → re-translate, re-render, re-check",
           size=7.6, color=FAIL_EDGE, ha="center", weight="bold")
lbl.set_rotation(90)

# ---------------------------------------------------------------- verify band
box(VX, 4.2, VW, 18.2, CHECK_FACE, CHECK_EDGE, lw=1.8)
text(VX + 3.2, 19.6, "run.py verify   —   8 acceptance checks", size=11.5,
     weight="bold", color=CHECK_INK)
text(VX + 3.2, 16.4,
     "1   image streams byte-identical (SHA-256)\n"
     "2   every block has a Marathi translation\n"
     "3   no passthrough English\n"
     "4   no number, date, % or amount dropped", size=7.6, color=CHECK_INK, va="top")
text(VX + 34, 16.4,
     "5   Devanagari font embedded\n"
     "6   text layer selectable, no tofu\n"
     "7   outline translated, targets valid\n"
     "8   every page kept its dimensions", size=7.6, color=CHECK_INK, va="top")
arrow(SX + SW, 19.3, VX, 15.5, color=CHECK_EDGE, lw=1.5, mut=11)

# ---------------------------------------------------------------- fail loudly
box(AX_, 4.8, 105 - AX_, 9.0, FAIL_FACE, FAIL_EDGE, lw=1.5)
text(AX_ + 3.0, 11.4, "Fail loudly", size=10.5, weight="bold", color="#a5281b")
text(AX_ + 3.0, 9.2,
     "Every failure path raises and exits non-zero — a bad API key exits 1, and rendering a page with no translation refuses\n"
     "with “refusing to emit English into a Marathi PDF”. The previous implementation caught it and printed SUCCESS!.",
     size=7.9, color="#8c2f22", va="top")

# ---------------------------------------------------------------- legend
for i, (lab, f, e) in enumerate([
    ("model call", LLM_FACE, LLM_EDGE),
    ("local / deterministic", STAGE_FACE, STAGE_EDGE),
    ("check", CHECK_FACE, CHECK_EDGE),
    ("state", STORE_FACE, STORE_EDGE),
    ("input / output", IO_FACE, IO_EDGE),
]):
    x = 6 + i * 24
    box(x, 1.0, 3.0, 2.4, f, e, radius=0.5, lw=1.2)
    text(x + 4.2, 2.2, lab, size=8.0, color=MUTED)

fig.savefig(OUT, dpi=200, bbox_inches="tight", facecolor="white", pad_inches=0.3)
print(f"wrote {OUT}  ({OUT.stat().st_size / 1024:.0f} KB)")
