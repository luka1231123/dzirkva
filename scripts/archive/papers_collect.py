"""Sequential, resumable OAI-PMH harvest; completed repositories refresh incrementally.

Save the start date of a successful run as its next watermark, with a one-day overlap. Default batch: one repository, up to 20 XML pages (8 MiB each).
Expired resumption tokens restart the same window without discarding existing records.
Use --limit to cap repositories and --pause for the global request trickle.
"""

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from dzirkva import papers, passages
from dzirkva.ingest import wait_for_host

RETRIES = 4


def retire(db, urls):
    # Durable outbox: a crash between the two databases must not retain deleted PDF passages.
    db.executemany("INSERT OR IGNORE INTO retired_text VALUES (?)", [(url,) for url in urls])


def drain_retired(db):
    urls = db.execute("SELECT url FROM retired_text").fetchall()
    if not urls or not passages.DB.exists():
        return
    pdb = passages.connect()
    try:
        pdb.executemany("DELETE FROM passages WHERE site = 'papers' AND url = ?", urls)
        pdb.commit()
        db.execute("DELETE FROM retired_text")
        db.commit()
    finally:
        pdb.close()


async def harvest(client, db, base, pause, max_pages=20):
    token, last, start, since = db.execute(
        "SELECT token, last_sync, sync_started, sync_from FROM repos WHERE base = ?", (base,)).fetchone()
    if not start:
        start = datetime.now(timezone.utc).date().isoformat()
        since = (datetime.fromisoformat(last) - timedelta(days=1)).date().isoformat() if last else None
        db.execute("UPDATE repos SET sync_started = ?, sync_from = ? WHERE base = ?", (start, since, base))
        db.commit()
    first = {"verb": "ListRecords", "metadataPrefix": "oai_dc"}
    if since:
        first["from"] = since  # date granularity is accepted by both OAI granularities
    params = {"verb": "ListRecords", "resumptionToken": token} if token else first
    reset = False
    pages_read = 0
    while params and pages_read < max_pages:
        for attempt in range(RETRIES):
            try:
                async with client.stream("GET", base, params=params) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for part in response.aiter_bytes():
                        if len(body) + len(part) > 8 * 1024 * 1024:
                            raise ValueError("OAI response exceeds 8 MiB")
                        body.extend(part)
                rows, token, total, error = papers.parse(bytes(body), base)
                observed, deleted = papers.page_changes(bytes(body))
                stamps = papers.page_stamps(bytes(body))
                break
            except (httpx.HTTPError, SyntaxError, ValueError) as exc:
                print(f"! {base}: {type(exc).__name__}, retry {attempt + 1}", flush=True)
                if attempt + 1 < RETRIES:
                    await asyncio.sleep(30 * (attempt + 1))
        else:
            db.execute("UPDATE repos SET state='error', next_sync=? WHERE base=?",
                       ((datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), base))
            db.commit()
            return
        if error == "badResumptionToken" and "resumptionToken" in params and not reset:
            db.execute("UPDATE repos SET token = NULL WHERE base = ?", (base,))
            db.commit()
            params, reset = first, True
            continue
        if error and error != "noRecordsMatch":
            print(f"! {base}: {error}", flush=True)
            db.execute("UPDATE repos SET state='error', next_sync=? WHERE base=?",
                       ((datetime.now(timezone.utc) + timedelta(days=1)).isoformat(), base))
            db.commit()
            return
        pages_read += 1
        kept = {row[0] for row in rows}
        # A formerly Georgian item may be replaced by a non-Georgian record.
        retire(db, papers.discard(db, [ident for ident in observed if ident not in kept]))
        for row in rows:
            previous = db.execute("SELECT rowid," + ",".join(f"c{i}" for i in range(12)) +
                                  " FROM papers_content WHERE c0 = ?", (row[0],)).fetchone()
            if previous:
                rid = previous[0]
                old_stamp = db.execute("SELECT stamp FROM record_stamps WHERE oai=?", (row[0],)).fetchone()
                if tuple(previous[1:]) != row or (old_stamp and old_stamp[0] != stamps.get(row[0], "")):
                    retire(db, papers.discard(db, [row[0]]))
                else:
                    db.execute("DELETE FROM papers WHERE rowid = ?", (rid,))
                db.execute(f"INSERT INTO papers VALUES ({','.join('?' * 12)})", row)
            elif db.execute("INSERT OR IGNORE INTO seen VALUES (?)", (row[0] or row[1],)).rowcount:
                db.execute(f"INSERT INTO papers VALUES ({','.join('?' * 12)})", row)
            db.execute("INSERT OR IGNORE INTO seen VALUES (?)", (row[0] or row[1],))
            db.execute("INSERT OR REPLACE INTO record_stamps VALUES (?,?)", (row[0], stamps.get(row[0], "")))
        done = db.execute("SELECT count(*) FROM papers WHERE repo = ?", (base,)).fetchone()[0]
        db.execute("UPDATE repos SET token = ?, records = ?, state = ? WHERE base = ?",
                   (token, done, "harvest" if token else "done", base))
        if not token:
            db.execute("UPDATE repos SET last_sync=?, sync_started=NULL, sync_from=NULL, next_sync=? WHERE base=?",
                       (start, (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(), base))
        db.commit()
        drain_retired(db)
        print(f"{done:>7} / {total or '?'} {base}; {len(deleted)} deleted", flush=True)
        params = {"verb": "ListRecords", "resumptionToken": token} if token else None


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", "--repo-limit", type=int, default=1, help="repository limit; 0 means all")
    parser.add_argument("--pause", type=float, default=5.0, help="seconds between requests across all repositories")
    parser.add_argument("--max-pages", type=int, default=20, help="ListRecords pages per repository per batch")
    parser.add_argument("--base", help="harvest one known endpoint")
    args = parser.parse_args()
    if args.limit < 0 or args.pause < 0 or args.max_pages <= 0:
        parser.error("limit and pause must be nonnegative")
    db = papers.connect()
    db.execute("CREATE TABLE IF NOT EXISTS retired_text(url TEXT PRIMARY KEY)")
    db.commit()
    drain_retired(db)
    todo = db.execute("SELECT base FROM repos WHERE next_sync IS NULL OR next_sync <= ? "
                      "ORDER BY state='done', coalesce(last_sync,''), base",
                      (datetime.now(timezone.utc).isoformat(),)).fetchall()
    if args.base:
        todo = db.execute("SELECT base FROM repos WHERE base=?", (args.base,)).fetchall()
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(todo)} repositories (one request at a time)", flush=True)
    async def throttle(request):
        await asyncio.sleep(args.pause)
        await wait_for_host(str(request.url), pause=args.pause, background=True)

    async with httpx.AsyncClient(event_hooks={"request": [throttle]}, follow_redirects=True, timeout=httpx.Timeout(120, connect=10), verify=False,
                                 headers={"User-Agent": papers.AGENT}) as client:
        for (base,) in todo:
            await harvest(client, db, base, args.pause, args.max_pages)
    db.close()


if __name__ == "__main__":
    asyncio.run(main())
