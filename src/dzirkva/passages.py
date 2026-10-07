"""Search by meaning: Georgian Wikipedia cut into paragraphs, one BGE-M3 vector each (data/passages.db).

Word search misses answers that use other words than the question: "რატომ წითლდება მზე როცა
ბნელდება" finds eclipse pages, but the answer says "ჰაერის მოლეკულები ანაწილებენ მზის თეთრ შუქს".
The question vector finds the nearest paragraphs directly. Built by scripts/build_passages.py.
"""

import re
import sqlite3
import subprocess
import tempfile
from functools import cache, lru_cache
from pathlib import Path
from urllib.parse import quote

import numpy as np

from dzirkva.georgian import from_keyboard, georgian_ratio

DB = Path(__file__).resolve().parents[2] / "data" / "passages.db"
SCHEMA = """CREATE TABLE IF NOT EXISTS passages (id INTEGER PRIMARY KEY, title TEXT, text TEXT, v BLOB);
CREATE INDEX IF NOT EXISTS passages_title ON passages(title);"""
CHARS = 600   # passage size: ~160 tokens, one paragraph
DIM = 1024
NAMES = {"iverieli": "ივერიელი", "papers": "სამეცნიერო ნაშრომი"}  # sites outside the wikis (passages with a url)
PDF_PAGE_GEORGIAN = 0.5  # a PDF page with less Georgian (English summary, tables) is skipped
LATIN_WORD = re.compile(r"[A-Za-z]{3,}")
RESCORE = 1000  # candidates from the 1-bit scan that get their exact fp16 score
LEADER = re.compile(r"(\.\s?){4,}|…{2,}")  # table of contents: "თავი I ........ 28"


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB, check_same_thread=False, timeout=60)  # several scripts add passages at once
    db.executescript(SCHEMA)
    columns = {c[1] for c in db.execute("PRAGMA table_info(passages)")}
    if "site" not in columns:  # wiki.SITES key, or 'iverieli'
        db.execute("ALTER TABLE passages ADD COLUMN site TEXT DEFAULT 'wikipedia'")
    if "url" not in columns:  # set for pages outside the wikis (scripts/archive/iverieli_text.py)
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


@cache
def _pdf_words():
    path = DB.with_name("pdf_words.db")
    if not path.exists():
        return None
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, check_same_thread=False)
    db.execute("PRAGMA cache_size=-2048")
    return db


@lru_cache(maxsize=8192)
def _known_pdf_word(word: str) -> bool:
    db = _pdf_words()
    # The exact vocabulary is built once by build_pdf_words.py. Never load the huge Python word dict
    # in a resource-capped PDF ingestion job; Unicode PDFs do not require this font conversion.
    return bool(db and db.execute("SELECT 1 FROM words WHERE word=?", (word,)).fetchone())


def _fix_page(page: str) -> str:
    """A page in the pre-Unicode AcadNusx font (Latin letters for Georgian) → Unicode Georgian: when most of its
    Latin words are Georgian words after georgian.from_keyboard. Unicode pages stay as they are."""
    latin = LATIN_WORD.findall(page)
    if latin and sum(_known_pdf_word(from_keyboard(w)) for w in latin) > len(latin) / 2:
        page = from_keyboard(page)
    return page


def pdf_text(pdf: bytes) -> str:
    """Georgian text of a PDF (needs pdftotext: brew install poppler): pages joined, hyphenated line ends and
    line breaks removed."""
    # Compressed PDFs can expand far beyond their download size. Spool tool output to disk before
    # retaining it, rather than capture_output allocating an unbounded Python byte string.
    with tempfile.TemporaryFile() as output:
        subprocess.run(["pdftotext", "-enc", "UTF-8", "-", "-"], input=pdf,
                       stdout=output, stderr=subprocess.DEVNULL, check=True, timeout=120)
        output.seek(0)
        out = output.read(8 * 1024 * 1024 + 1)
    if len(out) > 8 * 1024 * 1024:
        raise RuntimeError("PDF text exceeds 8 MiB extraction limit")
    pages = [_fix_page(p) for p in out.decode("utf-8", "replace").split("\f")]
    text = " ".join(p for p in pages if georgian_ratio(p) >= PDF_PAGE_GEORGIAN)
    return re.sub(r"\s+", " ", re.sub(r"-\n(?=\w)", "", text)).strip()


