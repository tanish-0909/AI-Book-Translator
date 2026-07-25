"""Stage 3 -- per-page draft translation.

One call per page, with chapter context. The response is constrained by a JSON
schema, so a parse failure is impossible by construction -- unlike the old
reflect stage, whose prompt asked for "JSON" without ever declaring the keys
the caller read, silently yielding the raw draft for 100% of paragraphs.

Every returned block is validated against what was sent. A missing or empty
block raises rather than being quietly backfilled with English.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

from .config import Config
from .glossary import as_prompt_block
from .llm import LLM, LLMError
from .prompts import system_prompt
from .state import State

TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "marathi": {"type": "string"},
                },
                "required": ["id", "marathi"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["blocks"],
    "additionalProperties": False,
}

CHAPTER_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}

USER_TEMPLATE = """CHAPTER: {chapter}

STORY SO FAR (for continuity of argument, pronouns and terminology):
{summary}

{lookaround}

TRANSLATE THE FOLLOWING {n} BLOCKS FROM PAGE {page}.
Return one entry per block id. Every id below must appear exactly once in your response.

{blocks}
"""

LOOKAROUND = """CONTEXT -- END OF PREVIOUS PAGE (do not translate, for continuity only):
{prev}

CONTEXT -- START OF NEXT PAGE (do not translate, for continuity only):
{next}
"""


def apply_markup(text: str, style: dict[str, Any]) -> str:
    """Wrap italic/bold character ranges in tags so emphasis survives the round trip."""
    runs = style.get("runs") or []
    if not runs:
        return text
    out: list[str] = []
    pos = 0
    for r in sorted(runs, key=lambda r: r["start"]):
        s, e = max(0, r["start"]), min(len(text), r["end"])
        if s >= e or s < pos:
            continue
        out.append(text[pos:s])
        inner = text[s:e]
        if r.get("b"):
            inner = f"<b>{inner}</b>"
        if r.get("i"):
            inner = f"<i>{inner}</i>"
        out.append(inner)
        pos = e
    out.append(text[pos:])
    return "".join(out)


def strip_markup(text: str) -> str:
    return re.sub(r"</?[ib]>", "", text)


def _page_text(st: State, page_no: int, head: int = 0, tail: int = 0) -> str:
    blocks = [b["source_text"] for b in st.blocks_for_page(page_no)]
    if not blocks:
        return "(no text on this page)"
    joined = "\n\n".join(blocks)
    if tail:
        return joined[-tail:]
    if head:
        return joined[:head]
    return joined


@dataclass
class PageJob:
    """Everything one page needs, read from SQLite up front.

    Pre-fetching matters: pages are translated concurrently and a sqlite3
    connection is not safe to share across threads, so workers touch no
    database at all -- they only make the API call.
    """
    page_no: int
    chapter: str
    summary: str
    lookaround: str
    payload: list[dict]
    block_ids: dict[int, str]


def build_job(cfg: Config, st: State, page_no: int, summary: str,
              chapters: dict[int, str] | None = None) -> PageJob | None:
    blocks = st.blocks_for_page(page_no)
    if not blocks:
        return None

    if chapters is None:
        chapters = chapter_index(st)
    chapter = chapters.get(page_no) or "(front matter)"

    ctx = int(cfg.get("translation.context_pages"))
    lookaround = ""
    if ctx:
        prev_txt = _page_text(st, page_no - ctx, tail=700) if page_no - ctx >= 1 else "(start of book)"
        next_txt = _page_text(st, page_no + ctx, head=700) if page_no + ctx <= st.page_count() else "(end of book)"
        lookaround = LOOKAROUND.format(prev=prev_txt, next=next_txt)

    payload = [
        {"id": b["block_index"], "text": apply_markup(b["source_text"], json.loads(b["style_json"]))}
        for b in blocks
    ]
    return PageJob(
        page_no=page_no, chapter=chapter, summary=summary, lookaround=lookaround,
        payload=payload, block_ids={b["block_index"]: b["id"] for b in blocks},
    )


def run_job(llm: LLM, sys_prompt: str, job: PageJob) -> dict[int, str]:
    """Pure API work -- safe to run in a worker thread."""
    user = USER_TEMPLATE.format(
        chapter=job.chapter, summary=job.summary or "(beginning of the book)",
        lookaround=job.lookaround, n=len(job.payload), page=job.page_no,
        blocks=json.dumps(job.payload, ensure_ascii=False, indent=1),
    )
    result = llm.json_call(
        system=sys_prompt, user=user, schema=TRANSLATE_SCHEMA,
        schema_name="page_translation", max_tokens=16000,
        label=f"translate/p{job.page_no}",
    )

    got = {int(e["id"]): e["marathi"] for e in result.get("blocks", [])}
    expected = {p["id"] for p in job.payload}
    missing = expected - set(got)
    empty = {i for i, v in got.items() if not v.strip()}
    if missing or empty:
        raise LLMError(
            f"page {job.page_no}: incomplete translation "
            f"(missing block ids {sorted(missing)}, empty {sorted(empty)}). "
            "Refusing to fall back to English."
        )
    return got


def translate_page(cfg: Config, st: State, llm: LLM, page_no: int,
                   sys_prompt: str, summary: str) -> tuple[dict[int, str], str]:
    """Single-page convenience wrapper (used by the benchmark command)."""
    job = build_job(cfg, st, page_no, summary)
    if job is None:
        return {}, summary
    return run_job(llm, sys_prompt, job), summary


TOC_SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "marathi": {"type": "string"},
                },
                "required": ["index", "marathi"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["entries"],
    "additionalProperties": False,
}


def translate_toc(cfg: Config, llm: LLM, sys_prompt: str) -> dict[int, str]:
    """Translate the PDF outline titles.

    The outline lives in the PDF catalogue, not in the page text, so it is not
    covered by the per-page pass. Left alone it would leave a Marathi book with
    an English table of contents in every reader's sidebar.
    """
    import fitz

    out_path = cfg.path("paths.workdir") / "toc.json"
    if out_path.exists():
        return {int(k): v for k, v in json.loads(out_path.read_text(encoding="utf-8")).items()}

    doc = fitz.open(cfg.path("paths.input_pdf"))
    toc = doc.get_toc()
    doc.close()
    if not toc:
        return {}

    payload = [{"index": i, "english": t[1]} for i, t in enumerate(toc)]
    result = llm.json_call(
        system=sys_prompt,
        user=(
            "Translate these table-of-contents entries into Marathi, applying the same "
            "register, proper-noun and idiom policy as the body text. Keep them short "
            "-- they are navigation labels, not sentences.\n\n"
            + json.dumps(payload, ensure_ascii=False, indent=1)
        ),
        schema=TOC_SCHEMA, schema_name="toc_translation",
        max_tokens=8000, label="translate/toc",
    )
    mapping = {int(e["index"]): e["marathi"] for e in result.get("entries", [])}
    out_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    translated {len(mapping)} table-of-contents entries")
    return mapping


def run(cfg: Config, st: State, llm: LLM, pages: list[int] | None = None,
        force: bool = False) -> dict:
    glossary_block = as_prompt_block(cfg.path("paths.glossary"))
    sys_prompt = system_prompt(cfg, glossary_block)

    candidates = [p["page_no"] for p in st.pages(only_with_text=True)]
    if pages:
        candidates = [p for p in candidates if p in pages]
    if not force:
        todo = set(st.pages_needing("draft"))
        candidates = [p for p in candidates if p in todo]

    if not candidates:
        print("\n[3/6] TRANSLATE  nothing to do (all selected pages already drafted)")
        return {"pages": 0}

    workers = max(1, int(cfg.get("translation.concurrency", 6)))
    print(f"\n[3/6] TRANSLATE  {len(candidates)} pages with {llm.model} "
          f"({workers} concurrent)", flush=True)

    translate_toc(cfg, llm, sys_prompt)
    summaries = chapter_summaries(cfg, st, llm)

    chapters = chapter_index(st)
    jobs = []
    for page_no in candidates:
        chapter = chapters.get(page_no) or "(front matter)"
        job = build_job(cfg, st, page_no, summaries.get(chapter, ""), chapters)
        if job:
            jobs.append(job)

    done = 0
    failures: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_job, llm, sys_prompt, j): j for j in jobs}
        for fut in as_completed(futures):
            job = futures[fut]
            try:
                translations = fut.result()
            except Exception as e:
                # Record and keep going, then fail the stage at the end. A page
                # left undrafted is caught again by render, which refuses to
                # emit English.
                failures.append(f"p{job.page_no}: {type(e).__name__}: {e}")
                continue
            # DB writes happen only here, on the main thread.
            with st.transaction():
                for idx, bid in job.block_ids.items():
                    st.set_draft(bid, translations[idx])
            done += 1
            if done % 5 == 0 or done == len(jobs):
                print(f"    {done}/{len(jobs)} pages  |  {llm.cost_report(5.0, 30.0)}",
                      flush=True)

    if failures:
        st.log("translate", f"{len(failures)} pages failed", level="ERROR")
        raise LLMError(
            f"{len(failures)} of {len(jobs)} pages failed to translate:\n  "
            + "\n  ".join(failures[:10])
        )

    st.log("translate", f"{done} pages drafted; {llm.cost_report(5.0, 30.0)}")
    return {"pages": done}


def chapter_index(st: State) -> dict[int, str]:
    """page_no -> chapter title, read once instead of rescanning per page."""
    return {p["page_no"]: (p["chapter"] or "(front matter)") for p in st.pages()}


def chapter_summaries(cfg: Config, st: State, llm: LLM) -> dict[str, str]:
    """One summary per chapter, computed once and cached.

    This replaces the old page-by-page running summary. That design forced the
    whole book to be translated strictly in order; a per-chapter summary is both
    better context (it covers the entire argument, not just what came before)
    and lets every page be translated independently and in parallel.
    """
    path = cfg.path("paths.workdir") / "chapter_summaries.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))

    by_chapter: dict[str, list[str]] = {}
    for page in st.pages(only_with_text=True):
        ch = page["chapter"] or "(front matter)"
        for b in st.blocks_for_page(page["page_no"]):
            by_chapter.setdefault(ch, []).append(b["source_text"])

    out: dict[str, str] = {}
    print(f"    summarising {len(by_chapter)} chapters for translation context", flush=True)
    for i, (chapter, parts) in enumerate(by_chapter.items(), 1):
        text = "\n\n".join(parts)[:40_000]
        if len(text) < 400:
            out[chapter] = ""
            continue
        res = llm.json_call(
            system=("You summarise book chapters to give a translator the context they need "
                    "for pronouns, terminology and continuity of argument."),
            user=("Summarise this chapter in 4-6 sentences: its argument, the people and "
                  "cases it discusses, and any terms it introduces.\n\n" + text),
            schema=CHAPTER_SUMMARY_SCHEMA, schema_name="chapter_summary",
            max_tokens=4000, label=f"summary/{chapter[:20]}",
        )
        out[chapter] = res.get("summary", "")
        print(f"      [{i}/{len(by_chapter)}] {chapter[:48]} ({llm.last_seconds:.0f}s)",
              flush=True)

    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
