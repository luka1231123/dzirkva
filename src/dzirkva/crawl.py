"""Own crawl of the trusted Georgian sites (config/sources.yaml): data/crawl.db (SQLite FTS5).

Filled by scripts/crawl_sites.py with the main text of each page (trafilatura: no menus, no footers)
and its date. Searched like an engine: pages with all query words (any form), best BM25 first.
This removes the engines' limit for the trusted part of the Georgian web: a page no engine returns
can still be found here.
"""

import sqlite3
from functools import cache
from pathlib import Path

from dzirkva.wiki import any_form

DB = Path(__file__).resolve().parents[2] / "data" / "crawl.db"
SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS queue (url TEXT PRIMARY KEY, host TEXT, depth INT, status TEXT DEFAULT 'todo');
CREATE INDEX IF NOT EXISTS queue_status ON queue(status, host);
CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(url UNINDEXED, title, date UNINDEXED, text, tokenize='unicode61');
"""


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB, check_same_thread=False)
    db.executescript(SCHEMA)
    return db


@cache
def _db() -> sqlite3.Connection | None:
    return connect() if DB.exists() else None


def search(words: list[str], limit: int = 20) -> list[dict]:
    """Crawled pages with all words (any form); if too few, with any of them. The date starts the snippet."""
    db = _db()
    if db is None or not words:
        return []
    rows = []
    for op in (" AND ", " OR "):
        expr = op.join(any_form(w) for w in words)
        rows = db.execute(
            "SELECT url, title, date, snippet(pages, 3, '', '', '…', 30) FROM pages "
            "WHERE pages MATCH ? ORDER BY bm25(pages, 0, 5, 0, 1) LIMIT ?", (expr, limit)).fetchall()
        if len(rows) >= 5 or len(words) == 1:
            break
    return [{"url": url, "title": title or url, "snippet": f"{date} — {snip}" if date else snip, "engine": "crawl"}
            for url, title, date, snip in rows]
