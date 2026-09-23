"""Crawl the trusted Georgian sites (config/sources.yaml) into data/crawl.db. Resumable.

Seeds: each site's home page and its sitemaps (robots.txt "Sitemap:" lines, else /sitemap.xml),
newest pages first. Follows same-site links up to MAX_DEPTH. Max PER_HOST new pages per site per run.
Polite: obeys robots.txt, one request per second per site (or the site's Crawl-delay); the sites
are fetched in parallel. Run it again later: new sitemap entries (new articles) join the queue.

Run in the background:  nohup uv run python scripts/crawl_sites.py > data/crawl.log 2>&1 &
More pages per site:    uv run python scripts/crawl_sites.py 10000
Progress:               sqlite3 data/crawl.db "select status, count(*) from queue group by 1"
"""

import asyncio
import gzip
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import lxml.html
import trafilatura

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dzirkva.archive import SKIP_EXT  # noqa: E402
from dzirkva.crawl import connect  # noqa: E402
from dzirkva.georgian import georgian_ratio  # noqa: E402
from dzirkva.sources import sources  # noqa: E402

AGENT = "dzirkva-crawler/0.1 (Georgian search research; 1 req/s)"
PER_HOST = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
MAX_DEPTH = 3
MAX_SITEMAPS = 30       # sitemap files read per site, newest first
PAUSE = 1.0
MIN_GEORGIAN = 0.3
MIN_TEXT = 200
DUMPS = re.compile(r"(^|\.)(wikipedia|wikisource|wiktionary)\.org$")  # have dumps, crawling them is rude
OTHER_LANGUAGE = re.compile(r"(?i)/(en|eng|english|ru|rus|russian)(/|$)|[?&]lang=(en|eng|ru|rus)\b")
ENTRY = re.compile(r"<(sitemap|url)>(.*?)</\1>", re.S)
LOC = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</loc>", re.S)
LASTMOD = re.compile(r"<lastmod>\s*(.*?)\s*</lastmod>")


