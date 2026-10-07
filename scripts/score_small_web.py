"""Backfill small-web voice counts without rereading the full crawl each run.

New crawler/feed pages update scores during ingestion. This bounded, resumable
backfill covers old pages; repeat until caught up. Existing scores remain available.
Run: uv run python scripts/score_small_web.py --limit 2000
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dzirkva.crawl import DB, domain_of  # noqa: E402
from dzirkva.smallweb import connect, update_voice  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=2000, help="maximum historical pages per run")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")
    out = connect()
    row = out.execute("SELECT value FROM progress WHERE name='crawl_rowid'").fetchone()
    cursor = row[0] if row else 0
    crawl = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
    processed = 0
    # pages_content avoids involving the FTS index during historical extraction.
    for rowid, url, text in crawl.execute("SELECT id, c0, c3 FROM pages_content WHERE id>? ORDER BY id LIMIT ?", (cursor, args.limit)):
        out.execute("BEGIN IMMEDIATE")
        # An ingestion refresh may already hold newer text: don't overwrite it.
        if not out.execute("SELECT 1 FROM page_counts WHERE url=?", (url,)).fetchone():
            update_voice(url, text or "", domain_of(url), db=out)
        out.execute("INSERT OR REPLACE INTO progress VALUES ('crawl_rowid', ?)", (rowid,))
        out.commit()
        cursor = rowid
        processed += 1
    scored = out.execute("SELECT count(*) FROM voice").fetchone()[0]
    print(f"{processed} historical pages processed through row {cursor}; {scored} domains scored")
    crawl.close()
    out.close()


if __name__ == "__main__":
    main()
