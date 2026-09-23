"""Georgian research papers: OAI-PMH records of the journals and repositories on Georgian sites (data/papers.db).

Repositories (table repos) are found by scripts/find_repos.py: every Georgian host is asked for an OAI-PMH
endpoint (OJS journals, DSpace, EPrints). scripts/papers_collect.py harvests their Dublin Core records: title,
authors, keywords, abstract, year, type, journal and PDF link. Multilingual journals give a title and an abstract
in each language: the most Georgian one is kept. Records without Georgian are left out.
Iverieli, the National Library's DSpace, has its own index (iverieli.py).
"""

import re
import sqlite3
from functools import cache
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from dzirkva.georgian import georgian_ratio
from dzirkva.wiki import any_form

DB = Path(__file__).resolve().parents[2] / "data" / "papers.db"
AGENT = "dzirkva/0.1 (Georgian search engine)"
SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS repos (base TEXT PRIMARY KEY, ident TEXT, host TEXT, name TEXT, software TEXT,
                                  token TEXT, records INT DEFAULT 0, state TEXT DEFAULT 'new');
CREATE VIRTUAL TABLE IF NOT EXISTS papers USING fts5(oai UNINDEXED, url UNINDEXED, pdf UNINDEXED, title, creator,
    subject, description, source, year UNINDEXED, type UNINDEXED, language UNINDEXED, repo UNINDEXED,
    tokenize='unicode61');
CREATE TABLE IF NOT EXISTS seen (oai TEXT PRIMARY KEY);  -- a record once: OJS installs answer under several names
"""
MIN_GEORGIAN = 0.3   # share of Georgian letters in the title + abstract kept
CONTROL = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")  # not allowed in XML; abstracts pasted from PDFs have them
NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "dc": "http://purl.org/dc/elements/1.1/"}
VERSION = re.compile(r"(?i)version$|^draft$|peer-reviewed|^text$")  # dc:type values that are not the item's type
ISSN = re.compile(r"^\d{4}-\d{3}[\dXx]$")
OJS_GALLEY = re.compile(r"/article/view/\d+/\d+")
TYPE_NAMES = {"article": "სტატია", "doctoralthesis": "დისერტაცია", "thesis": "ნაშრომი", "masterthesis": "სამაგისტრო",
              "bachelorthesis": "საბაკალავრო", "book": "წიგნი", "bookpart": "წიგნის თავი", "review": "რეცენზია",
              "conferenceobject": "კონფერენციის მასალა", "report": "ანგარიში", "lecture": "ლექცია"}


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB, check_same_thread=False, timeout=60)  # the web page reads while scripts write
    db.executescript(SCHEMA)
    return db


@cache
def _db() -> sqlite3.Connection | None:
    return connect() if DB.exists() else None


def _most_georgian(values: list[str]) -> str:
    return max(values, key=georgian_ratio, default="")


def parse(xml: bytes, base: str) -> tuple[list[tuple], str | None, int, str | None]:
    """One ListRecords page → paper rows, the next resumption token, the total record count, the OAI error code."""
    root = ET.fromstring(CONTROL.sub(b"", xml))
    error = root.find("oai:error", NS)
    if error is not None:
        return [], None, 0, error.get("code")
    site = re.sub(r"(/server)?/oai/request$", "", base)  # DSpace: handles of unregistered prefixes do not resolve
    rows = []
    for rec in root.iterfind(".//oai:record", NS):
        header = rec.find("oai:header", NS)
        if header.get("status") == "deleted":
            continue
        dc = lambda f: [(e.text or "").strip() for e in rec.iterfind(f".//dc:{f}", NS) if (e.text or "").strip()]
        title, description = _most_georgian(dc("title")), _most_georgian(dc("description"))
        links = [u for u in dc("identifier") + dc("relation") if u.startswith("http")]
        url = next((u for u in links if not u.lower().endswith(".pdf")), "")
        url = re.sub(r"^https?://hdl\.handle\.net/", site + "/handle/", url)
        if not url or georgian_ratio(f"{title} {description}") < MIN_GEORGIAN:
            continue
        galley = next((u for u in links if OJS_GALLEY.search(u)), "")  # OJS: the file's view page
        pdf = galley.replace("/article/view/", "/article/download/") or next(
            (u for u in links if u.lower().endswith(".pdf")), "")
        types = [t.rsplit("/", 1)[-1] for t in dc("type")]
        year = next((d[:4] for d in dc("date") if "T" not in d and d[:4].isdigit()), "")  # the others are upload times
        rows.append((header.findtext("oai:identifier", "", NS), url, pdf, title, "; ".join(dc("creator")),
                     "; ".join(dc("subject")), description,
                     _most_georgian([s for s in dc("source") if not ISSN.match(s)] or dc("publisher")),
                     year, next((t for t in types if not VERSION.search(t)), ""), ", ".join(dc("language")), base))
    token = root.find(".//oai:resumptionToken", NS)
    total = int(token.get("completeListSize") or 0) if token is not None else 0
    return rows, (token.text.strip() if token is not None and token.text else None), total, None


@cache
def hosts() -> frozenset[str]:
    """Hosts of the repositories (www. removed)."""
    db = _db()
    return frozenset(h.removeprefix("www.") for (h,) in db.execute("SELECT host FROM repos")) if db else frozenset()


def is_repo(url: str) -> bool:
    """A page on a site with an OAI-PMH repository (a journal, a university repository): academic."""
    host = (urlparse(url).hostname or "").removeprefix("www.")
    while host.count("."):
        if host in hosts():
            return True
        host = host.partition(".")[2]
    return False


def type_name(t: str) -> str:
    return TYPE_NAMES.get(t.lower(), t)


def search(words: list[str], limit: int = 10) -> list[dict]:
    """Papers with all words (any form) in title, authors, keywords, abstract or journal; titles weigh most."""
    db = _db()
    if db is None or not words:
        return []
    expr = " AND ".join(any_form(w) for w in words)
    rows = db.execute(
        "SELECT url, title, creator, description, year, type, source FROM papers WHERE papers MATCH ? "
        "ORDER BY bm25(papers, 0, 0, 0, 10, 3, 3, 1, 1) LIMIT ?", (expr, limit)).fetchall()
    out = []
    for url, title, creator, desc, year, typ, source in rows:
        meta = " · ".join(x for x in (type_name(typ), year, creator[:80], source.split(";")[0]) if x)
        out.append({"url": url, "title": title, "snippet": f"{meta}. {desc[:250]}".strip(" ."), "engine": "papers"})
    return out


if __name__ == "__main__":
    import sys

    for r in search(sys.argv[1:]):
        print(f"{r['title'][:90]}\n    {r['snippet'][:150]}\n    {r['url']}")
