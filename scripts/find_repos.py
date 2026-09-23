"""Find the OAI-PMH endpoints of Georgian journals and repositories → data/papers.db table repos. ~10 min.

Hosts: every .ge host the crawl knows (queue and domains), .ge hosts cited in Georgian Wikipedia, and the crawled
and trusted Georgian sites. Each host is asked for Identify at the usual paths (OJS, DSpace 6 and 7, EPrints)
and at the OJS paths seen in crawled URLs (/ojs/index.php/…). One endpoint per base URL of Identify
(hos.openjournals.ge answers as openjournals.ge). Iverieli (dspace.nplg.gov.ge) has its own index.
Run again after crawling: known endpoints stay, new ones are added.
Run: uv run python scripts/find_repos.py
"""

import asyncio
import bz2
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

from dzirkva import iverieli, papers
from dzirkva.crawl import DB as CRAWL_DB
from dzirkva.sources import sources

WIKI_DUMP = Path(__file__).resolve().parent.parent / "data" / "kawiki.xml.bz2"
PATHS = ("/index.php/index/oai", "/oai/request", "/server/oai/request", "/cgi/oai2", "/index/oai", "/oai")
SOFTWARE = {"/index.php/": "ojs", "/index/oai": "ojs", "/oai/request": "dspace", "/cgi/oai2": "eprints"}
OJS_PREFIX = re.compile(r"^(https?://[^/?#]+)(/[^?#]*?)/index\.php/")
URL = re.compile(r"https?://[^\s\[\]|<>\"'{}]+")
TASKS = 100
NS = {"oai": "http://www.openarchives.org/OAI/2.0/"}


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").removeprefix("www.")
    except ValueError:  # malformed URL
        return ""


def candidates() -> tuple[set[str], set[str]]:
    """(hosts, extra endpoints): Georgian hosts, and OJS endpoints under a path (/ojs/index.php/index/oai)."""
    crawl = sqlite3.connect(f"file:{CRAWL_DB}?mode=ro", uri=True)
    hosts, extra = set(sources()), set()
    for (url,) in crawl.execute("SELECT url FROM queue"):
        if (h := _host(url)).endswith(".ge"):
            hosts.add(h)
        if m := OJS_PREFIX.match(url):
            extra.add(f"{m[1]}{m[2]}/index.php/index/oai")
    hosts |= {h for (h,) in crawl.execute("SELECT host FROM domains WHERE host LIKE '%.ge' OR state = 'full'")}
    with bz2.open(WIKI_DUMP, "rt", encoding="utf-8") as f:
        for line in f:
            if ".ge" in line:
                hosts |= {h for u in URL.findall(line) if (h := _host(u)).endswith(".ge")}
    return hosts - {""}, extra


async def identify(client: httpx.AsyncClient, url: str) -> tuple[str, str] | None | bool:
    """(repository name, base URL) of an endpoint; None: no endpoint here; False: the host does not answer."""
    try:
        r = await client.get(url, params={"verb": "Identify"})
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return False
    except httpx.HTTPError:
        return None
    if r.status_code != 200 or b"<Identify" not in r.content:
        return None
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        return None
    return root.findtext(".//oai:repositoryName", "", NS).strip(), root.findtext(".//oai:baseURL", "", NS).strip()


async def probe(client: httpx.AsyncClient, urls: list[str]) -> tuple[str, str, str] | None:
    """The first endpoint that answers Identify: (url, name, base URL). A host without https is asked by http."""
    found = await identify(client, urls[0])
    if found is False and urls[0].startswith("https:"):
        urls = [u.replace("https:", "http:", 1) for u in urls]
        found = await identify(client, urls[0])
    if found is False:
        return None
    for url in urls:
        if url != urls[0]:
            found = await identify(client, url)
        if found:
            return (url, *found)
    return None


def _ident(base: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", base).rstrip("/")


async def main() -> None:
    db = papers.connect()
    known = {h for (h,) in db.execute("SELECT host FROM repos")}
    idents = {i for (i,) in db.execute("SELECT ident FROM repos")} | {_ident(iverieli.OAI)}
    hosts, extra = candidates()
    jobs = [[f"https://{h}{p}" for p in PATHS] for h in sorted(hosts - known)] + [[u] for u in sorted(extra)]
    print(f"{len(hosts):,} hosts, {len(extra)} OJS paths, {len(jobs):,} to ask", flush=True)
    limit = asyncio.Semaphore(TASKS)
    timeout = httpx.Timeout(20, connect=6)

    async def one(client: httpx.AsyncClient, urls: list[str]) -> None:
        async with limit:
            found = await probe(client, urls)
        if not found:
            return
        url, name, base = found
        ident = _ident(base or url)
        if ident in idents:
            return
        idents.add(ident)
        software = next((s for p, s in SOFTWARE.items() if p in url), "other")
        db.execute("INSERT OR IGNORE INTO repos (base, ident, host, name, software) VALUES (?, ?, ?, ?, ?)",
                   (url, ident, _host(url), name, software))
        db.commit()
        print(f"{software:<8} {url}  {name[:60]}", flush=True)

    # verify=False: university sites often have expired certificates; only public metadata is read
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, verify=False,
                                 headers={"User-Agent": papers.AGENT}) as client:
        await asyncio.gather(*(one(client, urls) for urls in jobs))
    print(f"{db.execute('SELECT count(*) FROM repos').fetchone()[0]} repositories", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
