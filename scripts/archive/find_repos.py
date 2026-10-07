"""Find OAI-PMH endpoints in bounded sequential batches (20 academic hosts by default).

Legacy --wide hosts: every .ge host the crawl knows (queue and domains), .ge hosts cited in Georgian Wikipedia, and the crawled
Georgian sites and the trusted ones. Foreign journals linked from Georgian pages are not asked. Each host is asked for Identify at the usual paths (OJS, DSpace 6 and 7, EPrints)
and at the OJS paths seen in crawled URLs (/ojs/index.php/…). One endpoint per base URL of Identify
(hos.openjournals.ge answers as openjournals.ge). Iverieli (dspace.nplg.gov.ge) has its own index.
Run again after crawling: known endpoints stay, new ones are added.
Run: uv run python scripts/archive/find_repos.py
"""

import argparse
import asyncio
import bz2
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

from dzirkva import iverieli, papers
from dzirkva.ingest import wait_for_host
from dzirkva.crawl import DB as CRAWL_DB, MIN_GEORGIAN, domain_of
from dzirkva.sources import sources

WIKI_DUMP = Path(__file__).resolve().parents[2] / "data" / "kawiki.xml.bz2"
PATHS = ("/index.php/index/oai", "/oai/request", "/server/oai/request", "/cgi/oai2", "/index/oai", "/oai")
SOFTWARE = {"/index.php/": "ojs", "/index/oai": "ojs", "/oai/request": "dspace", "/cgi/oai2": "eprints"}
OJS_PREFIX = re.compile(r"^(https?://[^/?#]+)(/[^?#]*?)/index\.php/")
URL = re.compile(r"https?://[^\s\[\]|<>\"'{}]+")
NS = {"oai": "http://www.openarchives.org/OAI/2.0/"}


class BudgetExceeded(Exception):
    pass


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").removeprefix("www.")
    except ValueError:  # malformed URL
        return ""


def candidates() -> tuple[set[str], set[str]]:
    """(hosts, extra endpoints): Georgian hosts, and OJS endpoints under a path (/ojs/index.php/index/oai)."""
    crawl = sqlite3.connect(f"file:{CRAWL_DB}?mode=ro", uri=True)
    hosts, extra = set(sources()), set()
    georgian = {h for (h,) in crawl.execute("SELECT host FROM domains WHERE state = 'full' AND georgian >= ?",
                                              (MIN_GEORGIAN,))}
    for (url,) in crawl.execute("SELECT url FROM queue"):
        if (h := _host(url)).endswith(".ge"):
            hosts.add(h)
        if (m := OJS_PREFIX.match(url)) and (h.endswith(".ge") or domain_of(url) in georgian):
            extra.add(f"{m[1]}{m[2]}/index.php/index/oai")
    hosts |= georgian | {h for (h,) in crawl.execute("SELECT host FROM domains WHERE host LIKE '%.ge'")}
    with bz2.open(WIKI_DUMP, "rt", encoding="utf-8") as f:
        for line in f:
            if ".ge" in line:
                hosts |= {h for u in URL.findall(line) if (h := _host(u)).endswith(".ge")}
    return hosts - {""}, extra


async def identify(client: httpx.AsyncClient, url: str, budget=None) -> tuple[str, str] | None | bool:
    """(repository name, base URL) of an endpoint; None: no endpoint here; False: the host does not answer."""
    try:
        if budget and budget["received"] >= budget["maximum"]:
            raise BudgetExceeded
        async with client.stream("GET", url, params={"verb": "Identify"}) as response:
            if response.status_code != 200:
                return None
            length = int(response.headers.get("content-length") or 0)
            if length > 256 * 1024:
                return None
            if budget and length > budget["maximum"] - budget["received"]:
                raise BudgetExceeded
            body = bytearray()
            async for part in response.aiter_bytes(chunk_size=65536):
                if budget is not None:
                    budget["received"] += len(part)
                    if budget["received"] > budget["maximum"]:
                        raise BudgetExceeded
                if len(body) + len(part) > 256 * 1024:
                    return None
                body.extend(part)
        content = bytes(body)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return False
    except (httpx.HTTPError, ValueError):
        return None
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return None
    if root.find("oai:Identify", NS) is None:
        return None
    return root.findtext(".//oai:repositoryName", "", NS).strip(), root.findtext(".//oai:baseURL", "", NS).strip()


