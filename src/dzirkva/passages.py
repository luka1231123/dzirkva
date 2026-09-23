"""Search by meaning: Georgian Wikipedia cut into paragraphs, one BGE-M3 vector each (data/passages.db).

Word search misses answers that use other words than the question: "რატომ წითლდება მზე როცა
ბნელდება" finds eclipse pages, but the answer says "ჰაერის მოლეკულები ანაწილებენ მზის თეთრ შუქს".
The question vector finds the nearest paragraphs directly. Built by scripts/build_passages.py.
"""

import re
import sqlite3
from functools import cache
from pathlib import Path
from urllib.parse import quote

import numpy as np

DB = Path(__file__).resolve().parents[2] / "data" / "passages.db"
SCHEMA = """CREATE TABLE IF NOT EXISTS passages (id INTEGER PRIMARY KEY, title TEXT, text TEXT, v BLOB);
CREATE INDEX IF NOT EXISTS passages_title ON passages(title);"""
CHARS = 600   # passage size: ~160 tokens, one paragraph
DIM = 1024


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB, check_same_thread=False)
    db.executescript(SCHEMA)
    columns = {c[1] for c in db.execute("PRAGMA table_info(passages)")}
    if "site" not in columns:  # wiki.SITES key, or 'iverieli'
        db.execute("ALTER TABLE passages ADD COLUMN site TEXT DEFAULT 'wikipedia'")
    if "url" not in columns:  # set for pages outside the wikis (scripts/iverieli_text.py)
        db.execute("ALTER TABLE passages ADD COLUMN url TEXT")
    return db


@cache
def _db() -> sqlite3.Connection:
    """One read connection per process. WAL: the web page reads while scripts add passages."""
    db = connect()
    db.execute("PRAGMA journal_mode=WAL")
    return db


def chunks(body: str) -> list[str]:
    """Whole sentences, up to ~600 characters per passage."""
    out, cur = [], ""
    for s in re.split(r"(?<=[.!?])\s+", body):
        if cur and len(cur) + len(s) > CHARS:
            out.append(cur.strip())
            cur = ""
        cur += s + " "
    return out + [cur.strip()] if cur.strip() else out


def embed_text(title: str, text: str) -> str:
    return f"{title}. {text}"


@cache
def _index():
    """All vectors on the GPU (fp16, ~1 GB). Loaded once per process; passages built later need a restart."""
    import torch

    if not DB.exists():
        return None
    rows = _db().execute("SELECT id, v FROM passages WHERE v IS NOT NULL").fetchall()
    if not rows:
        return None
    ids = np.array([i for i, _ in rows])
    vecs = np.frombuffer(b"".join(v for _, v in rows), dtype=np.float16).reshape(-1, DIM)
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    return ids, torch.from_numpy(vecs.copy()).to(device)


def best(title: str, qv, site: str = "wikipedia") -> str | None:
    """The paragraph of one article nearest to the question vector (a better snippet than the engine's)."""
    rows = _db().execute("SELECT text, v FROM passages WHERE title = ? AND site = ? AND v IS NOT NULL",
                         (title, site)).fetchall()
    if not rows:
        return None
    vecs = np.frombuffer(b"".join(v for _, v in rows), dtype=np.float16).reshape(-1, DIM).astype(np.float32)
    return rows[int(np.argmax(vecs @ qv))][0]


def search(query: str, limit: int = 20) -> list[dict]:
    """The paragraphs nearest to the question in meaning, best one per article (Wikipedia, Wikisource, Iverieli)."""
    from dzirkva.meaning import _model
    from dzirkva.wiki import SITES

    index = _index()
    if index is None:
        return []
    ids, vecs = index
    q = _model().encode([query], normalize_embeddings=True, convert_to_tensor=True).to(vecs)
    scores, top = (vecs @ q[0]).topk(min(limit * 4, len(ids)))
    db = _db()
    out, seen = [], set()
    for score, i in zip(scores.tolist(), top.tolist()):
        title, text, site, url = db.execute("SELECT title, text, site, url FROM passages WHERE id = ?",
                                            (int(ids[i]),)).fetchone()
        if (site, url or title) in seen:
            continue
        seen.add((site, url or title))
        if url:
            name = "ივერიელი"
        else:
            _, prefix, name = SITES[site]
            url = prefix + quote(title.replace(" ", "_"))
        out.append({"url": url, "title": f"{title} — {name}",
                    "snippet": text, "engine": "passages", "score": score})
        if len(out) == limit:
            break
    return out


if __name__ == "__main__":
    import sys

    for r in search(" ".join(sys.argv[1:]), 10):
        print(f"{r['score']:.3f} {r['title']}\n      {r['snippet'][:200]}")
