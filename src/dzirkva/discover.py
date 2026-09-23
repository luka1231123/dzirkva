"""Web surfing without a query: a profile of each site (/site) and the discover page (/discover).

Everything comes from our own data: the crawl (pages with dates, domains with kind and signals, links between
sites), small-web voice scores, the old web (archive.db), Wikipedia citations and the papers index.
Similar sites: sites linked from the same sites (co-citation) and sites that link to the same sites (coupling);
each shared neighbor counts, divided by √ links of the candidate, so hubs (netgazeti.ge) do not win everywhere.
People: personal sites (crawl.small_site) and one-person blogs (blogspot, wordpress.com) that write Georgian.
"""

import math
import random
import re
import time
from collections import Counter, defaultdict
from functools import cache
from urllib.parse import urlparse

from dzirkva import archive, crawl, papers, wiki
from dzirkva.georgian import georgian_ratio
from dzirkva.sources import by_category, host as host_of, lookup, site_names, sources

NEWEST = 10          # newest pages on a site profile
LINKS = 15           # linked sites shown per direction
SIMILAR = 10
MIN_SHARED = 2       # a similar site shares at least this many neighbors
POSTS = 12           # newest posts on /discover, one per site
DATE_FLOOR = "2000"  # older page dates are parse errors
# Page dates are guesses: a listing page gets the crawl day, a year alone becomes January 1. A post is a page that
# is not a listing, and its date agrees with the date in its URL (/2012/05/ on WordPress and Blogger).
LISTING = re.compile(r"/(category|tag|tags|author|page|label|search|feed|comments)/|/\d{4}/(\d{2}/?)?$|[?#]")
URL_DATE = re.compile(r"/(\d{4})/(\d{2})/")
CATEGORY_NAMES = {"reference": "ცნობარი", "science": "მეცნიერება", "history": "ისტორია", "religion": "რელიგია",
                  "culture": "კულტურა", "education": "განათლება", "law": "სამართალი", "government": "სახელმწიფო",
                  "news": "სიახლეები", "investigation": "გამოძიება", "economy": "ეკონომიკა", "community": "ფორუმები",
                  "sport": "სპორტი", "services": "სერვისები"}


def _prefixes(host: str) -> list[str]:
    return [f"{s}://{w}{host}/" for s in ("https", "http") for w in ("", "www.")]


def _range(db, table: str, column: str, host: str, columns: str) -> list[tuple]:
    """Rows whose URL starts with the site (any scheme, with or without www); the URL index serves the range."""
    rows = []
    for p in _prefixes(host):
        rows += db.execute(f"SELECT {columns} FROM {table} WHERE {column} >= ? AND {column} < ?",
                           (p, p[:-1] + "0")).fetchall()  # "0" follows "/"
    return rows


@cache
def _names() -> dict[str, str]:
    """host → the site's name (Wikidata, sites.tsv), Georgian name first."""
    out: dict[str, str] = {}
    for name, url in site_names().items():
        h = host_of(url)
        if h not in out or georgian_ratio(name) > georgian_ratio(out[h]):
            out[h] = name
    return out


@cache
def _graph() -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, tuple]]:
    """Links between sites (out, in) and the accepted Georgian domains: loaded once per process."""
    db = crawl._db()
    out, into = defaultdict(set), defaultdict(set)
    if db is None:
        return out, into, {}
    for src, dst in db.execute("SELECT src, dst FROM links"):
        out[src].add(dst)
        into[dst].add(src)
    full = {h: row for h, *row in db.execute(
        "SELECT host, pages, georgian, kind, signals, commercial FROM domains WHERE state = 'full' AND georgian >= ?",
        (crawl.MIN_GEORGIAN,))}
    return out, into, full


def similar(key: str) -> list[str]:
    out, into, full = _graph()
    score: Counter[str] = Counter()
    for s in into[key]:
        score.update(out[s])   # co-citation: linked from the same sites
    for d in out[key]:
        score.update(into[d])  # coupling: they link to the same sites
    ranked = {h: n / math.sqrt(len(out[h]) + len(into[h])) for h, n in score.items()
              if h != key and h in full and n >= MIN_SHARED}
    return sorted(ranked, key=ranked.get, reverse=True)[:SIMILAR]


@cache
def people() -> list[str]:
    """Personal sites and one-person blogs, most pages first."""
    _, _, full = _graph()
    return sorted((h for h, (pages, _, _, signals, commercial) in full.items()
                   if crawl.small_site(f"https://{h}/") or ("blog-host" in signals and commercial < crawl.COMMERCIAL)),
                  key=lambda h: -full[h][0])