class Site:
    def __init__(self, domain: str) -> None:
        self.domain = domain
        self.base = f"https://{domain}"
        self.robots = RobotFileParser()
        self.delay = PAUSE
        self.next = 0.0     # time.monotonic() of the next allowed request
        self.queued = 0     # pages queued in this run (+ left over from the last run)

    def allowed(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").removeprefix("www.")
        return ((host == self.domain or host.endswith("." + self.domain)) and url.startswith("http")
                and not SKIP_EXT.search(urlparse(url).path) and not OTHER_LANGUAGE.search(url)
                and self.robots.can_fetch(AGENT, url))


async def get(client: httpx.AsyncClient, url: str) -> httpx.Response | None:
    try:
        return await client.get(url)
    except httpx.HTTPError:
        return None


def enqueue(db, site: Site, urls: list[str], depth: int) -> None:
    urls = [u for u in dict.fromkeys(u.split("#")[0] for u in urls) if site.allowed(u)]
    cur = db.executemany("INSERT OR IGNORE INTO queue(url, host, depth) VALUES (?, ?, ?)",
                         [(u, site.domain, depth) for u in urls[: max(0, PER_HOST - site.queued)]])
    site.queued += cur.rowcount


async def sitemap_urls(client: httpx.AsyncClient, site: Site, maps: list[str]) -> list[str]:
    """Page URLs from the site's sitemaps (and sitemap indexes), newest first by <lastmod>."""
    todo, seen, pages = list(maps), set(), {}
    while todo and len(seen) < MAX_SITEMAPS and len(pages) < PER_HOST * 2:
        url = todo.pop(0)
        if url in seen:
            continue
        seen.add(url)
        r = await get(client, url)
        await asyncio.sleep(site.delay)
        if r is None or r.status_code != 200:
            continue
        raw = r.content
        try:
            raw = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
        except (OSError, EOFError):
            continue
        children = []
        for kind, body in ENTRY.findall(raw.decode("utf-8", "replace")):
            loc, mod = LOC.search(body), LASTMOD.search(body)
            if not loc:
                continue
            if kind == "sitemap":
                children.append((mod.group(1) if mod else "", loc.group(1)))
            else:
                pages[loc.group(1)] = mod.group(1) if mod else ""
        todo = [u for _, u in sorted(children, reverse=True)] + todo
    return sorted(pages, key=pages.get, reverse=True)


async def seed(client: httpx.AsyncClient, db, site: Site) -> None:
    r = await get(client, f"{site.base}/robots.txt")
    if r is None:  # no https: try http
        site.base = f"http://{site.domain}"
        r = await get(client, f"{site.base}/robots.txt")
    ok = r is not None and r.status_code == 200 and "html" not in r.headers.get("content-type", "")
    site.robots.parse(r.text.splitlines() if ok else [])
    site.delay = max(PAUSE, float(site.robots.crawl_delay(AGENT) or 0))
    maps = site.robots.site_maps() or [f"{site.base}/sitemap.xml"]
    enqueue(db, site, [f"{site.base}/"], 0)
    enqueue(db, site, await sitemap_urls(client, site, maps), 1)
    print(f"seeded {site.domain}: {site.queued} pages waiting", flush=True)


def links(html: str, base: str) -> list[str]:
    try:
        root = lxml.html.fromstring(html)
    except (ValueError, lxml.etree.ParserError):
        return []
    return [urljoin(base, h.strip()) for h in root.xpath("//a/@href")]


async def visit(client: httpx.AsyncClient, db, site: Site, url: str, depth: int) -> None:
    r = await get(client, url)
    status = "error" if r is None else f"http:{r.status_code}"
    if r is not None and r.status_code == 200 and "html" in r.headers.get("content-type", ""):
        try:
            doc = trafilatura.bare_extraction(r.content, url=str(r.url), with_metadata=True)
        except Exception:  # trafilatura raises many kinds on broken pages
            doc = None
        text = (doc.text or "") if doc else ""
        ratio = georgian_ratio(text)
        # home pages (depth 0) are link lists: follow their links, do not store them
        status = "done" if ratio >= MIN_GEORGIAN and len(text) >= MIN_TEXT and depth > 0 else "skipped"
        if status == "done":
            db.execute("INSERT INTO pages VALUES (?, ?, ?, ?)", (url, doc.title or "", doc.date or "", text))
        if depth < MAX_DEPTH:
            enqueue(db, site, links(r.text, str(r.url)), depth + 1)
        print(f"{status:<8} {ratio:4.0%} {url[:100]}", flush=True)
    db.execute("UPDATE queue SET status=? WHERE url=?", (status, url))


async def main() -> None:
    db = connect()
    sites = {d: Site(d) for d in sources() if not DUMPS.search(d)}
    for host, n in db.execute("SELECT host, count(*) FROM queue WHERE status='todo' GROUP BY host"):
        if host in sites:
            sites[host].queued = n
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=100)
    async with httpx.AsyncClient(follow_redirects=True, timeout=30, headers={"User-Agent": AGENT}, limits=limits) as client:
        await asyncio.gather(*(seed(client, db, s) for s in sites.values()))
        db.commit()
        while True:
            rows = db.execute("SELECT url, host, depth, min(rowid) FROM queue WHERE status='todo' GROUP BY host").fetchall()
            if not rows:
                print("queue empty", flush=True)
                return
            now = time.monotonic()
            for _, host, _, _ in rows:
                if host not in sites:  # site removed from sources.yaml
                    db.execute("UPDATE queue SET status='dropped' WHERE host=? AND status='todo'", (host,))
            ready = [(url, sites[h], d) for url, h, d, _ in rows if h in sites and sites[h].next <= now]
            for _, site, _ in ready:
                site.next = now + site.delay
            await asyncio.gather(*(visit(client, db, site, url, d) for url, site, d in ready))
            db.commit()
            await asyncio.sleep(PAUSE)


if __name__ == "__main__":
    asyncio.run(main())
