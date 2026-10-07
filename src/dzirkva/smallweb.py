"""Incremental personal-voice scores shared by crawler and feed ingestion.

Keep one count per URL so refreshing a post replaces its contribution. Historical
crawl pages are backfilled in bounded batches by scripts/score_small_web.py.
"""

import re
import sqlite3

from dzirkva.crawl import CORPORATE_WORDS, I_WORDS, REPORTING_WORDS, VOICE_DB

MIN_WORDS = 2000
SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS voice (voice REAL, corporate REAL, reporting REAL, host TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS page_counts (
    url TEXT PRIMARY KEY, host TEXT NOT NULL, words INT, personal INT, corporate INT, reporting INT);
CREATE TABLE IF NOT EXISTS totals (
    host TEXT PRIMARY KEY, words INT, personal INT, corporate INT, reporting INT);
CREATE TABLE IF NOT EXISTS progress (name TEXT PRIMARY KEY, value INT);
"""


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(VOICE_DB, timeout=60)
    db.executescript(SCHEMA)
    return db


def update_voice(url: str, text: str, host: str, *, db: sqlite3.Connection | None = None) -> None:
    """Replace one page's counts and refresh its host score; safe with legacy voice.db."""
    owned = db is None
    db = db or connect()
    counts = [0, 0, 0, 0]
    for word in re.findall(r"[ა-ჰ]+|₾", text):
        counts[0] += 1
        counts[1] += word in I_WORDS
        counts[2] += word in CORPORATE_WORDS
        counts[3] += word in REPORTING_WORDS
    try:
        # Lock before reading: feed/crawler/backfill can write concurrently.
        if not db.in_transaction:
            db.execute("BEGIN IMMEDIATE")
        previous = db.execute("SELECT host, words, personal, corporate, reporting FROM page_counts WHERE url=?", (url,)).fetchone()
        changed = {host}
        old_total = db.execute("SELECT words FROM totals WHERE host=?", (host,)).fetchone()
        had_score = bool(old_total and old_total[0] >= MIN_WORDS)
        if previous:
            changed.add(previous[0])
            db.execute("UPDATE totals SET words=words-?, personal=personal-?, corporate=corporate-?, reporting=reporting-? "
                       "WHERE host=?", (*previous[1:], previous[0]))
        db.execute("INSERT INTO totals VALUES (?, ?, ?, ?, ?) ON CONFLICT(host) DO UPDATE SET "
                   "words=words+excluded.words, personal=personal+excluded.personal, "
                   "corporate=corporate+excluded.corporate, reporting=reporting+excluded.reporting", (host, *counts))
        db.execute("INSERT OR REPLACE INTO page_counts VALUES (?, ?, ?, ?, ?, ?)", (url, host, *counts))
        for key in changed:
            n, personal, corporate, reporting = db.execute("SELECT words, personal, corporate, reporting FROM totals WHERE host=?", (key,)).fetchone()
            if n >= MIN_WORDS:
                db.execute("INSERT OR REPLACE INTO voice VALUES (?, ?, ?, ?)",
                           (1000 * personal / n, 1000 * corporate / n, 1000 * reporting / n, key))
            elif had_score or key != host:
                db.execute("DELETE FROM voice WHERE host=?", (key,))
        if owned:
            db.commit()
    finally:
        if owned:
            db.close()
