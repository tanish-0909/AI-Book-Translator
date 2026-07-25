"""Stage 2 -- build the enforced terminology list from the book itself.

This replaces the old `context/glossary.json`, which `orchestrator.py:102-106`
overwrote with "{}" on every single run before anything could read it, and
which nothing ever wrote back to.

Two properties matter here:

* **Derived from this book, not from a generic corpus.** The 3.6M-pair
  Samanantar corpus that used to back this was government/news register --
  wrong for a conversational trade book, and it contained no terms anyway.
* **Append-only.** Re-running merges into the existing file. A term you hand-edit
  stays edited; `locked: true` protects it from being overwritten.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import Config
from .llm import LLM
from .state import State

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "english": {"type": "string"},
                    "marathi": {"type": "string"},
                    "kind": {"type": "string",
                             "enum": ["framework", "concept", "technical", "recurring_phrase"]},
                    "rationale": {"type": "string"},
                },
                "required": ["english", "marathi", "kind", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["terms"],
    "additionalProperties": False,
}

CONSOLIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "terms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "english": {"type": "string"},
                    "marathi": {"type": "string"},
                    "kind": {"type": "string"},
                },
                "required": ["english", "marathi", "kind"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["terms"],
    "additionalProperties": False,
}

SYSTEM = """You are a senior Marathi translator preparing to translate an American \
popular-psychology book into Marathi. Before translating, you are building the \
terminology list that will be enforced across the whole book so the same concept \
is never rendered two different ways.

TARGET REGISTER: conversational, accessible modern Marathi -- the voice of a \
readable Marathi trade paperback, not academic or governmental prose.

CRITICAL RULES FOR THIS PROJECT:
- Proper nouns (people, companies, universities, places, book titles) are DELIBERATELY \
  LEFT IN LATIN SCRIPT in this translation. Do NOT propose Devanagari renderings for them \
  and do NOT include them in your term list.
- Recognisable English idioms and fixed expressions are DELIBERATELY LEFT IN ENGLISH \
  inline. Do NOT include those either.
- Only propose terms that are genuinely RECURRING and CONCEPTUAL: the book's framework \
  vocabulary and its repeated technical/psychological concepts. These are the terms \
  where inconsistent rendering would confuse a reader.

Aim for precision over volume. A term belongs on the list only if it appears repeatedly \
and carries specific meaning in the book's argument."""

EXTRACT_USER = """Below is the text of one section of the book.

Identify the recurring conceptual and framework terms that need ONE canonical Marathi \
rendering throughout the book. For each, give the natural conversational Marathi \
rendering you recommend, and one short line on why that rendering.

Exclude proper nouns and English idioms entirely -- those stay in English by project policy.

--- SECTION TEXT ---
{text}
--- END SECTION TEXT ---"""

CONSOLIDATE_USER = """These candidate terms were extracted independently from different \
sections of the same book, so there are duplicates, near-duplicates, and conflicting \
Marathi renderings for the same English term.

Produce the final consolidated glossary:
- Merge duplicates and near-duplicates into one entry.
- Where renderings conflict, choose the single best conversational Marathi rendering.
- Drop anything that is a proper noun or an English idiom (project policy keeps those in English).
- Drop anything too generic to need enforcing (ordinary words any translator would handle).

--- CANDIDATES ---
{candidates}
--- END CANDIDATES ---"""


# Measured against gpt-5.6-sol: one structured call takes ~65-85s almost
# regardless of input size (4k chars -> 66s, 12k -> 78s, 30k -> 83s), because
# latency is dominated by reasoning/output tokens rather than by the prompt.
# So fewer, larger sections finish the stage far faster than many small ones.
def _sections(st: State, max_chars: int = 40_000) -> list[tuple[str, str]]:
    """Group the book's text by chapter, splitting oversized chapters."""
    by_chapter: dict[str, list[str]] = defaultdict(list)
    order: list[str] = []
    for page in st.pages(only_with_text=True):
        chapter = page["chapter"] or "Front matter"
        if chapter not in by_chapter:
            order.append(chapter)
        for b in st.blocks_for_page(page["page_no"]):
            by_chapter[chapter].append(b["source_text"])

    out: list[tuple[str, str]] = []
    for chapter in order:
        text = "\n\n".join(by_chapter[chapter]).strip()
        if not text:
            continue
        if len(text) <= max_chars:
            out.append((chapter, text))
        else:
            for i in range(0, len(text), max_chars):
                out.append((f"{chapter} ({i // max_chars + 1})", text[i:i + max_chars]))
    return out


def load(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"terms": [], "version": 1}


def save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def as_prompt_block(path: Path, limit: int = 400) -> str:
    """Render the glossary for injection into translation prompts."""
    data = load(path)
    terms = data.get("terms", [])[:limit]
    if not terms:
        return "(no glossary terms yet)"
    return "\n".join(f"- {t['english']}  ->  {t['marathi']}" for t in terms)


def run(cfg: Config, st: State, llm: LLM, force: bool = False) -> dict:
    path = cfg.path("paths.glossary")
    existing = load(path)
    locked = {t["english"].lower(): t for t in existing["terms"] if t.get("locked")}

    if existing["terms"] and not force:
        print(f"\n[2/6] GLOSSARY  already built: {len(existing['terms'])} terms at {path.name}")
        print("      re-run with --force to rebuild (locked entries are preserved)")
        return existing

    sections = _sections(st)
    print(f"\n[2/6] GLOSSARY  scanning {len(sections)} sections with {llm.model}")

    candidates: list[dict] = []
    for i, (chapter, text) in enumerate(sections, 1):
        print(f"    [{i}/{len(sections)}] {chapter[:52]}  ({len(text):,} chars) ...",
              end="", flush=True)
        result = llm.json_call(
            system=SYSTEM,
            user=EXTRACT_USER.format(text=text),
            schema=EXTRACT_SCHEMA,
            schema_name="glossary_extract",
            max_tokens=12000,
            label=f"glossary/{chapter[:24]}",
        )
        found = result.get("terms", [])
        candidates.extend(found)
        print(f" {len(found)} terms  ({llm.last_seconds:.0f}s)", flush=True)

    print(f"    consolidating {len(candidates)} candidates ...")
    blob = "\n".join(
        f"- {t.get('english','')} -> {t.get('marathi','')} [{t.get('kind','')}]"
        for t in candidates
    )
    consolidated = llm.json_call(
        system=SYSTEM,
        user=CONSOLIDATE_USER.format(candidates=blob),
        schema=CONSOLIDATE_SCHEMA,
        schema_name="glossary_consolidate",
        max_tokens=16000,
        label="glossary/consolidate",
    )

    final: list[dict] = []
    seen: set[str] = set()
    for t in consolidated.get("terms", []):
        key = t["english"].strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        # A hand-edited, locked entry always wins over a regenerated one.
        final.append(locked.get(key, {**t, "locked": False}))
    for key, t in locked.items():
        if key not in seen:
            final.append(t)

    data = {"version": existing.get("version", 1) + 1, "terms": final}
    save(path, data)
    print(f"    {len(final)} terms -> {path}")
    print("    (edit that file and set \"locked\": true on any entry to pin your wording)")
    st.log("glossary", f"{len(final)} terms from {len(sections)} sections")
    return data
