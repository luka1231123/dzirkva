"""Iverieli, the digital library of the National Parliamentary Library (dspace.nplg.gov.ge): its catalog.

601k records (books, journals, newspapers, theses …), mostly page scans without a text layer. This
index has only the metadata from OAI-PMH (title, author, subject, description, year, type), so a
search finds an item by what it is, not by its full text. Filled by scripts/iverieli_collect.py.
"""

import re
import sqlite3
from functools import cache
from pathlib import Path
from xml.etree import ElementTree as ET

from dzirkva.wiki import any_form

DB = Path(__file__).resolve().parents[2] / "data" / "iverieli.db"
BASE = "https://dspace.nplg.gov.ge"
OAI = BASE + "/oai/request"
AGENT = "dzirkva/0.1 (Georgian search engine)"  # generic client names (curl, python-httpx) get 410 Gone
SCHEMA = """
CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY, v TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS items USING fts5(handle UNINDEXED, title, creator, subject, description,
                                                    year UNINDEXED, type UNINDEXED, publisher, tokenize='unicode61');
"""
ISSUE = re.compile(r"\bN\s?\d+[^—]*|\b\d{4}\b")  # issue number and year in a periodical's title
NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "dc": "http://purl.org/dc/elements/1.1/"}


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB, check_same_thread=False)
    db.executescript(SCHEMA)
    return db


@cache
def _db() -> sqlite3.Connection | None:
    return connect() if DB.exists() else None


def parse(xml: bytes) -> tuple[list[tuple], str | None, int]:
    """One ListRecords page → item rows, the next resumption token, the total record count."""
    root = ET.fromstring(xml)
    rows = []
    for rec in root.iterfind(".//oai:record", NS):
        if rec.find("oai:header", NS).get("status") == "deleted":
            continue
        dc = lambda f: [(e.text or "").strip() for e in rec.iterfind(f".//dc:{f}", NS) if (e.text or "").strip()]
        handle = next((re.sub(r"^.*/handle/", "", i) for i in dc("identifier") if "/handle/" in i), None)
        if not handle or any("Image" in t for t in dc("type")):  # single photos and posters: no text to find
            continue
        issued = next((d for d in dc("date") if "T" not in d), "")  # the others are upload times
        rows.append((handle, " — ".join(dc("title")), "; ".join(dc("creator") + dc("contributor")),
                     "; ".join(dc("subject")), " ".join(dc("description")), issued[:4],
                     ", ".join(dc("type")), "; ".join(dc("publisher"))))
    token = root.find(".//oai:resumptionToken", NS)
    total = int(token.get("completeListSize", 0)) if token is not None else 0
    return rows, (token.text if token is not None and token.text else None), total


def search(words: list[str], limit: int = 10) -> list[dict]:
    """Catalog items with all words (any form) in title, author, subject or description; titles weigh most."""
    db = _db()
    if db is None or not words:
        return []
    expr = " AND ".join(any_form(w) for w in words)
    rows = db.execute(
        "SELECT handle, title, creator, description, year, type, publisher FROM items WHERE items MATCH ? "
        "ORDER BY bm25(items, 0, 10, 3, 3, 1, 0, 0, 1) LIMIT ?", (expr, limit * 20)).fetchall()
    out, series = [], set()
    for handle, title, creator, desc, year, typ, publisher in rows:
        key = ISSUE.sub("", title)  # ივერია N1, N2 … are one series: show its best issue only
        if key in series:
            continue
        series.add(key)
        meta = " · ".join(x for x in (typ, year, creator, publisher) if x)
        out.append({"url": f"{BASE}/handle/{handle}", "title": f"{title.replace(' — ', ' / ')} · ივერიელი",
                    "snippet": f"{meta}. {desc[:250]}".strip(" ."), "engine": "iverieli"})
        if len(out) == limit:
            break
    return out


if __name__ == "__main__":
    import sys

    for r in search(sys.argv[1:]):
        print(f"{r['title'][:90]}\n    {r['snippet'][:150]}\n    {r['url']}")
