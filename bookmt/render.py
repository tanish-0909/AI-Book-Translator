"""Stage 5 -- write the Marathi PDF.

The pixel-perfect guarantee comes from what this stage does *not* do. It never
re-encodes, re-rasterises or re-places an image. It opens a copy of the original
PDF and removes only the text glyphs:

    page.apply_redactions(images=0, graphics=0, text=0)
                          ^^^^^^^^  ^^^^^^^^^^  ^^^^^^
                          IMAGE_NONE LINE_ART_NONE TEXT_REMOVE

Verified on page 183 of the source: text went 1,598 chars -> 0 while both
embedded JPEGs kept byte-identical sha256 hashes. Stage 6 re-asserts that over
every image in the finished document.

Page model is "page faithful, not page-count locked": geometry, margins and
image anchoring are preserved exactly and type size is never reduced; when the
Marathi overruns a page's boxes the remainder flows onto an inserted
continuation page.
"""

from __future__ import annotations

import json
from pathlib import Path

import fitz

from .config import Config
from .state import State
from . import typeset

PARAGRAPH_GAP = 6.0   # pt between paragraphs on a continuation page
CONT_TOP_PAD = 4.0


class RenderError(RuntimeError):
    pass


def _text_frame(blocks: list, page_rect: fitz.Rect,
                doc_frame: fitz.Rect | None = None) -> fitz.Rect:
    """The body text column for a page.

    A page's own blocks give the wrong bottom edge when its English text happens
    to stop early -- a chapter opener ending at y=300 would leave the Marathi no
    room to grow into, even though 400pt of page sits empty below it. So the
    book-wide body box is used as the floor and merely widened, never narrowed,
    by whatever this page actually contains.
    """
    if not blocks:
        if doc_frame is not None:
            return doc_frame
        m = 72.0
        return fitz.Rect(m, m, page_rect.width - m, page_rect.height - m)
    left = min(b["x0"] for b in blocks)
    right = max(b["x1"] for b in blocks)
    top = min(b["y0"] for b in blocks)
    bottom = max(b["y1"] for b in blocks)
    if doc_frame is not None:
        left, top = min(left, doc_frame.x0), min(top, doc_frame.y0)
        right, bottom = max(right, doc_frame.x1), max(bottom, doc_frame.y1)
    return fitz.Rect(left, top, right, max(bottom, top + 100))


def framed_regions(page: fitz.Page) -> list[fitz.Rect]:
    """Boxes drawn on the page that visually enclose text.

    `apply_redactions(graphics=0)` keeps line art exactly where it was, so a
    boxed sidebar keeps its border while the Marathi inside it grows. Page 146's
    WRAP box overran its rule, which then struck through the last two lines.

    The box is *not* one rectangle: it is four separate 1pt-thick filled rules,
    one per side. So thin rules are clustered by adjacency and each cluster's
    union is the container. A cluster is only treated as a frame when every part
    of it is thin -- otherwise a filled chart would be mistaken for a box.
    """
    THIN = 5.0
    try:
        rects = [fitz.Rect(d["rect"]) for d in page.get_drawings()]
    except Exception:
        return []
    rects = [r for r in rects if r.is_valid and not r.is_empty]
    if not rects:
        return []

    parent = list(range(len(rects)))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            a, b = fitz.Rect(rects[i]), fitz.Rect(rects[j])
            a += (-3, -3, 3, 3)                 # corners meet, not overlap
            if a.intersects(b):
                parent[find(i)] = find(j)

    clusters: dict[int, list[int]] = {}
    for i in range(len(rects)):
        clusters.setdefault(find(i), []).append(i)

    out: list[fitz.Rect] = []
    for members in clusters.values():
        if not all(min(rects[i].width, rects[i].height) <= THIN for i in members):
            continue                            # a solid shape, not a rule frame
        u = fitz.Rect(rects[members[0]])
        for i in members[1:]:
            u |= rects[i]
        if u.width > 60 and u.height > 25:
            out.append(u)
    return out