async def probe(client: httpx.AsyncClient, urls: list[str], budget=None) -> tuple[str, str, str] | None:
    """The first endpoint that answers Identify: (url, name, base URL). A host without https is asked by http."""
    found = await identify(client, urls[0], budget)
    if found is False and urls[0].startswith("https:"):
        urls = [u.replace("https:", "http:", 1) for u in urls]
        found = await identify(client, urls[0], budget)
    if found is False:
        return None
    for url in urls:
        if url != urls[0]:
            found = await identify(client, url, budget)
        if found:
            return (url, *found)
    return None


def _ident(base: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", base).rstrip("/")


def academic_candidates() -> tuple[set[str], set[str]]:
    """Accepted academic hosts and trusted science sources, without rescanning the entire web/dump."""
    hosts = {host for host, (category, _) in sources().items() if category == "science"}
    extra = set()
    if not CRAWL_DB.exists():
        return hosts, extra
    with sqlite3.connect(f"file:{CRAWL_DB}?mode=ro", uri=True) as crawl:
        hosts.update(host for (host,) in crawl.execute(
            "SELECT host FROM domains WHERE state='full' AND (kind='academic' OR "
            "signals LIKE '%academic%' OR signals LIKE '%ojs%' OR signals LIKE '%dspace%' OR signals LIKE '%eprints%')"))
        for host in sorted(hosts):
            for (url,) in crawl.execute("SELECT url FROM queue WHERE status='todo' AND host=? "
                                        "AND url LIKE '%/index.php/%' LIMIT 100", (host,)):
                if match := OJS_PREFIX.match(url):
                    extra.add(f"{match[1]}{match[2]}/index.php/index/oai")
    return hosts, extra


async def main() -> None:
    parser = argparse.ArgumentParser(description="Slow, bounded discovery of new academic OAI-PMH repositories")
    parser.add_argument("--limit", type=int, default=20, help="candidate hosts/endpoints per batch")
    parser.add_argument("--pause", type=float, default=5, help="seconds before every request")
    parser.add_argument("--max-bytes", type=int, default=5*1024*1024, help="Identify batch body budget (+ <=64KiB boundary)")
    parser.add_argument("--wide", action="store_true", help="include all legacy web/wiki candidates")
    args = parser.parse_args()
    if args.limit <= 0 or args.pause < 0 or args.max_bytes <= 0:
        parser.error("limit must be positive and pause nonnegative")
    db = papers.connect()
    db.execute("CREATE TABLE IF NOT EXISTS repo_probes(candidate TEXT PRIMARY KEY, checked TEXT, found INT)")
    known = {h for (h,) in db.execute("SELECT host FROM repos")}
    idents = {i for (i,) in db.execute("SELECT ident FROM repos")} | {_ident(iverieli.OAI)}
    hosts, extra = candidates() if args.wide else academic_candidates()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    recent = {candidate for (candidate,) in db.execute("SELECT candidate FROM repo_probes WHERE checked > ?", (cutoff,))}
    jobs = [(url, [url]) for url in sorted(extra) if url not in recent and _ident(url) not in idents]
    jobs += [(host, [f"https://{host}{path}" for path in PATHS]) for host in sorted(hosts - known - recent)]
    jobs = jobs[:args.limit]
    print(f"{len(hosts):,} academic hosts, {len(extra)} OJS paths, {len(jobs)} candidates this batch", flush=True)
    budget = {"maximum": args.max_bytes, "received": 0}
    async def throttle(request):
        await asyncio.sleep(args.pause)
        await wait_for_host(str(request.url), pause=args.pause, background=True)

    async with httpx.AsyncClient(event_hooks={"request": [throttle]}, follow_redirects=True, timeout=httpx.Timeout(20, connect=6), verify=False,
                                 headers={"User-Agent": papers.AGENT, "Accept-Encoding": "identity"}) as client:
        for candidate, urls in jobs:
            try:
                found = await probe(client, urls, budget)
            except BudgetExceeded:
                print(f"Identify budget stop: {budget['received']:,} body bytes", flush=True)
                break
            db.execute("INSERT OR REPLACE INTO repo_probes VALUES (?, ?, ?)",
                       (candidate, datetime.now(timezone.utc).isoformat(), int(bool(found))))
            if found:
                url, name, base = found
                ident = _ident(base or url)
                if ident not in idents:
                    idents.add(ident)
                    software = next((software for path, software in SOFTWARE.items() if path in url), "other")
                    db.execute("INSERT OR IGNORE INTO repos (base,ident,host,name,software) VALUES (?,?,?,?,?)",
                               (url, ident, _host(url), name, software))
                    print(f"{software:<8} {url} {name[:60]}", flush=True)
            db.commit()
    print(f"{db.execute('SELECT count(*) FROM repos').fetchone()[0]} repositories", flush=True)
    db.close()


if __name__ == "__main__":
    asyncio.run(main())
