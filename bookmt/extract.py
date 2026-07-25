"""Stage 1 -- PDF into text blocks and original image bytes.

Three defects in the previous extractor are fixed here:

1. Spurious spaces. The old code did `text += span["text"] + " "`, inserting a
   space wherever a PDF happened to split a word across spans. Real output from
   it: "E<TAB>cient Image Super-Resolution   Sep 2024..." -- heading, date and
   body glued into one blob. Spans within a line are now joined with no
   separator; lines are joined with a space; hyphenated line-breaks are healed.
2. Lost emphasis. Span font/flags were discarded, so CharisSIL-Italic emphasis
   vanished. Italic and bold runs are now recorded as character ranges.
3. Discarded images. `extract_images(...)` was assigned to a variable that was
   never read again. Images are now stored with their sha256 and their
   placement rectangles.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

import fitz

from .config import Config
from .state import State

# PyMuPDF span flag bits
FLAG_ITALIC = 1 << 1
FLAG_BOLD = 1 << 4

LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl",
    "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
}
PUNCT = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    " ": " ", " ": " ", " ": " ",
}


def normalise(text: str) -> str:
    """Expand ligatures and flatten smart punctuation.

    The source book uses CharisSIL, which emits real ligature codepoints
    (U+FB03 appears on page 1). Left alone these confuse tokenisation and
    round-trip badly; they are not meaningful to the translation.
    """
    for a, b in LIGATURES.items():
        text = text.replace(a, b)
    for a, b in PUNCT.items():
        text = text.replace(a, b)
    text = unicodedata.normalize("NFC", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _span_style(span: dict) -> tuple[bool, bool]:
    """(italic, bold) from both the flag bits and the font name."""
    flags = span.get("flags", 0)
    font = span.get("font", "") or ""
    italic = bool(flags & FLAG_ITALIC) or "italic" in font.lower() or "oblique" in font.lower()
    bold = bool(flags & FLAG_BOLD) or "bold" in font.lower() or "black" in font.lower()
    return italic, bold


def assemble_block(block: dict) -> tuple[str, dict]:
    """Turn a PyMuPDF text block into clean text plus style metadata.

    Returns (text, style) where style carries the dominant font size and the
    italic/bold character ranges, so the renderer can re-emit <i>/<b>.
    """
    text_parts: list[str] = []
    runs: list[dict] = []
    sizes: list[float] = []
    cursor = 0

    lines = block.get("lines", [])
    for li, line in enumerate(lines):
        line_start = cursor
        for span in line.get("spans", []):
            raw = span.get("text", "")
            if not raw:
                continue
            # No separator: PDFs split words across spans mid-word.
            piece = raw
            for a, b in LIGATURES.items():
                piece = piece.replace(a, b)
            for a, b in PUNCT.items():
                piece = piece.replace(a, b)
            if not piece:
                continue
            italic, bold = _span_style(span)
            sizes.append(round(span.get("size", 0.0), 2))
            text_parts.append(piece)
            if italic or bold:
                runs.append({"start": cursor, "end": cursor + len(piece),
                             "i": italic, "b": bold})
            cursor += len(piece)

        if li < len(lines) - 1 and cursor > line_start:
            joined = "".join(text_parts)
            # Heal a hyphenated line break rather than leaving "super-\nresolution".
            if joined.endswith("-") and not joined.endswith("--"):
                text_parts[-1] = text_parts[-1][:-1]
                cursor -= 1
                if runs and runs[-1]["end"] > cursor:
                    runs[-1]["end"] = cursor
            else:
                text_parts.append(" ")
                cursor += 1

    text = "".join(text_parts)
    # Collapse runs of whitespace, keeping the run offsets consistent.
    cleaned, offset_map = _collapse_ws(text)
    runs = _remap_runs(runs, offset_map, len(cleaned))

    dominant = max(set(sizes), key=sizes.count) if sizes else 10.0
    style = {
        "size": dominant,
        "size_max": max(sizes) if sizes else dominant,
        "runs": _merge_runs(runs),
    }
    return unicodedata.normalize("NFC", cleaned), style


def _collapse_ws(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace, returning the cleaned text and old->new index map."""
    out: list[str] = []
    mapping: list[int] = []
    prev_space = False
    for ch in text:
        is_space = ch in " \t\r\n"
        if is_space:
            if prev_space:
                mapping.append(len(out))
                continue
            ch = " "
        mapping.append(len(out))
        out.append(ch)
        prev_space = is_space
    mapping.append(len(out))
    s = "".join(out)
    lstrip = len(s) - len(s.lstrip())
    stripped = s.strip()
    if lstrip:
        mapping = [max(0, m - lstrip) for m in mapping]
    return stripped, mapping