def _document_frame(st: State, page_rect: fitz.Rect) -> fitz.Rect:
    """The book's body text box, from the bulk of blocks across every page.

    Percentiles rather than min/max so a stray page number in the margin or a
    full-bleed element does not stretch the frame off the text area.
    """
    xs0, ys0, xs1, ys1 = [], [], [], []
    for b in st.blocks_all():
        xs0.append(b["x0"]); ys0.append(b["y0"])
        xs1.append(b["x1"]); ys1.append(b["y1"])
    if not xs0:
        m = 72.0
        return fitz.Rect(m, m, page_rect.width - m, page_rect.height - m)

    def pct(v: list[float], q: float) -> float:
        v = sorted(v)
        return v[min(int(q * (len(v) - 1)), len(v) - 1)]

    return fitz.Rect(pct(xs0, 0.02), pct(ys0, 0.02), pct(xs1, 0.98), pct(ys1, 0.98))


_SENTENCE_END = (".", "!", "?", ":", ";", '."', '!"', '?"', ".'", "”", "’", "…")


def merge_fragments(blocks: list, column_right: float | None = None,
                    page_width: float = 612.0) -> list[dict]:
    """Join blocks that are really continuations of one paragraph.

    MuPDF's block segmentation is not always paragraph-level. On page 220 the
    sentence "…leaving past decisions unquestioned." arrives as two blocks, the
    second being a 90pt-wide box holding the single word "unquestioned." English
    fits because the fragment is short; the Marathi for it needs seven lines in
    90pt of width, which is where the 8.4x overflow on that page came from.

    Merging restores the paragraph *and* its full column width. The per-block
    translations concatenate cleanly because the model saw the whole page at
    once, so no re-translation is needed.
    """
    ordered = sorted(blocks, key=lambda b: (round(b["y0"], 1), b["x0"]))
    if column_right is None:
        column_right = max((b["x1"] for b in ordered), default=page_width)

    groups: list[dict] = []
    for b in ordered:
        text = b["final"] or b["draft"] or ""
        size = float(json.loads(b["style_json"]).get("size") or 10.0)
        width = b["x1"] - b["x0"]
        centre_off = abs(((b["x0"] + b["x1"]) / 2) - (page_width / 2))
        cur = {
            "x0": b["x0"], "y0": b["y0"], "x1": b["x1"], "y1": b["y1"],
            "text": typeset.snap_markup_to_clusters(text),
            "size": size, "indices": [b["block_index"]],
            "src": (b["source_text"] or "").strip(),
            # A short box centred on the page is a heading, not body copy.
            "align": "center" if (centre_off < 6.0 and width < (column_right - b["x0"]) * 0.92
                                  and b["y1"] - b["y0"] < size * 3) else "justify",
        }
        prev = groups[-1] if groups else None
        if prev is not None:
            gap = cur["y0"] - prev["y1"]
            same_col = min(prev["x1"], cur["x1"]) - max(prev["x0"], cur["x0"]) > 1.0
            same_size = abs(prev["size"] - cur["size"]) < 0.15
            # Consecutive lines routinely report a *negative* gap: bboxes carry
            # ascender/descender padding and overlap their neighbour by a few
            # points (measured -7.7pt at 14.4pt type). A floor of -2.0 rejected
            # every real continuation on the page.
            adjacent = -prev["size"] * 0.7 <= gap <= prev["size"] * 0.9
            unfinished = bool(prev["src"]) and not prev["src"].endswith(_SENTENCE_END)
            # The decisive test: a wrapped line runs to the right margin. A line
            # that stops short ended deliberately -- a heading, a list item, the
            # last line of a paragraph -- so it must not absorb what follows.
            # Without this, "Set a Tripwire" on p220 swallowed list item 1.
            wrapped = prev["x1"] >= column_right - max(prev["size"] * 1.2, 12.0)
            heading = prev["align"] == "center" or cur["align"] == "center"
            if same_col and same_size and adjacent and unfinished and wrapped \
                    and not heading:
                prev["x0"] = min(prev["x0"], cur["x0"])
                prev["x1"] = max(prev["x1"], cur["x1"])
                prev["y1"] = cur["y1"]
                prev["text"] = (prev["text"].rstrip() + " " + cur["text"].lstrip()).strip()
                prev["src"] = cur["src"]
                prev["indices"].extend(cur["indices"])
                continue
        groups.append(cur)
    return groups


