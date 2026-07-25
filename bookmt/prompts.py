"""Shared prompt construction.

The system prompt is deliberately identical for every page in a run so that
OpenAI's automatic prompt caching sees a stable prefix (cached input bills at
10%). Everything page-specific goes in the user message.
"""

from __future__ import annotations

from .config import Config

REGISTER_GUIDANCE = {
    "conversational": (
        "Conversational, accessible modern Marathi -- the voice of a readable Marathi "
        "trade paperback. The source is chatty American pop-psychology that addresses the "
        "reader directly; match that warmth and directness. Avoid heavy Sanskritised "
        "vocabulary, avoid governmental or academic register."
    ),
    "literary": (
        "Elevated literary Marathi with refined constructions and Sanskrit-derived "
        "vocabulary where natural."
    ),
    "academic": (
        "Precise academic Marathi with consistent technical terminology."
    ),
}

PROPER_NOUN_GUIDANCE = {
    "keep_latin": (
        "PROPER NOUNS STAY IN LATIN SCRIPT, EXACTLY AS WRITTEN. People, companies, "
        "universities, places, book titles, brand names and product names are copied "
        "through unchanged -- do NOT transliterate them into Devanagari. "
        "Example: 'Andrew Hallam bought a car at Costco' -> "
        "'Andrew Hallam ने Costco मधून कार घेतली'."
    ),
    "devanagari": "Transliterate all proper nouns into Devanagari.",
    "devanagari_first_use": (
        "Transliterate proper nouns into Devanagari, with the Latin original in "
        "parentheses at first use in each chapter."
    ),
}

IDIOM_GUIDANCE = {
    "keep_english": (
        "RECOGNISABLE ENGLISH IDIOMS AND FIXED EXPRESSIONS STAY IN ENGLISH, inline and "
        "unchanged. When the source uses a set phrase, a term of art, or a coined "
        "expression, keep the English wording inside the Marathi sentence rather than "
        "paraphrasing it. Example: 'half empty/half full' stays as "
        "'half empty/half full'. Ordinary prose around the idiom is still fully "
        "translated into Marathi. The result is deliberately code-mixed -- this is the "
        "intended house style, not an error."
    ),
    "translate_meaning": (
        "Render idioms as natural Marathi that conveys their meaning. Do not localise "
        "to Indian equivalents; keep the American frame (dollars stay dollars)."
    ),
    "english_plus_gloss": (
        "Keep the English idiom inline and add a short Marathi gloss in parentheses at "
        "first use."
    ),
}

BASE_RULES = """
NON-NEGOTIABLE RULES
1. Translate EVERY block you are given. Never summarise, merge, drop or reorder blocks.
2. Preserve every number, date, percentage, currency amount and unit exactly as written.
   Do not convert units or currencies.
3. Keep the American setting intact. This is a translation, not a localisation.
4. Preserve the emphasis markers you are given. Text wrapped in <i>...</i> or <b>...</b>
   must come back wrapped in the same tag around the corresponding Marathi.
   Use no other HTML.
5. Marathi grammatical gender and number must agree correctly. This is the single most
   common failure in machine-translated Marathi -- check every sentence.
6. Match the source's paragraph-level meaning faithfully. Do not add explanation,
   commentary, or translator's notes.
7. Keep the translation as close to the source's length as you naturally can. It will be
   typeset into the original book's page layout, so needless expansion costs space.
"""


def system_prompt(cfg: Config, glossary_block: str) -> str:
    register = cfg.get("translation.register")
    pn = cfg.get("translation.proper_nouns")
    idi = cfg.get("translation.idioms")

    return f"""You are an expert literary translator rendering an English book into Marathi.

TARGET REGISTER
{REGISTER_GUIDANCE[register]}

{PROPER_NOUN_GUIDANCE[pn]}

{IDIOM_GUIDANCE[idi]}
{BASE_RULES}
ENFORCED GLOSSARY
Render these terms exactly this way every time they appear. Consistency across the
whole book matters more than local elegance.
{glossary_block}
"""
