"""Local Georgian Wikipedia full-text index (data/wiki.db, built by scripts/build_wiki_index.py)."""

import sqlite3
from functools import cache
from pathlib import Path

from dzirkva.georgian import freq
from dzirkva.morph import analyze, forms_of

DB = Path(__file__).resolve().parents[2] / "data" / "wiki.db"
MAX_FORMS = 40


@cache
def _db() -> sqlite3.Connection | None:
    return sqlite3.connect(DB, check_same_thread=False) if DB.exists() else None


def _any_form(word: str) -> str:
    """FTS5 expression matching any known form of the word's lemma."""
    lemma = analyze(word)[0].lemma
    forms = sorted(set(forms_of(lemma)) | {word, lemma}, key=freq, reverse=True)[:MAX_FORMS]
    return "(" + " OR ".join(f'"{f}"' for f in forms) + ")"


@cache
def count(*words: str) -> int:
    """Number of articles that contain every word (in any form)."""
    db = _db()
    if db is None or not words:
        return 0
    expr = " AND ".join(_any_form(w) for w in words)
    return db.execute("SELECT count(*) FROM wiki WHERE wiki MATCH ?", (expr,)).fetchone()[0]