def layout_page(groups: list[dict], frame: fitz.Rect, image_rects: list[fitz.Rect],
                css_for, arch, page_size, gap_scale: float = 1.0,
                pin: bool = True,
                framed: list[fitz.Rect] | None = None
                ) -> tuple[list[tuple], list[tuple[str, float]]]:
    """Flow `groups` down the page, letting each use the room actually available.

    The previous version required every block to fit inside its own tight source
    bbox. Measured over sample pages, a page's Marathi needs ~1.00x the total
    space its English used -- the room is there, it was just locked inside
    per-paragraph boxes that could not share slack. Any single block spilling by
    one line forced a whole continuation page, which is how 271 of them appeared.

    Blocks only ever move *down*, never up, so the top of the page still matches
    the original. Returns (placements, spill).
    """
    placed: list[tuple] = []
    spill: list[tuple[str, float]] = []
    framed = framed or []
    y = groups[0]["y0"] if groups else frame.y0
    cur_x0, cur_x1 = (groups[0]["x0"], groups[0]["x1"]) if groups else (frame.x0, frame.x1)

    for i, g in enumerate(groups):
        if not g["text"].strip():
            continue
        # Side-by-side material (no horizontal overlap with what set the cursor)
        # keeps its own top; only same-column blocks get pushed down.
        overlaps_cursor = min(cur_x1, g["x1"]) - max(cur_x0, g["x0"]) > 1.0
        if not overlaps_cursor:
            top = g["y0"]
        elif pin:
            top = max(g["y0"], y)
        else:
            # Unpinned: flow purely from the cursor, so space saved by tighter
            # leading is actually usable. Without this the block would still sit
            # at its original y0 and the compression would buy nothing.
            top = y

        # Never flow text over a figure.
        for ir in image_rects:
            if min(ir.x1, g["x1"]) - max(ir.x0, g["x0"]) > 1.0 and ir.y1 > top \
                    and ir.y0 < top + g["size"] * 1.4:
                top = max(top, ir.y1 + 2.0)

        # A centred heading keeps the whole column so it can centre properly and
        # wrap onto a second line -- Marathi headings routinely run longer than
        # the English box they came from.
        bottom, right = frame.y1, g["x1"]
        for box_r in framed:                    # stay inside a drawn container
            if box_r.contains(fitz.Point(g["x0"] + 1, g["y0"] + 1)):
                bottom = min(bottom, box_r.y1 - 2.0)
                # Also lend it the container's full width. The block inherits the
                # *English* line's right edge ("Widen Your Options" is short), so
                # the longer Marathi wrapped early and ran out the bottom of the
                # rule even though the box had spare width.
                right = max(right, box_r.x1 - 6.0)
        css = css_for(g["size"], align=g["align"])

        # Try the block's own right edge first, and only fall back to the full
        # column when the text genuinely will not fit inside it. A short English
        # line ("• You make a choice.") leaves an arbitrary right edge that the
        # longer Marathi cannot live within -- page 21's bullet list wrapped into
        # a narrow stub. Widening only on demand keeps deliberate indents intact.
        candidates = [min(right, frame.x1)]
        if candidates[0] < frame.x1 - 1.0:
            # A short source box (one or two lines) has an arbitrary right edge.
            # If the Marathi no longer fits the height that box occupied, the
            # stub width is the reason -- widen first rather than let a one-line
            # bullet wrap onto two. Taller blocks keep their edge: for justified
            # copy it is the real column, and widening would destroy an indent.
            own_h = g["y1"] - g["y0"]
            if own_h <= g["size"] * 2.2:
                probe = fitz.Rect(g["x0"], 0, candidates[0], own_h + 1.0)
                if not typeset.fits(probe, typeset.to_html(g["text"]), css,
                                    arch, page_size)[0]:
                    candidates.insert(0, frame.x1)
            candidates.append(frame.x1)

        head = tail = ""
        for cand_right in candidates:
            if g["align"] == "center":
                avail = fitz.Rect(frame.x0, top, frame.x1, bottom)
            else:
                avail = fitz.Rect(g["x0"], top, cand_right, bottom)
            if avail.height < g["size"] * 1.4:
                head, tail = "", g["text"]
                continue
            head, tail = typeset.split_to_fit(avail, g["text"], css, arch, page_size)
            if not tail:
                break
        if tail:
            head, tail = typeset.close_open_tags(head, tail)

        if head:
            placed.append((avail, head, css))
        if tail:
            spill.append((tail, g["size"]))
            for rest in groups[i + 1:]:            # keep reading order intact
                if rest["text"].strip():
                    spill.append((rest["text"], rest["size"]))
            break

        # Preserve the original spacing to the next block where we can. When the
        # page is tight, `gap_scale` compresses the leading rather than pushing a
        # paragraph onto a continuation page -- vertical justification is a normal
        # typographic lever, and it leaves the type size untouched.
        nxt = groups[i + 1] if i + 1 < len(groups) else None
        gap = max(nxt["y0"] - g["y1"], 0.0) if nxt else 0.0
        gap = min(gap, g["size"] * 1.6) * gap_scale
        y = top + _drawn_height(avail, head, css, arch, page_size) + gap
        cur_x0, cur_x1 = g["x0"], g["x1"]

    return placed, spill


