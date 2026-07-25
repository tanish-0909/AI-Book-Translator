"""Stage 6 -- verification.

Two independent layers, both run on every page:

* **Automated** (no model, no cost): mechanical integrity. Did every block get
  translated? Did any number, date or currency amount change? Were the glossary
  and the Latin-script proper-noun policy honoured? Is any block still English?
* **Vision**: the rendered page image goes to the model, which reports clipped
  text, broken conjuncts, overlapping elements and displaced images.

Back-translation is deliberately not run -- that was the explicit choice for
this project.

Findings are written to the `qa` table so `run.py status` can report what is
still outstanding, and failing pages can be revised and re-checked.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import fitz

from .config import Config
from .glossary import load as load_glossary
from .llm import LLM
from .state import State
from .translate import strip_markup

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
DEVA_DIGITS = re.compile(r"[०-९]")
# Numbers, currency amounts, percentages and years. A comma only counts as a
# thousands separator when digits follow it -- `[\d,]*` captured the sentence
# comma in "in 1975, the ..." as part of the token, so the check then looked for
# a literal "1975," in the Marathi and reported 99 false losses.
NUMERIC = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
LATIN_WORD = re.compile(r"\b[A-Z][A-Za-z'&.-]{2,}\b")

VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {
                        "type": "string",
                        "enum": ["clipped_text", "overlapping_text", "broken_script",
                                 "displaced_image", "blank_area", "other"],
                    },
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "detail": {"type": "string"},
                },
                "required": ["kind", "severity", "detail"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["ok", "issues"],
    "additionalProperties": False,
}

VISION_SYSTEM = """You are inspecting a rendered page of a Marathi translation of an \
English book. The page layout was cloned from the original, so the geometry should look \
like a normal typeset book page.

Report ONLY visual/layout defects that you can actually see:
- text running outside its column, clipped at an edge, or cut off mid-word
- lines or paragraphs overlapping each other
- broken Devanagari: missing or detached matras, boxes/tofu, garbled conjuncts
- an image that is distorted, misplaced, or overlapping text
- a large unexpected blank region in the middle of otherwise continuous text

Do NOT report as issues:
- Latin-script names and English phrases mixed into the Marathi (that is intentional \
  house style for this project)
- ragged bottom margins or a short final page
- the content or accuracy of the translation (you cannot judge that from layout)

