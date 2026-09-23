"""Trusted Georgian sources from config/sources.yaml: domain -> (category, tier)."""

from functools import cache
from pathlib import Path
import re
from urllib.parse import urlparse

import yaml

SOURCES_FILE = Path(__file__).resolve().parents[2] / "config" / "sources.yaml"


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
# One result can have several filter tags; the page combines chosen filters with AND.
FILTERS = ("knowledge", "texts", "people", "academic", "small", "old")
BLOG_HOSTS = {"blogspot.com", "wordpress.com", "medium.com", "livejournal.com", "tumblr.com", "substack.com"}
TEXT_HOSTS = {"ka.wikisource.org", "lib.ge", "poetry.ge", "geolit.ge"}
TEXT_TITLE = re.compile(r"ლექს(?!იკ)|ტექსტ|სიმღერ|ნოტებ|ლოცვ|პოემ|მოთხრობ|წიგნ|lyrics|\.pdf\b", re.I)
ACADEMIC_URL = re.compile(r"(?i)\.edu(\.ge)?$|\.ac\.ge$|^dspace\.|^journals?\.|/handle/\d|/article/view/")
WAYBACK = re.compile(r"^https?://web\.archive\.org/web/[^/]+/")


def tags(url: str, title: str, signals: set[str], small: bool) -> set[str]:
    """Filter tags of one result. `signals` come from the crawl (crawl.domain_signals)."""
    out = {"old"} if WAYBACK.match(url) else set()
    url = WAYBACK.sub("", url)
    host = (urlparse(url).hostname or "").removeprefix("www.")
    base = ".".join(host.split(".")[-2:])
    k = kind(url)
    category, _ = lookup(url) or (None, None)
    if k == "knowledge":
        out.add("knowledge")
    if host in TEXT_HOSTS or TEXT_TITLE.search(title) or url.lower().endswith(".pdf"):
        out.add("texts")
    if k in ("forum", "social") or base in BLOG_HOSTS or "blog-host" in signals:
        out.add("people")
    if category == "science" or "academic" in signals or ACADEMIC_URL.search(host + urlparse(url).path):
        out |= {"academic", "knowledge"}
    if small:
        out.add("small")
    return out
