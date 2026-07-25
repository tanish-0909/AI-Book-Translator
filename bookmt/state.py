"""SQLite state. Rows are keyed by a hash of their content, not by position.

The previous schema keyed rows on f"p_{page}_{i}" where `i` was a global
enumeration index. Any edit to the source or the extractor shifted every id
after the change point, so stale rows accumulated and seq_order desynchronised
silently. Hashing the content makes a changed block a *new* row and leaves
unchanged blocks reusable, which is also what makes re-runs cheap.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    page_no      INTEGER PRIMARY KEY,
    width        REAL NOT NULL,
    height       REAL NOT NULL,
    rotation     INTEGER NOT NULL DEFAULT 0,
    has_text     INTEGER NOT NULL DEFAULT 1,
    chapter      TEXT,
    status       TEXT NOT NULL DEFAULT 'extracted',
    context_summary TEXT
);

CREATE TABLE IF NOT EXISTS blocks (
    id            TEXT PRIMARY KEY,      -- sha256(page|index|source_text)
    page_no       INTEGER NOT NULL,
    block_index   INTEGER NOT NULL,
    x0 REAL, y0 REAL, x1 REAL, y1 REAL,
    source_text   TEXT NOT NULL,
    style_json    TEXT NOT NULL,         -- font size, bold/italic runs
    draft         TEXT,
    final         TEXT,
    review_notes  TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    UNIQUE(page_no, block_index)
);
CREATE INDEX IF NOT EXISTS idx_blocks_page   ON blocks(page_no);
CREATE INDEX IF NOT EXISTS idx_blocks_status ON blocks(status);

CREATE TABLE IF NOT EXISTS images (
    xref       INTEGER PRIMARY KEY,
    page_no    INTEGER NOT NULL,
    sha256     TEXT NOT NULL,            -- of the ORIGINAL compressed stream
    ext        TEXT NOT NULL,
    width      INTEGER, height INTEGER,
    n_bytes    INTEGER NOT NULL,
    rects_json TEXT NOT NULL,
    filepath   TEXT
);

CREATE TABLE IF NOT EXISTS qa (
    page_no    INTEGER NOT NULL,
    attempt    INTEGER NOT NULL,
    kind       TEXT NOT NULL,            -- 'automated' | 'vision'
    passed     INTEGER NOT NULL,
    findings   TEXT,
    PRIMARY KEY (page_no, attempt, kind)
);

CREATE TABLE IF NOT EXISTS run_log (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    DATETIME DEFAULT CURRENT_TIMESTAMP,
    level TEXT NOT NULL,
    stage TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


def block_id(page_no: int, block_index: int, source_text: str) -> str:
    h = hashlib.sha256()
    h.update(f"{page_no}|{block_index}|".encode("utf-8"))
    h.update(source_text.encode("utf-8"))
    return h.hexdigest()[:32]


class State:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "State":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- logging ------------------------------------------------------------

    def log(self, stage: str, message: str, level: str = "INFO") -> None:
        # The old log_event() passed "ERROR" as the *stage* and left level at
        # its "INFO" default, so every error was recorded as INFO. Kept separate here.
        self.conn.execute(
            "INSERT INTO run_log (level, stage, message) VALUES (?,?,?)",
            (level, stage, message),
        )
        self.conn.commit()
        print(f"  [{level}] {stage}: {message}")

    # -- pages --------------------------------------------------------------

    def upsert_page(self, page_no: int, width: float, height: float, rotation: int,
                    has_text: bool, chapter: str | None) -> None:
        self.conn.execute(
            """INSERT INTO pages (page_no,width,height,rotation,has_text,chapter)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(page_no) DO UPDATE SET
                 width=excluded.width, height=excluded.height,
                 rotation=excluded.rotation, has_text=excluded.has_text,
                 chapter=excluded.chapter""",
            (page_no, width, height, rotation, int(has_text), chapter),
        )

    def set_page_status(self, page_no: int, status: str) -> None:
        self.conn.execute("UPDATE pages SET status=? WHERE page_no=?", (status, page_no))
        self.conn.commit()

    def set_page_summary(self, page_no: int, summary: str) -> None:
        self.conn.execute("UPDATE pages SET context_summary=? WHERE page_no=?", (summary, page_no))
        self.conn.commit()

    def pages(self, only_with_text: bool = False) -> list[sqlite3.Row]:
        q = "SELECT * FROM pages"
        if only_with_text:
            q += " WHERE has_text=1"
        return list(self.conn.execute(q + " ORDER BY page_no"))

    def page_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]

    # -- blocks -------------------------------------------------------------

    def upsert_block(self, page_no: int, block_index: int, bbox: tuple[float, float, float, float],
                     source_text: str, style: dict[str, Any]) -> str:
        bid = block_id(page_no, block_index, source_text)
        self.conn.execute(
            """INSERT INTO blocks (id,page_no,block_index,x0,y0,x1,y1,source_text,style_json)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(page_no, block_index) DO UPDATE SET
                 id=excluded.id, x0=excluded.x0, y0=excluded.y0,
                 x1=excluded.x1, y1=excluded.y1,
                 source_text=excluded.source_text, style_json=excluded.style_json,
                 draft=CASE WHEN blocks.id=excluded.id THEN blocks.draft ELSE NULL END,
                 final=CASE WHEN blocks.id=excluded.id THEN blocks.final ELSE NULL END,
                 status=CASE WHEN blocks.id=excluded.id THEN blocks.status ELSE 'pending' END""",
            (bid, page_no, block_index, *bbox, source_text, json.dumps(style, ensure_ascii=False)),
        )
        return bid

    def blocks_all(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM blocks ORDER BY page_no, block_index"))

    def blocks_for_page(self, page_no: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM blocks WHERE page_no=? ORDER BY block_index", (page_no,)
            )
        )

    def set_draft(self, bid: str, draft: str) -> None:
        self.conn.execute(
            "UPDATE blocks SET draft=?, status='drafted' WHERE id=?", (draft, bid)
        )

    def set_final(self, bid: str, final: str, notes: str | None = None) -> None:
        self.conn.execute(
            "UPDATE blocks SET final=?, review_notes=?, status='final' WHERE id=?",
            (final, notes, bid),
        )

    def pages_needing(self, stage: str) -> list[int]:
        """Pages with at least one block not yet past `stage`."""
        col = {"draft": "draft", "final": "final"}[stage]
        rows = self.conn.execute(
            f"SELECT DISTINCT page_no FROM blocks WHERE {col} IS NULL ORDER BY page_no"
        )
        return [r[0] for r in rows]

    def all_final(self) -> bool:
        n = self.conn.execute("SELECT COUNT(*) FROM blocks WHERE final IS NULL").fetchone()[0]
        return n == 0

    # -- images -------------------------------------------------------------

    def upsert_image(self, xref: int, page_no: int, sha: str, ext: str, w: int, h: int,
                     n_bytes: int, rects: list[tuple[float, float, float, float]],
                     filepath: str | None) -> None:
        self.conn.execute(
            """INSERT INTO images (xref,page_no,sha256,ext,width,height,n_bytes,rects_json,filepath)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(xref) DO UPDATE SET
                 page_no=excluded.page_no, sha256=excluded.sha256, ext=excluded.ext,
                 width=excluded.width, height=excluded.height, n_bytes=excluded.n_bytes,
                 rects_json=excluded.rects_json, filepath=excluded.filepath""",
            (xref, page_no, sha, ext, w, h, n_bytes, json.dumps(rects), filepath),
        )

    def images(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM images ORDER BY page_no, xref"))

    # -- qa -----------------------------------------------------------------

    def record_qa(self, page_no: int, attempt: int, kind: str, passed: bool,
                  findings: Any) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO qa (page_no,attempt,kind,passed,findings) VALUES (?,?,?,?,?)",
            (page_no, attempt, kind, int(passed), json.dumps(findings, ensure_ascii=False)),
        )
        self.conn.commit()

    def qa_failures(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """SELECT q.* FROM qa q
                   JOIN (SELECT page_no, kind, MAX(attempt) AS a FROM qa GROUP BY page_no, kind) m
                     ON q.page_no=m.page_no AND q.kind=m.kind AND q.attempt=m.a
                   WHERE q.passed=0 ORDER BY q.page_no"""
            )
        )

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
