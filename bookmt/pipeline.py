"""Stage dispatch and progress reporting.

Every stage runs inside a try/finally that commits state, and every stage
propagates its exceptions. There is deliberately no path where a failed stage
allows a later stage to proceed on partial data -- that is precisely how the
old pipeline shipped an untranslated English document while printing SUCCESS.
"""

from __future__ import annotations

from .config import Config, api_key
from .llm import LLM
from .preflight import resolve_model
from .state import State


def _llm(cfg: Config, model: str | None = None) -> LLM:
    caps = resolve_model(cfg, cfg.path("paths.workdir"), model)
    return LLM(api_key(), caps)


def run_stage(cfg: Config, stage: str, *, pages: list[int] | None = None,
              model: str | None = None, force: bool = False) -> None:
    db = cfg.path("paths.state_db")

    if stage == "all":
        for s in ("extract", "glossary", "translate", "review", "render", "qa", "verify"):
            run_stage(cfg, s, pages=pages, model=model, force=force)
        return

    with State(db) as st:
        if stage == "extract":
            from . import extract
            extract.run(cfg, st)
            return

        if stage == "glossary":
            from . import glossary
            glossary.run(cfg, st, _llm(cfg, model), force=force)
            return

        if stage == "translate":
            from . import translate
            translate.run(cfg, st, _llm(cfg, model), pages=pages, force=force)
            return

        if stage == "review":
            from . import review
            review.run(cfg, st, _llm(cfg, model), pages=pages, force=force)
            return

        if stage == "render":
            from . import render
            render.run(cfg, st, pages=pages)
            return

        if stage == "qa":
            from . import qa
            qa.run(cfg, st, _llm(cfg, model), pages=pages)
            return

        if stage == "verify":
            from . import verify
            if not verify.run(cfg, st):
                raise RuntimeError("acceptance checks failed -- see the report above")
            return

    raise ValueError(f"unknown stage: {stage}")


def benchmark(cfg: Config, pages: list[int]) -> None:
    """Translate the same pages on each configured tier for side-by-side comparison.

    Nothing is written to the pipeline state -- this only produces a report, so
    you can pick a tier before committing to a full-book run.
    """
    import json
    import time

    from .glossary import as_prompt_block
    from .llm import LLM, LLMError
    from .preflight import resolve_model
    from .prompts import system_prompt
    from .translate import translate_page

    tiers = list(cfg.get("models.benchmark"))
    sys_prompt = system_prompt(cfg, as_prompt_block(cfg.path("paths.glossary")))
    out_path = cfg.path("paths.workdir") / "benchmark.md"
    lines: list[str] = [f"# Tier benchmark -- pages {pages}\n"]

    with State(cfg.path("paths.state_db")) as st:
        sources = {p: {b["block_index"]: b["source_text"] for b in st.blocks_for_page(p)}
                   for p in pages}

        results: dict[str, dict] = {}
        for tier in tiers:
            print(f"\n  --- {tier} ---", flush=True)
            try:
                caps = resolve_model(cfg, cfg.path("paths.workdir"), force=tier)
            except Exception as e:
                print(f"    unavailable: {e}", flush=True)
                continue
            llm = LLM(api_key(), caps)
            per_page: dict[int, dict[int, str]] = {}
            t0 = time.monotonic()
            try:
                for p in pages:
                    tr, _ = translate_page(cfg, st, llm, p, sys_prompt, "")
                    per_page[p] = tr
                    print(f"    page {p} ok ({llm.last_seconds:.0f}s)", flush=True)
            except LLMError as e:
                print(f"    failed: {e}", flush=True)
                continue
            results[tier] = {
                "pages": per_page,
                "seconds": time.monotonic() - t0,
                "usage": dict(llm.usage),
                "cost": llm.cost_report(*_PRICES.get(tier, (5.0, 30.0))),
            }

    for tier, r in results.items():
        lines.append(f"\n## {tier}\n\n- {r['cost']}\n- wall clock: {r['seconds']:.0f}s\n")

    for p in pages:
        lines.append(f"\n---\n\n## Page {p}\n")
        for idx, src in sorted(sources[p].items()):
            lines.append(f"\n**Source [{idx}]**\n\n> {src}\n")
            for tier, r in results.items():
                mr = r["pages"].get(p, {}).get(idx, "(missing)")
                lines.append(f"\n*{tier}*\n\n> {mr}\n")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  side-by-side comparison -> {out_path}")
    for tier, r in results.items():
        print(f"    {tier:<16} {r['cost']}   {r['seconds']:.0f}s")


# (input, output) USD per 1M tokens, for the cost line in the benchmark report.
_PRICES = {
    "gpt-5.6-sol": (5.0, 30.0),
    "gpt-5.6-terra": (2.5, 15.0),
    "gpt-5.6-luna": (1.0, 6.0),
    "gpt-5.5": (5.0, 30.0),
    "gpt-5.4": (2.5, 15.0),
}


def status(cfg: Config) -> None:
    db = cfg.path("paths.state_db")
    if not db.exists():
        print("No state yet. Run:  python run.py extract")
        return

    with State(db) as st:
        c = st.conn
        pages = c.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        textless = c.execute("SELECT COUNT(*) FROM pages WHERE has_text=0").fetchone()[0]
        blocks = c.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
        drafted = c.execute("SELECT COUNT(*) FROM blocks WHERE draft IS NOT NULL").fetchone()[0]
        finalised = c.execute("SELECT COUNT(*) FROM blocks WHERE final IS NOT NULL").fetchone()[0]
        images = c.execute("SELECT COUNT(*) FROM images").fetchone()[0]

        def pct(n: int) -> str:
            return f"{n:,}/{blocks:,} ({100 * n / blocks:.1f}%)" if blocks else "0"

        print("\n  PIPELINE STATUS")
        print(f"    pages       {pages}  ({textless} image-only)")
        print(f"    blocks      {blocks:,}")
        print(f"    drafted     {pct(drafted)}")
        print(f"    finalised   {pct(finalised)}")
        print(f"    images      {images}")

        import json

        gl = cfg.path("paths.glossary")
        if gl.exists():
            data = json.loads(gl.read_text(encoding="utf-8"))
            terms = data.get("terms", [])
            locked = sum(1 for t in terms if t.get("locked"))
            print(f"    glossary    {len(terms)} terms ({locked} locked)")
        else:
            print("    glossary    not built")

        wd = cfg.path("paths.workdir")
        for label, name in (("summaries", "chapter_summaries.json"),
                            ("outline", "toc.json"),
                            ("page map", "page_map.json")):
            p = wd / name
            if p.exists():
                n = len(json.loads(p.read_text(encoding="utf-8")))
                print(f"    {label:<11} {n} entries")
            else:
                print(f"    {label:<11} not built")

        out = cfg.path("paths.output_dir") / f"{cfg.path('paths.input_pdf').stem} - Marathi.pdf"
        if out.exists():
            import fitz
            d = fitz.open(out)
            print(f"    output      {out.name} ({len(d)} pages, "
                  f"{out.stat().st_size / 1e6:.1f} MB)")
            d.close()
        else:
            print("    output      not rendered")

        fails = st.qa_failures()
        if fails:
            print(f"    QA failures {len(fails)} pages: "
                  f"{[f['page_no'] for f in fails][:15]}")
        else:
            done = c.execute("SELECT COUNT(DISTINCT page_no) FROM qa").fetchone()[0]
            print(f"    QA          {done} pages checked, no outstanding failures")