def pdf_chunks(pdf: bytes) -> list[str]:
    """Passages of a PDF's Georgian text; table-of-contents pieces and scraps left out."""
    return [c for c in chunks(pdf_text(pdf)) if len(c) >= 100 and len(LEADER.findall(c)) < 2]


def embed_text(title: str, text: str) -> str:
    return f"{title}. {text}"


def build_word_index(db: sqlite3.Connection, batch: int = 1000, limit: int = 20_000) -> tuple[int, bool]:
    """Bounded, resumable FTS backfill. New passage text is indexed by triggers, without embedding."""
    db.executescript("""
    CREATE TABLE IF NOT EXISTS words_progress (id INTEGER PRIMARY KEY CHECK(id=1), last INT, target INT);
    INSERT OR IGNORE INTO words_progress SELECT 1, 0, coalesce(max(id),0) FROM passages;
    CREATE VIRTUAL TABLE IF NOT EXISTS passages_fts USING fts5(title,text,content='passages',content_rowid='id',
                                                             tokenize='unicode61');
    CREATE TRIGGER IF NOT EXISTS passages_words_insert AFTER INSERT ON passages
      WHEN new.id <= (SELECT last FROM words_progress WHERE id=1)
        OR new.id > (SELECT target FROM words_progress WHERE id=1) BEGIN
        INSERT INTO passages_fts(rowid,title,text) VALUES(new.id,new.title,new.text);
    END;
    CREATE TRIGGER IF NOT EXISTS passages_words_delete AFTER DELETE ON passages
      WHEN old.id <= (SELECT last FROM words_progress WHERE id=1)
        OR old.id > (SELECT target FROM words_progress WHERE id=1) BEGIN
        INSERT INTO passages_fts(passages_fts,rowid,title,text) VALUES('delete',old.id,old.title,old.text);
    END;
    CREATE TRIGGER IF NOT EXISTS passages_words_update AFTER UPDATE OF title,text ON passages
      WHEN old.id <= (SELECT last FROM words_progress WHERE id=1)
        OR old.id > (SELECT target FROM words_progress WHERE id=1) BEGIN
        INSERT INTO passages_fts(passages_fts,rowid,title,text) VALUES('delete',old.id,old.title,old.text);
        INSERT INTO passages_fts(rowid,title,text) VALUES(new.id,new.title,new.text);
    END;
    """)
    done = 0
    while done < limit:
        # An immediate transaction keeps a concurrent edit from changing text between read and indexing.
        db.execute("BEGIN IMMEDIATE")
        try:
            last, target = db.execute("SELECT last,target FROM words_progress WHERE id=1").fetchone()
            rows = db.execute("SELECT id,title,text FROM passages WHERE id>? AND id<=? ORDER BY id LIMIT ?",
                              (last, target, min(batch, limit - done))).fetchall()
            if rows:
                db.executemany("INSERT INTO passages_fts(rowid,title,text) VALUES (?,?,?)", rows)
                last = rows[-1][0]
            else:
                last = target
            db.execute("UPDATE words_progress SET last=? WHERE id=1", (last,))
            db.commit()
        except Exception:
            db.rollback()
            raise
        done += len(rows)
        if last >= target:
            return done, True
    return done, False


