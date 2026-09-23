"""Sources: trusted Georgian sites (config/sources.yaml: domain -> (category, tier)), sites a query names
(Wikidata names, Georgian site names from Common Crawl), result kinds and filter tags."""

from functools import cache
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlparse

import yaml

from dzirkva.georgian import to_latin

SOURCES_FILE = Path(__file__).resolve().parents[2] / "config" / "sources.yaml"
DATA = Path(__file__).resolve().parents[2] / "data"
SITES_FILE = DATA / "sites.tsv"      # name → official site, from Wikidata (scripts/build_sites.py)
HOSTS_DB = DATA / "cc_hosts.db"      # sites with Georgian pages in Common Crawl (scripts/cc_hosts.py)
MIN_HOST_PAGES = 5                   # a site name counts when Common Crawl has this many of its pages
MAX_NAME_WORDS = 5
MAX_NAMED = 2
WORLD_SITE = 100                     # Wikipedia language versions of a world-known site (facebook.com: 263)


@cache
def sources() -> dict[str, tuple[str, int]]:
    data = yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8"))
    return {domain: (category, tier) for category, sites in data.items() for domain, tier in sites.items()}


def lookup(url: str) -> tuple[str, int] | None:
    """(category, tier) for a URL on a trusted domain or its subdomain, else None."""
    host = (urlparse(url).hostname or "").removeprefix("www.")
    while host:
        if host in sources():
            return sources()[host]
        host = host.partition(".")[2]
    return None


def by_category(category: str) -> list[str]:
    """Domains of one category, best tier first."""
    return sorted((d for d, (c, _) in sources().items() if c == category), key=lambda d: sources()[d][1])


# ---- sites a query names ------------------------------------------------------

