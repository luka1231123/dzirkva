"""Local Georgian Wikipedia full-text index (data/wiki.db, built by scripts/build_wiki_index.py).

Used for spelling in context (count), for the answer box (article), and for the pages articles cite (cites)."""

import re
import sqlite3
from functools import cache
from pathlib import Path
from urllib.parse import quote

import numpy as np

from dzirkva.georgian import freq
from dzirkva.morph import analyze, families, forms_of

DB = Path(__file__).resolve().parents[2] / "data" / "wiki.db"
TITLES = DB.with_name("titles.tsv")         # site<TAB>title, one line per row of TITLE_VECTORS
TITLE_VECTORS = DB.with_name("titles.npy")  # BGE-M3 title vectors, fp16 (scripts/build_titles.py)
# Local indexes built by scripts/build_wiki_index.py: file, URL prefix, name shown in titles.
SITES = {"wikipedia": (DB, "https://ka.wikipedia.org/wiki/", "ვიკიპედია"),
         "wikisource": (DB.with_name("wikisource.db"), "https://ka.wikisource.org/wiki/", "ვიკიწყარო")}
MAX_FORMS = 40
ANSWER_CHARS = 350
WIKI_URL = "https://ka.wikipedia.org/wiki/"


@cache
def _db(site: str = "wikipedia") -> sqlite3.Connection | None:
    path = SITES[site][0]
    return sqlite3.connect(path, check_same_thread=False) if path.exists() else None


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


def search(words: list[str], limit: int = 20, site: str = "wikipedia") -> list[dict]:
    """Articles with all words (any form), title matches weigh 10×; if too few, any of the words."""
    db = _db(site)
    prefix, name = SITES[site][1:]
    if db is None or not words:
        return []
    rows = []
    for op in (" AND ", " OR "):
        expr = op.join(any_form(w) for w in words)
        rows = db.execute(
            "SELECT title, snippet(wiki, 1, '', '', '…', 30) FROM wiki WHERE wiki MATCH ? "
            "ORDER BY bm25(wiki, 10, 1) LIMIT ?", (expr, limit)).fetchall()
        if len(rows) >= 5 or len(words) == 1:
            break
    return [{"url": prefix + quote(title.replace(" ", "_")), "title": f"{title} — {name}",
             "snippet": snip, "engine": site} for title, snip in rows]


def cites(titles: list[str]) -> list[str]:
    """URLs the articles cite: their references and external links."""
    db = _db()
    if db is None or not titles:
        return []
    marks = ",".join("?" * len(titles))
    return [u for (u,) in db.execute(f"SELECT DISTINCT url FROM cites WHERE title IN ({marks})", titles)]


def cited_on(host: str) -> list[str]:
    """URLs on one site that Georgian Wikipedia cites (host as crawl.domain_of gives it)."""
    db = _db()
    return [u for (u,) in db.execute("SELECT DISTINCT url FROM cites WHERE host = ?", (host,))] if db else []


@cache
def _titles() -> tuple[dict[tuple[str, str], int], np.ndarray] | None:
    if not TITLE_VECTORS.exists():
        return None
    rows = TITLES.read_text(encoding="utf-8").splitlines()
    return {tuple(r.split("\t", 1)): i for i, r in enumerate(rows)}, np.load(TITLE_VECTORS, mmap_mode="r")


def title_similarity(site: str, title: str, qv) -> float | None:
    """Cosine similarity of an article title and the query vector; None if the title has no vector."""
    index = _titles()
    if index is None or (row := index[0].get((site, title))) is None:
        return None
    return float(index[1][row].astype(np.float32) @ qv)
