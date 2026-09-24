"""Search by meaning: Georgian Wikipedia cut into paragraphs, one BGE-M3 vector each (data/passages.db).

Word search misses answers that use other words than the question: "რატომ წითლდება მზე როცა
ბნელდება" finds eclipse pages, but the answer says "ჰაერის მოლეკულები ანაწილებენ მზის თეთრ შუქს".
The question vector finds the nearest paragraphs directly. Built by scripts/build_passages.py.
"""

import re
import sqlite3
import subprocess
from functools import cache
from pathlib import Path
from urllib.parse import quote

import numpy as np

from dzirkva.georgian import count, from_keyboard, georgian_ratio

DB = Path(__file__).resolve().parents[2] / "data" / "passages.db"
SCHEMA = """CREATE TABLE IF NOT EXISTS passages (id INTEGER PRIMARY KEY, title TEXT, text TEXT, v BLOB);
CREATE INDEX IF NOT EXISTS passages_title ON passages(title);"""
CHARS = 600   # passage size: ~160 tokens, one paragraph
DIM = 1024
NAMES = {"iverieli": "ივერიელი", "papers": "სამეცნიერო ნაშრომი"}  # sites outside the wikis (passages with a url)
PDF_PAGE_GEORGIAN = 0.5  # a PDF page with less Georgian (English summary, tables) is skipped
LATIN_WORD = re.compile(r"[A-Za-z]{3,}")
LEADER = re.compile(r"(\.\s?){4,}|…{2,}")  # table of contents: "თავი I ........ 28"


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB, check_same_thread=False, timeout=60)  # several scripts add passages at once
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


def _fix_page(page: str) -> str:
    """A page in the pre-Unicode AcadNusx font (Latin letters for Georgian) → Unicode Georgian: when most of its
    Latin words are Georgian words after georgian.from_keyboard. Unicode pages stay as they are."""
    latin = LATIN_WORD.findall(page)
    if latin and sum(count(from_keyboard(w)) > 0 for w in latin) > len(latin) / 2:
        page = from_keyboard(page)
    return page


def pdf_text(pdf: bytes) -> str:
    """Georgian text of a PDF (needs pdftotext: brew install poppler): pages joined, hyphenated line ends and
    line breaks removed."""
    out = subprocess.run(["pdftotext", "-enc", "UTF-8", "-", "-"], input=pdf, capture_output=True).stdout
    pages = [_fix_page(p) for p in out.decode("utf-8", "replace").split("\f")]
    text = " ".join(p for p in pages if georgian_ratio(p) >= PDF_PAGE_GEORGIAN)
    return re.sub(r"\s+", " ", re.sub(r"-\n(?=\w)", "", text)).strip()


def pdf_chunks(pdf: bytes) -> list[str]:
    """Passages of a PDF's Georgian text; table-of-contents pieces and scraps left out."""
    return [c for c in chunks(pdf_text(pdf)) if len(c) >= 100 and len(LEADER.findall(c)) < 2]


def embed_text(title: str, text: str) -> str:
    return f"{title}. {text}"


@cache
def _index():
    """All vectors in memory (fp16, ~1.8 GB), on the CPU: a torch fp16 dot product with all 872k takes ~20 ms, as fast
    as the GPU, and the GPU memory stays free. Loaded once per process; passages built later need a restart."""
    import torch

    if not DB.exists():
        return None
    rows = _db().execute("SELECT id, v FROM passages WHERE v IS NOT NULL").fetchall()
    if not rows:
        return None
    ids = np.array([i for i, _ in rows])
    vecs = np.frombuffer(b"".join(v for _, v in rows), dtype=np.float16).reshape(-1, DIM)
    return ids, torch.from_numpy(vecs.copy())


def best(title: str, qv, site: str = "wikipedia") -> tuple[str, np.ndarray] | None:
    """The paragraph of one article nearest to the question vector (a better snippet than the engine's) and its
    vector: search ranks the page by it, no new embedding."""
    rows = _db().execute("SELECT text, v FROM passages WHERE title = ? AND site = ? AND v IS NOT NULL",
                         (title, site)).fetchall()
    if not rows:
        return None
    vecs = np.frombuffer(b"".join(v for _, v in rows), dtype=np.float16).reshape(-1, DIM).astype(np.float32)
    i = int(np.argmax(vecs @ qv))
    return rows[i][0], vecs[i]


def search(query: str, limit: int = 20, qv=None) -> list[dict]:
    """The paragraphs nearest to the question in meaning, best one per article (Wikipedia, Wikisource, Iverieli,
    papers). qv: the question vector, when the caller has it already."""
    import torch

    from dzirkva.meaning import vectors
    from dzirkva.wiki import SITES

    index = _index()
    if index is None:
        return []
    ids, vecs = index
    q = torch.as_tensor(vectors([query])[0] if qv is None else qv).to(vecs)
    scores, top = (vecs @ q).topk(min(limit * 4, len(ids)))
    db = _db()
    out, seen = [], set()
    for score, i in zip(scores.tolist(), top.tolist()):
        title, text, site, url = db.execute("SELECT title, text, site, url FROM passages WHERE id = ?",
                                            (int(ids[i]),)).fetchone()
        if (site, url or title) in seen:
            continue
        seen.add((site, url or title))
        if url:
            name = NAMES.get(site, site)
        else:
            _, prefix, name = SITES[site]
            url = prefix + quote(title.replace(" ", "_"))
        out.append({"url": url, "title": f"{title} · {name}",
                    "snippet": text, "engine": "passages", "score": score})
        if len(out) == limit:
            break
    return out


if __name__ == "__main__":
    import sys

    for r in search(" ".join(sys.argv[1:]), 10):
        print(f"{r['score']:.3f} {r['title']}\n      {r['snippet'][:200]}")
