"""Collect old Georgian pages from the Internet Archive into data/archive.db. Resumable.

Seeds: pages on dead .ge sites cited in Georgian Wikipedia (data/archive_dead_seeds.txt)
plus each site's home page. Follows same-site links up to MAX_DEPTH, max PER_HOST pages per site.
Polite: one request per second, long pause after HTTP 429/503.

Run in the background:  uv run python scripts/archive_collect.py
Progress:               sqlite3 data/archive.db "select status, count(*) from queue group by 1"
"""

import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dzirkva.archive import connect, parse  # noqa: E402
from dzirkva.georgian import georgian_ratio  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"
RAW = "https://web.archive.org/web/2id_/{}"   # "2" = earliest capture from 2000 on; id_ = original bytes
MIN_GEORGIAN = 0.3
MAX_DEPTH = 2
PER_HOST = 60
PAUSE = 1.0
BUSY_PAUSE = 60


def seed(db) -> None:
    urls = [u for u in (DATA / "archive_dead_seeds.txt").read_text().split("\n") if u.startswith("http")]
    homes = {f"{urlparse(u).scheme}://{urlparse(u).hostname}/" for u in urls}
    db.executemany("INSERT OR IGNORE INTO queue(url, host, depth) VALUES (?, ?, 0)",
                   [(u, urlparse(u).hostname) for u in sorted(homes) + urls])
    db.commit()


def main() -> None:
    db = connect()
    seed(db)
    client = httpx.Client(follow_redirects=True, timeout=60,
                          headers={"User-Agent": "dzirkva-archive/0.1 (Georgian research search; 1 req/s)"})
    while True:
        row = db.execute("SELECT url, host, depth FROM queue WHERE status='todo' ORDER BY depth, rowid LIMIT 1").fetchone()
        if row is None:
            print("queue empty", flush=True)
            return
        url, host, depth = row
        try:
            r = client.get(RAW.format(url))
        except httpx.HTTPError as e:
            db.execute("UPDATE queue SET status=? WHERE url=?", (f"error:{type(e).__name__}", url))
            db.commit()
            time.sleep(PAUSE * 5)
            continue
        if r.status_code in (429, 503):
            print(f"archive busy ({r.status_code}), pause {BUSY_PAUSE}s", flush=True)
            time.sleep(BUSY_PAUSE)
            continue
        status = f"http:{r.status_code}"
        if r.status_code == 200 and "html" in r.headers.get("content-type", ""):
            snapshot = str(r.url).split("/web/")[1].split("/")[0].removesuffix("id_")
            title, text, links, converted = parse(r.content, r.headers.get("content-type", ""), url)
            ratio = georgian_ratio(text)
            status = "done" if ratio >= MIN_GEORGIAN and len(text) > 200 else "not-georgian"
            if status == "done":
                db.execute("INSERT OR REPLACE INTO pages VALUES (?, ?, ?, ?, ?, ?)", (url, snapshot, title, text, ratio, converted))
                db.execute("INSERT INTO pages_fts VALUES (?, ?, ?, ?)", (url, snapshot, title, text))
            have = db.execute("SELECT count(*) FROM queue WHERE host=?", (host,)).fetchone()[0]
            if depth < MAX_DEPTH and have < PER_HOST:
                db.executemany("INSERT OR IGNORE INTO queue(url, host, depth) VALUES (?, ?, ?)",
                               [(l, host, depth + 1) for l in links[: PER_HOST - have]])
            print(f"{status:<13} {'AcadNusx ' if converted else ''}{ratio:4.0%} {url[:90]}", flush=True)
        db.execute("UPDATE queue SET status=? WHERE url=?", (status, url))
        db.commit()
        time.sleep(PAUSE)


if __name__ == "__main__":
    main()