# Least-invasive first. Each rung gives up a little more of the original page
# geometry, and we stop at the first that fits: keep positions and leading ->
# keep positions, tighter leading -> let paragraphs close up -> close up hard ->
# finally tighten line spacing itself. Type SIZE is never touched on any rung;
# only the space between lines, which a reader does not perceive as smaller text.
# The last two rungs exist because a page spilling one line otherwise produced a
# continuation page holding a single line, which vision QA rightly called blank.
#                (gap_scale, pin_positions, line_height)
GAP_LADDER = ((1.0, True, 1.30), (0.65, True, 1.30), (1.0, False, 1.30),
              (0.6, False, 1.30), (0.3, False, 1.30),
              (0.3, False, 1.22), (0.25, False, 1.16))


def layout_page_best(groups, frame, image_rects, css_for, arch, page_size,
                     framed=None):
    """Lay out the page, giving up page fidelity only as far as needed to fit."""
    placed = spill = None
    for scale, pin, lh in GAP_LADDER:
        def css(size, align="justify", _lh=lh):
            return css_for(size, align=align, line_height=_lh)

        placed, spill = layout_page(groups, frame, image_rects, css, arch,
                                    page_size, gap_scale=scale, pin=pin,
                                    framed=framed)
        if not spill:
            return placed, spill, scale
    return placed, spill, GAP_LADDER[-1][0]


def _css_for(size: float, align: str = "justify", line_height: float = 1.30) -> str:
    return typeset.build_css(size, line_height=line_height, align=align)


def _drawn_height(rect: fitz.Rect, html_text: str, css: str, arch, page_size) -> float:
    """Height actually consumed by `html_text` inside `rect`."""
    ok, spare = typeset.fits(rect, typeset.to_html(html_text), css, arch, page_size)
    return rect.height - spare if ok and spare >= 0 else rect.height


def deoverlap(blocks: list) -> dict[int, fitz.Rect]:
    """Trim block rectangles so filled text cannot collide with the next block.

    PyMuPDF block bboxes include ascender/descender padding and routinely
    overlap their neighbour by a few points (e.g. page 148: block 1 ends at
    y=232 while block 2 starts at y=225). With the original English that was
    invisible because the text underfilled its box. Marathi fills the box, so
    the overlap becomes visible collision.

    Each block's bottom is pulled up to the top of the next block that both
    starts lower and shares horizontal extent -- so genuine side-by-side
    columns are left alone.
    """
    rects: dict[int, fitz.Rect] = {}
    ordered = sorted(blocks, key=lambda b: (b["y0"], b["x0"]))
    for i, b in enumerate(ordered):
        top, bottom = b["y0"], b["y1"]
        for other in ordered[i + 1:]:
            if other["y0"] <= top:
                continue
            # Only clip against a block in the same column.
            h_overlap = min(b["x1"], other["x1"]) - max(b["x0"], other["x0"])
            if h_overlap <= 1.0:
                continue
            if other["y0"] < bottom:
                bottom = other["y0"] - 0.5
            break
        rects[b["block_index"]] = fitz.Rect(b["x0"], top, b["x1"], max(bottom, top + 1.0))
    return rects


