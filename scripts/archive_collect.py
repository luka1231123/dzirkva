"""Bounded, resumable trickle of old Georgian pages from the Internet Archive.

CDX selects the latest successful HTML capture, collapsed by content digest.
One HTTP request at a time, at least seven seconds apart, including CDX requests
and redirects. Defaults: 20 URLs and at most 20 MB per run; 2 MB per response.
Run: uv run python scripts/archive_collect.py --limit 20 --max-mb 20
"""

import argparse
import asyncio
import fcntl
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dzirkva.archive import connect, parse  # noqa: E402
from dzirkva.georgian import georgian_ratio  # noqa: E402
from dzirkva.ingest import wait_for_host  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"
CDX = "https://web.archive.org/cdx/search/cdx"
RAW = "https://web.archive.org/web/{}id_/{}"
MIN_GEORGIAN = 0.3
MAX_DEPTH = 2
PER_HOST = 60
MAX_BYTES = 2_000_000


class TooBig(Exception):
    pass


class BudgetReached(Exception):
    pass


class Trickle:
    def __init__(self, client, pause: float, max_bytes: int):
        self.client, self.pause, self.max_bytes = client, pause, max_bytes
        self.next = 0.0
        self.bytes = 0

    async def get(self, url: str, params=None):
        for _ in range(6):
            await asyncio.sleep(max(0, self.next - time.monotonic()))
            if self.bytes >= self.max_bytes:
                raise BudgetReached
            await wait_for_host(url, pause=self.pause, background=True)
            self.next = time.monotonic() + self.pause
            async with self.client.stream("GET", url, params=params) as response:
                if response.is_redirect:
                    location = response.headers.get("location", "")
                    target = urljoin(str(response.url), location)
                    if urlparse(target).hostname != "web.archive.org":
                        return httpx.Response(400, request=response.request)
                    url, params = target, None
                    continue
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    self.bytes += len(chunk)
                    if self.bytes > self.max_bytes:
                        raise BudgetReached
                    if len(data) + len(chunk) > MAX_BYTES:
                        raise TooBig
                    data.extend(chunk)
                return httpx.Response(response.status_code, headers=response.headers,
                                      content=bytes(data), request=response.request)
        return httpx.Response(310, request=httpx.Request("GET", url))


def seed(db) -> None:
    path = DATA / "archive_dead_seeds.txt"
    if not path.exists():
        return
    urls = [u for u in path.read_text().splitlines() if u.startswith("http")]
    homes = {f"{urlparse(u).scheme}://{urlparse(u).hostname}/" for u in urls}
    db.executemany("INSERT OR IGNORE INTO queue(url, host, depth) VALUES (?, ?, 0)",
                   [(u, urlparse(u).hostname) for u in sorted(homes) + urls])
    db.commit()


def capture(response: httpx.Response) -> tuple[str, str] | None:
    rows = response.json()
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    fields = rows[0]
    candidates = [dict(zip(fields, row)) for row in rows[1:]]
    valid = [row for row in candidates if row.get("timestamp", "").isdigit() and len(row["timestamp"]) == 14]
    if not valid:
        return None
    newest = max(valid, key=lambda row: row["timestamp"])
    return newest["timestamp"], newest.get("digest", "")


def retry(db, row, response=None) -> None:
    url, _, _, attempts, _, _ = row
    delay = min(86400, 60 * 2 ** min(attempts, 10))
    if response is not None:
        try:
            delay = max(delay, min(86400, float(response.headers.get("retry-after", "0"))))
        except ValueError:
            pass
    db.execute("UPDATE queue SET status='todo', attempts=attempts+1, next_attempt=? WHERE url=?",
               (time.time() + delay, url))
    print(f"retry in {delay:.0f}s {url[:90]}", flush=True)


