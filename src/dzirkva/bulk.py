"""Read the locally imported Georgian FineWeb2 corpus; no network or embeddings."""

import sqlite3
from functools import cache
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "bulk.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);
CREATE TABLE IF NOT EXISTS docs(
 id INTEGER PRIMARY KEY,url TEXT NOT NULL UNIQUE,title TEXT,text TEXT,date TEXT,source TEXT,
 digest TEXT NOT NULL UNIQUE,dataset_id TEXT,dump TEXT,file_path TEXT);
CREATE TABLE IF NOT EXISTS captures(
 url TEXT PRIMARY KEY,digest TEXT NOT NULL,title TEXT,date TEXT,capture_time REAL,
 dataset_id TEXT,dump TEXT,file_path TEXT);
CREATE INDEX IF NOT EXISTS captures_digest ON captures(digest);
CREATE TABLE IF NOT EXISTS imports(
 path TEXT PRIMARY KEY,identity TEXT NOT NULL,row_offset INTEGER DEFAULT 0,done INTEGER DEFAULT 0);
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(title,text,content='docs',content_rowid='id',tokenize='unicode61');
CREATE TRIGGER IF NOT EXISTS docs_insert AFTER INSERT ON docs BEGIN
 INSERT INTO docs_fts(rowid,title,text) VALUES(new.id,new.title,new.text); END;
CREATE TRIGGER IF NOT EXISTS docs_delete AFTER DELETE ON docs BEGIN
 INSERT INTO docs_fts(docs_fts,rowid,title,text) VALUES('delete',old.id,old.title,old.text); END;
CREATE TRIGGER IF NOT EXISTS docs_update AFTER UPDATE OF title,text ON docs BEGIN
 INSERT INTO docs_fts(docs_fts,rowid,title,text) VALUES('delete',old.id,old.title,old.text);
 INSERT INTO docs_fts(rowid,title,text) VALUES(new.id,new.title,new.text); END;
"""


def connect(path: Path = DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=60)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute("PRAGMA cache_size=-32768")
    db.execute("PRAGMA temp_store=FILE")
    db.executescript(SCHEMA)
    db.execute("INSERT INTO docs_fts(docs_fts,rank) VALUES('rank','bm25(5.0,1.0)')")
    db.commit()
    return db


@cache
def _db() -> sqlite3.Connection | None:
    if not DB.exists():
        return None
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, check_same_thread=False, timeout=30)
    db.execute("PRAGMA cache_size=-8192")
    return db


def count() -> int:
    db = _db()
    return db.execute("SELECT count(*) FROM docs").fetchone()[0] if db else 0


def get(url: str, offset: int = 0, max_chars: int | None = None) -> dict | None:
    db = _db()
    if db is None:
        return None
    offset = max(0, offset)
    text = "substr(d.text,?,?)" if max_chars is not None else "substr(d.text,?)"
    params = [offset + 1, max(0, max_chars), url] if max_chars is not None else [offset + 1, url]
    row = db.execute(f"SELECT c.url,c.title,c.date,d.source,{text},length(d.text),c.dataset_id,c.dump,c.file_path,c.digest "
                     "FROM captures c JOIN docs d ON d.digest=c.digest WHERE c.url=?", params).fetchone()
    if not row:
        return None
    provenance = dict(db.execute("SELECT key,value FROM metadata"))
    provenance.update(zip(("dataset_id", "dump", "file_path", "digest"), row[6:]))
    return dict(zip(("url", "title", "date", "source", "text", "total_chars"), row[:6]), provenance=provenance)


def search(words: list[str], limit: int = 20) -> list[dict]:
    db = _db()
    if db is None or not words or limit <= 0:
        return []
    from dzirkva.wiki import any_form

    rows = []
    for op in (" AND ", " OR "):
        expr = op.join(any_form(w) for w in words)
        # Native FTS rank supplies top rowids before loading their metadata/body snippets.
        rows = db.execute("WITH hits AS MATERIALIZED (SELECT rowid,rank AS score,snippet(docs_fts,1,'','','…',35) AS preview "
                          "FROM docs_fts WHERE docs_fts MATCH ? ORDER BY rank LIMIT ?) "
                          "SELECT d.url,d.title,d.date,h.preview FROM hits h JOIN docs d ON d.id=h.rowid ORDER BY h.score,h.rowid",
                          (expr, min(limit, 100))).fetchall()
        if len(rows) >= 5 or len(words) == 1:
            break
    return [{"url": u, "title": t, "date": date, "snippet": (f"{date[:10]} · " if date else "") + preview,
             "engine": "bulk"} for u, t, date, preview in rows]
