"""Text of the papers' PDFs → passages (data/passages.db, site 'papers'). Vectors: scripts/build_passages.py.

Papers with a PDF link (OJS galleys, EPrints files), newest first. A file bigger than MAX_MB is page scans or a
whole book: not read. Text and passages: passages.pdf_chunks (Georgian pages only, AcadNusx converted).
Each repository is its own task, one request per PAUSE seconds. Resumable: done papers are in papers.db table texts.
Needs pdftotext (brew install poppler).
Run in background: nohup uv run python scripts/papers_text.py > data/papers_text.log 2>&1 &
"""

import asyncio
from collections import defaultdict

import httpx

from dzirkva import papers, passages

MAX_MB = 10
MAX_PASSAGES = 20   # per paper: the first pages (abstract, introduction); a long thesis would swamp the index
PAUSE = 1.0


async def fetch_pdf(client: httpx.AsyncClient, url: str) -> bytes | None:
    """The file if it is a PDF not bigger than MAX_MB (an HTML galley or an error page is not)."""
    data = b""
    try:
        async with client.stream("GET", url) as r:
            if r.status_code != 200 or int(r.headers.get("content-length") or 0) > MAX_MB * 1e6:
                return None
            async for part in r.aiter_bytes():
                data += part
                if len(data) > MAX_MB * 1e6:
                    return None
    except (httpx.HTTPError, ValueError):
        return None
    return data if data.startswith(b"%PDF-") else None


async def read(client: httpx.AsyncClient, db, pdb, items: list[tuple[str, str, str]], done: list[int]) -> None:
    for url, pdf, title in items:
        data = await fetch_pdf(client, pdf)
        chunks = (await asyncio.to_thread(passages.pdf_chunks, data))[:MAX_PASSAGES] if data else []
        pdb.executemany("INSERT INTO passages (title, text, site, url) VALUES (?, ?, 'papers', ?)",
                        [(title, c, url) for c in chunks])
        pdb.commit()
        db.execute("INSERT OR REPLACE INTO texts VALUES (?, ?)", (url, len(chunks)))
        db.commit()
        done[0] += 1
        done[1] += bool(chunks)
        if done[0] % 50 == 0:
            print(f"{done[0]} papers read, {done[1]} with Georgian text", flush=True)
        await asyncio.sleep(PAUSE)


async def main() -> None:
    db = papers.connect()
    pdb = passages.connect()
    by_repo = defaultdict(list)
    for url, pdf, title, repo in db.execute("SELECT url, pdf, title, repo FROM papers WHERE pdf != '' "
                                            "AND url NOT IN (SELECT url FROM texts) ORDER BY year DESC"):
        by_repo[repo].append((url, pdf, title))
    print(f"{sum(map(len, by_repo.values())):,} papers with a PDF in {len(by_repo)} repositories", flush=True)
    done = [0, 0]
    # verify=False: university sites often have expired certificates; only public files are read
    async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(120, connect=10), verify=False,
                                 headers={"User-Agent": papers.AGENT}) as client:
        await asyncio.gather(*(read(client, db, pdb, items, done) for items in by_repo.values()))
    print(f"done: {done[0]} papers read, {done[1]} with Georgian text", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
