"""Georgian explanatory dictionary from ka.wiktionary: data/dictionary.db (word -> senses, synonyms).

Run: uv run python scripts/build_dictionary.py   (needs data/kawiktionary.xml.bz2 from
https://dumps.wikimedia.org/kawiktionary/latest/kawiktionary-latest-pages-articles.xml.bz2, ~5 MB)
"""

import bz2
import json
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

from dzirkva.dictionary import DB, parse
from dzirkva.georgian import normalize

DATA = Path(__file__).resolve().parent.parent / "data"
NS = "{http://www.mediawiki.org/xml/export-0.11/}"

db = sqlite3.connect(DB)
db.executescript("DROP TABLE IF EXISTS words; CREATE TABLE words (word TEXT PRIMARY KEY, entry TEXT);")
pages = n = 0
with bz2.open(DATA / "kawiktionary.xml.bz2") as f:
    for _, el in ET.iterparse(f):
        if el.tag != NS + "page":
            continue
        if el.findtext(NS + "ns") == "0" and el.find(NS + "redirect") is None:
            pages += 1
            entry = parse(el.findtext(f"{NS}revision/{NS}text") or "")
            if entry:
                db.execute("INSERT OR REPLACE INTO words VALUES (?, ?)",
                           (normalize(el.findtext(NS + "title")), json.dumps(entry, ensure_ascii=False)))
                n += 1
        el.clear()
db.commit()
print(f"{n} words with Georgian senses from {pages} pages -> {DB}")
