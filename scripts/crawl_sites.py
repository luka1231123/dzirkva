"""Crawl trusted Georgian sites and discover rare ones (blogs, academic, non-commercial) into data/crawl.db.

Domains (table `domains`):
- trusted (config/sources.yaml): full budget from the start.
- discovered: hosts cited in Georgian Wikipedia (data/kawiki.xml.bz2, read once) and hosts linked from
  crawled Georgian pages (table `links`). A new domain is probed: robots.txt, home page + 5 pages
  (cited pages first). Full budget only if its pages are >30% Georgian and it is not commercial.
Rules, no ML (signals in the HTML of each page, collected per domain):
- commercial score: ad/tracker scripts 1, WooCommerce 2, shop page (3+ shop words on one page: კალათა, ყიდვა, ₾ …) 2
- kind: academic (.edu, OJS, DSpace, or two of ISSN / DOI / ანოტაცია), blog (Blogger, WordPress, blogspot, RSS)
Full domains: pages cited in Georgian Wikipedia first (outside the budget), then home page + sitemaps
(newest first) each run, same-site links up to MAX_DEPTH, max PER_HOST new pages per run.
Polite: robots.txt, one request per second per domain.
Each domain runs as its own task, one page at a time; up to MAX_TASKS in flight. New domains
are probed best first: .ge and blogs, then most linked.

Run in the background:  nohup uv run python scripts/crawl_sites.py > data/crawl.log 2>&1 &
More pages per site:    uv run python scripts/crawl_sites.py 10000
Progress:               sqlite3 data/crawl.db "select state, kind, count(*) from domains group by 1, 2"
"""

import asyncio
import bz2
import gzip
import html
import re
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import lxml.html
import trafilatura

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dzirkva.archive import SKIP_EXT  # noqa: E402
from dzirkva.crawl import COMMERCIAL, MIN_GEORGIAN, connect, domain_of  # noqa: E402
from dzirkva.georgian import georgian_ratio  # noqa: E402
from dzirkva.sources import SOCIAL_HOSTS, VIDEO_HOSTS, sources  # noqa: E402
from dzirkva.wiki import cited_on  # noqa: E402

WIKI_DUMP = Path(__file__).resolve().parent.parent / "data" / "kawiki.xml.bz2"
AGENT = "dzirkva-crawler/0.1 (Georgian search research; 1 req/s)"
PER_HOST = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
PROBE = 6               # new domain: home page + 5 pages
MAX_DEPTH = 3
MAX_SITEMAPS = 30       # sitemap files read per site, newest first
MAX_FULL = 300          # full domains started per step (least recent first)
MAX_PROBES = 100        # new domains started per step (best first)
MAX_TASKS = 400         # pages in flight
PAUSE = 1.0
MIN_TEXT = 200
URL = re.compile(r"https?://[^\s\[\]|<>\"'{}]+")
HOST = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$")
# dumps, archives, link and file services: not sites to discover
SKIP = re.compile(r"(^|\.)(wiki(pedia|source|quote|media|data|books)\.org|wiktionary\.org|archive\.(org|today|is|ph)"
                  r"|webcitation\.org|doi\.org|google\.[a-z.]+|googleapis\.com|gstatic\.com|blogger\.com|wordpress\.org)$")
OTHER_LANGUAGE = re.compile(r"(?i)/(en|eng|english|ru|rus|russian)(/|$)|[?&]lang=(en|eng|ru|rus)\b")
BLOG_HOST = re.compile(r"(^|\.)(blogspot\.[a-z.]+|wordpress\.com)$")
ENTRY = re.compile(r"<(sitemap|url)>(.*?)</\1>", re.S)
LOC = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</loc>", re.S)
LASTMOD = re.compile(r"<lastmod>\s*(.*?)\s*</lastmod>")
SIGNALS = {
    "ads": re.compile(r"googlesyndication|adsbygoogle|doubleclick\.net|adocean|admixer|taboola|outbrain|mgid\.com|fbevents\.js"),
    "woocommerce": re.compile(r"woocommerce", re.I),
    "ojs": re.compile(r"Open Journal Systems|/index\.php/[^/\"]+/article/view|pkp_structure"),
    "dspace": re.compile(r"dspace", re.I),
    "issn": re.compile(r"\bISSN\b"),
    "doi": re.compile(r"doi\.org/10\.|\bdoi:\s*10\.", re.I),
    "ანოტაცია": re.compile(r"ანოტაცია"),
    "blog-engine": re.compile(r"<meta[^>]*(generator[^>]*(Blogger|WordPress)|(Blogger|WordPress)[^>]*generator)", re.I),
    "rss": re.compile(r"application/rss\+xml"),
    "news": re.compile(r'"@type"\s*:\s*"(News|Reportage)Article"'),
}
SHOP = ("კალათა", "ყიდვა", "იყიდე", "შეკვეთა", "ფასდაკლება", "₾")
SHOP_WORDS = 3          # on one page: a history blog uses one or two of them across many posts