async def main(args) -> None:
    db = connect()
    seed(db)
    # A single collector is expected; restarting resumes interrupted pages.
    db.execute("UPDATE queue SET status='todo' WHERE status='doing'")
    db.commit()
    processed = 0
    async with httpx.AsyncClient(timeout=60, follow_redirects=False, headers={
            "User-Agent": "dzirkva-archive/0.2 (Georgian research search)"}) as client:
        fetch = Trickle(client, args.pause, int(args.max_mb * 1_000_000))
        while processed < args.limit:
            row = db.execute("SELECT url, host, depth, attempts, snapshot, digest FROM queue "
                             "WHERE status='todo' AND next_attempt<=? ORDER BY depth, attempts, rowid LIMIT 1",
                             (time.time(),)).fetchone()
            if row is None:
                break
            url, host, depth, _, snapshot, digest = row
            db.execute("UPDATE queue SET status='doing' WHERE url=?", (url,))
            db.commit()
            processed += 1
            try:
                if not snapshot:
                    r = await fetch.get(CDX, params={"url": url, "output": "json", "filter": ["statuscode:200", "mimetype:text/html"],
                                                    "collapse": "digest", "fl": "timestamp,digest", "limit": "-5"})
                    if r.status_code in (429, 503) or r.status_code >= 500:
                        retry(db, row, r)
                        # End this batch instead of continuing against an overloaded Archive.
                        db.commit()
                        break
                    r.raise_for_status()
                    found = capture(r)
                    if found is None:
                        db.execute("UPDATE queue SET status='no-capture' WHERE url=?", (url,))
                        db.commit()
                        continue
                    snapshot, digest = found
                    db.execute("UPDATE queue SET snapshot=?, digest=? WHERE url=?", (snapshot, digest, url))
                    db.commit()
                if digest and db.execute("SELECT 1 FROM captures WHERE host=? AND digest=?", (host, digest)).fetchone():
                    db.execute("UPDATE queue SET status='duplicate' WHERE url=?", (url,))
                    db.commit()
                    continue
                r = await fetch.get(RAW.format(snapshot, url))
                if r.status_code in (429, 503) or r.status_code >= 500:
                    retry(db, row, r)
                    db.commit()
                    break
                status = f"http:{r.status_code}"
                if r.status_code == 200 and "html" in r.headers.get("content-type", ""):
                    title, text, links, converted = parse(r.content, r.headers.get("content-type", ""), url)
                    ratio = georgian_ratio(text)
                    status = "done" if ratio >= MIN_GEORGIAN and len(text) > 200 else "not-georgian"
                    if status == "done":
                        db.execute("INSERT OR REPLACE INTO pages VALUES (?, ?, ?, ?, ?, ?)", (url, snapshot, title, text, ratio, converted))
                        db.execute("DELETE FROM pages_fts WHERE url=?", (url,))
                        db.execute("INSERT INTO pages_fts VALUES (?, ?, ?, ?)", (url, snapshot, title, text))
                        db.execute("INSERT OR REPLACE INTO captures VALUES (?, ?, ?)", (url, host, digest))
                    have = db.execute("SELECT count(*) FROM queue WHERE host=?", (host,)).fetchone()[0]
                    if depth < MAX_DEPTH and have < PER_HOST:
                        db.executemany("INSERT OR IGNORE INTO queue(url, host, depth) VALUES (?, ?, ?)",
                                       [(link, host, depth + 1) for link in links[: PER_HOST - have]])
                    print(f"{status:<13} {ratio:4.0%} {snapshot} {url[:90]}", flush=True)
                db.execute("UPDATE queue SET status=? WHERE url=?", (status, url))
            except BudgetReached:
                db.execute("UPDATE queue SET status='todo' WHERE url=?", (url,))
                db.commit()
                break
            except TooBig:
                db.execute("UPDATE queue SET status='too-big' WHERE url=?", (url,))
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                retry(db, row)
                db.commit()
                break
            db.commit()
    print(f"batch finished: {processed} URLs, {fetch.bytes / 1_000_000:.2f} MB", flush=True)
    db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--max-mb", type=float, default=20)
    parser.add_argument("--pause", type=float, default=7)
    args = parser.parse_args()
    if args.limit < 1 or args.max_mb <= 0 or args.pause < 5:
        parser.error("positive budgets and --pause >= 5 are required")
    # Don't reset another running collector's claimed URL on overlapping runs.
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / "archive_collect.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(message="archive collector already running\n")
        asyncio.run(main(args))