def site(host: str) -> dict:
    """Everything known about one site. host: as typed (www. removed)."""
    host = host.lower().strip().removeprefix("www.")
    url = f"https://{host}/"
    key = crawl.domain_of(url)
    out, into, full = _graph()
    db = crawl._db()
    row = db.execute("SELECT source, state, pages, georgian, kind, inbound FROM domains WHERE host = ?",
                     (key,)).fetchone() if db else None
    pages = sorted(_range(db, "pages_content", "c0", host, "c0, c1, c2"), key=lambda r: r[2] or "",
                   reverse=True) if db else []
    wdb = wiki._db()
    cited = wdb.execute("SELECT title, count(*) FROM cites WHERE host = ? GROUP BY title ORDER BY 2 DESC",
                        (key,)).fetchall() if wdb else []
    adb = archive._db()
    old = sorted(_range(adb, "pages", "url", host, "url, snapshot, title"), key=lambda r: r[1]) if adb else []
    pdb = papers._db()
    repos = pdb.execute("SELECT base, name, records FROM repos WHERE host = ? OR host LIKE ?",
                        (host, f"%.{host}")).fetchall() if pdb else []
    by_links = lambda hs: sorted((h for h in hs if h in full), key=lambda h: -len(into[h]))[:LINKS]
    return {"host": host, "key": key, "name": _names().get(host) or _names().get(key, ""),
            "trusted": lookup(url), "small": crawl.small_site(url), "domain": row,
            "pages": pages[:NEWEST], "page_count": len(pages), "cited": cited[:8], "cited_count": sum(n for _, n in cited),
            "old": old[:8], "old_count": len(old), "repos": repos,
            "links_out": by_links(out[key]), "links_in": by_links(into[key]), "similar": similar(key),
            "in_count": len(into[key]), "out_count": len(out[key])}


def is_post(url: str, date: str) -> bool:
    """A dated post, not a listing page or a year-only date (see LISTING)."""
    if not date or date.endswith("-01-01") or LISTING.search(url) or urlparse(url).path in ("", "/"):
        return False
    m = URL_DATE.search(url)
    return not m or date.startswith(f"{m[1]}-{m[2]}")


def newest_posts(limit: int = POSTS) -> list[tuple[str, str, str]]:
    """(url, title, date) of the newest posts of people's sites, one per site. Read again each hour (~1 s)."""
    return _newest_posts(limit, time.strftime("%Y-%m-%d %H"))


@cache
def _newest_posts(limit: int, hour: str) -> list[tuple[str, str, str]]:
    db = crawl._db()
    if db is None:
        return []
    mine = set(people())
    today = time.strftime("%Y-%m-%d")
    out, seen = [], set()
    for url, title, date in db.execute("SELECT c0, c1, c2 FROM pages_content WHERE c2 BETWEEN ? AND ? "
                                       "ORDER BY c2 DESC", (DATE_FLOOR, today)):  # read until enough
        h = crawl.domain_of(url)
        if h in mine and h not in seen and title and is_post(url, date):
            seen.add(h)
            out.append((url, title, date))
            if len(out) == limit:
                break
    return out


def old_find(rng: random.Random) -> tuple[str, str, str] | None:
    """(Wayback URL, title, year) of a Georgian page from the old web."""
    db = archive._db()
    n = db.execute("SELECT max(rowid) FROM pages").fetchone()[0] if db else None
    for _ in range(20):
        row = n and db.execute("SELECT url, snapshot, title FROM pages WHERE rowid = ? AND georgian >= 0.5 "
                               "AND length(text) >= 1000 AND title != ''", (rng.randint(1, n),)).fetchone()
        if row:
            return archive.WAYBACK.format(row[1], row[0]), row[2], row[1][:4]
    return None


def new_papers(limit: int = 8) -> list[tuple]:
    """(url, title, authors, year, journal) of the newest papers."""
    db = papers._db()
    if db is None:
        return []
    return db.execute("SELECT url, title, creator, year, source FROM papers WHERE year <= ? "
                      "ORDER BY year DESC, rowid DESC LIMIT ?", (time.strftime("%Y"), limit)).fetchall()


def shelves() -> list[tuple[str, list[str]]]:
    """(shelf name, hosts): the trusted sites by category, then people's sites and research repositories."""
    out = [(CATEGORY_NAMES.get(c, c), by_category(c)) for c in dict.fromkeys(c for c, _ in sources().values())]
    out.append(("პატარა ვები", people()[:40]))
    pdb = papers._db()
    if pdb:
        out.append(("სამეცნიერო ჟურნალები და რეპოზიტორიები",
                    [h for (h,) in pdb.execute("SELECT host FROM repos ORDER BY records DESC")]))
    return out


def random_site(rng: random.Random | None = None) -> str | None:
    hosts = people()
    return (rng or random).choice(hosts) if hosts else None
