"""Clients for the outside search engines: local SearXNG and the Brave Search API.

Every answer is cached in data/cache.db (repeat searches and eval runs cost no engine calls).
Google back-off: when SearXNG reports a Google CAPTCHA, Google is left out for 1 h, then 2, 4, 8, 24 h
if it blocks again. SearXNG's own pause is a fixed 1 h, and hitting Google again right away extends the block.
"""

import asyncio
import html
import json
import os
import re
import sqlite3
import time
from functools import cache
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv()

SEARXNG_URL = "http://127.0.0.1:8888/search"
SEARXNG_ENGINES = ("google", "yandex", "yahoo")  # same as config/searxng.yml
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
CACHE_DB = Path(__file__).resolve().parents[2] / "data" / "cache.db"
CACHE_HOURS = {"searxng": 24, "brave": 24 * 7}  # Brave: paid per call, keep longer
# Brave API calls cost money: caps per day and per month (.env; 0 turns Brave off). A spent cap skips Brave,
# the other engines and our own indexes still answer.
BRAVE_DAILY_LIMIT = int(os.environ.get("BRAVE_DAILY_LIMIT", "20"))
BRAVE_MONTHLY_LIMIT = int(os.environ.get("BRAVE_MONTHLY_LIMIT", "300"))
BLOCK_HOURS = (1, 2, 4, 8, 24)                   # Google pause after the 1st, 2nd, ... block in a row
BLOCKING = re.compile(r"CAPTCHA|too many|denied", re.I)


def clean(text: str) -> str:
    """Engines send snippets with HTML (<strong>, &quot;): plain text only."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", text or "")).split())


# ---- cache and blocks ---------------------------------------------------

@cache
def _db() -> sqlite3.Connection:
    db = sqlite3.connect(CACHE_DB, check_same_thread=False)
    db.executescript("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, time REAL, results TEXT);"
                     "CREATE TABLE IF NOT EXISTS blocks (engine TEXT PRIMARY KEY, until REAL, strikes INT);"
                     "CREATE TABLE IF NOT EXISTS brave_usage (day TEXT PRIMARY KEY, calls INT);")
    return db


def _cached(engine: str, key: str) -> list[dict] | None:
    row = _db().execute("SELECT time, results FROM cache WHERE key=?", (key,)).fetchone()
    if row and time.time() - row[0] < CACHE_HOURS[engine] * 3600:
        return json.loads(row[1])
    return None


def _store(key: str, results: list[dict]) -> None:
    if results:  # empty = engine trouble: ask again next time
        _db().execute("INSERT OR REPLACE INTO cache VALUES (?, ?, ?)", (key, time.time(), json.dumps(results)))
        _db().commit()


def blocked(engine: str) -> bool:
    row = _db().execute("SELECT until FROM blocks WHERE engine=?", (engine,)).fetchone()
    return bool(row) and row[0] > time.time()


def _block(engine: str) -> None:
    """Pause the engine; each block within a day of the last pause doubles the pause."""
    row = _db().execute("SELECT until, strikes FROM blocks WHERE engine=?", (engine,)).fetchone()
    strikes = row[1] + 1 if row and time.time() - row[0] < 24 * 3600 else 1
    hours = BLOCK_HOURS[min(strikes, len(BLOCK_HOURS)) - 1]
    _db().execute("INSERT OR REPLACE INTO blocks VALUES (?, ?, ?)", (engine, time.time() + hours * 3600, strikes))
    _db().commit()
    print(f"  ! {engine} blocked us (block {strikes} in a row): left out for {hours} h", flush=True)


# ---- engines ------------------------------------------------------------

async def searxng(client: httpx.AsyncClient, query: str, page: int = 1) -> list[dict]:
    """Google + Yandex + Yahoo through the local SearXNG (without Google while it is paused). page: 2, 3 … for deep search."""
    use = [e for e in SEARXNG_ENGINES if not blocked(e)]
    key = f"searxng|{','.join(use)}|{query}" + (f"|{page}" if page > 1 else "")
    if (hit := _cached("searxng", key)) is not None:
        return hit
    r = await client.get(SEARXNG_URL, params={"q": query, "format": "json", "engines": ",".join(use), "pageno": page},
                         timeout=15)
    r.raise_for_status()
    data = r.json()
    failed = {e for e, error in data.get("unresponsive_engines", []) if BLOCKING.search(error)}
    for e in failed & set(use):
        if not blocked(e):  # parallel requests report the same block
            _block(e)
    results = [
        {"url": x["url"], "title": clean(x.get("title")), "snippet": clean(x.get("content")),
         "engine": "+".join(x.get("engines", []))}
        for x in data["results"]
    ]
    if not failed:
        _store(key, results)
    return results


def brave_used() -> tuple[int, int]:
    """Brave API calls today and this month."""
    day, month = time.strftime("%Y-%m-%d"), time.strftime("%Y-%m")
    rows = _db().execute("SELECT day, calls FROM brave_usage WHERE day LIKE ?", (month + "%",)).fetchall()
    return sum(n for d, n in rows if d == day), sum(n for _, n in rows)


async def brave(client: httpx.AsyncClient, query: str) -> list[dict]:
    """Official Brave Search API. Paid per call: cached a week, and no call once a daily or monthly cap is spent."""
    key = f"brave|{query}"
    if (hit := _cached("brave", key)) is not None:
        return hit
    today, month = brave_used()
    if today >= BRAVE_DAILY_LIMIT or month >= BRAVE_MONTHLY_LIMIT:
        return []
    _db().execute("INSERT INTO brave_usage VALUES (?, 1) ON CONFLICT(day) DO UPDATE SET calls = calls + 1",
                  (time.strftime("%Y-%m-%d"),))
    _db().commit()
    r = await client.get(
        BRAVE_URL,
        params={"q": query, "count": 20},
        headers={"X-Subscription-Token": os.environ["BRAVE_API_KEY"]},
        timeout=15,
    )
    r.raise_for_status()
    results = [
        {"url": x["url"], "title": clean(x.get("title")), "snippet": clean(x.get("description")),
         "engine": "brave-api"}
        for x in r.json().get("web", {}).get("results", [])
    ]
    _store(key, results)
    return results


async def _demo(query: str) -> None:
    async with httpx.AsyncClient() as client:
        for name, fn in (("searxng", searxng), ("brave", brave)):
            results = await fn(client, query)
            print(f"{name}: {len(results)} results")
            for x in results[:5]:
                print(f"  [{x['engine']}] {x['title'][:60]}  {x['url'][:80]}")


if __name__ == "__main__":
    import sys
    asyncio.run(_demo(" ".join(sys.argv[1:]) or "ხაჭაპური"))
