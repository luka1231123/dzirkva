"""Stream local Georgian FineWeb2 Parquet shards into a resumable independent word index.

uv run python scripts/import_fineweb.py data/fineweb-2/test/000_00000.parquet
Append explicitly named completed shards; --finalize optimizes/verifies a copy-ready SQLite file.
No per-page requests, crawler changes, embeddings, or loading whole shards into memory.
URLs are deduplicated by exact URL (fragments removed); matching normalized text also shares one indexed body.
"""

import argparse
import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

import duckdb

from dzirkva import bulk
from dzirkva.georgian import georgian_ratio

REVISION = "af9c13333eb981300149d5ca60a8e9d659b276b9"
DATASET = "https://huggingface.co/datasets/HuggingFaceFW/fineweb-2"
WIKIMEDIA = ("wikipedia.org", "wikimedia.org", "wiktionary.org", "wikisource.org", "wikibooks.org",
             "wikiquote.org", "wikinews.org", "wikiversity.org", "wikispecies.org")
NOT_TEXT = re.compile(r"\.(?:pdf|jpe?g|png|gif|webp|mp[34]|zip|rar|exe|css|js|woff2?|docx?|xlsx?)$", re.I)
FIELDS = "text,id,dump,url,date,file_path"


def accepted_url(raw: str) -> str:
    try:
        u = urlsplit(raw.strip())
        if u.scheme not in {"http", "https"} or not u.hostname or u.username or u.password or not u.netloc:
            return ""
        _ = u.port  # reject malformed ports
        host = u.hostname.lower().rstrip(".")
        if any(host == w or host.endswith("." + w) for w in WIKIMEDIA) or NOT_TEXT.search(unquote(u.path)):
            return ""
        if any(ord(c) < 32 or c.isspace() for c in raw):
            return ""
        return urlunsplit((u.scheme, u.netloc, u.path or "/", u.query, ""))
    except (ValueError, AttributeError):
        return ""


def title_for(text: str, url: str) -> str:
    first = text.split("\n", 1)[0].strip()
    if 10 <= len(first) <= 180 and georgian_ratio(first) >= .5 and first not in {"მთავარი გვერდი", "საიტის რუკა"}:
        return " ".join(first.split())
    u = urlsplit(url)
    path = re.sub(r"[-_]+", " ", unquote(u.path)).strip("/")
    return (u.hostname + (" / " + path if path else ""))[:180]


def stamp(date: str) -> float:
    try:
        return datetime.fromisoformat(date.replace("Z", "+00:00")).timestamp()
    except (ValueError, OverflowError, OSError):
        return 0


def refresh_group(db, digest, text=None):
    row = db.execute("SELECT url,title,date,dataset_id,dump,file_path FROM captures WHERE digest=? "
                     "ORDER BY capture_time DESC,dataset_id DESC,dump DESC,url LIMIT 1", (digest,)).fetchone()
    if row is None:
        db.execute("DELETE FROM docs WHERE digest=?", (digest,))
        return
    old = db.execute("SELECT id,title FROM docs WHERE digest=?", (digest,)).fetchone()
    if old and old[1] == row[1]:
        # Do not name the title/text columns: the existing FTS trigger only fires for those.
        # Duplicate captures changing URL/date/provenance should not reindex a large unchanged body.
        db.execute("UPDATE docs SET url=?,date=?,dataset_id=?,dump=?,file_path=? WHERE id=?",
                   (row[0], *row[2:], old[0]))
    elif old:
        db.execute("UPDATE docs SET url=?,title=?,date=?,dataset_id=?,dump=?,file_path=? WHERE id=?", (*row, old[0]))
    else:
        db.execute("INSERT INTO docs(url,title,date,dataset_id,dump,file_path,digest,text,source) VALUES (?,?,?,?,?,?,?,?,'FineWeb2')",
                   (*row, digest, text))


def ingest_row(db, row):
    text, dataset_id, dump, raw_url, date, file_path = row
    url = accepted_url(raw_url or "")
    if not url or not isinstance(text, str):
        return False
    # Normalized whitespace gives exact-text dedup across formatting-only copies.
    body = text.strip()
    if len(body) < 300 or georgian_ratio(body) < .5:
        return False
    digest = hashlib.sha256(" ".join(body.split()).encode()).hexdigest()
    date, dataset_id, dump, file_path = date or "", dataset_id or "", dump or "", file_path or ""
    capture_time = stamp(date)
    previous = db.execute("SELECT digest,capture_time,dataset_id,dump FROM captures WHERE url=?", (url,)).fetchone()
    rank = (capture_time, dataset_id, dump, digest)
    if previous and (previous[1], previous[2], previous[3], previous[0]) >= rank:
        return False
    db.execute("INSERT INTO captures VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET "
               "digest=excluded.digest,title=excluded.title,date=excluded.date,capture_time=excluded.capture_time,"
               "dataset_id=excluded.dataset_id,dump=excluded.dump,file_path=excluded.file_path",
               (url, digest, title_for(body, url), date, capture_time, dataset_id, dump, file_path))
    if previous and previous[0] != digest:
        refresh_group(db, previous[0])  # release old canonical URL before assigning its new text
    refresh_group(db, digest, body)
    return True


def file_identity(path: Path) -> str:
    marker = Path(str(path) + ".sha256")
    if marker.exists():
        value = marker.read_text().strip().split()[0]
        if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
            raise ValueError(f"invalid SHA256 marker: {marker}")
        return value.lower()
    stat = path.stat()
    return f"size:{stat.st_size};mtime_ns:{stat.st_mtime_ns}"