def page_signals(page: str, host: str) -> set[str]:
    found = {name for name, rx in SIGNALS.items() if rx.search(page)}
    if sum(w in page for w in SHOP) >= SHOP_WORDS:
        found.add("shop")
    if re.search(r"(^|\.)edu(\.[a-z]+)?$", host):
        found.add("edu")
    if BLOG_HOST.search(host):
        found.add("blog-host")
    return found


def commercial(signals: set[str]) -> int:
    return ("ads" in signals) + 2 * ("woocommerce" in signals) + 2 * ("shop" in signals)


def kind(signals: set[str]) -> str:
    if signals & {"edu", "ojs", "dspace"} or len(signals & {"issn", "doi", "ანოტაცია"}) >= 2:
        return "academic"
    if signals & {"blog-engine", "blog-host", "rss"}:
        return "blog"
    return "other"


def skip(host: str) -> bool:
    base = ".".join(host.split(".")[-2:])
    return (not HOST.match(host) or bool(SKIP.search(host))
            or host in SOCIAL_HOSTS | VIDEO_HOSTS or base in SOCIAL_HOSTS | VIDEO_HOSTS)


class Site:
    def __init__(self, host: str, source: str, state: str, pages: int = 0, georgian: float = 0.0,
                 signals: str = "", inbound: int = 0) -> None:
        self.host, self.source, self.state = host, source, state
        self.pages, self.georgian, self.inbound = pages, georgian, inbound
        self.signals = set(filter(None, signals.split(",")))
        self.base = f"https://{host}"
        self.robots: RobotFileParser | None = None
        self.delay = PAUSE
        self.next = 0.0         # time.monotonic() of the next allowed request
        self.queued = 0         # pages queued in this run (+ left over from the last run)
        self.todo = 0           # pages waiting in the queue
        self.seeded = False     # full domain: home page and sitemaps queued in this run
        self.busy = False       # a page or the seeding is in flight

    @property
    def budget(self) -> int:
        return {"full": PER_HOST, "probe": PROBE}.get(self.state, 0)

    def priority(self) -> tuple:
        return (not (self.host.endswith(".ge") or BLOG_HOST.search(self.host)), -self.inbound)

    def allowed(self, url: str) -> bool:
        return (url.startswith("http") and domain_of(url) == self.host and not SKIP_EXT.search(urlparse(url).path)
                and not OTHER_LANGUAGE.search(url) and (self.robots is None or self.robots.can_fetch(AGENT, url)))


def save(db, s: Site) -> None:
    db.execute("INSERT OR REPLACE INTO domains VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
               (s.host, s.source, s.state, s.pages, s.georgian, ",".join(sorted(s.signals)),
                commercial(s.signals), kind(s.signals), s.inbound))


def add_site(db, sites: dict[str, Site], host: str, source: str, state: str = "probe") -> Site:
    s = sites[host] = Site(host, source, state)
    save(db, s)
    return s


def clean(site: Site, urls: list[str]) -> list[str]:
    return [u for u in dict.fromkeys(html.unescape(u).split("#")[0].rstrip(".,;)") for u in urls) if site.allowed(u)]


def enqueue(db, site: Site, urls: list[str], depth: int) -> None:
    urls = clean(site, urls)
    cur = db.executemany("INSERT OR IGNORE INTO queue(url, host, depth) VALUES (?, ?, ?)",
                         [(u, site.host, depth) for u in urls[: max(0, site.budget - site.queued)]])
    site.queued += cur.rowcount
    site.todo += cur.rowcount


