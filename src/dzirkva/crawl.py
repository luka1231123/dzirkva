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
from collections import Counter
from functools import cache
from pathlib import Path
from urllib.parse import urlparse

from dzirkva.sources import sources
from dzirkva.wiki import any_form, soft_and, soft_and_exprs

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
CREATE TABLE IF NOT EXISTS ingest_changes (host TEXT PRIMARY KEY); -- notify the live crawler of feed writes
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
    columns = {c[1] for c in db.execute("PRAGMA table_info(queue)")}
    for column in ("cited", "priority"):
        if column not in columns:
            db.execute(f"ALTER TABLE queue ADD COLUMN {column} INT DEFAULT 0")
    return db


def queue_url(db: sqlite3.Connection, url: str, priority: int = 2) -> bool:
    """Feed excerpts join the existing slow crawler, rather than another page downloader."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            return False
    except ValueError:
        return False
    url = url.split("#", 1)[0]
    host = domain_of(url)
    row = db.execute("SELECT state FROM domains WHERE host=?", (host,)).fetchone()
    if row is None and host in sources():
        db.execute("INSERT INTO domains(host,source,state) VALUES (?,'trusted','full')", (host,))
        row = ("full",)
    if not row or row[0] not in ("full", "probe"):
        return False
    if db.execute("SELECT 1 FROM pages_content WHERE c0=? LIMIT 1", (url,)).fetchone():
        return False
    changed = db.execute("INSERT INTO queue(url,host,depth,status,priority) VALUES (?,?,1,'todo',?) "
               "ON CONFLICT(url) DO UPDATE SET priority=max(priority,excluded.priority), "
               "status='todo' WHERE (queue.status IN ('todo','error','http:404','http:429') "
               "OR queue.status LIKE 'http:5%') "
               "AND (queue.priority<excluded.priority OR queue.status!='todo')",
               (url, host, priority)).rowcount
    if changed:
        db.execute("INSERT OR IGNORE INTO ingest_changes VALUES (?)", (host,))
    return bool(changed)


def upsert_page(db: sqlite3.Connection, url: str, title: str, date: str, text: str,
                *, update_domain: bool = True, prefer_existing_longer: bool = False) -> bool:
    """Replace a URL's indexed text; caller commits. RSS and the crawler share this path."""
    from dzirkva.georgian import georgian_ratio
    from dzirkva.smallweb import update_voice

    url = url.split("#", 1)[0]
    host = domain_of(url)
    if host in sources():
        db.execute("INSERT OR IGNORE INTO domains(host,source,state) VALUES (?,'trusted','full')", (host,))
    old = db.execute("SELECT c1,c2,c3 FROM pages_content WHERE c0=? ORDER BY length(c3) DESC LIMIT 1",
                     (url,)).fetchone()
    new = old is None
    if old:
        title, date = title or old[0], date or old[1]
        if prefer_existing_longer and len(old[2] or "") > len(text):
            text = old[2]
    db.execute("DELETE FROM pages WHERE rowid IN (SELECT id FROM pages_content WHERE c0=?)", (url,))
    db.execute("INSERT INTO pages(url,title,date,text) VALUES (?,?,?,?)", (url, title, date, text))
    if new and update_domain:
        ratio = georgian_ratio(text)
        db.execute("UPDATE domains SET georgian=(georgian*pages+?)/(pages+1),pages=pages+1 WHERE host=?",
                   (ratio, host))
    db.execute("UPDATE queue SET status='done' WHERE url=?", (url,))
    if update_domain:
        db.execute("INSERT OR IGNORE INTO ingest_changes VALUES (?)", (host,))
    update_voice(url, text, host)
    return new


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


KEEP_POOL = 2000  # matches read (URL only) to find the `keep` pages beyond the top results
KEEP_PER_SITE = 2
KEEP_CHUNK = 200  # URLs read at a time; about 10% of matches pass search.people