def word_search(words: list[str], limit: int = 20) -> list[dict]:
    """Stored passage text, including PDFs without vectors. Works while bounded backfill is underway."""
    from dzirkva.wiki import SITES, any_form

    if not words or not DB.exists():
        return []
    db = _db()
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='passages_fts'").fetchone():
        return []
    rows = []
    for op in (" AND ", " OR "):
        expr = op.join(any_form(w) for w in words)
        ids = [r[0] for r in db.execute("SELECT rowid FROM passages_fts WHERE passages_fts MATCH ? "
                                      "AND rank MATCH 'bm25(5,1)' ORDER BY rank LIMIT ?",
                                      (expr, limit * 4))]
        # Rank before joining: otherwise SQLite loads text and vectors for every matching passage
        # into a temporary sort, even though search needs only a small candidate list.
        found = db.execute("SELECT p.id,p.title,p.site,p.url,snippet(passages_fts,1,'','','…',35),p.v "
                           "FROM passages_fts JOIN passages p ON p.id=passages_fts.rowid "
                           f"WHERE passages_fts MATCH ? AND passages_fts.rowid IN ({','.join('?' for _ in ids)})",
                           (expr, *ids)).fetchall() if ids else []
        by_id = {r[0]: r[1:] for r in found}
        rows = [by_id[i] for i in ids if i in by_id]
        if len(rows) >= 5 or len(words) == 1:
            break
    out, seen = [], set()
    for title, site, url, text, v in rows:
        if not url:
            if site not in SITES:
                continue
            url = SITES[site][1] + quote(title.replace(" ", "_"))
        if url in seen:
            continue
        seen.add(url)
        hit = {"url": url, "title": title, "snippet": text, "engine": "passage_words"}
        if v:
            hit["vector"] = np.frombuffer(v, dtype=np.float16).astype(np.float32)
        out.append(hit)
        if len(out) == limit:
            break
    return out


@cache
def _index():
    """A 1-bit copy of every vector in memory: the sign of each dimension, 128 bytes (~110 MB for 870k). The fp16
    vectors (~1.8 GB) stay on disk; search reads only the RESCORE nearest by bits. Read in chunks, so the full
    vectors are never all in memory (the home server has 7 GB). Passages built later need a restart."""
    if not DB.exists():
        return None
    ids, bits = [], []
    rows = _db().execute("SELECT id, v FROM passages WHERE v IS NOT NULL")
    while chunk := rows.fetchmany(50_000):
        ids.append(np.array([i for i, _ in chunk]))
        vecs = np.frombuffer(b"".join(v for _, v in chunk), dtype=np.float16).reshape(-1, DIM)
        bits.append(np.packbits(vecs > 0, axis=1).view(np.uint64))
    if not ids:
        return None
    return np.concatenate(ids), np.concatenate(bits)


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
    from dzirkva.meaning import vectors
    from dzirkva.wiki import SITES

    index = _index()
    if index is None:
        return []
    ids, bits = index
    q = np.asarray(vectors([query])[0] if qv is None else qv, dtype=np.float32)
    dist = np.bitwise_count(bits ^ np.packbits(q > 0).view(np.uint64)).sum(axis=1, dtype=np.uint16)
    n = min(RESCORE, len(ids))
    near = ids[np.argpartition(dist, n - 1)[:n]].tolist()
    db = _db()
    rows = db.execute(f"SELECT title, text, site, url, v FROM passages WHERE id IN ({','.join('?' * n)})",
                      near).fetchall()
    vecs = np.frombuffer(b"".join(r[4] for r in rows), dtype=np.float16).reshape(-1, DIM).astype(np.float32)
    scores = vecs @ q
    out, seen = [], set()
    for i in np.argsort(-scores)[:limit * 4]:
        title, text, site, url, _ = rows[i]
        score = float(scores[i])
        if (site, url or title) in seen:
            continue
        seen.add((site, url or title))
        if url:
            name = NAMES.get(site, site)
        else:
            _, prefix, name = SITES[site]
            url = prefix + quote(title.replace(" ", "_"))
        out.append({"url": url, "title": f"{title} · {name}",
                    "snippet": text, "engine": "passages", "score": score, "vector": vecs[i]})
        if len(out) == limit:
            break
    return out


if __name__ == "__main__":
    import sys

    for r in search(" ".join(sys.argv[1:]), 10):
        print(f"{r['score']:.3f} {r['title']}\n      {r['snippet'][:200]}")