def seed_wiki(db, sites: dict[str, Site]) -> None:
    """Hosts cited in Georgian Wikipedia: inbound = citing pages, probe = home page + cited pages."""
    count: Counter[str] = Counter()
    cited: dict[str, list[str]] = {}
    page: set[str] = set()
    with bz2.open(WIKI_DUMP, "rt", encoding="utf-8") as f:
        for line in f:
            if "<page>" in line:
                count.update(page)
                page = set()
            for url in URL.findall(line):
                host = domain_of(url)
                page.add(host)
                if len(urls := cited.setdefault(host, [])) < PROBE - 1:
                    urls.append(url)
    count.update(page)
    for host, n in count.items():
        if host in sites:
            sites[host].inbound += n
            save(db, sites[host])
        elif not skip(host):
            s = add_site(db, sites, host, "wiki")
            s.inbound = n
            enqueue(db, s, [f"https://{host}/"], 0)
            enqueue(db, s, cited[host], 1)
            save(db, s)
    db.commit()
    print(f"wiki seeds: {sum(s.source == 'wiki' for s in sites.values()):,} domains", flush=True)


async def get(client: httpx.AsyncClient, url: str) -> httpx.Response | None:
    try:
        return await client.get(url)
    except httpx.HTTPError:
        return None


async def load_robots(client: httpx.AsyncClient, site: Site) -> bool:
    """Read robots.txt (https, else http). False: the host does not answer at all."""
    site.robots = RobotFileParser()
    r = await get(client, f"{site.base}/robots.txt")
    if r is None:
        site.base = f"http://{site.host}"
        r = await get(client, f"{site.base}/robots.txt")
    ok = r is not None and r.status_code == 200 and "html" not in r.headers.get("content-type", "")
    site.robots.parse(r.text.splitlines() if ok else [])
    site.delay = min(max(PAUSE, float(site.robots.crawl_delay(AGENT) or 0)), 30)
    return r is not None


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
        for tag, body in ENTRY.findall(raw.decode("utf-8", "replace")):
            loc, mod = LOC.search(body), LASTMOD.search(body)
            if not loc:
                continue
            if tag == "sitemap":
                children.append((mod.group(1) if mod else "", loc.group(1)))
            else:
                pages[loc.group(1)] = mod.group(1) if mod else ""
        todo = [u for _, u in sorted(children, reverse=True)] + todo
    return sorted(pages, key=pages.get, reverse=True)


async def seed(client: httpx.AsyncClient, db, site: Site) -> None:
    """Full domain, once per run: home page again (new links) + sitemap pages."""
    if site.robots is not None or await load_robots(client, site):
        home = f"{site.base}/"
        site.todo += db.execute("UPDATE queue SET status='todo' WHERE url=? AND status!='todo'", (home,)).rowcount
        enqueue(db, site, [home], 0)
        enqueue(db, site, await sitemap_urls(client, site, site.robots.site_maps() or [f"{home}sitemap.xml"]), 1)
        db.executemany("INSERT INTO queue(url, host, depth, cited) VALUES (?, ?, 1, 1) ON CONFLICT(url) DO UPDATE SET cited=1",
                       [(u, site.host) for u in clean(site, cited_on(site.host))])
        site.todo = db.execute("SELECT count(*) FROM queue WHERE host=? AND status='todo'", (site.host,)).fetchone()[0]
        print(f"seeded {site.host}: {site.todo} pages waiting", flush=True)
    site.seeded, site.busy = True, False


def decide(site: Site) -> None:
    """End of a probe: full budget for Georgian, non-commercial domains."""
    ok = site.georgian > MIN_GEORGIAN and commercial(site.signals) < COMMERCIAL
    site.state = "full" if ok else "rejected"
    print(f"{site.state:<8} {site.georgian:4.0%} c{commercial(site.signals)} {kind(site.signals):<8} {site.host}", flush=True)


def discover(db, sites: dict[str, Site], src: Site, urls: list[str]) -> None:
    """Links to other hosts: remember them; unknown hosts join the queue as probes."""
    for url in urls:
        host = domain_of(url)
        if host == src.host or skip(host):
            continue
        if db.execute("INSERT OR IGNORE INTO links VALUES (?, ?)", (src.host, host)).rowcount:
            dst = sites.get(host) or add_site(db, sites, host, "link")
            dst.inbound += 1
            if dst.state == "probe":
                enqueue(db, dst, [f"https://{host}/"], 0)
                enqueue(db, dst, [url], 1)
            save(db, dst)


def links(page: str, base: str) -> list[str]:
    try:
        root = lxml.html.fromstring(page)
    except (ValueError, lxml.etree.ParserError):
        return []
    return [urljoin(base, h.strip()) for h in root.xpath("//a/@href")]


