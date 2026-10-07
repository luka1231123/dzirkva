"""Georgian research papers: OAI-PMH records of the journals and repositories on Georgian sites (data/papers.db).

Repositories (table repos) are found by scripts/archive/find_repos.py: every Georgian host is asked for an OAI-PMH
endpoint (OJS journals, DSpace, EPrints). scripts/archive/papers_collect.py harvests their Dublin Core records: title,
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
CREATE INDEX IF NOT EXISTS papers_oai ON papers_content(c0);
CREATE TABLE IF NOT EXISTS retired_text(url TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS record_stamps(oai TEXT PRIMARY KEY, stamp TEXT);
CREATE INDEX IF NOT EXISTS papers_url ON papers_content(c1);  -- a paper by its URL (c1 = url): meta()
CREATE VIRTUAL TABLE IF NOT EXISTS fulltext USING fts5(url UNINDEXED, text, tokenize='unicode61');
CREATE TABLE IF NOT EXISTS texts (url TEXT PRIMARY KEY, passages INT);  -- PDFs read by scripts/archive/papers_text.py
"""
MIN_GEORGIAN = 0.3   # share of Georgian letters in the title + abstract kept
CONTROL = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")  # not allowed in XML; abstracts pasted from PDFs have them
NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "dc": "http://purl.org/dc/elements/1.1/"}
VERSION = re.compile(r"(?i)version$|^draft$|peer-reviewed|^text$")  # dc:type values that are not the item's type
ISSN = re.compile(r"^\d{4}-\d{3}[\dXx]$")
OJS_GALLEY = re.compile(r"/article/view/\d+/\d+")
PAGES = re.compile(r"\d+\s*[-–]\s*\d+")
KIND_WORDS = {"დისერტაცია": ("doctoralthesis", "thesis", "masterthesis", "bachelorthesis"),  # query word → types
              "სტატია": ("article",), "მონოგრაფია": ("book",), "წიგნი": ("book", "bookpart"),
              "თეზისი": ("conferenceobject",), "რეცენზია": ("review",)}
TYPE_NAMES = {"article": "სტატია", "doctoralthesis": "დისერტაცია", "thesis": "ნაშრომი", "masterthesis": "სამაგისტრო",
              "bachelorthesis": "საბაკალავრო", "book": "წიგნი", "bookpart": "წიგნის თავი", "review": "რეცენზია",
              "conferenceobject": "კონფერენციის მასალა", "report": "ანგარიში", "lecture": "ლექცია"}


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB, check_same_thread=False, timeout=60)  # the web page reads while scripts write
    db.executescript(SCHEMA)
    additions = {
        "repos": {"last_sync": "TEXT", "sync_from": "TEXT", "sync_started": "TEXT", "next_sync": "TEXT"},
        "texts": {"status": "TEXT", "attempts": "INT DEFAULT 0", "retry_after": "TEXT",
                  "error": "TEXT", "bytes": "INT DEFAULT 0", "updated": "TEXT"},
    }
    for table, fields in additions.items():
        columns = {c[1] for c in db.execute(f"PRAGMA table_info({table})")}
        for name, kind in fields.items():
            if name not in columns:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    db.execute("UPDATE texts SET status = CASE WHEN passages > 0 THEN 'ok' ELSE 'legacy_empty' END "
               "WHERE status IS NULL")
    db.commit()
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
        if header is None or header.get("status") == "deleted":
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


def page_changes(xml: bytes) -> tuple[list[str], list[str]]:
    """All identifiers observed and explicit tombstones (including non-Georgian changed records)."""
    root = ET.fromstring(CONTROL.sub(b"", xml))
    observed, deleted = [], []
    for header in root.iterfind(".//oai:record/oai:header", NS):
        ident = header.findtext("oai:identifier", "", NS)
        if ident:
            observed.append(ident)
            if header.get("status") == "deleted":
                deleted.append(ident)
    return observed, deleted


def page_stamps(xml: bytes) -> dict[str, str]:
    root = ET.fromstring(CONTROL.sub(b"", xml))
    return {header.findtext("oai:identifier", "", NS): header.findtext("oai:datestamp", "", NS)
            for header in root.iterfind(".//oai:record/oai:header", NS)}


def discard(db: sqlite3.Connection, identifiers: list[str]) -> list[str]:
    """Remove obsolete metadata and extracted text; return URLs whose passages must be retired."""
    urls = []
    for ident in identifiers:
        rows = db.execute("SELECT rowid, c1 FROM papers_content WHERE c0 = ?", (ident,)).fetchall()
        urls.extend(url for _, url in rows)
        db.executemany("DELETE FROM papers WHERE rowid = ?", [(rid,) for rid, _ in rows])
        db.execute("DELETE FROM record_stamps WHERE oai = ?", (ident,))
        db.execute("DELETE FROM seen WHERE oai = ?", (ident,))
    for url in urls:
        db.execute("DELETE FROM texts WHERE url = ?", (url,))
        db.execute("DELETE FROM fulltext WHERE url = ?", (url,))
    return urls


