"""Bounded PDF batches: full Georgian text for word search, a few passages for later vectors.

Default sample: at most 20 papers and a 25 MiB body budget (one stream chunk,
at most 64 KiB, may cross the boundary), globally sequential with a five-second
request pause. --limit and --max-bytes allow explicit larger budgets. Temporary failures retry
on a later run; textless PDFs are recorded separately. --discover-missing probes known landing
pages for citation_pdf_url metadata, under the same request and byte budget. No model is loaded.
"""

import argparse
import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import shutil
import subprocess
from urllib.parse import urljoin, urlparse

import httpx
from lxml import html

from dzirkva import papers, passages
from dzirkva.ingest import wait_for_host

MAX_PASSAGES = 20


@dataclass
class Budget:
    maximum: int
    received: int = 0
    pause: float = 5

    @property
    def remaining(self):
        return max(0, self.maximum - self.received)


async def fetch(client, url, budget, maximum):
    """Account every received body byte, including failed/oversize downloads, before retaining it."""
    if not budget.remaining:
        return None, "budget", "batch byte limit reached"
    data = bytearray()
    try:
        async with client.stream("GET", url) as response:
            if response.status_code != 200:
                status = "retry" if response.status_code in (408, 425, 429) or response.status_code >= 500 else "unavailable"
                return None, status, f"HTTP {response.status_code}"
            length = int(response.headers.get("content-length") or 0)
            if length > maximum:
                return None, "oversize", f"advertised {length} bytes"
            if length > budget.remaining:
                return None, "budget", "advertised file exceeds remaining batch budget"
            async for part in response.aiter_raw(chunk_size=65536):
                budget.received += len(part)
                if budget.received > budget.maximum:
                    return None, "budget", "batch byte limit reached"
                if len(data) + len(part) > maximum:
                    return None, "oversize", f"file exceeds {maximum} bytes"
                data.extend(part)
    except (httpx.HTTPError, ValueError) as exc:
        return None, "retry", str(exc)[:200]
    return bytes(data), "ok", ""


async def discover_pdf(client, url, budget):
    data, status, error = await fetch(client, url, budget, 512 * 1024)
    if data is None:
        return "", status, error
    try:
        page = html.fromstring(data)
        links = page.xpath("//meta[translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')="
                           "'citation_pdf_url']/@content")
        links += page.xpath("//a[contains(translate(@href,'PDF','pdf'),'.pdf')]/@href")
        for link in links:
            target = urljoin(url, link)
            if urlparse(target).scheme in ("http", "https"):
                return target, "ok", ""
    except (ValueError, html.etree.ParserError):
        pass
    return "", "no_pdf", "no PDF link on landing page"


def selected(chunks):
    if len(chunks) <= MAX_PASSAGES:
        return chunks
    return [chunks[round(i * (len(chunks) - 1) / (MAX_PASSAGES - 1))] for i in range(MAX_PASSAGES)]