def host(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


@cache
def site_names() -> dict[str, str]:
    """Name (Georgian or English, lower case) → official site URL, for sites Georgians use: Georgian sites
    (.ge or Georgian pages in Common Crawl) and world sites. პოლიცია is also a band's name: not its site."""
    if not SITES_FILE.exists():
        return {}
    known = {h for h, pages, _ in _hosts() if pages >= MIN_HOST_PAGES}
    with open(SITES_FILE, encoding="utf-8") as f:
        rows = [line.rstrip("\n").split("\t") for line in f]
    return {name: url for name, url, links in rows
            if host(url).endswith(".ge") or host(url) in known or int(links) >= WORLD_SITE}


@cache
def _hosts() -> list[tuple[str, int, int]]:
    """(host, pages, Georgian pages) of the sites with Georgian pages in Common Crawl."""
    if not HOSTS_DB.exists():
        return []
    with sqlite3.connect(HOSTS_DB) as db:
        return db.execute("SELECT host, sum(pages), sum(main) FROM hosts GROUP BY host").fetchall()


@cache
def georgian_hosts() -> frozenset[str]:
    """Sites that write mostly Georgian: their pages count as Georgian even with a Latin title."""
    return frozenset(h for h, pages, main in _hosts() if pages >= MIN_HOST_PAGES and 2 * main >= pages)


@cache
def host_names() -> dict[str, str]:
    """Site name → host: the full host (ss.ge) and its first label (bolt → bolt.eu), the site with most pages wins."""
    out: dict[str, tuple[str, int]] = {}
    for h, pages, _ in _hosts():
        if pages >= MIN_HOST_PAGES:
            for name in (h, h.split(".")[0]):
                if pages > out.get(name, ("", 0))[1]:
                    out[name] = (h, pages)
    return {name: h for name, (h, _) in out.items()}


def named_sites(tokens: list[str], words: list[str], lemmas: list[str]) -> list[tuple[str, float, str]]:
    """Sites a query names: (host, share of the query words the name covers, name), surest first, max MAX_NAMED.

    A name from Wikidata (ფეისბუქი, თბილისის აეროპორტი, magticom; longest first) or a site name typed in Latin
    (myhome, "ss ge", bolt) is sure. A Georgian word that is a site name in Latin letters (ბოლტი → bolt.eu) is
    a guess (ამინდი → amindi.in): share 0.
    tokens: the query as typed; words: its content words read in Georgian; lemmas: their dictionary forms."""
    found: list[tuple[str, float, str]] = []
    for seq in (words, lemmas):
        for n in range(min(MAX_NAME_WORDS, len(seq)), 0, -1):
            for i in range(len(seq) - n + 1):
                if url := site_names().get(name := " ".join(seq[i:i + n])):
                    found.append((host(url), n / len(seq), name))
    latin = [t.lower().strip(".") for t in tokens if t.isascii()]
    names = [a + "." + b for a, b in zip(latin, latin[1:])] + [t for t in latin if len(t) > 2]
    found += [(host_names()[t], len(t.split(".")) / len(tokens), t) for t in names if t in host_names()]
    found += [(host_names()[t], 0.0, w) for w in words if len(w) > 3
              for t in (to_latin(w), to_latin(w).removesuffix("i")) if t in host_names()]
    return list({h: (h, share, name) for h, share, name in reversed(found)}.values())[::-1][:MAX_NAMED]


# ---- result kinds (tabs) ----------------------------------------------------
# Every result stays; its kind decides the tab and the block it appears in.
VIDEO_HOSTS = {"youtube.com", "youtu.be", "tiktok.com", "vimeo.com", "myvideo.ge", "dailymotion.com", "palitravideo.ge"}
SOCIAL_HOSTS = {"facebook.com", "ok.ru", "instagram.com", "x.com", "twitter.com", "vk.com", "t.me", "threads.net",
                "linkedin.com", "reddit.com", "pinterest.com"}
FILM_HOST = re.compile(r"film|movie|kino|kadri|imovie|adjaranet|saitebi|serial|anime|cinema")
KNOWLEDGE = {"reference", "science", "history", "religion", "culture", "education", "law", "government"}
KINDS = ("knowledge", "news", "web", "forum", "archive", "video", "film", "social")


def kind(url: str) -> str:
    host = (urlparse(url).hostname or "").removeprefix("www.").removeprefix("m.")
    base = ".".join(host.split(".")[-2:])
    category, _ = lookup(url) or (None, None)
    if host == "web.archive.org":
        return "archive"
    if host in VIDEO_HOSTS or base in VIDEO_HOSTS:
        return "video"
    if host in SOCIAL_HOSTS or base in SOCIAL_HOSTS:
        return "social"
    if category in ("news", "investigation", "economy"):
        return "news"
    if category == "community":
        return "forum"
    if category in KNOWLEDGE or base == "wikipedia.org":
        return "knowledge"
    if FILM_HOST.search(host):
        return "film"
    return "web"


# ---- filters (source type) ----------------------------------------------
# A tab changes the layout (kind above); a filter keeps the list and narrows the sources.
# One result can have several filter tags; the page shows one filter at a time.
FILTERS = ("knowledge", "texts", "people", "small", "academic", "old")
BLOG_HOSTS = {"blogspot.com", "wordpress.com", "medium.com", "livejournal.com", "tumblr.com", "substack.com"}
TEXT_HOSTS = {"ka.wikisource.org", "lib.ge", "poetry.ge", "geolit.ge", "scribd.com"}  # scribd: user documents (PDF)
TEXT_TITLE = re.compile(r"ლექს(?!იკ)|ტექსტ|სიმღერ|ნოტებ|ლოცვ|პოემ|მოთხრობ|წიგნ|lyrics|\bpdf\b", re.I)  # scribd titles end "| PDF"
ACADEMIC_URL = re.compile(r"(?i)\.edu(\.ge)?/|\.ac\.ge/|^dspace\.|^journals?\.|/handle/\d|/article/view/|/id/eprint/")
WAYBACK = re.compile(r"^https?://web\.archive\.org/web/[^/]+/")


def tags(url: str, title: str, signals: set[str], small: bool = False, repo: bool = False) -> set[str]:
    """Filter tags of one result. `signals` come from the crawl (crawl.domain_signals), `small` from crawl.small_site,
    `repo` from papers.is_repo (the site has an OAI-PMH repository). Academic needs evidence on the page's own
    site: words and links in its pages do not count (a link to dspace.nplg.gov.ge made netgazeti.ge academic)."""
    out = {"old"} if WAYBACK.match(url) else set()
    if small:
        out.add("small")
    url = WAYBACK.sub("", url)
    host = (urlparse(url).hostname or "").removeprefix("www.")
    base = ".".join(host.split(".")[-2:])
    k = kind(url)
    category, _ = lookup(url) or (None, None)
    if k == "knowledge":
        out.add("knowledge")
    if host in TEXT_HOSTS or base in TEXT_HOSTS or TEXT_TITLE.search(title) or url.lower().endswith(".pdf"):
        out.add("texts")
    if k in ("forum", "social") or base in BLOG_HOSTS or "blog-host" in signals:
        out.add("people")
    if category == "science" or repo or ACADEMIC_URL.search(host + (urlparse(url).path or "/")):
        out |= {"academic", "knowledge"}
    return out