def metadata(db, revision):
    existing = db.execute("SELECT value FROM metadata WHERE key='revision'").fetchone()
    if existing and existing[0] != revision:
        raise ValueError("bulk.db belongs to another dataset revision; use a separate output database")
    values = {"dataset": DATASET, "revision": revision, "license": "ODC-BY-1.0",
              "license_note": "Database license; underlying webpage rights are retained. See the dataset card.",
              "language": "kat_Geor", "source": "FineWeb2"}
    db.executemany("INSERT OR REPLACE INTO metadata VALUES (?,?)", values.items())
    db.commit()


def import_file(db, reader, path, batch, limit):
    identity = file_identity(path)
    previous = db.execute("SELECT identity,row_offset,done FROM imports WHERE path=?", (str(path),)).fetchone()
    if previous and previous[0] != identity:
        raise ValueError(f"input changed since checkpoint: {path}")
    if previous and previous[2]:
        print(f"{path.name}: already imported", flush=True)
        return 0
    offset = previous[1] if previous else 0
    db.execute("INSERT OR IGNORE INTO imports(path,identity) VALUES (?,?)", (str(path), identity))
    db.commit()
    # Parquet physical order is stable for a pinned file; one reader and ordered delivery preserve offsets.
    cursor = reader.execute(f"SELECT {FIELDS} FROM read_parquet(?) LIMIT ? OFFSET ?", [str(path), limit or 2**63 - 1, offset])
    processed = changed = 0
    started = time.monotonic()
    while rows := cursor.fetchmany(batch):
        db.execute("BEGIN IMMEDIATE")
        try:
            for row in rows:
                changed += ingest_row(db, row)
            processed += len(rows)
            db.execute("UPDATE imports SET row_offset=? WHERE path=?", (offset + processed, str(path)))
            db.commit()
        except Exception:
            db.rollback()
            raise
        if processed % (batch * 10) == 0:
            print(f"{path.name}: rows={offset+processed:,}, accepted_updates={changed:,}, elapsed={time.monotonic()-started:.1f}s", flush=True)
    # A user limit is a checkpoint, not proof of end-of-file.
    if not limit or processed < limit:
        db.execute("UPDATE imports SET done=1 WHERE path=?", (str(path),))
        db.commit()
    print(f"{path.name}: +{processed:,} rows, {changed:,} accepted updates in {time.monotonic()-started:.1f}s", flush=True)
    return processed


def finalize(db, compact=False):
    """Offline copy preparation; INTEGER PRIMARY KEY document IDs survive VACUUM."""
    print("finalize: optimizing FTS segments", flush=True)
    db.execute("INSERT INTO docs_fts(docs_fts) VALUES('optimize')")
    db.commit()
    free = db.execute("PRAGMA freelist_count").fetchone()[0]
    pages = db.execute("PRAGMA page_count").fetchone()[0]
    ratio = free / pages if pages else 0
    print(f"finalize: {free:,}/{pages:,} pages free ({ratio:.1%})", flush=True)
    if compact or ratio >= .05:
        # Offline only: avoid keeping a database-sized VACUUM transaction in WAL.
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.execute("PRAGMA journal_mode=DELETE")
        print("finalize: compacting database with VACUUM", flush=True)
        db.execute("VACUUM")
    print("finalize: checking FTS against stored documents", flush=True)
    db.execute("INSERT INTO docs_fts(docs_fts,rank) VALUES('integrity-check',1)")
    db.commit()
    print("finalize: running SQLite quick_check", flush=True)
    result = db.execute("PRAGMA quick_check").fetchall()
    if result != [("ok",)]:
        raise sqlite3.DatabaseError(str(result))
    print("finalize: checkpointing and removing WAL", flush=True)
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.execute("PRAGMA journal_mode=DELETE")
    print("quick_check and FTS integrity check: ok; optimized and copy-ready", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--db", type=Path, default=bulk.DB)
    parser.add_argument("--batch", type=int, default=1000)
    parser.add_argument("--limit", type=int, default=0, help="rows per file this run; 0 reads to EOF")
    parser.add_argument("--revision", default=REVISION)
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--memory-limit", default="512MB")
    parser.add_argument("--sqlite-cache-mb", type=int, default=256, help="offline importer SQLite page cache in MiB")
    parser.add_argument("--compact", action="store_true", help="force offline VACUUM with --finalize (automatic at 5%% free pages)")
    parser.add_argument("--finalize", action="store_true", help="verify and optimize index; remove WAL for copying")
    args = parser.parse_args()
    if args.batch < 1 or args.limit < 0 or args.threads < 1 or args.sqlite_cache_mb < 1:
        parser.error("batch/threads/cache must be positive and limit nonnegative")
    if args.compact and not args.finalize:
        parser.error("--compact requires --finalize")
    db = bulk.connect(args.db)
    db.execute(f"PRAGMA cache_size={-args.sqlite_cache_mb * 1024}")
    metadata(db, args.revision)
    reader = duckdb.connect()
    reader.execute("SET threads=?", [args.threads])
    reader.execute("SET memory_limit=?", [args.memory_limit])
    reader.execute("SET preserve_insertion_order=true")
    reader.execute("SET enable_progress_bar=false")
    for path in args.files:
        import_file(db, reader, path.resolve(strict=True), args.batch, args.limit)
    reader.close()
    document_count = db.execute("SELECT count(*) FROM docs").fetchone()[0]
    url_count = db.execute("SELECT count(*) FROM captures").fetchone()[0]
    db.executemany("INSERT OR REPLACE INTO metadata VALUES (?,?)",
                   [("document_count", str(document_count)), ("url_count", str(url_count))])
    db.commit()
    if args.finalize:
        finalize(db, args.compact)
    print(json.dumps({"documents": document_count,
                      "urls": url_count,
                      "imports": db.execute("SELECT path,row_offset,done FROM imports").fetchall()}, ensure_ascii=False), flush=True)
    db.close()


if __name__ == "__main__":
    main()