def search(words: list[str], limit: int = 20, urls: list[str] = (), hosts: list[str] = (),
           keep=None, keep_limit: int = 0) -> list[dict]:
    """Crawled pages with all words (any form), some without the most common word (wiki.soft_and). The date starts
    the snippet.

    urls: only these pages (http and https both), for the pages Wikipedia articles cite.
    hosts: only pages on these sites (http and https, with and without www.; not their other subdomains).
    keep(url) → bool: after the top `limit`, also the next best `keep_limit` pages it accepts, KEEP_PER_SITE per
    site, from the first KEEP_POOL matches. Big sites fill the top; this finds people's sites (search.people)."""
    if keep and keep_limit:
        return _search_keep(words, limit, keep, keep_limit)
    db = _db()
    if db is None or not words:
        return []
    urls = sorted({re.sub(r"^https?:", s, u) for u in urls for s in ("http:", "https:")})
    # Keep FTS driving the query: rowid IN otherwise repeats MATCH for every site's page.
    only = f" AND +rowid IN (SELECT id FROM pages_content WHERE c0 IN ({','.join('?' * len(urls))}))" if urls else ""
    # a site's pages: URL ranges on the pages_url index ("/" + 1 = "0" ends the range)
    starts = [f"{s}://{w}{h}/" for h in hosts for s in ("http", "https") for w in ("", "www.")]
    if starts:
        only += f" AND +rowid IN (SELECT id FROM pages_content WHERE {' OR '.join(['(c0 >= ? AND c0 < ?)'] * len(starts))})"
    sites = [x for p in starts for x in (p, p[:-1] + "0")]
    rows = soft_and(lambda expr, n: db.execute(
        f"SELECT url, title, date, snippet(pages, 3, '', '', '…', 30) FROM pages "
        f"WHERE pages MATCH ?{only} AND rank MATCH 'bm25(0,5,0,1)' ORDER BY rank LIMIT ?",
        (expr, *urls, *sites, n)).fetchall(), words, limit)
    return [{"url": url, "title": title or url, "snippet": f"{date} · {snip}" if date else snip, "engine": "crawl"}
            for url, title, date, snip in rows]


def _search_keep(words: list[str], limit: int, keep, keep_limit: int) -> list[dict]:
    """search() with keep: rank KEEP_POOL matches by rowid only, read their URLs in chunks until keep_limit
    pages pass (a cold disk made reading 2000 URLs at once cost 1.5 s on the server), then the snippets."""
    db = _db()
    if db is None or not words:
        return []
    run = lambda expr, n: [r for r, in db.execute(
        "SELECT rowid FROM pages WHERE pages MATCH ? AND rank MATCH 'bm25(0,5,0,1)' ORDER BY rank LIMIT ?", (expr, n))]
    # wiki.soft_and, split: the top of both searches is the result list, the rest is the pool for keep
    exprs, pool = soft_and_exprs(words), max(KEEP_POOL, limit)
    full = run(exprs[0], pool)
    seen = set(full)
    part = [i for i in run(exprs[-1], len(full) + pool // 2) if i not in seen] if len(exprs) > 1 else []
    expr = exprs[-1]  # matches every row of both: snippets
    chosen, rest, sites = full[:limit] + part[:limit // 2], full[limit:] + part[limit // 2:], Counter()
    top = len(chosen)
    for i in range(0, len(rest), KEEP_CHUNK):
        chunk = rest[i:i + KEEP_CHUNK]
        urls = dict(db.execute(f"SELECT id, c0 FROM pages_content WHERE id IN ({','.join('?' * len(chunk))})", chunk))
        for rowid in chunk:
            site = domain_of(urls[rowid])
            if len(chosen) < top + keep_limit and sites[site] < KEEP_PER_SITE and keep(urls[rowid]):
                sites[site] += 1
                chosen.append(rowid)
        if len(chosen) >= top + keep_limit:
            break
    rows = {r[0]: r[1:] for r in db.execute(
        f"SELECT rowid, url, title, date, snippet(pages, 3, '', '', '…', 30) FROM pages WHERE pages MATCH ? "
        f"AND rowid IN ({','.join('?' * len(chosen))})", (expr, *chosen))} if chosen else {}
    return [{"url": url, "title": title or url, "snippet": f"{date} · {snip}" if date else snip, "engine": "crawl"}
            for url, title, date, snip in (rows[i] for i in chosen if i in rows)]


def count(word: str, context: list[str] = (), limit: int = 1000) -> int:
    """Crawled pages with this exact word form and every context word (any form), counted up to limit."""
    db = _db()
    if db is None:
        return 0
    expr = " AND ".join(['"' + word.replace('"', '""') + '"', *map(any_form, context)])
    return db.execute("SELECT count(*) FROM (SELECT 1 FROM pages WHERE pages MATCH ? LIMIT ?)", (expr, limit)).fetchone()[0]
