"""Unit tests for the pure logic -- no API calls, no PDF writes.

Run either way:
    python tests/test_core.py
    pytest tests/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz  # noqa: E402

from bookmt import typeset  # noqa: E402
from bookmt.extract import assemble_block, normalise  # noqa: E402
from bookmt.qa import automated_checks, numbers_in, proper_nouns  # noqa: E402
from bookmt.render import deoverlap, merge_fragments  # noqa: E402
from bookmt.state import block_id  # noqa: E402
from bookmt.translate import apply_markup, strip_markup  # noqa: E402


# --------------------------------------------------------------------------
# extract
# --------------------------------------------------------------------------

def _span(text, size=10.0, flags=0, font="CharisSIL"):
    return {"text": text, "size": size, "flags": flags, "font": font}


def _block(lines):
    return {"type": 0, "bbox": (0, 0, 100, 100),
            "lines": [{"spans": spans} for spans in lines]}


def test_spans_join_without_spurious_spaces():
    """The old extractor did `text += span + " "`, splitting words mid-token."""
    blk = _block([[_span("Effi"), _span("cient"), _span(" Image")]])
    text, _ = assemble_block(blk)
    assert text == "Efficient Image", repr(text)


def test_lines_join_with_a_space():
    blk = _block([[_span("first line")], [_span("second line")]])
    text, _ = assemble_block(blk)
    assert text == "first line second line", repr(text)


def test_hyphenated_linebreak_is_healed():
    blk = _block([[_span("super-")], [_span("resolution")]])
    text, _ = assemble_block(blk)
    assert text == "superresolution", repr(text)


def test_italic_runs_are_captured():
    blk = _block([[_span("plain "), _span("emph", flags=1 << 1), _span(" tail")]])
    text, style = assemble_block(blk)
    assert text == "plain emph tail"
    runs = style["runs"]
    assert len(runs) == 1 and runs[0]["i"] is True
    assert text[runs[0]["start"]:runs[0]["end"]] == "emph"


def test_bold_detected_from_font_name():
    blk = _block([[_span("HEADING", font="CharisSIL-Bold")]])
    _, style = assemble_block(blk)
    assert style["runs"][0]["b"] is True


def test_ligatures_and_smart_quotes_normalised():
    assert normalise("oﬃce") == "office"
    assert normalise("“quoted”") == '"quoted"'
    assert normalise("it’s") == "it's"


# --------------------------------------------------------------------------
# markup round trip
# --------------------------------------------------------------------------

def test_apply_and_strip_markup():
    text = "plain emph tail"
    style = {"runs": [{"start": 6, "end": 10, "i": True, "b": False}]}
    marked = apply_markup(text, style)
    assert marked == "plain <i>emph</i> tail", marked
    assert strip_markup(marked) == text


def test_apply_markup_nests_bold_and_italic():
    style = {"runs": [{"start": 0, "end": 4, "i": True, "b": True}]}
    assert apply_markup("word rest", style) == "<i><b>word</b></i> rest"


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------

def test_block_id_is_content_addressed():
    a = block_id(1, 0, "hello")
    assert a == block_id(1, 0, "hello")
    assert a != block_id(1, 0, "hello!")   # text change -> new id
    assert a != block_id(2, 0, "hello")    # page change -> new id


# --------------------------------------------------------------------------
# render geometry
# --------------------------------------------------------------------------

class _Row(dict):
    def __getitem__(self, k):
        return dict.__getitem__(self, k)


def test_deoverlap_trims_colliding_boxes():
    """Source bboxes overlap by a few points; filled Marathi would collide."""
    blocks = [
        _Row(block_index=0, x0=72, y0=100, x1=540, y1=232),
        _Row(block_index=1, x0=72, y0=225, x1=540, y1=350),
    ]
    rects = deoverlap(blocks)
    assert rects[0].y1 <= 225, rects[0]
    assert rects[1].y0 == 225


def test_deoverlap_leaves_side_by_side_columns_alone():
    blocks = [
        _Row(block_index=0, x0=72, y0=100, x1=290, y1=400),
        _Row(block_index=1, x0=310, y0=110, x1=540, y1=400),
    ]
    rects = deoverlap(blocks)
    assert rects[0].y1 == 400, "columns must not clip each other"


def _frag(i, x0, y0, x1, y1, src, out, size=14.4):
    return _Row(block_index=i, x0=x0, y0=y0, x1=x1, y1=y1, source_text=src,
                final=out, draft=out, style_json=json.dumps({"size": size}))


# Real geometry from page 220, where consecutive lines overlap by 7.7pt.
def test_merge_joins_a_wrapped_continuation_line():
    """A line reaching the right margin continues into the next block.

    p220 split "…leaving past decisions unquestioned." into two blocks, the
    second a 90pt-wide box. Unmerged, its Marathi needed 7 lines in that width.
    """
    blocks = [
        _frag(2, 79, 132, 539, 157, "1. In life, we slip into autopilot, leaving past decisions", "AAA"),
        _frag(3, 101, 149, 190, 174, "unquestioned.", "BBB"),
    ]
    groups = merge_fragments(blocks, column_right=540.0, page_width=612.0)
    assert len(groups) == 1, [g["text"] for g in groups]
    assert groups[0]["text"] == "AAA BBB"
    assert groups[0]["x1"] == 539, "merged group must take the full column width"


def test_merge_rejects_negative_gap_beyond_bbox_padding():
    """Overlap of ~half the type size is padding; a big overlap is not adjacency."""
    blocks = [
        _frag(0, 79, 132, 539, 200, "a line that runs to the right margin here", "AAA"),
        _frag(1, 79, 140, 539, 210, "second", "BBB"),
    ]
    assert len(merge_fragments(blocks, 540.0, 612.0)) == 2


def test_merge_does_not_swallow_a_centred_heading():
    """"Set a Tripwire" is centred and stops short: it must not absorb item 1."""
    blocks = [
        _frag(1, 258, 100, 355, 125, "Set a Tripwire", "HEAD"),
        _frag(2, 79, 132, 539, 157, "1. In life, we slip into autopilot", "BODY"),
    ]
    groups = merge_fragments(blocks, column_right=540.0, page_width=612.0)
    assert len(groups) == 2, [g["text"] for g in groups]
    assert groups[0]["align"] == "center"
    assert groups[1]["align"] == "justify"


def test_merge_stops_at_a_line_that_ends_short_of_the_margin():
    """A short last line ended deliberately, so the next block is a new para."""
    blocks = [
        _frag(0, 79, 132, 300, 157, "a deliberately short line", "AAA"),
        _frag(1, 79, 149, 539, 174, "next paragraph starts here", "BBB"),
    ]
    assert len(merge_fragments(blocks, 540.0, 612.0)) == 2


def test_merge_keeps_separate_when_previous_sentence_is_finished():
    blocks = [
        _frag(0, 79, 132, 539, 157, "A complete sentence ending in a period.", "AAA"),
        _frag(1, 79, 149, 539, 174, "A new one.", "BBB"),
    ]
    assert len(merge_fragments(blocks, 540.0, 612.0)) == 2


# --------------------------------------------------------------------------
# typeset
# --------------------------------------------------------------------------

def test_to_html_escapes_but_keeps_emphasis():
    out = typeset.to_html("a < b & c <i>emph</i>")
    assert "<i>emph</i>" in out
    assert "&lt;" in out and "&amp;" in out


def test_snap_markup_keeps_a_matra_with_its_consonant():
    """`<b>त</b>ुम…` split a cluster, so the matra rendered as a dotted circle."""
    out = typeset.snap_markup_to_clusters("<b>त</b>ुमच्यासमोरचे पर्याय")
    assert out == "<b>तु</b>मच्यासमोरचे पर्याय", out


def test_snap_markup_carries_a_virama_conjunct():
    out = typeset.snap_markup_to_clusters("<b>क</b>्षत्रिय")
    assert out.startswith("<b>क्ष</b>"), out


def test_snap_markup_leaves_clean_boundaries_alone():
    for s in ("<b>आपला अंदाज</b> चुकू", "no markup at all", "<i>Switch</i> मध्ये"):
        assert typeset.snap_markup_to_clusters(s) == s, s


def test_close_open_tags_repairs_a_split():
    head, tail = typeset.close_open_tags("start <i>middle", "rest</i> end")
    assert head == "start <i>middle</i>"
    assert tail == "<i>rest</i> end"


def test_split_to_fit_returns_everything_when_it_fits():
    fonts = Path(__file__).resolve().parent.parent / "assets" / "fonts"
    if not list(fonts.glob("*.ttf")):
        return  # fonts not downloaded yet; preflight covers this
    arch = typeset.archive(fonts)
    css = typeset.build_css(12.0)
    head, tail = typeset.split_to_fit(
        fitz.Rect(0, 0, 400, 300), "छोटा मजकूर", css, arch, (612, 792))
    assert tail == "" and head == "छोटा मजकूर"


def test_split_to_fit_splits_on_word_boundary():
    fonts = Path(__file__).resolve().parent.parent / "assets" / "fonts"
    if not list(fonts.glob("*.ttf")):
        return
    arch = typeset.archive(fonts)
    css = typeset.build_css(12.0)
    text = "शब्द " * 400
    head, tail = typeset.split_to_fit(
        fitz.Rect(0, 0, 200, 60), text.strip(), css, arch, (612, 792))
    assert head and tail, "long text in a small box must split"
    # Never split inside a word -- that would break conjuncts and matras.
    assert not head.endswith("श") and not head.endswith("ब")
    assert (head + " " + tail).split() == text.split()


# --------------------------------------------------------------------------
# qa
# --------------------------------------------------------------------------

def test_proper_nouns_ignores_sentence_openers():
    got = proper_nouns("Following protocol, he called Michael. Once done, Phillips left.")
    assert "Michael" in got and "Phillips" in got
    assert "Following" not in got and "Once" not in got


def test_proper_nouns_ignores_small_caps_typography():
    """The book opens sections in small caps: "IT WAS PRECISELY THE fear ..."."""
    got = proper_nouns("IT WAS PRECISELY THE fear that drove Andrew Hallam onward.")
    assert "Andrew" in got and "Hallam" in got
    assert "WAS" not in got and "PRECISELY" not in got


def test_proper_nouns_ignores_nationality_adjectives():
    got = proper_nouns("He met a Canadian teacher and an American banker at Costco.")
    assert "Costco" in got
    assert "Canadian" not in got and "American" not in got


def test_proper_nouns_keeps_names_resembling_demonyms():
    """A suffix rule would wrongly swallow these; the demonym list is explicit."""
    got = proper_nouns("At the fair, Vivian introduced Duncan to Sebastian.")
    assert {"Vivian", "Duncan", "Sebastian"} <= got


def test_numbers_in():
    assert numbers_in("He paid $1,299.50 in 2013") == ["1,299.50", "2013"]


def _qa_row(idx, src, tgt):
    return _Row(block_index=idx, source_text=src, final=tgt, draft=None, page_no=1)


def test_qa_flags_untranslated_and_lost_numbers():
    rows = [
        _qa_row(0, "He paid 250 dollars in 2013.", "त्याने 250 डॉलर दिले."),   # 2013 lost
        _qa_row(1, "Plain English text here.", "Plain English text here."),   # untranslated
    ]
    kinds = {f["kind"] for f in automated_checks(rows, [])}
    assert "number_lost" in kinds
    assert "untranslated" in kinds and "no_devanagari" in kinds


def test_qa_clean_on_good_translation():
    rows = [_qa_row(0, "Andrew Hallam bought a car in 2013.",
                    "Andrew Hallam ने 2013 मध्ये कार घेतली.")]
    assert automated_checks(rows, []) == []


def test_qa_flags_devanagari_digits():
    rows = [_qa_row(0, "He paid 250.", "त्याने २५० दिले.")]
    kinds = {f["kind"] for f in automated_checks(rows, [])}
    assert "devanagari_digits" in kinds


def test_qa_enforces_glossary():
    rows = [_qa_row(0, "This is a narrow frame problem.",
                    "ही एक संकुचित चौकट समस्या आहे.")]
    terms = [{"english": "narrow frame", "marathi": "संकुचित दृष्टिकोन"}]
    kinds = {f["kind"] for f in automated_checks(rows, terms)}
    assert "glossary_miss" in kinds


# --------------------------------------------------------------------------

def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed.append(name)
            print(f"  FAIL  {name}: {e}")
        except Exception as e:
            failed.append(name)
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n  {len(tests) - len(failed)}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