def _final_text(block) -> str:
    text = block["final"] or block["draft"]
    if not text:
        raise RenderError(
            f"block {block['id']} on page {block['page_no']} has no translation. "
            "Run translate/review first; refusing to emit English into a Marathi PDF."
        )
    return text


def render(cfg: Config, st: State, pages: list[int] | None = None) -> dict:
    src = cfg.path("paths.input_pdf")
    font_dir = cfg.path("paths.font_dir")
    out_dir = cfg.path("paths.output_dir")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{src.stem} - Marathi.pdf"

    arch = typeset.archive(font_dir)
    doc = fitz.open(src)
    toc = doc.get_toc()

    all_pages = [p["page_no"] for p in st.pages()]
    selected = set(pages) if pages else set(all_pages)
    doc_frame = _document_frame(st, doc[0].rect)

    # Figure placements, so flowed text is never laid over an image.
    image_rects: dict[int, list[list[float]]] = {}
    for row in st.images():
        image_rects.setdefault(row["page_no"], []).extend(
            json.loads(row["rects_json"] or "[]")
        )

    page_map: dict[int, int] = {}     # original 1-based page -> output 0-based index
    offset = 0
    n_continuation = 0
    overflow_pages: list[int] = []

    for orig in all_pages:
        idx = orig - 1 + offset
        page = doc[idx]
        page_map[orig] = idx

        blocks = st.blocks_for_page(orig)
        if not blocks or orig not in selected:
            # Image-only pages (cover, title, back matter) pass through untouched.
            continue

        # 1. Strip only the English glyphs.
        for b in blocks:
            page.add_redact_annot(fitz.Rect(b["x0"], b["y0"], b["x1"], b["y1"]), fill=None)
        page.apply_redactions(images=0, graphics=0, text=0)

        # 2. Lay the Marathi into the page at the original size, flowing down the
        #    column so blocks can share the page's vertical space.
        page_size = (page.rect.width, page.rect.height)
        for b in blocks:
            _final_text(b)                      # raise before drawing anything
        frame = _text_frame(blocks, page.rect, doc_frame)
        groups = merge_fragments(blocks, doc_frame.x1, page.rect.width)
        img_rects = [fitz.Rect(r) for r in image_rects.get(orig, [])]
        placed, spill, _scale = layout_page_best(
            groups, frame, img_rects, _css_for, arch, page_size,
            framed=framed_regions(page),
        )
        for rect, head, css in placed:
            typeset.draw(page, rect, typeset.to_html(head), css, arch)

        # 3. Flow any remainder onto continuation pages.
        if spill:
            overflow_pages.append(orig)
            frame = _text_frame(blocks, page.rect)
            queue = list(spill)
            while queue:
                idx += 1
                offset += 1
                n_continuation += 1
                cont = doc.new_page(pno=idx, width=page.rect.width, height=page.rect.height)
                y = frame.y0 + CONT_TOP_PAD
                progressed = False

                while queue:
                    text, size = queue[0]
                    css = typeset.build_css(size)
                    avail = fitz.Rect(frame.x0, y, frame.x1, frame.y1)
                    if avail.height < size * 1.6:
                        break  # no usable room left on this page
                    head, tail = typeset.split_to_fit(avail, text, css, arch, page_size)
                    if not head:
                        break
                    head2, tail = typeset.close_open_tags(head, tail) if tail else (head, tail)
                    spare = typeset.draw(cont, avail, typeset.to_html(head2), css, arch)
                    used = avail.height - max(spare, 0.0)
                    y += used + PARAGRAPH_GAP
                    progressed = True
                    if tail:
                        queue[0] = (tail, size)
                        break
                    queue.pop(0)

                if not progressed:
                    raise RenderError(
                        f"page {orig}: could not place overflow text on a continuation page "
                        f"(frame {frame}). The text frame is too small for a single line."
                    )

    # 4. Rebuild the outline: translated titles, and page numbers shifted by
    #    however many continuation pages were inserted before each entry.
    if toc:
        toc_path = cfg.path("paths.workdir") / "toc.json"
        titles: dict[int, str] = {}
        if toc_path.exists():
            titles = {int(k): v for k, v in
                      json.loads(toc_path.read_text(encoding="utf-8")).items()}
        remapped = []
        for i, (level, title, pno) in enumerate(toc):
            new_title = titles.get(i, title)
            new_page = page_map.get(pno, pno) + 1 if pno in page_map else pno
            remapped.append([level, new_title, new_page])
        doc.set_toc(remapped)
        if titles:
            print(f"    outline: {len(titles)} entries translated, page numbers remapped")

    # garbage=4 merges duplicate *stream content*. Every insert_htmlbox call
    # re-embeds the font programs, so without it the book carried one copy of
    # ~3 MB of fonts per page: 1.5 MB of source became 1.35 GB of output.
    # garbage=3 is not enough -- the copies are distinct objects with identical
    # content. Merging identical streams never decodes or re-encodes an image,
    # so the byte-identical guarantee holds; verify re-asserts it either way.
    doc.save(out_path, garbage=4, deflate=True, clean=False)
    total = doc.page_count
    doc.close()

    # Persist the real source-page -> output-index map. A single overflowing page
    # can spawn more than one continuation page, so this cannot be reconstructed
    # by assuming one-for-one; Stage 6 reads this file to find the page to inspect.
    map_path = cfg.path("paths.workdir") / "page_map.json"
    map_path.write_text(json.dumps(page_map, indent=0), encoding="utf-8")

    print(f"    wrote {out_path}")
    print(f"    {len(all_pages)} source pages -> {total} output pages "
          f"({n_continuation} continuation pages inserted)")
    if overflow_pages:
        preview = overflow_pages[:12]
        more = "" if len(overflow_pages) <= 12 else f" (+{len(overflow_pages) - 12} more)"
        print(f"    pages that overflowed: {preview}{more}")

    return {
        "output": str(out_path),
        "source_pages": len(all_pages),
        "output_pages": total,
        "continuation_pages": n_continuation,
        "overflow_pages": overflow_pages,
    }


