"""Local full-text index of Georgian Wikipedia: data/wiki.db (SQLite FTS5).

Used for spelling in context (how many articles contain both words?) and as a local engine.
Table `cites`: the URLs each article cites (references, external links; Wayback links → original URL).
Run: uv run python scripts/build_wiki_index.py   (needs data/kawiki.xml.bz2)
Wikisource: uv run python scripts/build_wiki_index.py wikisource   (data/kawikisource.xml.bz2 → data/wikisource.db)
"""

import bz2
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from dzirkva.crawl import domain_of

DATA = Path(__file__).resolve().parent.parent / "data"
NS = "{http://www.mediawiki.org/xml/export-0.11/}"
MARKUP = [
    (re.compile(r"<ref[^>]*/>|<ref.*?</ref>", re.S), " "),
    (re.compile(r"\{\{[^{}]*\}\}"), " "),              # templates (inner first; run twice)
    (re.compile(r"\{\{[^{}]*\}\}"), " "),
    (re.compile(r"\[\[(?:ფაილი|File|სურათი|Image|კატეგორია|Category):[^\]]*\]\]"), " "),
    (re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]"), r"\1"),  # [[target|text]] -> text
    (re.compile(r"\[https?://\S+ ?([^\]]*)\]"), r"\1"),
    (re.compile(r"<[^>]+>|'''?|={2,}|\{\||\|\}|^[|!].*$", re.M), " "),
]
URL = re.compile(r"https?://[^\s\[\]|<>\"'{}]+")
WAYBACK = re.compile(r"https?://web\.archive\.org/web/\d+[a-z_]*/")


def plain(text: str) -> str:
    for rx, rep in MARKUP:
        text = rx.sub(rep, text)
    return " ".join(text.split())


SOURCE = sys.argv[1] if len(sys.argv) > 1 else "wiki"
DUMP, OUT = {"wiki": ("kawiki.xml.bz2", "wiki.db"), "wikisource": ("kawikisource.xml.bz2", "wikisource.db")}[SOURCE]
db = sqlite3.connect(DATA / OUT)
db.executescript("DROP TABLE IF EXISTS wiki; CREATE VIRTUAL TABLE wiki USING fts5(title, body, tokenize='unicode61');"
                 "DROP TABLE IF EXISTS cites; CREATE TABLE cites (title TEXT, url TEXT, host TEXT);")
n = 0
with bz2.open(DATA / DUMP) as f:
    for _, el in ET.iterparse(f):
        if el.tag != NS + "page":
            continue
        if el.findtext(NS + "ns") == "0" and el.find(NS + "redirect") is None:
            raw = el.findtext(f"{NS}revision/{NS}text") or ""
            body, title = plain(raw), el.findtext(NS + "title")
            if len(body) > 200:
                db.execute("INSERT INTO wiki VALUES (?, ?)", (title, body))
                urls = dict.fromkeys(u.rstrip(".,;)") for u in URL.findall(WAYBACK.sub("", raw)))
                db.executemany("INSERT INTO cites VALUES (?, ?, ?)", [(title, u, domain_of(u)) for u in urls])
                n += 1
                if n % 20000 == 0:
                    print(f"{n:,} articles", flush=True)
        el.clear()
db.executescript("CREATE INDEX cites_title ON cites(title); CREATE INDEX cites_host ON cites(host);")
db.commit()
db.execute("INSERT INTO wiki(wiki) VALUES ('optimize')")
db.commit()
print(f"done: {n:,} articles")