def drain_retired(db: sqlite3.Connection) -> None:
    """Finish pending cross-database deletes before harvesting or replacing PDF text."""
    from dzirkva import passages

    if not passages.DB.exists():
        return
    rows = db.execute("SELECT url FROM retired_text")
    pdb = None
    try:
        while batch := rows.fetchmany(100):
            if pdb is None:
                pdb = passages.connect()
            pdb.executemany("DELETE FROM passages WHERE site='papers' AND url=?", batch)
            pdb.commit()
            db.executemany("DELETE FROM retired_text WHERE url=?", batch)
        db.commit()
    finally:
        if pdb is not None:
            pdb.close()


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


def meta(url: str) -> dict | None:
    """The record of a paper page (for the paper layout and its citation), else None."""
    db = _db()
    if db is None:
        return None
    urls = [url, re.sub(r"^https:", "http:", url), re.sub(r"^http:", "https:", url)]
    row = db.execute("SELECT c1, c2, c3, c4, c6, c7, c8, c9 FROM papers_content WHERE c1 IN (?, ?, ?)", urls).fetchone()
    return dict(zip(("url", "pdf", "title", "creator", "description", "source", "year", "type"), row)) if row else None


def authors(creator: str) -> list[str]:
    """Author names; journals list each name in two scripts (ჩიქოვანი, გურამ; Chikovani, Guram): Georgian ones only."""
    names = [a.strip() for a in creator.split(";") if a.strip()]
    return [a for a in names if georgian_ratio(a) > 0.5] or names


def journal(source: str) -> str:
    """Journal, issue, pages: "ქრონოსი; Vol. 1 (2020): ქრონოსი; 270-279" → "ქრონოსი, Vol. 1 (2020), 270-279"."""
    parts = [p.strip() for p in source.split(";")]
    out = parts[:1] + [p.split(":")[0].strip() for p in parts[1:2]] + [p for p in parts[2:3] if PAGES.fullmatch(p)]
    return ", ".join(x for x in out if x)


def citation(m: dict) -> str:
    """A citation line in APA style: Authors (year). Title. Journal. URL"""
    short = lambda a: f"{a.partition(',')[0].strip()}, {a.partition(',')[2].strip()[:1]}." if "," in a else a
    names = [short(a) for a in authors(m["creator"])]
    who = ", ".join(names[:-1]) + " & " + names[-1] if len(names) > 1 else "".join(names)
    return ". ".join(x for x in (f"{who} ({m['year'] or 'უ. თ.'})", m["title"].rstrip("."),
                                 journal(m["source"]), m["url"]) if x)


def search(words: list[str], limit: int = 10, kinds: set[str] = frozenset()) -> list[dict]:
    """Fuse metadata and complete PDF text matches; titles weigh most within metadata search.
    kinds: words of KIND_WORDS in the query (დისერტაცია): papers of those types come first."""
    db = _db()
    if db is None or not words or limit <= 0:
        return []
    expr = " AND ".join(any_form(w) for w in words)
    types = sorted({t for k in kinds for t in KIND_WORDS.get(k, ())})
    first = f"lower(type) IN ({','.join('?' * len(types))}) DESC, " if types else ""
    rows = db.execute(
        "SELECT url, title, creator, description, year, type, source FROM papers WHERE papers MATCH ? "
        f"ORDER BY {first}bm25(papers, 0, 0, 0, 10, 3, 3, 1, 1) LIMIT ?", (expr, *types, limit)).fetchall()
    text_first = f"lower(p.c9) IN ({','.join('?' * len(types))}) DESC, " if types else ""
    text_rows = db.execute(
        "SELECT p.c1, p.c3, p.c4, snippet(fulltext, 1, '', '', ' … ', 45), p.c8, p.c9, p.c7 "
        "FROM fulltext JOIN papers_content p ON p.rowid=(SELECT rowid FROM papers_content "
        "WHERE c1=fulltext.url LIMIT 1) WHERE fulltext MATCH ? "
        f"ORDER BY {text_first}bm25(fulltext) LIMIT ?", (expr, *types, limit)).fetchall()
    # Separate ranked lists need fusion before trimming: metadata must not crowd out PDF-only matches.
    scores, candidates = {}, {}
    for ranked in (rows, text_rows):
        seen = set()
        rank = 0
        for row in ranked:
            url = row[0]
            if url in seen:
                continue
            seen.add(url)
            rank += 1
            scores[url] = scores.get(url, 0) + 1 / (60 + rank)
            candidates[url] = row  # PDF matches overwrite abstract snippets with the matching body passage.
    rows = sorted(candidates.values(), key=lambda row: (
        (row[5] or "").lower() in types, scores[row[0]]), reverse=True)
    out = []
    for url, title, creator, desc, year, typ, source in rows[:limit]:
        meta = " · ".join(x for x in (type_name(typ), year, creator[:80], journal(source)) if x)
        out.append({"url": url, "title": title, "snippet": f"{meta}. {desc[:250]}".strip(" ."), "engine": "papers"})
    return out


if __name__ == "__main__":
    import sys

    for r in search(sys.argv[1:]):
        print(f"{r['title'][:90]}\n    {r['snippet'][:150]}\n    {r['url']}")
