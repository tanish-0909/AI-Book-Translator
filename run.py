#!/usr/bin/env python
"""English -> Marathi book translator. Stage-based CLI.

    python run.py preflight              verify model, font, shaping
    python run.py extract                PDF -> blocks + images
    python run.py glossary               build the enforced term list
    python run.py translate [--pages]    draft translation
    python run.py review    [--pages]    revision pass
    python run.py render                 write the Marathi PDF
    python run.py qa        [--pages]    integrity + vision checks
    python run.py all                    every stage in order
    python run.py benchmark --pages 100-103
                                         same pages across model tiers, for comparison
    python run.py status                 what is done and what is not

Any stage failing raises and exits non-zero. Nothing reports success on failure.
"""

from __future__ import annotations

import argparse
import sys
import traceback

from bookmt.config import ensure_dirs, load_config


def parse_pages(spec: str | None) -> list[int] | None:
    """'100', '100-104', '1,5,9-12' -> sorted page numbers (1-based). None = all."""
    if not spec:
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif part:
            out.add(int(part))
    return sorted(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="run.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("stage", choices=["preflight", "extract", "glossary", "translate",
                                     "review", "render", "qa", "verify", "all",
                                     "benchmark", "status"])
    p.add_argument("--pages", help="page selection, e.g. 100-104 or 1,5,9-12")
    p.add_argument("--model", help="force a model id, skipping the preference chain")
    p.add_argument("--force", action="store_true", help="redo work already marked done")
    args = p.parse_args(argv)

    cfg = load_config()
    ensure_dirs(cfg)
    pages = parse_pages(args.pages)

    from bookmt import preflight

    try:
        if args.stage == "preflight":
            preflight.run(cfg, args.model)
            return 0

        if args.stage == "status":
            from bookmt import pipeline
            pipeline.status(cfg)
            return 0

        from bookmt import pipeline

        if args.stage == "benchmark":
            if not pages:
                p.error("benchmark requires --pages, e.g. --pages 100-103")
            pipeline.benchmark(cfg, pages)
            return 0

        pipeline.run_stage(cfg, args.stage, pages=pages, model=args.model, force=args.force)
        return 0

    except Exception as e:
        print(f"\nFAILED [{args.stage}]: {type(e).__name__}: {e}\n", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
