"""Local Georgian Wikipedia full-text index (data/wiki.db, built by scripts/build_wiki_index.py).

Used for spelling in context (count) and for the answer box (article)."""

import re
import sqlite3
from functools import cache
from pathlib import Path
from urllib.parse import quote

from dzirkva.georgian import freq
from dzirkva.morph import analyze, families, forms_of

DB = Path(__file__).resolve().parents[2] / "data" / "wiki.db"
MAX_FORMS = 40
ANSWER_CHARS = 350
WIKI_URL = "https://ka.wikipedia.org/wiki/"


@cache
def _db() -> sqlite3.Connection | None:
    return sqlite3.connect(DB, check_same_thread=False) if DB.exists() else None


def any_form(word: str) -> str:
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
    expr = " AND ".join(any_form(w) for w in words)
    return db.execute("SELECT count(*) FROM wiki WHERE wiki MATCH ?", (expr,)).fetchone()[0]


def _names(text: str) -> list[set[str]]:
    return [families(w) | {w} for w in re.findall(r"[ა-ჰ]+", text)]


def _opening(body: str) -> str:
    """First sentences of the article, up to ~350 characters, without markup leftovers like '( ; '."""
    text = re.sub(r"\(\s*[;,]?\s*\)|\(\s*[;,]\s*", lambda m: "" if m.group().endswith(")") else "(", body)
    if len(text) <= ANSWER_CHARS:
        return text
    cut = text.rfind(". ", 0, ANSWER_CHARS)
    return text[: cut + 1] if cut > ANSWER_CHARS // 3 else text[:ANSWER_CHARS] + "…"


def article(words: list[str]) -> dict | None:
    """Answer box: the article whose title names exactly the query words, in any form.

    "ვინ იყო ილია ჭავჭავაძე" → ილია ჭავჭავაძე. The title must have no extra words, so
    "ილია ჭავჭავაძის ლექსები" shows no box. Several meanings: title without "(…)" first, then the longest.
    """
    db = _db()
    if db is None or not words:
        return None
    want = _names(" ".join(words))
    expr = " AND ".join(f"title: {any_form(w)}" for w in words)
    best = None
    for title, body in db.execute("SELECT title, body FROM wiki WHERE wiki MATCH ? LIMIT 50", (expr,)):
        have = _names(re.sub(r"\(.*?\)", "", title))
        if len(have) != len(want) or "მრავალმნიშვნელოვანი" in title:
            continue
        if all(any(a & b for b in have) for a in want) and all(any(a & b for a in want) for b in have):
            key = ("(" not in title, len(body))
            if best is None or key > best[0]:
                best = (key, title, body)
    if best is None:
        return None
    _, title, body = best
    return {"title": title, "text": _opening(body), "url": WIKI_URL + quote(title.replace(" ", "_"))}
