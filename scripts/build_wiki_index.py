"""Local full-text index of Georgian Wikipedia: data/wiki.db (SQLite FTS5).

Used for spelling in context (how many articles contain both words?) and later as a local engine.
Run: uv run python scripts/build_wiki_index.py   (needs data/kawiki.xml.bz2)
"""

import bz2
import re
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

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


def plain(text: str) -> str:
    for rx, rep in MARKUP:
        text = rx.sub(rep, text)
    return " ".join(text.split())


db = sqlite3.connect(DATA / "wiki.db")
db.executescript("DROP TABLE IF EXISTS wiki; CREATE VIRTUAL TABLE wiki USING fts5(title, body, tokenize='unicode61');")
n = 0
with bz2.open(DATA / "kawiki.xml.bz2") as f:
    for _, el in ET.iterparse(f):
        if el.tag != NS + "page":
            continue
        if el.findtext(NS + "ns") == "0" and el.find(NS + "redirect") is None:
            body = plain(el.findtext(f"{NS}revision/{NS}text") or "")
            if len(body) > 200:
                db.execute("INSERT INTO wiki VALUES (?, ?)", (el.findtext(NS + "title"), body))
                n += 1
                if n % 20000 == 0:
                    print(f"{n:,} articles", flush=True)
        el.clear()
db.commit()
db.execute("INSERT INTO wiki(wiki) VALUES ('optimize')")
db.commit()
print(f"done: {n:,} articles")
