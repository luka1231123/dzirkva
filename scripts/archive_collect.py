"""Collect old Georgian pages from the Internet Archive into data/archive.db. Resumable.

Seeds: pages on dead .ge sites cited in Georgian Wikipedia (data/archive_dead_seeds.txt)
plus each site's home page. Follows same-site links up to MAX_DEPTH, max PER_HOST pages per site.
WORKERS parallel downloads, short PAUSE each. HTTP 503 (Archive overloaded, short-lived): that download
retries after RETRY_PAUSE s. HTTP 429 (rate limit) or 5 overloads in a row: all pause BUSY_PAUSE s.

Run in the background:  uv run python scripts/archive_collect.py
Progress:               sqlite3 data/archive.db "select status, count(*) from queue group by 1"
"""

import asyncio
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
WORKERS = 3  # 6 made the Archive refuse connections
PAUSE = 0.2
BUSY_PAUSE = 60
RETRY_PAUSE = 5


def seed(db) -> None:
    urls = [u for u in (DATA / "archive_dead_seeds.txt").read_text().split("\n") if u.startswith("http")]
    homes = {f"{urlparse(u).scheme}://{urlparse(u).hostname}/" for u in urls}
    db.executemany("INSERT OR IGNORE INTO queue(url, host, depth) VALUES (?, ?, 0)",
                   [(u, urlparse(u).hostname) for u in sorted(homes) + urls])
    db.commit()


async def main() -> None:
    db = connect()
    seed(db)
    db.execute("UPDATE queue SET status='todo' WHERE status='doing'")  # pages cut off by a restart
    db.commit()
    busy_until = 0.0
    in_flight = 0
    overloads = 0  # 503s in a row

    def claim():
        row = db.execute("SELECT url, host, depth FROM queue WHERE status='todo' ORDER BY depth, rowid LIMIT 1").fetchone()
        if row:
            db.execute("UPDATE queue SET status='doing' WHERE url=?", (row[0],))
        return row

    async def worker(client: httpx.AsyncClient) -> None:
        nonlocal busy_until, in_flight, overloads
        while True:
            row = claim()  # asyncio runs one worker at a time here: no race
            if row is None:
                if in_flight == 0:
                    return
                await asyncio.sleep(2)
                continue
            url, host, depth = row
            in_flight += 1
            try:
                await asyncio.sleep(max(0.0, busy_until - time.time()))
                r = await client.get(RAW.format(url))
            except httpx.HTTPError:  # refused / timed out: the Archive is overloaded, retry later
                in_flight -= 1
                overloads += 1
                db.execute("UPDATE queue SET status='todo' WHERE url=?", (url,))
                db.commit()
                if overloads >= 5:
                    busy_until, overloads = time.time() + BUSY_PAUSE, 0
                    print(f"archive refusing connections, all pause {BUSY_PAUSE}s", flush=True)
                await asyncio.sleep(RETRY_PAUSE)
                continue
            in_flight -= 1
            if r.status_code in (429, 503):
                overloads += 1
                db.execute("UPDATE queue SET status='todo' WHERE url=?", (url,))
                db.commit()
                if r.status_code == 429 or overloads >= 5:
                    busy_until, overloads = time.time() + BUSY_PAUSE, 0
                    print(f"archive busy ({r.status_code}), all pause {BUSY_PAUSE}s", flush=True)
                await asyncio.sleep(RETRY_PAUSE)
                continue
            overloads = 0
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
            await asyncio.sleep(PAUSE)

    async with httpx.AsyncClient(follow_redirects=True, timeout=60, headers={
            "User-Agent": "dzirkva-archive/0.1 (Georgian research search)"}) as client:
        await asyncio.gather(*(worker(client) for _ in range(WORKERS)))
    print("queue empty", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
