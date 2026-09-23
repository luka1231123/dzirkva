"""Text of small Iverieli PDFs → passages (data/passages.db, site 'iverieli'). Vectors: scripts/build_passages.py.

A small PDF (≤ MAX_MB) is a Word export with a text layer; a big one is page scans, so it is not downloaded.
The item page lists each file's size. Some PDFs use the pre-Unicode AcadNusx font (Latin letters for Georgian):
a page whose Latin words are mostly real Georgian words after georgian.from_keyboard is converted.
Needs pdftotext (brew install poppler). Resumable: done items are in iverieli.db table 'texts'.
Run in background: nohup uv run python scripts/iverieli_text.py > data/iverieli_text.log 2>&1 &
"""

import re
import subprocess
import sys
import time

import httpx

from dzirkva import iverieli, passages
from dzirkva.georgian import count, from_keyboard, georgian_ratio

MAX_MB = 5
MAX_PASSAGES = 20   # per item: the first pages (summary, introduction); a whole book would swamp the index
MIN_RATIO = 0.5     # a page with less Georgian (English summary, tables) is skipped
PAUSE = 0.5         # seconds between requests: one library server, be polite
TYPES = ("Thesis", "Article", "Book", "Journal", "Newspaper", "Other", "")  # in this order; before 1990 all scans
UNIT = {"bytes": 1e-6, "kB": 1e-3, "MB": 1, "GB": 1e3}
ROW = re.compile(r'href="(/bitstream/[^"]+\.pdf)".*?headers="t3"[^>]*>([\d.,]+) (bytes|kB|MB|GB)<', re.I)
LATIN_WORD = re.compile(r"[A-Za-z]{3,}")
LEADER = re.compile(r"(\.\s?){4,}|…{2,}")  # table of contents: "თავი I ........ 28"


def small_pdfs(html: str) -> list[str]:
    """Links of the PDFs on an item page not bigger than MAX_MB."""
    return [href for row in html.split("<tr>") if (m := ROW.search(row))
            for href, size, unit in [m.groups()] if float(size.replace(",", "")) * UNIT[unit] <= MAX_MB]


def fix_page(page: str) -> str:
    """AcadNusx page → Unicode Georgian; Unicode pages stay as they are."""
    latin = LATIN_WORD.findall(page)
    if latin and sum(count(from_keyboard(w)) > 0 for w in latin) > len(latin) / 2:
        page = from_keyboard(page)
    return page


def pdf_text(pdf: bytes) -> str:
    """Georgian text of a PDF: pages joined, hyphenated line ends and line breaks removed."""
    out = subprocess.run(["pdftotext", "-enc", "UTF-8", "-", "-"], input=pdf, capture_output=True).stdout
    pages = [fix_page(p) for p in out.decode("utf-8", "replace").split("\f")]
    text = " ".join(p for p in pages if georgian_ratio(p) >= MIN_RATIO)
    return re.sub(r"\s+", " ", re.sub(r"-\n(?=\w)", "", text)).strip()


db = iverieli.connect()
db.execute("CREATE TABLE IF NOT EXISTS texts (handle TEXT PRIMARY KEY, passages INTEGER)")
pdb = passages.connect()
limit = int(sys.argv[1]) if len(sys.argv) > 1 else None  # a trial run: only the first N items
order = " ".join(f"WHEN '{t}' THEN {i}" for i, t in enumerate(TYPES))
todo = db.execute(f"SELECT handle, title FROM items WHERE type IN ({','.join('?' * len(TYPES))}) AND year >= '1990' "
                  f"AND handle NOT IN (SELECT handle FROM texts) ORDER BY CASE type {order} END, year DESC",
                  TYPES).fetchall()[:limit]
print(f"{len(todo)} items", flush=True)
t0, found = time.time(), 0
with httpx.Client(base_url=iverieli.BASE, headers={"User-Agent": iverieli.AGENT}, timeout=120) as client:
    for n, (handle, title) in enumerate(todo, 1):
        chunks = []
        try:
            for href in small_pdfs(client.get(f"/handle/{handle}?mode=full").text):
                if len(chunks) >= MAX_PASSAGES:
                    break
                time.sleep(PAUSE)
                chunks += [c for c in passages.chunks(pdf_text(client.get(href).content))
                           if len(c) >= 100 and len(LEADER.findall(c)) < 2]
            time.sleep(PAUSE)
        except httpx.HTTPError as e:
            print(f"  ! {handle} {type(e).__name__}", flush=True)
            time.sleep(30)
            continue
        chunks = chunks[:MAX_PASSAGES]
        pdb.executemany("INSERT INTO passages (title, text, site, url) VALUES (?, ?, 'iverieli', ?)",
                        [(title, c, f"{iverieli.BASE}/handle/{handle}") for c in chunks])
        pdb.commit()
        db.execute("INSERT INTO texts VALUES (?, ?)", (handle, len(chunks)))
        db.commit()
        found += bool(chunks)
        if n % 20 == 0 or n == len(todo):
            print(f"{n}/{len(todo)}  with text {found}  {(time.time() - t0) / 60:.0f} min", flush=True)
print("done")
