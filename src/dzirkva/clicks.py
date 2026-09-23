"""Clicks on results (data/clicks.db): the pages people chose for a question rank higher next time.

A click is good when no other click on the same question follows within POGO seconds: a quick
return to the list means the page did not answer (a short click). The question key is its content
words in dictionary form, sorted (search.click_key), so ილია ჭავჭავაძის and ჭავჭავაძე ილია are one question.
"""

import math
import sqlite3
import time
from collections import Counter
from functools import cache
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "clicks.db"
POGO = 30  # seconds


@cache
def _db() -> sqlite3.Connection:
    db = sqlite3.connect(DB, check_same_thread=False)
    db.executescript("CREATE TABLE IF NOT EXISTS clicks (time REAL, key TEXT, url TEXT, rank INT);"
                     "CREATE INDEX IF NOT EXISTS clicks_key ON clicks(key);")
    return db


def log(key: str, url: str, rank: int) -> None:
    _db().execute("INSERT INTO clicks VALUES (?, ?, ?, ?)", (time.time(), key, url, rank))
    _db().commit()


def good(key: str) -> Counter[str]:
    """Good clicks per URL for the question."""
    rows = _db().execute("SELECT time, url FROM clicks WHERE key = ? ORDER BY time", (key,)).fetchall()
    return Counter(url for (t, url), (after, _) in zip(rows, rows[1:] + [(math.inf, None)]) if after - t > POGO)