def _remap_runs(runs: list[dict], mapping: list[int], length: int) -> list[dict]:
    out = []
    for r in runs:
        s = mapping[min(r["start"], len(mapping) - 1)]
        e = mapping[min(r["end"], len(mapping) - 1)]
        s, e = max(0, min(s, length)), max(0, min(e, length))
        if e > s:
            out.append({"start": s, "end": e, "i": r["i"], "b": r["b"]})
    return out


def _merge_runs(runs: list[dict]) -> list[dict]:
    """Merge adjacent runs with identical styling."""
    if not runs:
        return []
    runs = sorted(runs, key=lambda r: r["start"])
    merged = [runs[0]]
    for r in runs[1:]:
        last = merged[-1]
        if r["start"] <= last["end"] and r["i"] == last["i"] and r["b"] == last["b"]:
            last["end"] = max(last["end"], r["end"])
        else:
            merged.append(r)
    return merged


def chapter_map(doc: fitz.Document) -> dict[int, str]:
    """Map every page to the title of the TOC entry it falls under."""
    toc = doc.get_toc()
    if not toc:
        return {}
    marks = sorted(((page, title) for _lvl, title, page in toc if page > 0),
                   key=lambda t: t[0])
    out: dict[int, str] = {}
    for idx, (start, title) in enumerate(marks):
        end = marks[idx + 1][0] - 1 if idx + 1 < len(marks) else len(doc)
        for p in range(start, end + 1):
            out[p] = title
    return out


def extract_images(doc: fitz.Document, out_dir: Path, st: State) -> int:
    """Write original compressed image streams byte-for-byte. No re-encoding.

    `doc.extract_image(xref)` hands back the stream exactly as stored, so a
    source JPEG stays that JPEG -- no resampling, no colourspace conversion,
    no DPI change. This is what makes the "pixel perfect" claim checkable:
    Stage 5 asserts these same hashes survive into the output PDF.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    seen: set[int] = set()
    for pno in range(len(doc)):
        page = doc[pno]
        for img in page.get_images(full=True):
            xref = img[0]
            if xref in seen:
                continue
            seen.add(xref)
            info = doc.extract_image(xref)
            data = info["image"]
            ext = info["ext"]
            sha = hashlib.sha256(data).hexdigest()
            fname = f"p{pno + 1:04d}_x{xref}.{ext}"
            (out_dir / fname).write_bytes(data)
            try:
                rects = [tuple(round(v, 2) for v in r) for r in page.get_image_rects(xref)]
            except Exception:
                rects = []
            st.upsert_image(xref, pno + 1, sha, ext, info.get("width", 0),
                            info.get("height", 0), len(data), rects, fname)
    return len(seen)


def run(cfg: Config, st: State) -> dict:
    pdf_path = cfg.path("paths.input_pdf")
    print(f"\n[1/6] EXTRACT  {pdf_path.name}")
    doc = fitz.open(pdf_path)
    chapters = chapter_map(doc)

    n_blocks = 0
    n_textless = 0
    with st.transaction():
        for pno in range(len(doc)):
            page = doc[pno]
            page_no = pno + 1
            rect = page.rect
            raw = page.get_text("dict")

            blocks = []
            for b in raw.get("blocks", []):
                if b.get("type") != 0:
                    continue
                text, style = assemble_block(b)
                if not text or not text.strip():
                    continue
                blocks.append((tuple(round(v, 2) for v in b["bbox"]), text, style))

            has_text = bool(blocks)
            if not has_text:
                n_textless += 1
            st.upsert_page(page_no, rect.width, rect.height, page.rotation,
                           has_text, chapters.get(page_no))

            for idx, (bbox, text, style) in enumerate(blocks):
                st.upsert_block(page_no, idx, bbox, text, style)
                n_blocks += 1

            if page_no % 50 == 0:
                print(f"    ...{page_no}/{len(doc)} pages")

    print(f"    extracting images losslessly -> {cfg.path('paths.images_dir')}")
    n_imgs = extract_images(doc, cfg.path("paths.images_dir"), st)
    st.conn.commit()
    doc.close()

    summary = {"pages": st.page_count(), "blocks": n_blocks,
               "textless_pages": n_textless, "images": n_imgs}
    print(f"    {summary['pages']} pages | {n_blocks:,} blocks | "
          f"{n_imgs} images | {n_textless} image-only pages")
    st.log("extract", json.dumps(summary))
    return summary
