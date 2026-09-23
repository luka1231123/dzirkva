"""Old Georgian web from the Internet Archive: parse archived pages, store them, search them.

Pages come from scripts/archive_collect.py (Wayback Machine raw copies, `id_` URLs) and live in
data/archive.db (SQLite FTS5). Old Georgian sites often used pre-Unicode fonts (AcadNusx, LitNusx):
the file holds Latin letters ("saqarTvelo") that the font draws as Georgian. parse() converts them.
"""

import re
import sqlite3
from functools import cache
from pathlib import Path
from urllib.parse import urljoin, urlparse

import lxml.html

from dzirkva.georgian import from_keyboard, georgian_ratio
from dzirkva.wiki import any_form

DB = Path(__file__).resolve().parents[2] / "data" / "archive.db"
FONT_FONTS = re.compile(r"(?i)acad|nusx|lit ?mtavr|grigol")   # fonts that draw Latin letters as Georgian
SKIP_EXT = re.compile(r"(?i)\.(jpe?g|png|gif|bmp|pdf|docx?|xlsx?|zip|rar|mp3|mp4|avi|swf|exe|css|js)$")
WAYBACK = "https://web.archive.org/web/{}/{}"
SCHEMA = """
CREATE TABLE IF NOT EXISTS queue (url TEXT PRIMARY KEY, host TEXT, depth INT, status TEXT DEFAULT 'todo');
CREATE TABLE IF NOT EXISTS pages (url TEXT PRIMARY KEY, snapshot TEXT, title TEXT, text TEXT,
                                  georgian REAL, converted INT);
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(url UNINDEXED, snapshot UNINDEXED, title, text,
                                                        tokenize='unicode61');
"""


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB, check_same_thread=False)
    db.executescript(SCHEMA)
    return db


def _charset(raw: bytes, header: str) -> str:
    m = re.search(r"charset=([\w-]+)", header) or re.search(rb"charset=[\"']?([\w-]+)", raw[:4000])
    cs = m.group(1) if m else "utf-8"
    return cs.decode() if isinstance(cs, bytes) else cs


def _convert(el, inside: bool, classes: set[str]) -> int:
    """Convert text drawn with a Latin-as-Georgian font. Returns how many characters changed."""
    style = (el.get("face") or "") + (el.get("style") or "") if isinstance(el.tag, str) else ""
    acad = inside or bool(FONT_FONTS.search(style)) or bool(classes & set((el.get("class") or "").split()))
    n = 0
    if acad and el.text:
        el.text, n = from_keyboard(el.text), n + len(el.text)
    for child in el:
        n += _convert(child, acad, classes)
        if acad and child.tail:
            child.tail, n = from_keyboard(child.tail), n + len(child.tail)
    return n


def parse(raw: bytes, header: str, url: str) -> tuple[str, str, list[str], bool]:
    """(title, text, same-site links, converted-from-old-font) for one archived page."""
    try:
        html = raw.decode(_charset(raw, header), errors="replace")
        root = lxml.html.fromstring(html)
    except (LookupError, ValueError, lxml.etree.ParserError):
        return "", "", [], False
    css = " ".join(root.xpath("//style/text()"))
    classes = {c for block in re.findall(r"([^{}]+)\{[^}]*?(?:acad|nusx|mtavr|grigol)[^}]*\}", css, re.I)
               for c in re.findall(r"\.([\w-]+)", block)}
    for bad in root.xpath("//script|//style|//noscript"):
        bad.drop_tree()
    converted = _convert(root, False, classes) > 0
    title = " ".join((root.findtext(".//title") or "").split())
    if converted and title.isascii() and georgian_ratio(from_keyboard(title)) > 0.5:
        title = from_keyboard(title)
    body = root.find(".//body")
    text = " ".join(" ".join((body if body is not None else root).itertext()).split())
    host = urlparse(url).hostname
    links = []
    for href in root.xpath("//a/@href"):
        link = urljoin(url, href.strip()).split("#")[0]
        if urlparse(link).hostname == host and link.startswith("http") and not SKIP_EXT.search(link):
            links.append(link)
    return title, text, links, converted


@cache
def _db() -> sqlite3.Connection | None:
    return connect() if DB.exists() else None


def search(words: list[str], limit: int = 20) -> list[dict]:
    """Archived pages with all words (any form); if too few, with any of them. Links go to the Wayback copy."""
    db = _db()
    if db is None or not words:
        return []
    rows = []
    for op in (" AND ", " OR "):
        expr = op.join(any_form(w) for w in words)
        rows = db.execute(
            "SELECT url, snapshot, title, snippet(pages_fts, 3, '', '', '…', 30) FROM pages_fts "
            "WHERE pages_fts MATCH ? ORDER BY bm25(pages_fts, 0, 0, 5, 1) LIMIT ?", (expr, limit)).fetchall()
        if len(rows) >= 5 or len(words) == 1:
            break
    return [{"url": WAYBACK.format(snap, url), "title": title or url, "snippet": snip, "engine": "archive"}
            for url, snap, title, snip in rows]
