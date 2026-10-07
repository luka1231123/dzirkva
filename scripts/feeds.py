"""Bounded RSS/Atom discovery and refresh; complete Georgian posts enter the crawl index.

Run hourly: uv run python scripts/feeds.py --discover-limit 10 --poll-limit 30
Registry and validators live in feeds.db; article excerpts enter the slow crawler's queue.
No embeddings, graph loading, or unbounded concurrent requests.
"""

import argparse
import asyncio
import hashlib
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from lxml import etree, html

from dzirkva import crawl
from dzirkva.georgian import georgian_ratio
from dzirkva.ingest import wait_for_host
from dzirkva.sources import sources

FEEDS_DB = crawl.DB.with_name("feeds.db")
AGENT = "dzirkva-crawler/0.1 (Georgian search research; 1 req/s)"
MAX_BYTES = 2_000_000
MAX_ITEMS = 200
MIN_FULL_TEXT = 500
SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS posts (url TEXT PRIMARY KEY, host TEXT, title TEXT, date TEXT);
CREATE TABLE IF NOT EXISTS feeds (
 host TEXT PRIMARY KEY, url TEXT DEFAULT '', etag TEXT DEFAULT '', modified TEXT DEFAULT '',
 last_check INTEGER DEFAULT 0, next_check INTEGER DEFAULT 0, failures INTEGER DEFAULT 0,
 interval INTEGER DEFAULT 86400, yield INTEGER DEFAULT 0, total_new INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS feeds_due ON feeds(next_check);
CREATE TABLE IF NOT EXISTS items (url TEXT PRIMARY KEY, digest TEXT, indexed INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS items_digest ON items(digest);
"""


@dataclass
class Post:
    url: str
    title: str
    date: str
    text: str = ""
    complete: bool = False
    indexable: bool = True


def _date(text: str) -> str:
    text = (text or "").strip()
    try:
        if re.match(r"\d{4}-\d{2}-\d{2}", text):
            day = datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        else:
            day = parsedate_to_datetime(text).strftime("%Y-%m-%d")
        return day if day <= datetime.now(timezone.utc).strftime("%Y-%m-%d") else ""
    except (TypeError, ValueError, OverflowError):
        return ""


def clean_url(url: str, base: str = "") -> str:
    try:
        u = urlsplit(urljoin(base, url.strip()))
        if u.scheme not in {"http", "https"} or not u.hostname or u.username or u.password:
            return ""
        return urlunsplit((u.scheme, u.netloc, u.path or "/", u.query, ""))
    except ValueError:
        return ""


def plain(markup: str) -> str:
    try:
        tree = html.fromstring(markup)
        for e in tree.xpath("//script|//style"):
            e.drop_tree()
        return " ".join(tree.text_content().split())
    except (ValueError, etree.ParserError):
        return " ".join(markup.split())


def parse(xml: bytes, base: str = "") -> list[Post]:
    """RSS 2, RDF RSS 1, Atom; only explicit content fields count as complete text."""
    if len(xml) > MAX_BYTES or b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ValueError("oversized feed or XML declarations")
    root = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True))
    posts = []
    for entry in root.iter():
        name = etree.QName(entry).localname if isinstance(entry.tag, str) else ""
        if name not in {"item", "entry"}:
            continue
        fields = {etree.QName(e).localname: e for e in entry if isinstance(e.tag, str)}
        def value(key):
            e = fields.get(key)
            return "" if e is None else "".join(e.itertext()).strip()
        if name == "entry":
            link = next((e.get("href", "") for e in entry if isinstance(e.tag, str)
                         and etree.QName(e).localname == "link" and e.get("rel", "alternate") == "alternate"), "")
        else:
            link = value("link")
            if not link and fields.get("guid") is not None and fields["guid"].get("isPermaLink", "true") == "true":
                link = value("guid")
        content_key = "encoded" if "encoded" in fields else "content" if name == "entry" and "content" in fields else ""
        content = fields.get(content_key)
        if content is not None and len(content):
            markup = "".join(etree.tostring(e, encoding="unicode") for e in content)
        else:
            markup = value(content_key) if content_key else value("description") or value("summary")
        content_type = content.get("type", "text").lower() if content is not None else ""
        complete = bool(content_key) and content is not None and not content.get("src") and (
            name != "entry" or content_type in {"text", "html", "xhtml", "text/plain", "text/html", "application/xhtml+xml"})
        text = plain(markup)
        if re.search(r"read more|continue reading|ვრცლად|სრულად ნახვა", text[-160:], re.I):
            complete = False
        indexable = True
        try:
            body_tree = html.fromstring(markup)
            indexable = not any(re.search(r"\b(?:noindex|none)\b", e.get("content", ""), re.I) for e in body_tree.xpath(
                "//meta[translate(@name,'ROBOTS','robots')='robots']"))
        except (ValueError, etree.ParserError):
            pass
        u = clean_url(link, base)
        if u:
            posts.append(Post(u, plain(value("title")), _date(value("pubDate") or value("published")
                              or value("updated") or value("date")), text, complete, indexable))
        if len(posts) >= MAX_ITEMS:
            break
    return posts


class BudgetExhausted(Exception):
    """Leave remaining due feeds untouched for the next bounded run."""


class Fetcher:
    """Sequential requests with host delay and robots checks, including feed/home redirects."""
    def __init__(self, client, pause, max_bytes=10 * 1024 * 1024):
        self.client, self.pause = client, pause
        self.max_bytes, self.bytes_read = max_bytes, 0
        self.next = {}
        self.robots = {}
        self.delays = {}

    async def request(self, url, headers=None):
        if self.bytes_read >= self.max_bytes:
            raise BudgetExhausted()
        host = urlsplit(url).netloc
        await wait_for_host(url, pause=self.delays.get(host, self.pause), background=True)
        await asyncio.sleep(max(0, self.next.get(host, 0) - time.monotonic()))
        self.next[host] = time.monotonic() + self.delays.get(host, self.pause)
        async with self.client.stream("GET", url, headers=headers, follow_redirects=False) as response:
            if response.is_redirect:
                return response.status_code, response.headers, b""
            if response.status_code != 304:
                response.raise_for_status()
            try:
                advertised = int(response.headers.get("content-length", "0"))
            except ValueError:
                advertised = 0
            if advertised > self.max_bytes - self.bytes_read:
                raise BudgetExhausted()
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=16384):
                self.bytes_read += len(chunk)
                if self.bytes_read > self.max_bytes:
                    raise BudgetExhausted()
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise ValueError("response too large")
            return response.status_code, response.headers, bytes(body)

    async def allowed(self, url):
        u = urlsplit(url)
        origin = f"{u.scheme}://{u.netloc}"
        if origin not in self.robots:
            robot = RobotFileParser()
            try:
                robot_url = origin + "/robots.txt"
                for _ in range(6):
                    status, headers, body = await self.request(robot_url)
                    if not 300 <= status < 400:
                        break
                    robot_url = clean_url(headers.get("location", ""), robot_url)
                    if not robot_url:
                        return False
                if status != 200:
                    return False
                robot.parse(body.decode("utf-8", "replace").splitlines())
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in {404, 410}:
                    return False
                robot.parse([])
            self.robots[origin] = robot
            self.delays[u.netloc] = max(self.pause, float(robot.crawl_delay(AGENT) or 0))
            self.next[u.netloc] = max(self.next.get(u.netloc, 0), time.monotonic() + self.delays[u.netloc])
        return self.robots[origin].can_fetch(AGENT, url)

    async def get(self, url, headers=None):
        for _ in range(6):
            if not await self.allowed(url):
                raise ValueError("robots disallow or unavailable")
            status, response_headers, body = await self.request(url, headers)
            if 300 <= status < 400 and status != 304:
                redirected = clean_url(response_headers.get("location", ""), url)
                if not redirected:
                    raise ValueError("bad redirect")
                url = redirected
                headers = None
                continue
            return url, status, response_headers, body
        raise ValueError("too many redirects")


def seed(db, crawler, limit):
    """Persistent discovery rows, never constructing the sites/links graph in memory."""
    for host, (category, _) in sources().items():
        interval = 7200 if category == "news" else 86400
        db.execute("INSERT OR IGNORE INTO feeds(host,interval) VALUES (?,?)", (host, interval))
    db.commit()
    # Indexed lookup into the registry avoids repeatedly trying the same accepted domains.
    crawler.execute("ATTACH DATABASE ? AS rss_registry", (str(FEEDS_DB),))
    rows = crawler.execute("SELECT d.host, d.kind FROM domains d LEFT JOIN rss_registry.feeds f ON f.host=d.host "
                           "WHERE f.host IS NULL AND d.state='full' AND "
                           "(d.signals LIKE '%rss%' OR d.kind='academic') LIMIT ?", (limit,)).fetchall()
    crawler.execute("DETACH DATABASE rss_registry")
    db.executemany("INSERT OR IGNORE INTO feeds(host) VALUES (?)", [(h,) for h, _ in rows])
    db.commit()


async def discover(fetcher, host):
    home, _, _, body = await fetcher.get(f"https://{host}/")
    tree = html.fromstring(body)
    for e in tree.xpath("//link[@href]"):
        if "alternate" in e.get("rel", "").lower().split() and e.get("type", "").lower() in {
                "application/rss+xml", "application/atom+xml", "application/rdf+xml"}:
            if "comments" not in e.get("href", "").lower():
                feed = clean_url(e.get("href"), home)
                if feed:
                    return feed
    return ""


async def poll(fetcher, db, crawler, row):
    host, url, etag, modified, _, _, failures, interval, _, _ = row
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if modified:
        headers["If-Modified-Since"] = modified
    final, status, response_headers, body = await fetcher.get(url, headers)
    new = indexed = queued = 0
    if status != 304:
        posts = parse(body, final)
        # An HTML error page with HTTP 200 must not silently become an empty healthy feed.
        root = etree.fromstring(body, etree.XMLParser(resolve_entities=False, no_network=True))
        if etree.QName(root).localname not in {"rss", "RDF", "feed"}:
            raise ValueError("not a feed")
        feed_indexable = not re.search(r"\b(?:noindex|none)\b", response_headers.get("x-robots-tag", ""), re.I)
        for post in posts:
            if crawl.domain_of(post.url) != host or georgian_ratio(post.title + " " + post.text) < crawl.MIN_GEORGIAN:
                continue
            existing = db.execute("SELECT digest,indexed FROM items WHERE url=?", (post.url,)).fetchone()
            digest = hashlib.sha256(post.text.encode()).hexdigest()
            db.execute("INSERT INTO posts VALUES (?,?,?,?) ON CONFLICT(url) DO UPDATE SET title=excluded.title, "
                       "date=CASE WHEN excluded.date='' THEN posts.date ELSE excluded.date END",
                       (post.url, host, post.title, post.date))
            new += existing is None
            complete = post.complete and post.indexable and feed_indexable and len(post.text) >= MIN_FULL_TEXT and georgian_ratio(post.text) >= crawl.MIN_GEORGIAN
            if existing and existing[0] == digest and (existing[1] or not complete):
                continue
            duplicate = db.execute("SELECT 1 FROM items WHERE digest=? AND indexed=1 AND url<>?", (digest, post.url)).fetchone()
            stored = False
            if complete and not duplicate and await fetcher.allowed(post.url):
                crawl.upsert_page(crawler, post.url, post.title, post.date, post.text, prefer_existing_longer=True)
                indexed += 1
                stored = True
            elif not duplicate:
                queued += int(crawl.queue_url(crawler, post.url, priority=2))
            db.execute("INSERT INTO items VALUES (?,?,?) ON CONFLICT(url) DO UPDATE SET "
                       "digest=excluded.digest,indexed=excluded.indexed", (post.url, digest, int(stored)))
        crawler.commit()
        # Validators are replaced on 200; a server can stop supplying an old validator.
        etag = response_headers.get("etag", "")
        modified = response_headers.get("last-modified", "")
    if new:
        interval = max(3600, interval // 2)
    else:
        interval = min(7 * 86400, max(3600, int(interval * 1.5)))
    now = int(time.time())
    db.execute("UPDATE feeds SET url=?,etag=?,modified=?,last_check=?,next_check=?,failures=0,"
               "interval=?,yield=?,total_new=total_new+? WHERE host=?",
               (final, etag, modified, now, now + interval, interval, new, new, host))
    db.commit()
    return new, indexed, queued


async def main(args):
    db = sqlite3.connect(FEEDS_DB, timeout=60)
    db.executescript(SCHEMA)
    crawler = crawl.connect()
    seed(db, crawler, args.discover_limit)
    counts = [0, 0, 0]
    now = int(time.time())
    async with httpx.AsyncClient(timeout=30, headers={"User-Agent": AGENT}) as client:
        fetcher = Fetcher(client, args.pause, args.max_bytes)
        rows = db.execute("SELECT host FROM feeds WHERE url='' AND next_check<=? "
                          "ORDER BY next_check,host LIMIT ?", (now, args.discover_limit)).fetchall()
        for (host,) in rows:
            try:
                url = await discover(fetcher, host)
                db.execute("UPDATE feeds SET url=?,last_check=?,next_check=?,failures=0 WHERE host=?",
                           (url, now, now if url else now + 30 * 86400, host))
            except BudgetExhausted:
                print("RSS download budget reached during discovery; remaining hosts stay due", flush=True)
                break
            except (httpx.HTTPError, ValueError, etree.LxmlError) as e:
                fail(db, host, e)
            db.commit()
        rows = db.execute("SELECT * FROM feeds WHERE url<>'' AND next_check<=? "
                          "ORDER BY next_check,host LIMIT ?", (now, args.poll_limit)).fetchall()
        for row in rows:
            try:
                result = await poll(fetcher, db, crawler, row)
                counts = [a + b for a, b in zip(counts, result)]
                print(f"{row[0]}: {result[0]} new, {result[1]} indexed, {result[2]} queued", flush=True)
            except BudgetExhausted:
                crawler.rollback()
                db.rollback()
                print("RSS download budget reached; remaining feeds stay due", flush=True)
                break
            except (httpx.HTTPError, ValueError, etree.LxmlError) as e:
                crawler.rollback()
                db.rollback()
                fail(db, row[0], e)
                db.commit()
    crawler.close()
    db.close()
    print(f"new={counts[0]} indexed={counts[1]} queued={counts[2]} bytes={fetcher.bytes_read}", flush=True)


def fail(db, host, error):
    failures = db.execute("SELECT failures FROM feeds WHERE host=?", (host,)).fetchone()[0] + 1
    now = int(time.time())
    delay = min(7 * 86400, 3600 * 2 ** min(failures, 8))
    db.execute("UPDATE feeds SET failures=?,last_check=?,next_check=? WHERE host=?", (failures, now, now + delay, host))
    print(f"{host}: {type(error).__name__}: {str(error)[:160]}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discover-limit", type=int, default=10)
    parser.add_argument("--poll-limit", type=int, default=30)
    parser.add_argument("--max-bytes", type=int, default=10 * 1024 * 1024, help="total response body budget per run (default 10 MiB)")
    parser.add_argument("--pause", type=float, default=2.0, help="minimum seconds between requests to a host")
    args = parser.parse_args()
    if min(args.discover_limit, args.poll_limit, args.max_bytes) < 0 or args.pause < 1:
        parser.error("limits must be nonnegative and pause at least 1 second")
    asyncio.run(main(args))