Set ok=true with an empty issues list if the page looks structurally sound."""


def numbers_in(text: str) -> list[str]:
    return NUMERIC.findall(text.replace(" ", " "))


# A fully-lowercase word of 3+ letters is prose that should have been
# translated. Anything else left in Latin is legitimate under the house style:
# titles ("Switch", "Made to Stick"), bibliographic data ("p. cm.",
# "eISBN: 978-0-30795641-5"), attributions ("—Harry Warner, Warner Bros."),
# bare numerals ("1.") and ornaments ("• • •"). Without this distinction the
# front matter alone produced 70 false "untranslated" reports.
LOWERCASE_WORD = re.compile(r"\b[a-z]{3,}\b")


def translatable_words(text: str) -> list[str]:
    return LOWERCASE_WORD.findall(text)


_WORD = re.compile(r"\b[A-Za-z][A-Za-z'’-]*\b")


def looks_like_title(text: str) -> bool:
    """Title-case runs are cited titles and bibliography entries, kept in Latin.

    Distinguishes '"Decisive for the Chronically Indecisive"' and
    'Hoeksema (2003), Women Who Think Too Much: ...' -- which are supposed to
    stay English -- from a genuinely missed fragment like 'warning bells.'
    """
    words = _WORD.findall(text)
    if len(words) < 2:
        return False
    caps = sum(1 for w in words if w[0].isupper())
    return caps / len(words) >= 0.6


_DEVA_DIGIT_MAP = str.maketrans("०१२३४५६७८९", "0123456789")


def latinise_digits(text: str) -> str:
    """Devanagari numerals compare equal to Latin ones.

    A number written १० is present, not lost; it is a *style* violation, which
    the separate devanagari_digits rule reports.
    """
    return text.translate(_DEVA_DIGIT_MAP)


def lowercase_lexicon(st) -> set[str]:
    """Every word the book ever writes in lowercase.

    A capitalised token is only a name if the book never uses it lowercase.
    "Should", "Any" and "It's" open sentences and appear lowercase elsewhere;
    "Costco", "Zappos" and "Andrew" never do. Without this the proper-noun rule
    fired 64 times on ordinary sentence-case words.
    """
    words: set[str] = set()
    for b in st.blocks_all():
        for w in re.findall(r"\b[a-z][a-z'’-]{1,}\b", b["source_text"] or ""):
            words.add(w.lower())
    return words


def automated_checks(block_rows: list, glossary_terms: list[dict],
                     lexicon: set[str] | None = None) -> list[dict]:
    """Mechanical integrity checks for one page. Returns a list of findings."""
    findings: list[dict] = []

    for b in block_rows:
        src = b["source_text"]
        tgt_raw = b["final"] or b["draft"] or ""
        tgt = strip_markup(tgt_raw)
        idx = b["block_index"]

        if not tgt.strip():
            findings.append({"block": idx, "kind": "missing_translation",
                             "detail": "no translation stored"})
            continue

        # Untranslated passthrough -- but only where the source was prose.
        # Titles, ISBNs, bare numerals and attributions stay Latin by policy, and
        # flagging them buried the real misses under ~140 false reports.
        is_prose = bool(translatable_words(src)) and not looks_like_title(src)
        if is_prose and not DEVANAGARI.search(tgt):
            findings.append({"block": idx, "kind": "no_devanagari",
                             "detail": f"target has no Devanagari: {tgt[:80]!r}"})
        if is_prose and tgt.strip() == src.strip():
            findings.append({"block": idx, "kind": "untranslated",
                             "detail": "target identical to source"})

        # Numbers must survive unchanged. Devanagari numerals still count as
        # present -- that is a style issue, reported separately below.
        src_nums, tgt_nums = numbers_in(src), numbers_in(latinise_digits(tgt))
        lost = [n for n in src_nums if n not in tgt_nums]
        if lost:
            findings.append({"block": idx, "kind": "number_lost",
                             "detail": f"missing from translation: {lost[:6]}"})
        if DEVA_DIGITS.search(tgt):
            findings.append({"block": idx, "kind": "devanagari_digits",
                             "detail": "digits converted to Devanagari numerals"})

        # Emphasis tags must be balanced.
        for tag in ("i", "b"):
            if tgt_raw.count(f"<{tag}>") != tgt_raw.count(f"</{tag}>"):
                findings.append({"block": idx, "kind": "unbalanced_tag",
                                 "detail": f"<{tag}> unbalanced"})

        # Project policy: proper nouns stay in Latin script. Skipped for
        # title-case runs -- in "Other Books by the Author" every word is
        # capitalised, so the rule reported Author/Books/Other as lost names.
        src_caps = [] if looks_like_title(src) else proper_nouns(src)
        if lexicon:
            src_caps = [w for w in src_caps
                        if w.lower().strip("'’s") not in lexicon and w.lower() not in lexicon]
        dropped = [w for w in src_caps if w not in tgt]
        if len(dropped) > 2:
            findings.append({"block": idx, "kind": "latin_proper_noun_lost",
                             "detail": f"Latin tokens absent from target: {sorted(dropped)[:6]}"})

        # Glossary compliance.
        for term in glossary_terms:
            en, mr = term.get("english", ""), term.get("marathi", "")
            if not en or not mr:
                continue
            if re.search(rf"\b{re.escape(en)}\b", src, re.IGNORECASE) and mr not in tgt:
                findings.append({"block": idx, "kind": "glossary_miss",
                                 "detail": f"'{en}' present but canonical '{mr}' not used"})

        # Gross length anomalies usually mean truncation or padding.
        if len(src) > 120:
            ratio = len(tgt) / len(src)
            if ratio < 0.45:
                findings.append({"block": idx, "kind": "suspiciously_short",
                                 "detail": f"target/source length ratio {ratio:.2f}"})
            elif ratio > 2.2:
                findings.append({"block": idx, "kind": "suspiciously_long",
                                 "detail": f"target/source length ratio {ratio:.2f}"})

    return findings


_COMMON_SENTENCE_STARTERS = {
    "the", "a", "an", "and", "but", "or", "if", "when", "he", "she", "they", "it",
    "this", "that", "these", "those", "in", "on", "at", "to", "for", "we", "you",
    "i", "his", "her", "their", "its", "there", "then", "so", "as", "by", "of",
    "one", "two", "after", "before", "because", "what", "why", "how", "who",
}


def proper_nouns(text: str) -> set[str]:
    """Capitalised words that are probably names, not sentence openers.

    A bare "starts with a capital" test flags every sentence-initial word --
    "Following", "Once", "During" -- which a Marathi translation drops for
    perfectly good reasons. Only capitals appearing *mid-sentence* are treated
    as proper nouns, since those are the ones project policy requires to
    survive in Latin script.
    """
    out: set[str] = set()
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        words = sentence.split()
        for i, raw in enumerate(words):
            w = raw.strip("\"'()[],;:.!?")
            if i == 0 or not w or not LATIN_WORD.fullmatch(w):
                continue
            if w.lower() in _COMMON_SENTENCE_STARTERS:
                continue
            # ALL-CAPS is typography, not a name. This book opens sections with
            # small-caps runs ("IT WAS PRECISELY THE ..."), whose words are
            # ordinary prose and are correctly translated into Marathi.
            if w.isupper() and len(w) > 1:
                continue
            # Nationality adjectives are capitalised but are adjectives, not
            # names, and translate normally. Matched against an explicit list --
            # a suffix rule like "-ian" would also swallow real names such as
            # Duncan and Vivian.
            if w.lower() in _DEMONYMS:
                continue
            out.add(w)
    return out


# Capitalised words that read like names but are ordinary vocabulary.
_DEMONYMS = {
    "american", "british", "canadian", "indian", "chinese", "japanese",
    "european", "african", "australian", "russian", "german", "french",
    "spanish", "italian", "mexican", "korean", "english", "irish", "scottish",
    "dutch", "swedish", "swiss", "brazilian", "israeli", "egyptian",
}


def render_page_png(pdf_path: Path, page_index: int, dpi: int) -> bytes:
    doc = fitz.open(pdf_path)
    pix = doc[page_index].get_pixmap(dpi=dpi)
    data = pix.tobytes("png")
    doc.close()
    return data


def run(cfg: Config, st: State, llm: LLM, pages: list[int] | None = None) -> dict:
    glossary_terms = load_glossary(cfg.path("paths.glossary")).get("terms", [])
    do_auto = bool(cfg.get("qa.automated"))
    do_vision = bool(cfg.get("qa.vision"))
    dpi = int(cfg.get("qa.vision_dpi"))

    src = cfg.path("paths.input_pdf")
    out_pdf = cfg.path("paths.output_dir") / f"{src.stem} - Marathi.pdf"

    candidates = [p["page_no"] for p in st.pages(only_with_text=True)]
    if pages:
        candidates = [p for p in candidates if p in pages]

    print(f"\n[6/6] QA  {len(candidates)} pages"
          f"  (automated={do_auto}, vision={do_vision})")

    if do_vision and not out_pdf.exists():
        raise FileNotFoundError(
            f"{out_pdf} not found -- run `python run.py render` before the vision pass"
        )

    # Map source page -> output page index (continuation pages shift the numbering).
    page_index = _output_index_map(cfg, st, out_pdf) if do_vision else {}

    auto_fail = 0
    vis_fail = 0
    all_findings: dict[int, list] = {}

    # --- automated checks: pure local work, no API ------------------------
    if do_auto:
        lexicon = lowercase_lexicon(st)
        for page_no in candidates:
            findings = automated_checks(st.blocks_for_page(page_no), glossary_terms,
                                        lexicon)
            st.record_qa(page_no, 1, "automated", not findings, findings)
            if findings:
                auto_fail += 1
                all_findings.setdefault(page_no, []).extend(findings)
        print(f"    automated: {len(candidates)} pages checked, {auto_fail} with findings",
              flush=True)

    # --- vision: one call per rendered page, run concurrently -------------
    if do_vision:
        workers = max(1, int(cfg.get("translation.concurrency", 6)))
        # Inspect each page plus any continuation pages it spawned -- those are
        # newly generated layout and exactly where overflow problems surface.
        targets: list[tuple[int, int, str]] = []
        for page_no in candidates:
            idx = page_index.get(page_no)
            if idx is None:
                continue
            next_idx = page_index.get(page_no + 1, idx + 1)
            for t in range(idx, max(next_idx, idx + 1)):
                targets.append((page_no, t, "page" if t == idx else f"continuation +{t - idx}"))

        print(f"    vision: {len(targets)} rendered pages, {workers} concurrent", flush=True)

        def inspect(job: tuple[int, int, str]) -> tuple[int, str, list[dict]]:
            page_no, t, kind = job
            png = render_page_png(out_pdf, t, dpi)
            res = llm.vision_call(
                system=VISION_SYSTEM,
                user=(f"Inspect this rendered page for layout defects. "
                      f"(source page {page_no}, {kind})"),
                image_png=png, schema=VISION_SCHEMA, schema_name="page_layout_qa",
                label=f"qa/p{page_no}[{kind}]",
            )
            return page_no, kind, [
                {**x, "where": kind} for x in res.get("issues", [])
                if x.get("severity") in ("medium", "high")
            ]

        per_page: dict[int, list[dict]] = {}
        done = 0
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(inspect, j): j for j in targets}
            for fut in as_completed(futures):
                job = futures[fut]
                try:
                    page_no, _kind, issues = fut.result()
                except Exception as e:
                    errors.append(f"p{job[0]}: {type(e).__name__}: {e}")
                    continue
                per_page.setdefault(page_no, []).extend(issues)
                done += 1
                if done % 25 == 0 or done == len(targets):
                    print(f"      {done}/{len(targets)} inspected", flush=True)

        for page_no, issues in per_page.items():
            st.record_qa(page_no, 1, "vision", not issues, issues)
            if issues:
                vis_fail += 1
                all_findings.setdefault(page_no, []).extend(issues)
        if errors:
            print(f"    WARNING: {len(errors)} vision calls failed, e.g. {errors[0][:120]}")

    report = cfg.path("paths.workdir") / "qa_report.json"
    report.write_text(json.dumps(all_findings, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"    automated failures: {auto_fail} pages")
    if do_vision:
        print(f"    vision failures:    {vis_fail} pages")
    print(f"    full report -> {report}")

    st.log("qa", f"auto_fail={auto_fail} vision_fail={vis_fail} pages={len(candidates)}")
    return {"pages": len(candidates), "auto_fail": auto_fail, "vision_fail": vis_fail,
            "report": str(report)}


def _output_index_map(cfg: Config, st: State, out_pdf: Path) -> dict[int, int]:
    """Source page -> output page index, as recorded by the renderer.

    This is read from workdir/page_map.json rather than reconstructed: a single
    overflowing page can spawn several continuation pages, so any "one extra
    page per overflow" assumption drifts and would send the vision pass to
    inspect the wrong page.
    """
    map_path = cfg.path("paths.workdir") / "page_map.json"
    if map_path.exists():
        return {int(k): int(v) for k, v in
                json.loads(map_path.read_text(encoding="utf-8")).items()}

    doc = fitz.open(out_pdf)
    n_out = len(doc)
    doc.close()
    src_pages = [p["page_no"] for p in st.pages()]
    if n_out == len(src_pages):
        return {p: i for i, p in enumerate(src_pages)}

    raise FileNotFoundError(
        f"{map_path} is missing and the output page count ({n_out}) differs from the "
        f"source ({len(src_pages)}), so pages cannot be matched reliably. Re-run "
        "`python run.py render` to regenerate it."
    )