def verify_images_lossless(cfg: Config, st: State, out_path: Path) -> dict:
    """Assert every original image stream survived byte-identically.

    This is the check that makes "pixel perfect images" falsifiable rather than
    a matter of opinion.
    """
    import hashlib

    expected = {r["sha256"] for r in st.images()}
    if not expected:
        return {"checked": 0, "ok": True, "missing": []}

    doc = fitz.open(out_path)
    found = set()
    for pno in range(len(doc)):
        for img in doc[pno].get_images(full=True):
            data = doc.extract_image(img[0])["image"]
            found.add(hashlib.sha256(data).hexdigest())
    doc.close()

    missing = sorted(expected - found)
    return {"checked": len(expected), "ok": not missing, "missing": missing}


def run(cfg: Config, st: State, pages: list[int] | None = None) -> dict:
    print(f"\n[5/6] RENDER")
    result = render(cfg, st, pages)

    check = verify_images_lossless(cfg, st, Path(result["output"]))
    if check["checked"]:
        if check["ok"]:
            print(f"    image losslessness: {check['checked']}/{check['checked']} "
                  f"original streams byte-identical  OK")
        else:
            raise RenderError(
                f"{len(check['missing'])} of {check['checked']} original image streams "
                f"were altered or lost: {check['missing'][:5]}"
            )
    st.log("render", json.dumps({k: v for k, v in result.items() if k != "overflow_pages"}))
    return result
