"""Harvest the repositories found by scripts/find_repos.py into data/papers.db (Dublin Core records). Resumable.

Each repository is its own task: one request at a time, PAUSE seconds apart; the resumption token is saved
after each page of records. An expired token starts that repository again. A repository that fails RETRIES times
in a row is marked 'error' and asked again on the next run. Journals on one OJS install under several names
give the same records: a record is stored once (table seen).
Run in background: nohup uv run python scripts/papers_collect.py > data/papers.log 2>&1 &
"""

import asyncio

import httpx

from dzirkva import papers

PAUSE = 1.0
RETRIES = 4


async def harvest(client: httpx.AsyncClient, db, base: str, token: str | None) -> None:
    first = {"verb": "ListRecords", "metadataPrefix": "oai_dc"}
    params = {"verb": "ListRecords", "resumptionToken": token} if token else first
    while params:
        for attempt in range(RETRIES):
            try:
                r = await client.get(base, params=params)
                r.raise_for_status()
                rows, token, total, error = papers.parse(r.content, base)
                break
            except (httpx.HTTPError, SyntaxError) as e:  # ET.ParseError is a SyntaxError
                print(f"  ! {base} {type(e).__name__}, retry in {30 * (attempt + 1)} s", flush=True)
                await asyncio.sleep(30 * (attempt + 1))
        else:
            db.execute("UPDATE repos SET state = 'error' WHERE base = ?", (base,))
            db.commit()
            return
        if error == "badResumptionToken" and params is not first:  # expired: start again
            db.execute("DELETE FROM seen WHERE oai IN (SELECT oai FROM papers WHERE repo = ?)", (base,))
            db.execute("DELETE FROM papers WHERE repo = ?", (base,))
            db.execute("UPDATE repos SET records = 0, token = NULL WHERE base = ?", (base,))
            params = first
            continue
        if error and error != "noRecordsMatch":
            print(f"  ! {base} {error}", flush=True)
            db.execute("UPDATE repos SET state = 'error' WHERE base = ?", (base,))
            db.commit()
            return
        new = [row for row in rows
               if db.execute("INSERT OR IGNORE INTO seen VALUES (?)", (row[0] or row[1],)).rowcount]
        db.executemany(f"INSERT INTO papers VALUES ({','.join('?' * 12)})", new)
        db.execute("UPDATE repos SET token = ?, records = records + ?, state = ? WHERE base = ?",
                   (token, len(new), "harvest" if token else "done", base))
        db.commit()
        if new or not token:
            done = db.execute("SELECT records FROM repos WHERE base = ?", (base,)).fetchone()[0]
            print(f"{done:>7} / {total or '?'}  {base}", flush=True)
        params = {"verb": "ListRecords", "resumptionToken": token} if token else None
        await asyncio.sleep(PAUSE)


async def main() -> None:
    db = papers.connect()
    todo = db.execute("SELECT base, token FROM repos WHERE state != 'done'").fetchall()
    print(f"{len(todo)} repositories", flush=True)
    # verify=False: university sites often have expired certificates; only public metadata is read
    async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(120, connect=10), verify=False,
                                 headers={"User-Agent": papers.AGENT}) as client:
        await asyncio.gather(*(harvest(client, db, base, token) for base, token in todo))
    db.execute("INSERT INTO papers(papers) VALUES ('optimize')")
    db.commit()
    print(f"done: {db.execute('SELECT count(*) FROM papers').fetchone()[0]:,} papers", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
