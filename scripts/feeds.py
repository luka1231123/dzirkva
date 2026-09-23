"""Newest posts of the small web from RSS and Atom feeds → data/feeds.db (discover.newest_posts). ~2 min.

Sites: discover.people() (personal sites and one-person blogs of the crawl). The feed link is on the home page
(<link rel="alternate" type="application/rss+xml">, WordPress and Blogger have one); feed dates are the real
publication dates, unlike the dates guessed from crawled pages. Run daily; old posts stay.
Run: uv run python scripts/feeds.py
"""

import asyncio
import re
import sqlite3
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

import httpx

from dzirkva.discover import FEEDS_DB, people

AGENT = "dzirkva-crawler/0.1 (Georgian search research; 1 req/s)"
FEED_LINK = re.compile(r"<link[^>]+(?:application/(?:rss|atom)\+xml)[^>]*>", re.I)
HREF = re.compile(r'href=["\']([^"\']+)', re.I)
ATOM = "{http://www.w3.org/2005/Atom}"
TASKS = 20
PAUSE = 1.0


def _date(text: str) -> str:
    """RSS (Tue, 15 Sep 2026 10:00:00 +0000) or Atom (2026-09-15T10:00:00Z) date → 2026-09-15."""
    text = (text or "").strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", text):
        return text[:10]
    try:
        return parsedate_to_datetime(text).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


def parse(xml: bytes) -> list[tuple[str, str, str]]:
    """(url, title, date) of the posts in an RSS or Atom feed."""
    root = ET.fromstring(xml)
    out = []
    for item in root.iter("item"):  # RSS
        out.append((item.findtext("link", ""), item.findtext("title", ""), _date(item.findtext("pubDate", ""))))
    for entry in root.iter(f"{ATOM}entry"):
        link = next((e.get("href", "") for e in entry.iter(f"{ATOM}link") if e.get("rel", "alternate") == "alternate"), "")
        out.append((link, entry.findtext(f"{ATOM}title", ""),
                    _date(entry.findtext(f"{ATOM}published", "") or entry.findtext(f"{ATOM}updated", ""))))
    return [(u.strip(), " ".join(t.split()), d) for u, t, d in out if u.strip().startswith("http") and d]


async def one(client: httpx.AsyncClient, db: sqlite3.Connection, host: str, limit: asyncio.Semaphore) -> int:
    async with limit:
        try:
            home = await client.get(f"https://{host}/")
            links = [HREF.search(tag) for tag in FEED_LINK.findall(home.text)]
            feed = next((urljoin(str(home.url), m[1]) for m in links if m and "comments" not in m[1]), None)
            if feed is None:
                return 0
            await asyncio.sleep(PAUSE)
            posts = parse((await client.get(feed)).content)
        except (httpx.HTTPError, ET.ParseError, ValueError):
            return 0
    db.executemany("INSERT OR REPLACE INTO posts VALUES (?, ?, ?, ?)", [(u, host, t, d) for u, t, d in posts])
    db.commit()
    return len(posts)


async def main() -> None:
    db = sqlite3.connect(FEEDS_DB)
    db.execute("CREATE TABLE IF NOT EXISTS posts (url TEXT PRIMARY KEY, host TEXT, title TEXT, date TEXT)")
    hosts = people()
    limit = asyncio.Semaphore(TASKS)
    async with httpx.AsyncClient(follow_redirects=True, timeout=30, headers={"User-Agent": AGENT}) as client:
        found = await asyncio.gather(*(one(client, db, h, limit) for h in hosts))
    print(f"{sum(found):,} posts from {sum(n > 0 for n in found)} of {len(hosts)} sites", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
