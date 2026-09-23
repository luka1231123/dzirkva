"""Text of small Iverieli PDFs → passages (data/passages.db, site 'iverieli'). Vectors: scripts/build_passages.py.

A small PDF (≤ MAX_MB) is a Word export with a text layer; a big one is page scans, so it is not downloaded.
The item page lists each file's size. Text and passages: passages.pdf_chunks.
Needs pdftotext (brew install poppler). Resumable: done items are in iverieli.db table 'texts'.
Run in background: nohup uv run python scripts/iverieli_text.py > data/iverieli_text.log 2>&1 &
"""

import re
import sys
import time

import httpx

from dzirkva import iverieli, passages

MAX_MB = 5
MAX_PASSAGES = 20   # per item: the first pages (summary, introduction); a whole book would swamp the index
PAUSE = 0.5         # seconds between requests: one library server, be polite
TYPES = ("Thesis", "Article", "Book", "Journal", "Newspaper", "Other", "")  # in this order; before 1990 all scans
UNIT = {"bytes": 1e-6, "kB": 1e-3, "MB": 1, "GB": 1e3}
ROW = re.compile(r'href="(/bitstream/[^"]+\.pdf)".*?headers="t3"[^>]*>([\d.,]+) (bytes|kB|MB|GB)<', re.I)


def small_pdfs(html: str) -> list[str]:
    """Links of the PDFs on an item page not bigger than MAX_MB."""
    return [href for row in html.split("<tr>") if (m := ROW.search(row))
            for href, size, unit in [m.groups()] if float(size.replace(",", "")) * UNIT[unit] <= MAX_MB]


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
                chunks += passages.pdf_chunks(client.get(href).content)
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