def record(db, url, status, size, count, error):
    now = datetime.now(timezone.utc)
    delay = 1 if status == "retry" else 7
    retry = (now + timedelta(days=delay)).isoformat() if status in ("retry", "unavailable", "no_pdf", "extract_error") else None
    db.execute("INSERT INTO texts(url, passages, status, attempts, retry_after, error, bytes, updated) "
               "VALUES (?, ?, ?, 1, ?, ?, ?, ?) ON CONFLICT(url) DO UPDATE SET passages=excluded.passages, "
               "status=excluded.status, attempts=texts.attempts+1, retry_after=excluded.retry_after, "
               "error=excluded.error, bytes=excluded.bytes, updated=excluded.updated",
               (url, count, status, retry, error, size, now.isoformat()))
    db.commit()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20, help="maximum papers attempted")
    parser.add_argument("--sample", type=int, help="sample this many papers (alias for --limit)")
    parser.add_argument("--max-bytes", type=int, default=25 * 1024 * 1024, help="batch body-byte budget")
    parser.add_argument("--max-file-bytes", type=int, default=10 * 1024 * 1024)
    parser.add_argument("--pause", type=float, default=5)
    parser.add_argument("--discover-missing", action="store_true")
    parser.add_argument("--retry-legacy", action="store_true", help="sample old ambiguous zero-passage records")
    args = parser.parse_args()
    if args.sample is not None:
        args.limit = args.sample
    if min(args.limit, args.max_bytes, args.max_file_bytes) <= 0 or args.pause < 0:
        parser.error("limits must be positive and pause nonnegative")
    if not shutil.which("pdftotext"):
        parser.error("pdftotext is required; install poppler before downloading")
    db = papers.connect()
    papers.drain_retired(db)
    pdb = passages.connect()
    print("checking PDF passage URL index (first build scans the passage table once)", flush=True)
    passages.ensure_url_index(pdb)
    now = datetime.now(timezone.utc).isoformat()
    candidates = db.execute(
        "SELECT p.url, p.pdf, p.title, p.repo FROM papers p LEFT JOIN texts t ON t.url=p.url "
        "WHERE (p.pdf != '' OR ?) AND (t.url IS NULL OR "
        "(t.status IN ('retry','unavailable','no_pdf','extract_error') AND t.attempts < 3 AND t.retry_after <= ?) "
        "OR (t.status='legacy_empty' AND ?)) ORDER BY p.year DESC",
        (args.discover_missing, now, args.retry_legacy))
    by_repo = defaultdict(deque)
    count, queued_urls = 0, set()
    for row in candidates:
        count += 1
        if len(by_repo[row[3]]) < args.limit and row[0] not in queued_urls:
            by_repo[row[3]].append(row[:3])
            queued_urls.add(row[0])
    # Prefer productive repositories while sampling across every repository before going deeper.
    yields = dict(db.execute("SELECT p.repo, avg(t.passages > 0) FROM papers p JOIN texts t ON t.url=p.url "
                             "GROUP BY p.repo"))
    repos = deque(sorted((repo for repo in by_repo if by_repo[repo]), key=lambda repo: -(yields.get(repo) or 0)))
    budget = Budget(args.max_bytes, pause=args.pause)
    attempted = useful = 0
    print(f"batch: <= {args.limit} papers, {args.max_bytes:,} body-byte budget (+ <=64KiB stream boundary); {count:,} candidate records", flush=True)
    async def throttle(request):
        await asyncio.sleep(args.pause)
        await wait_for_host(str(request.url), pause=args.pause, background=True)

    async with httpx.AsyncClient(event_hooks={"request": [throttle]}, follow_redirects=True, timeout=httpx.Timeout(120, connect=10), verify=False,
                                 headers={"User-Agent": papers.AGENT, "Accept-Encoding": "identity"}) as client:
        while repos and attempted < args.limit and budget.remaining:
            repo = repos.popleft()
            url, pdf, title = by_repo[repo].popleft()
            if by_repo[repo]:
                repos.append(repo)
            before = budget.received
            picks, status, error = [], "retry", ""
            if not pdf:
                pdf, status, error = await discover_pdf(client, url, budget)
                if pdf:
                    db.execute("UPDATE papers SET pdf = ? WHERE url = ?", (pdf, url))
                    db.commit()
            if pdf:
                data, status, error = await fetch(client, pdf, budget, args.max_file_bytes)
                if data is not None:
                    if not data.startswith(b"%PDF-"):
                        status, error = "unavailable", "response is not PDF"
                    else:
                        try:
                            text = await asyncio.to_thread(passages.pdf_text, data)
                            chunks = [c for c in passages.chunks(text) if len(c) >= 100 and len(passages.LEADER.findall(c)) < 2]
                            picks = selected(chunks)
                            status = "ok" if text else "textless"
                            if text:
                                db.execute("DELETE FROM fulltext WHERE url = ?", (url,))
                                db.execute("INSERT INTO fulltext(url,text) VALUES (?,?)", (url, text))
                            pdb.execute("DELETE FROM passages WHERE site='papers' AND url=?", (url,))
                            pdb.executemany("INSERT INTO passages(title,text,site,url) VALUES (?,?,'papers',?)",
                                            [(title, chunk, url) for chunk in picks])
                            pdb.commit()
                            useful += bool(text)
                        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                            status, error = "extract_error", str(exc)[:200]
            if status == "budget":
                print(f"budget stop: {error}; {budget.received:,} bytes received", flush=True)
                break  # incomplete files remain eligible next run, no terminal marker
            count = len(picks) if status == "ok" else 0
            record(db, url, status, budget.received - before, count, error)
            attempted += 1
            print(f"{attempted}: {status}, {budget.received-before:,} bytes, {url}", flush=True)
    print(f"done: {attempted} attempted, {useful} searchable texts, {budget.received:,} body bytes", flush=True)
    db.close()
    pdb.close()


if __name__ == "__main__":
    asyncio.run(main())
