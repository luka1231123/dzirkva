"""Collect the Iverieli catalog (601k records) by OAI-PMH into data/iverieli.db. ~6,000 requests, ~3 h.

Resumable: the resumption token is saved after each page of 100 records.
Run in background: nohup uv run python scripts/iverieli_collect.py > data/iverieli.log 2>&1 &
"""

import time

import httpx

from dzirkva.iverieli import AGENT, OAI, connect, parse

PAUSE = 0.5  # seconds between requests: one library server, be polite
RETRIES = 5

db = connect()
row = db.execute("SELECT v FROM state WHERE k = 'token'").fetchone()
if row and not row[0]:
    raise SystemExit("already complete")
params = {"verb": "ListRecords", "resumptionToken": row[0]} if row else {"verb": "ListRecords", "metadataPrefix": "oai_dc"}
done = db.execute("SELECT count(*) FROM items").fetchone()[0]
t0 = time.time()
with httpx.Client(headers={"User-Agent": AGENT}, timeout=120) as client:
    while params:
        for attempt in range(RETRIES):
            try:
                r = client.get(OAI, params=params)
                r.raise_for_status()
                rows, token, total = parse(r.content)
                break
            except (httpx.HTTPError, SyntaxError) as e:  # ET.ParseError is a SyntaxError
                print(f"  ! {type(e).__name__}, retry in {30 * (attempt + 1)} s", flush=True)
                time.sleep(30 * (attempt + 1))
        else:
            raise SystemExit("gave up: run again later to continue")
        db.executemany("INSERT INTO items VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        db.execute("INSERT OR REPLACE INTO state VALUES ('token', ?)", (token or "",))
        db.commit()
        done += len(rows)
        print(f"{done}/{total}  {(time.time() - t0) / 60:.0f} min", flush=True)
        params = {"verb": "ListRecords", "resumptionToken": token} if token else None
        time.sleep(PAUSE)
db.execute("INSERT INTO items(items) VALUES ('optimize')")
db.commit()
print("done")