async def visit(client: httpx.AsyncClient, db, sites: dict[str, Site], site: Site, url: str, depth: int) -> None:
    if site.robots is None and not await load_robots(client, site):
        db.execute("UPDATE queue SET status='dead' WHERE host=? AND status='todo'", (site.host,))
        site.todo, site.state = 0, "rejected"
        save(db, site)
        return
    site.todo -= 1
    fetch = url.replace("https:", "http:", 1) if site.base.startswith("http:") else url
    if not site.robots.can_fetch(AGENT, fetch):
        status, r = "robots", None
    else:
        r = await get(client, fetch)
        status = "error" if r is None else f"http:{r.status_code}"
    if r is not None and r.status_code == 200 and "html" in r.headers.get("content-type", ""):
        try:
            doc = trafilatura.bare_extraction(r.content, url=str(r.url), with_metadata=True)
        except Exception:  # trafilatura raises many kinds on broken pages
            doc = None
        text = (doc.text or "") if doc else ""
        ratio = georgian_ratio(text)
        site.signals |= page_signals(r.text, site.host)
        if text:
            site.pages += 1
            site.georgian += (ratio - site.georgian) / site.pages  # running mean
        # home pages (depth 0) are link lists: follow their links, do not store them
        status = "done" if ratio >= MIN_GEORGIAN and len(text) >= MIN_TEXT and depth > 0 else "skipped"
        if status == "done":
            db.execute("INSERT INTO pages VALUES (?, ?, ?, ?)", (fetch, doc.title or "", doc.date or "", text))
        page_links = links(r.text, str(r.url))
        if depth < MAX_DEPTH:
            enqueue(db, site, page_links, depth + 1)
        if status == "done":
            discover(db, sites, site, page_links)
        print(f"{status:<8} {ratio:4.0%} {fetch[:100]}", flush=True)
    db.execute("UPDATE queue SET status=? WHERE url=?", (status, url))
    if site.state == "probe" and site.todo <= 0:
        decide(site)
    save(db, site)


async def main() -> None:
    db = connect()
    sites = {h: Site(h, *row) for h, *row in db.execute(
        "SELECT host, source, state, pages, georgian, signals, inbound FROM domains")}
    for d in sources():
        if d not in sites and not SKIP.search(d):
            add_site(db, sites, d, "trusted", "full")
        elif d in sites and sites[d].source != "trusted":  # found first, trusted later (poetry.ge)
            sites[d].source, sites[d].state = "trusted", "full"
            save(db, sites[d])
    if not any(s.source == "wiki" for s in sites.values()):
        seed_wiki(db, sites)
    for host, n in db.execute("SELECT host, count(*) FROM queue WHERE status='todo' GROUP BY host"):
        if host in sites:
            sites[host].queued = sites[host].todo = n
    for s in sites.values():
        if s.state == "probe" and s.todo == 0:  # probe finished when the last run stopped
            decide(s)
            save(db, s)
    db.commit()
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=100)
    timeout = httpx.Timeout(20, connect=8)
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout, headers={"User-Agent": AGENT}, limits=limits) as client:
        tasks: set[asyncio.Task] = set()

        def start(s: Site, job) -> None:
            s.busy = True
            tasks.add(task := asyncio.create_task(job))
            task.add_done_callback(tasks.discard)

        async def step(s: Site, url: str, depth: int) -> None:
            await visit(client, db, sites, s, url, depth)
            s.busy, s.next = False, time.monotonic() + s.delay

        while True:
            for s in sites.values():
                if s.state == "full" and not s.seeded and not s.busy:
                    start(s, seed(client, db, s))
            now = time.monotonic()
            due = [s for s in sites.values() if s.todo > 0 and not s.busy and s.next <= now]
            full = sorted((s for s in due if s.state == "full"), key=lambda s: s.next)[:MAX_FULL]
            probes = sorted((s for s in due if s.state == "probe"), key=Site.priority)[:MAX_PROBES]
            for s in (full + probes)[: max(0, MAX_TASKS - len(tasks))]:
                row = db.execute("SELECT url, depth FROM queue WHERE host=? AND status='todo' "
                                 "ORDER BY cited DESC, depth, rowid LIMIT 1", (s.host,)).fetchone()
                if row is None:
                    s.todo = 0
                else:
                    start(s, step(s, *row))
            db.commit()
            if not tasks and not any(s.todo > 0 for s in sites.values()):
                print("queue empty", flush=True)
                return
            await asyncio.sleep(PAUSE / 2)

if __name__ == "__main__":
    asyncio.run(main())
