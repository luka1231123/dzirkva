"""Own crawl of Georgian sites: data/crawl.db (SQLite FTS5), filled by scripts/crawl_sites.py.

Pages: the main text of each page (trafilatura: no menus, no footers) and its date. Searched like
an engine: pages with all query words (any form), best BM25 first. A page no engine returns can
still be found here.
Domains: trusted sites (config/sources.yaml) and discovered ones (cited in Georgian Wikipedia or
linked from crawled pages), with Georgian share, commercial score, kind and inbound links.
Search boosts rare domains (small_site): small, non-commercial, Georgian, not on the trusted list.
"""

import re
import sqlite3
from functools import cache
from pathlib import Path
from urllib.parse import urlparse

from dzirkva.sources import sources
from dzirkva.wiki import any_form

DB = Path(__file__).resolve().parents[2] / "data" / "crawl.db"
SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS queue (url TEXT PRIMARY KEY, host TEXT, depth INT, status TEXT DEFAULT 'todo');
CREATE INDEX IF NOT EXISTS queue_status ON queue(status, host);
CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(url UNINDEXED, title, date UNINDEXED, text, tokenize='unicode61');
CREATE INDEX IF NOT EXISTS pages_url ON pages_content(c0);  -- page by URL (c0 = url): pages cited in Wikipedia
CREATE INDEX IF NOT EXISTS pages_date ON pages_content(c2);  -- newest pages (c2 = date): discover.newest_posts
CREATE TABLE IF NOT EXISTS domains (
    host TEXT PRIMARY KEY, source TEXT, state TEXT,   -- source: trusted/wiki/link; state: probe/full/rejected
    pages INT DEFAULT 0, georgian REAL DEFAULT 0,     -- pages with text, mean share of Georgian letters
    signals TEXT DEFAULT '', commercial INT DEFAULT 0, kind TEXT DEFAULT 'other', inbound INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS links (src TEXT, dst TEXT, PRIMARY KEY (src, dst)) WITHOUT ROWID;
"""
MIN_GEORGIAN = 0.3
COMMERCIAL = 2       # commercial score from which a domain is commercial
SMALL_INBOUND = 30   # rare domain: at most this many citing Wikipedia pages + linking crawled sites
# known kinds of sites that are never small web: government, news, TV, radio, sport, schools, courts
NOT_RARE = re.compile(r"\.gov\.ge$|news|ambebi|tv|radio|media|press|post|sport|goal|\.edu|school|court|library")
# Small web = a person writing, not an office. Per 1,000 words of a domain's pages (scripts/score_small_web.py):
# personal blogs 25–50 first-person words ("მე", "ჩემი", "მიყვარს"), companies and media under 5.
I_WORDS = set("მე ჩემი ჩემს ჩემო ჩემმა ჩემთვის ჩემზე ჩემთან ვარ ვიყავი ვფიქრობ მგონია მიყვარს მინდა ვწერ "
              "დავწერე ვნახე წავედი ვიცი მახსოვს".split())
CORPORATE_WORDS = set("შპს კომპანია კომპანიის მომსახურება მომსახურების სერვისი კლიენტი კლიენტებს შეკვეთა ფასი "
                      "ფასად ლარი ₾ მიწოდება პროდუქცია ტელ".split())
REPORTING_WORDS = set("განაცხადა აცხადებს ინფორმაციით სააგენტო რედაქცია ბრიფინგზე ცნობით".split())
VOICE_DB = DB.with_name("voice.db")  # separate file: the running crawler keeps crawl.db locked
MIN_VOICE = 10       # first-person words per 1,000
MAX_CORPORATE = 5
MAX_REPORTING = 1


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB, check_same_thread=False, timeout=60)  # the crawler writes all the time
    db.executescript(SCHEMA)
    if "cited" not in {c[1] for c in db.execute("PRAGMA table_info(queue)")}:  # cited in Wikipedia: crawled first
        db.execute("ALTER TABLE queue ADD COLUMN cited INT DEFAULT 0")
    return db


@cache
def _voice() -> sqlite3.Connection | None:
    return sqlite3.connect(VOICE_DB, check_same_thread=False) if VOICE_DB.exists() else None


@cache
def _db() -> sqlite3.Connection | None:
    return connect() if DB.exists() else None


def domain_of(url: str) -> str:
    """Crawl key of a URL: the trusted domain that covers it (tsu.ge for press.tsu.ge), else the host without www."""
    try:
        host = (urlparse(url).hostname or "").removeprefix("www.")
    except ValueError:  # malformed URL
        return ""
    h = host
    while h:
        if h in sources():
            return h
        h = h.partition(".")[2]
    return host


def small_site(url: str) -> bool:
    """Small web: a person's own site. Found by the crawl (not trusted), Georgian, non-commercial, not news,
    government or school; written in the first person (voice), not like a company or a newsroom; few inbound
    links, except one-person blogs (blogspot, wordpress.com) at any count."""
    db = _db()
    host = domain_of(url)
    voice = _voice()
    personal = voice and voice.execute("SELECT 1 FROM voice WHERE host=? AND voice>=? AND corporate<? AND reporting<?",
                                       (host, MIN_VOICE, MAX_CORPORATE, MAX_REPORTING)).fetchone()
    row = personal and db and db.execute("SELECT source, signals, inbound FROM domains WHERE host=? AND state='full' "
                                         "AND commercial<? AND georgian>=?", (host, COMMERCIAL, MIN_GEORGIAN)).fetchone()
    name = re.sub(r"\.(wordpress|blogspot)\.com$", "", host)  # "post" must not match wordpress
    if not row or row[0] == "trusted" or NOT_RARE.search(name):
        return False
    signals = set(row[1].split(","))
    return "news" not in signals and (row[2] <= SMALL_INBOUND or "blog-host" in signals)


def domain_signals(url: str) -> set[str]:
    """Kind and signals of the accepted crawled domain ({'academic', 'dspace', 'rss'}); empty if unknown."""
    db = _db()
    row = db and db.execute("SELECT kind, signals FROM domains WHERE host=? AND state='full'",
                            (domain_of(url),)).fetchone()
    return {row[0], *row[1].split(",")} - {"", "other"} if row else set()


def search(words: list[str], limit: int = 20, urls: list[str] = ()) -> list[dict]:
    """Crawled pages with all words (any form); if too few, with any of them. The date starts the snippet.

    urls: only these pages (http and https both), for the pages Wikipedia articles cite."""
    db = _db()
    if db is None or not words:
        return []
    urls = sorted({re.sub(r"^https?:", s, u) for u in urls for s in ("http:", "https:")})
    only = f" AND rowid IN (SELECT id FROM pages_content WHERE c0 IN ({','.join('?' * len(urls))}))" if urls else ""
    rows = []
    for op in (" AND ", " OR "):
        expr = op.join(any_form(w) for w in words)
        rows = db.execute(
            f"SELECT url, title, date, snippet(pages, 3, '', '', '…', 30) FROM pages "
            f"WHERE pages MATCH ?{only} ORDER BY bm25(pages, 0, 5, 0, 1) LIMIT ?", (expr, *urls, limit)).fetchall()
        if len(rows) >= 5 or len(words) == 1:
            break
    return [{"url": url, "title": title or url, "snippet": f"{date} — {snip}" if date else snip, "engine": "crawl"}
            for url, title, date, snip in rows]
