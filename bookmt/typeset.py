"""Font stack and text-fitting helpers shared by preflight and the renderer.

Two facts about PyMuPDF that this module depends on, both verified empirically
against MuPDF 1.29.0 rather than assumed:

* `page.insert_htmlbox(...)` returns `(spare_height, scale)`. When the content
  does not fit it returns `spare == -1` **and draws nothing**. That makes
  measure-then-draw safe: a failed fit leaves the page untouched.
* `font-size: Npx` in the CSS renders as exactly N points, so a source font
  size can be passed straight through with no conversion.
"""

from __future__ import annotations

import html as html_mod
import re
import unicodedata
from pathlib import Path

import fitz

LATIN_FAMILY = "LatinMT"
DEVA_FAMILY = "DevaMT"

# Latin first: each glyph is drawn by the first family that covers it, so Latin
# runs (proper nouns and English idioms, which project policy keeps in Latin)
# get a true italic, while Devanagari falls through to the Devanagari face.
FONT_CSS = f"""
@font-face {{font-family: {LATIN_FAMILY}; src: url(NotoSerif-Regular.ttf);}}
@font-face {{font-family: {LATIN_FAMILY}; font-style: italic; src: url(NotoSerif-Italic.ttf);}}
@font-face {{font-family: {LATIN_FAMILY}; font-weight: bold; src: url(NotoSerif-Bold.ttf);}}
@font-face {{font-family: {LATIN_FAMILY}; font-weight: bold; font-style: italic;
             src: url(NotoSerif-BoldItalic.ttf);}}
@font-face {{font-family: {DEVA_FAMILY}; src: url(NotoSerifDevanagari-Regular.ttf);}}
@font-face {{font-family: {DEVA_FAMILY}; font-weight: bold;
             src: url(NotoSerifDevanagari-Bold.ttf);}}

* {{ font-family: {LATIN_FAMILY}, {DEVA_FAMILY}; }}
body {{ margin: 0; padding: 0; }}
p {{ margin: 0; padding: 0; text-align: justify; }}
"""

_ALLOWED_TAGS = re.compile(r"</?(?:i|b)>")
_TAG_AT = re.compile(r"</?(?:i|b)>")
VIRAMA = "्"


def _is_mark(ch: str) -> bool:
    return unicodedata.category(ch) in ("Mn", "Mc")


def snap_markup_to_clusters(text: str) -> str:
    """Move <i>/<b> boundaries out of the middle of a grapheme cluster.

    The source marks the WRAP acronym with a bold initial ("**W**iden Your
    Options"), captured as a one-character bold run. Applied to Marathi that
    becomes `<b>त</b>ुमच्यासमोरचे` -- the tag lands between the consonant and its
    matra. Each side is then shaped as its own run, so the matra has no base
    consonant and MuPDF draws a dotted-circle placeholder. Observed on page 146.

    Boundaries are pushed forward past any combining marks, and past the
    consonant that follows a virama, so a cluster is never divided.
    """
    if "<" not in text:
        return text

    out: list[str] = []
    i = 0
    while i < len(text):
        m = _TAG_AT.match(text, i)
        if not m:
            out.append(text[i])
            i += 1
            continue
        j = m.end()
        # Absorb the rest of the cluster that would otherwise be orphaned.
        while j < len(text):
            ch = text[j]
            if _is_mark(ch):
                j += 1
            elif ch == VIRAMA or (j > 0 and text[j - 1] == VIRAMA):
                j += 1
            else:
                break
        out.append(text[m.end():j])   # cluster tail moves inside the tag
        out.append(m.group(0))        # ...and the tag moves after it
        i = j
    return "".join(out)


def build_css(font_size: float, line_height: float = 1.30,
              align: str = "justify") -> str:
    return FONT_CSS + (
        f"p {{ font-size: {font_size}px; line-height: {line_height}; "
        f"text-align: {align}; }}"
    )


def archive(font_dir: Path) -> fitz.Archive:
    return fitz.Archive(str(font_dir))


def to_html(text: str) -> str:
    """Escape the text but keep the <i>/<b> emphasis tags the model round-trips.

    The model is told to return only <i> and <b>; anything else it emits is
    escaped and shown literally rather than silently altering the layout.
    """
    placeholders: list[str] = []

    def stash(m: re.Match) -> str:
        placeholders.append(m.group(0))
        return f"\x00{len(placeholders) - 1}\x00"

    stashed = _ALLOWED_TAGS.sub(stash, text)
    escaped = html_mod.escape(stashed, quote=False)
    for i, tag in enumerate(placeholders):
        escaped = escaped.replace(f"\x00{i}\x00", tag)
    return f"<p>{escaped}</p>"


def fits(rect: fitz.Rect, html: str, css: str, arch: fitz.Archive,
         page_size: tuple[float, float]) -> tuple[bool, float]:
    """Measure on a scratch page. Returns (fits, spare_height)."""
    doc = fitz.open()
    page = doc.new_page(width=page_size[0], height=page_size[1])
    try:
        spare, _scale = page.insert_htmlbox(rect, html, css=css, archive=arch, scale_low=1)
    except Exception:
        doc.close()
        return False, -1.0
    doc.close()
    return spare >= 0, float(spare)


def draw(page: fitz.Page, rect: fitz.Rect, html: str, css: str,
         arch: fitz.Archive) -> float:
    """Draw into `rect` at full size. Returns spare height; -1 means nothing drawn."""
    spare, _scale = page.insert_htmlbox(rect, html, css=css, archive=arch, scale_low=1)
    return float(spare)


def split_to_fit(rect: fitz.Rect, text: str, css: str, arch: fitz.Archive,
                 page_size: tuple[float, float]) -> tuple[str, str]:
    """Largest whitespace-delimited prefix of `text` that fits `rect`.

    Returns (head, tail). `tail` is "" when everything fits. Splitting on word
    boundaries keeps Devanagari clusters intact -- never split inside a word,
    or conjuncts and matras break.
    """
    if fits(rect, to_html(text), css, arch, page_size)[0]:
        return text, ""

    words = text.split(" ")
    if len(words) <= 1:
        return "", text  # single unsplittable token: push it wholesale

    # Binary search on "does the first N words fit". The predicate must be
    # monotonic in N for this to be correct, so tag balance is deliberately NOT
    # part of it -- an unbalanced split is repaired afterwards by
    # close_open_tags(), which closes the tag on the head and reopens it on the
    # tail. Folding balance into the predicate would make it non-monotonic and
    # the search could settle on an arbitrarily short prefix.
    lo, hi, best = 1, len(words), 0
    while lo <= hi:
        mid = (lo + hi) // 2
        if fits(rect, to_html(" ".join(words[:mid])), css, arch, page_size)[0]:
            best, lo = mid, mid + 1
        else:
            hi = mid - 1

    if best == 0:
        return "", text
    return " ".join(words[:best]), " ".join(words[best:])


def close_open_tags(head: str, tail: str) -> tuple[str, str]:
    """If a split lands inside emphasis, close it on the head and reopen on the tail."""
    for tag in ("b", "i"):
        if head.count(f"<{tag}>") > head.count(f"</{tag}>"):
            head += f"</{tag}>"
            tail = f"<{tag}>" + tail
    return head, tail
