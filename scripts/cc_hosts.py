"""Georgian hosts from Common Crawl → data/cc_hosts.db.

Reads only two columns (host, languages) of the Common Crawl columnar index over HTTPS: a few GB per crawl.
Default crawls: one recent, one old (language detection starts with CC-MAIN-2018-39).
Resumable: done files are skipped. Run in background:
    nohup uv run python scripts/cc_hosts.py > data/cc_hosts.log 2>&1 &
Other crawls: uv run python scripts/cc_hosts.py CC-MAIN-2022-05
"""

import gzip
import sqlite3
import sys
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import duckdb

DATA = Path(__file__).resolve().parents[1] / "data"
BASE = "https://data.commoncrawl.org/"
CRAWLS = sys.argv[1:] or ["CC-MAIN-2026-39", "CC-MAIN-2018-43"]
WORKERS = 6
LOCK = threading.Lock()   # one SQLite writer at a time

QUERY = """
SELECT regexp_replace(url_host_name, '^www\\.', '') AS host,
       count(*) AS pages,
       count(*) FILTER (WHERE content_languages LIKE 'kat%') AS main,   -- Georgian is the first language
       any_value(url) AS url
FROM read_parquet(?)
WHERE content_languages LIKE '%kat%' AND fetch_status = 200
GROUP BY 1
"""

db = sqlite3.connect(DATA / "cc_hosts.db", check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS hosts (host TEXT, crawl TEXT, pages INT, main INT, url TEXT, PRIMARY KEY (host, crawl));
CREATE TABLE IF NOT EXISTS done (file TEXT PRIMARY KEY);
""")


def files(crawl: str) -> list[str]:
    with urllib.request.urlopen(f"{BASE}crawl-data/{crawl}/cc-index-table.paths.gz") as r:
        return [BASE + p for p in gzip.decompress(r.read()).decode().split() if "subset=warc" in p]


def one(crawl: str, url: str) -> int:
    con = duckdb.connect()
    for attempt in range(3):
        try:
            rows = con.execute(QUERY, [url]).fetchall()
            break
        except duckdb.Error as e:
            print("retry", url.rsplit("/", 1)[1], e, flush=True)
    else:
        return 0
    with LOCK:
        db.executemany("""INSERT INTO hosts VALUES (?, ?, ?, ?, ?) ON CONFLICT DO UPDATE SET
                      pages = pages + excluded.pages, main = main + excluded.main""",
                       [(h, crawl, p, m, u) for h, p, m, u in rows])
        db.execute("INSERT INTO done VALUES (?)", (url,))
        db.commit()
    return len(rows)


for crawl in CRAWLS:
    done = {f for (f,) in db.execute("SELECT file FROM done")}
    todo = [f for f in files(crawl) if f not in done]
    print(f"{crawl}: {len(todo)} files to read", flush=True)
    with ThreadPoolExecutor(WORKERS) as pool:
        for i, n in enumerate(pool.map(lambda f: one(crawl, f), todo), 1):
            print(f"{crawl} {i}/{len(todo)}  +{n} hosts", flush=True)

# how many are new to our own crawl
db.execute(f"ATTACH '{DATA / 'crawl.db'}' AS c")
for crawl in CRAWLS:
    total, new = db.execute("""SELECT count(*), count(*) FILTER (WHERE host NOT IN (SELECT host FROM c.domains))
                               FROM hosts WHERE crawl = ? AND main > 0""", (crawl,)).fetchone()
    print(f"{crawl}: {total} hosts with Georgian main pages, {new} not in crawl.db")
print("done")
