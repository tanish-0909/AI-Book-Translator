"""Stage 0 -- prove the environment works before spending anything.

Everything downstream assumes: a reachable model, vision input, an embeddable
Devanagari font, and correct complex-script shaping. Each of those is checked
here and each failure is fatal. The old pipeline discovered its problems
mid-run and reported success anyway.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import fitz
import requests

from .config import Config, api_key
from .llm import LLM, LLMError, ModelCaps

# All SIL Open Font License, so they can be embedded and redistributed.
#
# Two families are fetched, not one. Noto Serif Devanagari has no italic face --
# Devanagari has no italic tradition -- so <i> would silently render as regular.
# Because this project deliberately keeps proper nouns AND English idioms in Latin
# script, much of the source's italic content is Latin, and a Latin serif with a
# true italic recovers that emphasis. The CSS fallback chain is "LatinMT, DevaMT",
# so each glyph is drawn by the first family that covers it.
_NOTO = "https://github.com/googlefonts/noto-fonts/raw/main"

FONT_SOURCES = {
    "NotoSerifDevanagari-Regular.ttf": [
        "https://github.com/notofonts/devanagari/raw/main/fonts/NotoSerifDevanagari/hinted/ttf/NotoSerifDevanagari-Regular.ttf",
        f"{_NOTO}/hinted/ttf/NotoSerifDevanagari/NotoSerifDevanagari-Regular.ttf",
    ],
    "NotoSerifDevanagari-Bold.ttf": [
        "https://github.com/notofonts/devanagari/raw/main/fonts/NotoSerifDevanagari/hinted/ttf/NotoSerifDevanagari-Bold.ttf",
        f"{_NOTO}/hinted/ttf/NotoSerifDevanagari/NotoSerifDevanagari-Bold.ttf",
    ],
    "NotoSerif-Regular.ttf": [f"{_NOTO}/hinted/ttf/NotoSerif/NotoSerif-Regular.ttf"],
    "NotoSerif-Italic.ttf": [f"{_NOTO}/hinted/ttf/NotoSerif/NotoSerif-Italic.ttf"],
    "NotoSerif-Bold.ttf": [f"{_NOTO}/hinted/ttf/NotoSerif/NotoSerif-Bold.ttf"],
    "NotoSerif-BoldItalic.ttf": [f"{_NOTO}/hinted/ttf/NotoSerif/NotoSerif-BoldItalic.ttf"],
}

# Each pair needs real shaping: conjunct formation and/or matra reordering.
# A naive 1-glyph-per-codepoint renderer produces len(text) glyphs and fails.
SHAPING_CASES = [
    ("क्षत्रिय", 8),      # क + ् + ष conjunct
    ("विद्यार्थी", 10),   # i-matra reorders before its consonant
    ("श्री", 4),          # श + ् + र ligature
    ("प्रश्न", 6),        # ra-form + conjunct
]


class PreflightError(RuntimeError):
    pass


def _download(name: str, dest: Path) -> Path:
    out = dest / name
    if out.exists() and out.stat().st_size > 50_000:
        print(f"    {name}: already present ({out.stat().st_size / 1024:.0f} KB)")
        return out
    last: Exception | None = None
    for url in FONT_SOURCES[name]:
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            if len(r.content) < 50_000:
                raise PreflightError(f"suspiciously small download ({len(r.content)} bytes)")
            out.write_bytes(r.content)
            print(f"    {name}: downloaded {len(r.content) / 1024:.0f} KB")
            return out
        except Exception as e:  # try the next mirror
            last = e
    raise PreflightError(
        f"could not download {name} from any mirror ({last}). "
        f"Download it manually into {dest} and re-run."
    )


def check_shaping(font_dir: Path) -> None:
    """Assert MuPDF shapes Devanagari using the exact font stack we will embed."""
    from . import typeset

    css = typeset.build_css(24.0)
    arch = typeset.archive(font_dir)

    failures = []
    for text, n_codepoints in SHAPING_CASES:
        doc = fitz.open()
        page = doc.new_page(width=500, height=200)
        page.insert_htmlbox(fitz.Rect(10, 10, 490, 190), typeset.to_html(text),
                            css=css, archive=arch, scale_low=1)
        glyphs = sum(len(s["chars"]) for s in page.get_texttrace())
        doc.close()
        if glyphs >= n_codepoints:
            failures.append(f"{text!r}: {glyphs} glyphs from {n_codepoints} codepoints (no shaping)")
        else:
            print(f"    {text}  {n_codepoints} codepoints -> {glyphs} glyphs  OK")

    if failures:
        raise PreflightError(
            "Devanagari shaping is NOT working with the embedded font:\n  "
            + "\n  ".join(failures)
            + "\nOutput would contain broken conjuncts and misplaced matras."
        )

    # The Latin fallback must actually engage, or <i> emphasis is silently lost.
    doc = fitz.open()
    page = doc.new_page(width=500, height=200)
    page.insert_htmlbox(
        fitz.Rect(10, 10, 490, 190),
        "<p>मराठी <i>italic latin</i> <b>bold latin</b> Costco</p>",
        css=css, archive=arch, scale_low=1,
    )
    faces = sorted({s["font"] for s in page.get_texttrace()})
    doc.close()
    print(f"    font stack in use: {', '.join(faces)}")
    if not any("Italic" in f for f in faces):
        print("    WARNING: no italic face engaged; <i> emphasis on Latin text will be lost")


def check_source_pdf(cfg: Config) -> dict:
    pdf = cfg.path("paths.input_pdf")
    if not pdf.exists():
        raise PreflightError(f"input PDF not found: {pdf}")
    doc = fitz.open(pdf)
    n_pages = len(doc)
    chars = 0
    textless = []
    images = set()
    for i, page in enumerate(doc):
        t = page.get_text("text").strip()
        chars += len(t)
        if len(t) < 20:
            textless.append(i + 1)
        for img in page.get_images(full=True):
            images.add(img[0])
    toc = len(doc.get_toc())
    doc.close()

    if chars < 1000:
        raise PreflightError(
            f"{pdf.name} has almost no extractable text ({chars} chars). "
            "This pipeline requires a real text layer; it has no OCR branch."
        )
    info = {
        "pages": n_pages, "chars": chars, "textless_pages": textless,
        "images": len(images), "toc": toc,
    }
    print(f"    {pdf.name}")
    print(f"    {n_pages} pages | {chars:,} chars | {len(images)} images | {toc} TOC entries")
    if textless:
        preview = textless[:10]
        more = "" if len(textless) <= 10 else f" (+{len(textless) - 10} more)"
        print(f"    image-only pages (passed through untouched): {preview}{more}")
    return info


def resolve_model(cfg: Config, workdir: Path, force: str | None = None) -> ModelCaps:
    """Probe candidate models in order; use the first that answers. Never guess.

    Capabilities are cached per model, so benchmarking an alternative tier does
    not clobber the primary model's probe result.
    """
    key = api_key()
    default_path = workdir / "model_caps.json"

    def cache_path(model: str) -> Path:
        return workdir / f"model_caps.{model}.json"

    if force:
        cached = ModelCaps.load(cache_path(force))
        if cached:
            print(f"    using cached capabilities for {force}")
            return cached
    else:
        cached = ModelCaps.load(default_path)
        if cached:
            print(f"    using cached capabilities for {cached.model} ({default_path.name})")
            return cached

    candidates = [force] if force else list(cfg.get("models.preference"))
    errors = []
    for model in candidates:
        print(f"    probing {model} ...", flush=True)
        try:
            caps = LLM.probe(key, model)
        except LLMError as e:
            errors.append(f"{model}: {e}")
            print(f"      unavailable -- {e}")
            continue
        cache_path(model).write_text(caps.to_json(), encoding="utf-8")
        if not force:
            default_path.write_text(caps.to_json(), encoding="utf-8")
        print(f"      OK  token_param={caps.token_param}  "
              f"json_schema={caps.supports_json_schema}  vision={caps.supports_vision}")
        for n in caps.notes:
            print(f"      note: {n}")
        return caps

    raise PreflightError(
        "No configured model is reachable with this API key:\n  " + "\n  ".join(errors)
        + "\nCheck OPENAI_API_KEY and models.preference in config.yaml."
    )


def run(cfg: Config, force_model: str | None = None) -> dict:
    workdir = cfg.path("paths.workdir")
    workdir.mkdir(parents=True, exist_ok=True)
    font_dir = cfg.path("paths.font_dir")
    font_dir.mkdir(parents=True, exist_ok=True)

    print("\n[0/6] PREFLIGHT")

    print("\n  1. Source PDF")
    pdf_info = check_source_pdf(cfg)

    print("\n  2. Model access")
    caps = resolve_model(cfg, workdir, force_model)
    if not caps.supports_json_schema:
        print("      WARNING: no strict structured outputs; falling back to json_object mode.")
    if not caps.supports_vision and cfg.get("qa.vision"):
        raise PreflightError(
            f"{caps.model} has no vision support but qa.vision is true in config.yaml. "
            "Set qa.vision: false, or choose a vision-capable model."
        )

    print("\n  3. Fonts (Devanagari + Latin fallback)")
    for name in FONT_SOURCES:
        _download(name, font_dir)

    print("\n  4. Complex-script shaping")
    check_shaping(font_dir)

    print("\n  PREFLIGHT PASSED")
    return {"model": caps.model, "caps": caps, "pdf": pdf_info, "font_dir": font_dir}
