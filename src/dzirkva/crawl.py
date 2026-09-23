"""Own crawl of Georgian sites: data/crawl.db (SQLite FTS5), filled by scripts/crawl_sites.py.

Pages: the main text of each page (trafilatura: no menus, no footers) and its date. Searched like
an engine: pages with all query words (any form), best BM25 first. A page no engine returns can
still be found here.
Domains: trusted sites (config/sources.yaml) and discovered ones (cited in Georgian Wikipedia or
linked from crawled pages), with Georgian share, commercial score, kind and inbound links.
Search boosts small, non-commercial, Georgian domains (small_site).
"""

import sqlite3
from functools import cache
from pathlib import Path
from urllib.parse import urlparse

from dzirkva.sources import sources
from dzirkva.wiki import any_form

DB = Path(__file__).resolve().parents[2] / "data" / "crawl.db"
SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS queue (url TEXT PRIMARY KEY, host TEXT, depth INT, status TEXT DEFAULT 'todo');
CREATE INDEX IF NOT EXISTS queue_status ON queue(status, host);
CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(url UNINDEXED, title, date UNINDEXED, text, tokenize='unicode61');
CREATE TABLE IF NOT EXISTS domains (
    host TEXT PRIMARY KEY, source TEXT, state TEXT,   -- source: trusted/wiki/link; state: probe/full/rejected
    pages INT DEFAULT 0, georgian REAL DEFAULT 0,     -- pages with text, mean share of Georgian letters
    signals TEXT DEFAULT '', commercial INT DEFAULT 0, kind TEXT DEFAULT 'other', inbound INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS links (src TEXT, dst TEXT, PRIMARY KEY (src, dst)) WITHOUT ROWID;
"""
MIN_GEORGIAN = 0.3
COMMERCIAL = 2       # commercial score from which a domain is commercial
SMALL_INBOUND = 100  # small domain: at most this many citing Wikipedia pages + linking crawled sites


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB, check_same_thread=False)
    db.executescript(SCHEMA)
    return db


@cache
def _db() -> sqlite3.Connection | None:
    return connect() if DB.exists() else None


def domain_of(url: str) -> str:
    """Crawl key of a URL: the trusted domain that covers it (tsu.ge for press.tsu.ge), else the host without www."""
    try:
        host = (urlparse(url).hostname or "").removeprefix("www.")
    except ValueError:  # malformed URL
        return ""
    h = host
    while h:
        if h in sources():
            return h
        h = h.partition(".")[2]
    return host


def small_site(url: str) -> bool:
    """The URL is on a small, non-commercial, Georgian domain that the crawl accepted."""
    db = _db()
    return db is not None and db.execute(
        "SELECT 1 FROM domains WHERE host=? AND state='full' AND commercial<? AND georgian>=? AND inbound<=?",
        (domain_of(url), COMMERCIAL, MIN_GEORGIAN, SMALL_INBOUND)).fetchone() is not None


def domain_signals(url: str) -> set[str]:
    """Kind and signals of the accepted crawled domain ({'academic', 'dspace', 'rss'}); empty if unknown."""
    db = _db()
    row = db and db.execute("SELECT kind, signals FROM domains WHERE host=? AND state='full'",
                            (domain_of(url),)).fetchone()
    return {row[0], *row[1].split(",")} - {"", "other"} if row else set()


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
