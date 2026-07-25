"""Stage 4 -- revision pass.

The model sees the English source and its own Marathi draft together and
revises. This is the stage that was completely inert in the previous pipeline:
its prompt ended with "Output your analysis as JSON" while never naming the
`final_translation` key the caller read, so `.get()` fell through to the draft
for every single paragraph and `reflection_notes` was empty on all 37 rows.

Here the output shape is enforced by schema, and a block that comes back empty
or missing raises rather than silently reusing the draft.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from .config import Config
from .glossary import as_prompt_block
from .llm import LLM, LLMError
from .prompts import system_prompt
from .state import State
from .translate import apply_markup, chapter_index

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "marathi": {"type": "string"},
                    "changed": {"type": "boolean"},
                    "note": {"type": "string"},
                },
                "required": ["id", "marathi", "changed", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["blocks"],
    "additionalProperties": False,
}

REVIEW_SYSTEM_SUFFIX = """

YOU ARE NOW IN REVIEW MODE.
You will be shown the English source and an existing Marathi draft of the same
blocks. Produce the FINAL Marathi for each block.

Check, in this order:
1. COMPLETENESS -- is anything in the source missing from, or invented in, the draft?
2. ACCURACY -- numbers, dates, currency, names: identical to the source?
3. POLICY -- proper nouns still in Latin script; English idioms still in English;
   glossary terms rendered exactly as the glossary says.
4. GRAMMAR -- Marathi gender and number agreement, case endings, verb concord.
5. FLOW -- does it read like natural conversational Marathi written by a person,
   or like translated English? Fix stiffness, awkward calques and word order.
6. EMPHASIS -- <i>/<b> tags preserved around the right words.

Return the final text for EVERY block, whether or not you changed it. Set
"changed" honestly and keep "note" to a brief phrase (empty string if unchanged).
"""

USER_TEMPLATE = """CHAPTER: {chapter}
PAGE: {page}

Review and finalise these {n} blocks.

{blocks}
"""


@dataclass
class ReviewJob:
    """Pre-fetched page data. Workers never touch SQLite (not thread-safe)."""
    page_no: int
    chapter: str
    payload: list[dict]
    block_ids: dict[int, str]
    drafts: dict[int, str]


def build_review_job(st: State, page_no: int,
                     chapters: dict[int, str] | None = None) -> ReviewJob | None:
    blocks = st.blocks_for_page(page_no)
    if not blocks:
        return None
    if chapters is None:
        chapters = chapter_index(st)
    chapter = chapters.get(page_no) or "(front matter)"
    payload = [
        {
            "id": b["block_index"],
            "english": apply_markup(b["source_text"], json.loads(b["style_json"])),
            "marathi_draft": b["draft"] or "",
        }
        for b in blocks
    ]
    return ReviewJob(
        page_no=page_no, chapter=chapter, payload=payload,
        block_ids={b["block_index"]: b["id"] for b in blocks},
        drafts={b["block_index"]: (b["draft"] or "") for b in blocks},
    )


def run_review_job(llm: LLM, sys_prompt: str, job: ReviewJob) -> dict[int, tuple[str, str]]:
    user = USER_TEMPLATE.format(
        chapter=job.chapter, page=job.page_no, n=len(job.payload),
        blocks=json.dumps(job.payload, ensure_ascii=False, indent=1),
    )
    result = llm.json_call(
        system=sys_prompt, user=user, schema=REVIEW_SCHEMA,
        schema_name="page_review", max_tokens=16000,
        label=f"review/p{job.page_no}",
    )
    got = {int(e["id"]): (e["marathi"], e.get("note", "")) for e in result.get("blocks", [])}
    expected = {p["id"] for p in job.payload}
    missing = expected - set(got)
    empty = {i for i, (m, _) in got.items() if not m.strip()}
    if missing or empty:
        raise LLMError(
            f"page {job.page_no}: incomplete review "
            f"(missing {sorted(missing)}, empty {sorted(empty)})"
        )
    return got


def review_page(cfg: Config, st: State, llm: LLM, page_no: int,
                sys_prompt: str) -> dict[int, tuple[str, str]]:
    """Single-page convenience wrapper."""
    job = build_review_job(st, page_no)
    return run_review_job(llm, sys_prompt, job) if job else {}


def run(cfg: Config, st: State, llm: LLM, pages: list[int] | None = None,
        force: bool = False) -> dict:
    glossary_block = as_prompt_block(cfg.path("paths.glossary"))
    sys_prompt = system_prompt(cfg, glossary_block) + REVIEW_SYSTEM_SUFFIX

    candidates = [p["page_no"] for p in st.pages(only_with_text=True)]
    if pages:
        candidates = [p for p in candidates if p in pages]

    # Only review pages whose blocks are fully drafted.
    ready = []
    for p in candidates:
        bl = st.blocks_for_page(p)
        if bl and all(b["draft"] for b in bl):
            if force or any(b["final"] is None for b in bl):
                ready.append(p)

    if not ready:
        print("\n[4/6] REVIEW  nothing to do (all selected pages already finalised)")
        return {"pages": 0}

    workers = max(1, int(cfg.get("translation.concurrency", 6)))
    print(f"\n[4/6] REVIEW  {len(ready)} pages with {llm.model} ({workers} concurrent)",
          flush=True)

    chapters = chapter_index(st)
    jobs = [j for j in (build_review_job(st, p, chapters) for p in ready) if j]
    changed_total = 0
    done = 0
    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_review_job, llm, sys_prompt, j): j for j in jobs}
        for fut in as_completed(futures):
            job = futures[fut]
            try:
                results = fut.result()
            except Exception as e:
                failures.append(f"p{job.page_no}: {type(e).__name__}: {e}")
                continue
            with st.transaction():
                for idx, bid in job.block_ids.items():
                    final, note = results[idx]
                    if final.strip() != job.drafts[idx].strip():
                        changed_total += 1
                    st.set_final(bid, final, note or None)
            done += 1
            if done % 5 == 0 or done == len(jobs):
                print(f"    {done}/{len(jobs)} pages  |  {changed_total} blocks revised  |  "
                      f"{llm.cost_report(5.0, 30.0)}", flush=True)

    if failures:
        st.log("review", f"{len(failures)} pages failed", level="ERROR")
        raise LLMError(
            f"{len(failures)} of {len(jobs)} pages failed review:\n  "
            + "\n  ".join(failures[:10])
        )

    # A revision rate of ~0 would mean this stage is inert again -- say so loudly.
    total_blocks = sum(len(j.payload) for j in jobs)
    rate = 100 * changed_total / total_blocks if total_blocks else 0
    print(f"    revision rate: {rate:.1f}% of blocks changed")
    if rate < 1.0:
        print("    WARNING: almost nothing changed. Verify the review stage is doing real work.")
    st.log("review", f"{len(ready)} pages, {changed_total} blocks revised ({rate:.1f}%)")
    return {"pages": len(ready), "changed": changed_total}
