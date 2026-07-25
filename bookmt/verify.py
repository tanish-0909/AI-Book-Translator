"""Acceptance checks on the finished PDF.

Everything here is falsifiable. The point is that "pixel perfect images" and
"nothing was dropped" are assertions that pass or fail, not opinions.

    python run.py verify
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import fitz

from .config import Config
from .qa import NUMERIC, latinise_digits, looks_like_title, translatable_words
from .state import State
from .translate import strip_markup

DEVANAGARI = re.compile(r"[ऀ-ॿ]")


class Check:
    def __init__(self, name: str):
        self.name = name
        self.ok = True
        self.lines: list[str] = []

    def fail(self, msg: str) -> None:
        self.ok = False
        self.lines.append(msg)

    def note(self, msg: str) -> None:
        self.lines.append(msg)

    def report(self) -> bool:
        mark = "PASS" if self.ok else "FAIL"
        print(f"  [{mark}] {self.name}")
        for line in self.lines:
            print(f"         {line}")
        return self.ok


def _out_pdf(cfg: Config) -> Path:
    src = cfg.path("paths.input_pdf")
    return cfg.path("paths.output_dir") / f"{src.stem} - Marathi.pdf"


def check_images(cfg: Config, st: State, out: Path) -> Check:
    c = Check("Images are byte-identical to the source")
    expected = {r["sha256"]: r for r in st.images()}
    if not expected:
        c.note("no images in source")
        return c

    doc = fitz.open(out)
    found: set[str] = set()
    for pno in range(len(doc)):
        for img in doc[pno].get_images(full=True):
            data = doc.extract_image(img[0])["image"]
            found.add(hashlib.sha256(data).hexdigest())
    doc.close()

    missing = [s for s in expected if s not in found]
    if missing:
        for s in missing[:5]:
            r = expected[s]
            c.fail(f"altered/lost: page {r['page_no']} xref {r['xref']} "
                   f"{r['width']}x{r['height']} {r['ext']}")
    else:
        total = sum(r["n_bytes"] for r in expected.values())
        c.note(f"{len(expected)}/{len(expected)} streams identical "
               f"({total:,} bytes, no re-encoding)")
    return c


def check_translation_complete(st: State) -> Check:
    c = Check("Every block has a Marathi translation")
    rows = st.conn.execute(
        "SELECT COUNT(*) total,"
        " SUM(CASE WHEN final IS NULL AND draft IS NULL THEN 1 ELSE 0 END) untranslated,"
        " SUM(CASE WHEN final IS NULL THEN 1 ELSE 0 END) unreviewed FROM blocks"
    ).fetchone()
    total, untranslated, unreviewed = rows[0], rows[1] or 0, rows[2] or 0
    if untranslated:
        c.fail(f"{untranslated}/{total} blocks have no translation at all")
    else:
        c.note(f"{total:,} blocks translated")
    if unreviewed:
        c.note(f"note: {unreviewed} blocks not yet through the review stage")
    return c


# Blocks inspected by hand and judged correct despite tripping a rule. Listing
# them keeps the checks strict -- anything NOT on this list still fails -- while
# recording the judgement and its reason in the open.
REVIEWED_EXCEPTIONS: dict[tuple[int, int], str] = {
    (51, 0): "'warning bells.' -- translation.idioms=keep_english keeps English "
             "idioms inline by design; the model reproduced this across three "
             "forced re-translations. Policy behaviour, not a miss.",
    (243, 5): "'late 20s' -> 'वयाच्या विशीच्या उत्तरार्धात' -- the number is "
              "correctly expressed in words, so the numeral is absent by choice.",
}


def _excused(page_no: int, block_index: int) -> str | None:
    return REVIEWED_EXCEPTIONS.get((page_no, block_index))


def _merged_units(st: State) -> list[dict]:
    """Blocks grouped exactly as render.merge_fragments() will draw them."""
    from .render import merge_fragments

    out: list[dict] = []
    for p in st.pages():
        blocks = st.blocks_for_page(p["page_no"])
        if not blocks:
            continue
        srcs = {b["block_index"]: (b["source_text"] or "") for b in blocks}
        for g in merge_fragments(blocks):
            out.append({
                "page_no": p["page_no"],
                "block_index": g["indices"][0],
                "source_text": " ".join(srcs[i] for i in g["indices"]).strip(),
                "t": g["text"],
            })
    return out


def check_devanagari(st: State) -> Check:
    c = Check("Output is Marathi, not passthrough English")
    rows = st.conn.execute(
        "SELECT 1 FROM blocks WHERE COALESCE(final, draft) IS NOT NULL LIMIT 1"
    ).fetchall()
    if not rows:
        c.fail("no translated blocks")
        return c
    # Evaluate the units the renderer actually draws. MuPDF splits paragraphs,
    # so a lone block can be the tail of a URL ("...copy-" / "success.") or of a
    # quoted English phrase ('"Can I do' / 'this AND that?"'). Judged alone each
    # looks like untranslated English; judged as the merged paragraph it is not.
    rows = _merged_units(st)

    no_deva = [r for r in rows if not DEVANAGARI.search(strip_markup(r["t"]))]
    identical = [r for r in rows if strip_markup(r["t"]).strip() == r["source_text"].strip()]
    # Latin-only output is only a defect when the source held prose. Titles,
    # bibliographic data, attributions, bare numerals and ornaments are supposed
    # to stay in Latin under the proper-noun policy.
    def is_prose(r) -> bool:
        s = r["source_text"] or ""
        if _excused(r["page_no"], r["block_index"]):
            return False
        return bool(translatable_words(s)) and not looks_like_title(s)

    substantive = [r for r in no_deva if is_prose(r)]
    passthrough = [r for r in identical if is_prose(r)]
    if substantive:
        c.fail(f"{len(substantive)} blocks of English prose left untranslated, e.g. "
               f"p{substantive[0]['page_no']}:{substantive[0]['block_index']} "
               f"{r_short(substantive[0]['source_text'])}")
    if passthrough:
        c.fail(f"{len(passthrough)} blocks identical to the English source, e.g. "
               f"p{passthrough[0]['page_no']}:{passthrough[0]['block_index']} "
               f"{r_short(passthrough[0]['source_text'])}")
    if not substantive and not passthrough:
        c.note(f"{len(rows):,} merged units translated; {len(identical)} left in "
               f"Latin by policy (titles, ISBNs, numerals, attributions)")
        if REVIEWED_EXCEPTIONS:
            c.note(f"{len(REVIEWED_EXCEPTIONS)} reviewed exceptions "
                   f"({', '.join(f'p{p}:{b}' for p, b in REVIEWED_EXCEPTIONS)})")
    return c


def r_short(s: str, n: int = 40) -> str:
    s = " ".join((s or "").split())
    return repr(s if len(s) <= n else s[:n] + "…")


def check_no_text_dropped(cfg: Config, st: State, out_path: Path,
                          sample: int = 40) -> Check:
    """Prove the layout dropped nothing, without trusting text extraction.

    Extracted text is unreliable for Devanagari: MuPDF composes a conjunct into
    one glyph whose ToUnicode entry is a single private-use codepoint, so
    'वर्षां' comes back as 'वȴषा'. Counting characters therefore understates the
    content by ~20% even when the page is perfect.

    So count the ink-bearing GLYPHS actually painted, and compare with what the
    same engine paints when given the whole expected text with room to spare.
    Whitespace is excluded because a narrower column drops more line-end spaces.
    """
    from . import typeset
    from .render import _document_frame, merge_fragments

    c = Check("No text dropped by the layout (glyph audit)")
    try:
        pmap = json.loads((cfg.path("paths.workdir") / "page_map.json").read_text())
    except Exception:
        c.fail("page_map.json missing -- run render first")
        return c

    arch = typeset.archive(cfg.path("paths.font_dir"))
    out = fitz.open(out_path)
    # Group exactly as the renderer does -- a different column edge would merge
    # blocks differently and change the expected count for reasons unrelated to
    # anything being dropped.
    src = fitz.open(cfg.path("paths.input_pdf"))
    doc_frame = _document_frame(st, src[0].rect)
    page_w = src[0].rect.width
    src.close()

    def ink(page) -> int:
        d = page.get_text("rawdict")
        return sum(1 for blk in d["blocks"] if blk["type"] == 0
                   for ln in blk["lines"] for sp in ln["spans"]
                   for ch in sp["chars"] if not ch["c"].isspace())

    def ink_for(text: str, size: float) -> int:
        doc = fitz.open()
        pg = doc.new_page(width=612, height=3000)
        pg.insert_htmlbox(fitz.Rect(20, 20, 592, 2980), typeset.to_html(text),
                          css=typeset.build_css(size), archive=arch, scale_low=1)
        n = ink(pg)
        doc.close()
        return n

    pages = [p["page_no"] for p in st.pages() if st.blocks_for_page(p["page_no"])]
    step = max(1, len(pages) // sample)
    chosen = pages[::step][:sample]

    exp_total = got_total = 0
    short = []
    for pno in chosen:
        blocks = st.blocks_for_page(pno)
        exp = sum(ink_for(g["text"], g["size"])
                  for g in merge_fragments(blocks, doc_frame.x1, page_w))
        got = ink(out[pmap[str(pno)]])
        exp_total += exp
        got_total += got
        # Tolerate 2 glyphs: the reference is typeset in a wider box, and a
        # different line break can legitimately change whether one cluster forms
        # a ligature. A dropped word would be tens of glyphs, not two.
        if got < exp - 2:
            short.append((pno, exp - got))
    out.close()

    if short:
        worst = sorted(short, key=lambda t: -t[1])[:3]
        c.fail(f"{len(short)} of {len(chosen)} sampled pages painted fewer glyphs "
               f"than expected; worst: " +
               ", ".join(f"p{p} short {d}" for p, d in worst))
    else:
        c.note(f"{got_total:,} ink glyphs across {len(chosen)} sampled pages; "
               f"none missing (expected {exp_total:,})")
    return c


def check_numbers(st: State) -> Check:
    c = Check("Numbers, dates and amounts preserved")
    num = NUMERIC      # shared with qa.py so both stages agree on what a number is
    rows = st.conn.execute(
        "SELECT page_no, block_index, source_text, COALESCE(final, draft) t "
        "FROM blocks WHERE COALESCE(final, draft) IS NOT NULL"
    ).fetchall()

    # Compare per page, not per block. MuPDF splits paragraphs across blocks, so
    # "envelopes versus 1." lands in one block while its "1" sits in the sibling
    # the renderer merges it with -- a block-level check calls that a loss.
    by_page: dict[int, list] = {}
    for r in rows:
        by_page.setdefault(r["page_no"], []).append(r)

    bad = []
    for pno, prs in by_page.items():
        keep = [p for p in prs if not _excused(pno, p["block_index"])]
        src = num.findall(" ".join(p["source_text"] or "" for p in keep))
        tgt = num.findall(latinise_digits(" ".join(p["t"] or "" for p in prs)))
        lost = [n for n in src if n not in tgt]
        if lost:
            bad.append((pno, keep[0]["block_index"] if keep else 0, lost[:4]))
    bad.sort()
    if bad:
        c.fail(f"{len(bad)} blocks dropped a number; first: "
               f"p{bad[0][0]}:{bad[0][1]} lost {bad[0][2]}")
    else:
        c.note("no numeric tokens lost")
    return c


def _rendered_indices(cfg: Config, st: State, limit: int = 25) -> list[int]:
    """Output page indices that actually received a translation.

    Sampling a fixed page range would report a false failure whenever only part
    of the book has been rendered (e.g. `render --pages 100-104`).
    """
    map_path = cfg.path("paths.workdir") / "page_map.json"
    mapping = ({int(k): int(v) for k, v in json.loads(map_path.read_text(encoding="utf-8")).items()}
               if map_path.exists() else {})
    rows = st.conn.execute(
        "SELECT DISTINCT page_no FROM blocks WHERE COALESCE(final, draft) IS NOT NULL "
        "ORDER BY page_no"
    ).fetchall()
    pages = [r[0] for r in rows]
    if not pages:
        return []
    step = max(1, len(pages) // limit)
    return [mapping.get(p, p - 1) for p in pages[::step]][:limit]


def check_fonts(cfg: Config, st: State, out: Path) -> Check:
    c = Check("Fonts are embedded in the PDF")
    indices = _rendered_indices(cfg, st)
    if not indices:
        c.fail("nothing has been translated yet")
        return c
    doc = fitz.open(out)
    fonts: set[str] = set()
    for i in indices:
        if i < len(doc):
            for f in doc[i].get_fonts(full=True):
                fonts.add(f[3])
    doc.close()
    if not any("Devanagari" in f for f in fonts):
        c.fail(f"no Devanagari face embedded; found {sorted(fonts)[:6]}")
    else:
        c.note(f"embedded: {', '.join(sorted(fonts)[:6])}")
    return c


def check_text_layer(cfg: Config, st: State, out: Path) -> Check:
    c = Check("Output text is selectable and correctly shaped")
    indices = _rendered_indices(cfg, st)
    if not indices:
        c.fail("nothing has been translated yet")
        return c
    doc = fitz.open(out)
    sample = "".join(doc[i].get_text("text") for i in indices if i < len(doc))
    doc.close()
    if not DEVANAGARI.search(sample):
        c.fail("no Devanagari recoverable from the output text layer")
        return c
    # Tofu boxes indicate a glyph the font could not draw.
    if "�" in sample or "□" in sample:
        c.fail("replacement/tofu characters present in the text layer")
    c.note(f"{len(sample):,} chars extractable across {len(indices)} sampled pages")
    return c


def check_toc(cfg: Config, out: Path) -> Check:
    c = Check("Outline translated and page numbers remapped")
    doc = fitz.open(out)
    toc = doc.get_toc()
    n_pages = len(doc)
    doc.close()
    if not toc:
        c.note("source had no outline")
        return c
    bad_pages = [t for t in toc if not (1 <= t[2] <= n_pages)]
    if bad_pages:
        c.fail(f"{len(bad_pages)} outline entries point outside the document")
    translated = sum(1 for t in toc if DEVANAGARI.search(t[1]))
    if translated == 0:
        c.fail(f"none of the {len(toc)} outline entries are in Marathi")
    else:
        c.note(f"{translated}/{len(toc)} outline entries in Marathi, all page targets valid")
    return c


def check_page_model(cfg: Config, st: State, out: Path) -> Check:
    c = Check("Page geometry preserved")
    src = fitz.open(cfg.path("paths.input_pdf"))
    dst = fitz.open(out)
    map_path = cfg.path("paths.workdir") / "page_map.json"
    mapping = ({int(k): int(v) for k, v in json.loads(map_path.read_text(encoding="utf-8")).items()}
               if map_path.exists() else {})
    n_src, n_dst = len(src), len(dst)
    mismatched = 0
    for s_pno in range(n_src):
        d_idx = mapping.get(s_pno + 1, s_pno)
        if d_idx >= n_dst:
            continue
        if (round(src[s_pno].rect.width) != round(dst[d_idx].rect.width)
                or round(src[s_pno].rect.height) != round(dst[d_idx].rect.height)):
            mismatched += 1
    src.close()
    dst.close()

    if mismatched:
        c.fail(f"{mismatched} pages changed size")
    else:
        c.note(f"all {n_src} source pages keep their original dimensions")
    c.note(f"source {n_src} pages -> output {n_dst} "
           f"({n_dst - n_src} continuation pages inserted)")
    return c


def run(cfg: Config, st: State) -> bool:
    out = _out_pdf(cfg)
    print(f"\n  VERIFY  {out.name}")
    if not out.exists():
        print(f"  [FAIL] output not found: {out}\n         run `python run.py render` first")
        return False

    checks = [
        check_images(cfg, st, out),
        check_translation_complete(st),
        check_devanagari(st),
        check_no_text_dropped(cfg, st, out),
        check_numbers(st),
        check_fonts(cfg, st, out),
        check_text_layer(cfg, st, out),
        check_toc(cfg, out),
        check_page_model(cfg, st, out),
    ]
    print()
    # Report every check before deciding: `all(gen)` would short-circuit and
    # hide the results of everything after the first failure.
    results = [c.report() for c in checks]
    ok = all(results)
    print()
    print("  ALL CHECKS PASSED" if ok
          else f"  {results.count(False)} of {len(results)} CHECKS FAILED")
    return ok
