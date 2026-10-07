"""Stream the existing spelling vocabulary into a tiny-memory SQLite font-detection lookup.

Run in the foreground on the Mac; copy data/pdf_words.db to the server. This preserves the original
PDF conversion heuristic without loading the entire vocabulary into each 300 MB ingestion job.
"""

import os
import sqlite3
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"


def main():
    source = DATA / "vocab.tsv"
    if not source.exists():
        source = DATA / "words.tsv"
    path = DATA / "pdf_words.db"
    temporary = path.with_suffix(".db.tmp")
    temporary.unlink(missing_ok=True)
    db = sqlite3.connect(temporary)
    db.execute("CREATE TABLE words(word TEXT PRIMARY KEY) WITHOUT ROWID")
    db.execute("PRAGMA cache_size=-4096")
    count, rows = 0, []
    with source.open() as stream:
        for line in stream:
            word, frequency = line.rstrip("\n").split("\t")
            if int(frequency) > 0:
                rows.append((word,))
            if len(rows) >= 1000:
                db.executemany("INSERT OR IGNORE INTO words VALUES (?)", rows)
                count += len(rows)
                rows.clear()
    db.executemany("INSERT OR IGNORE INTO words VALUES (?)", rows)
    count += len(rows)
    db.commit()
    db.close()
    os.replace(temporary, path)
    print(f"PDF font lookup: {count:,} vocabulary entries, {path.stat().st_size:,} bytes", flush=True)


if __name__ == "__main__":
    main()
