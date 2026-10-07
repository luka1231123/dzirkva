"""Check each trusted source has Georgian pages.

1. Home page: online and its text is mostly Georgian.
2. If not (bot block, JavaScript page, English home page, geo-block): the share of Georgian letters
   in its crawled pages (crawl.db table domains).

Run: uv run python scripts/check_sources.py
"""

import asyncio
import re

import httpx

from dzirkva import crawl
from dzirkva.georgian import georgian_ratio
from dzirkva.sources import sources

MIN_GEORGIAN = 0.3
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 Safari/605.1.15",
           "Accept-Language": "ka,en;q=0.5"}


def visible_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    return re.sub(r"<[^>]+>", " ", html)


async def check(client: httpx.AsyncClient, sem: asyncio.Semaphore, domain: str) -> tuple[str, str, float]:
    async with sem:
        for url in (f"https://{domain}/", f"https://www.{domain}/", f"http://{domain}/"):
            try:
                r = await client.get(url)
                ratio = georgian_ratio(visible_text(r.text))
                if r.status_code < 400:
                    return domain, f"{r.status_code} {r.url.host}", ratio
                last = f"HTTP {r.status_code}"
            except httpx.HTTPError as e:
                last = type(e).__name__
        return domain, last, 0.0


def check_crawl(domain: str) -> tuple[str, float]:
    """Pages and mean Georgian share of the domain in the crawl."""
    db = crawl._db()
    row = db and db.execute("SELECT pages, georgian FROM domains WHERE host = ?", (domain,)).fetchone()
    return (f"crawl: {row[0]} pages", row[1]) if row and row[0] else ("crawl: no pages", 0.0)


async def main() -> None:
    sem = asyncio.Semaphore(12)
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15) as client:
        results = await asyncio.gather(*(check(client, sem, d) for d in sources()))
    for i, (domain, status, ratio) in enumerate(results):
        if ratio < MIN_GEORGIAN:  # the home page check failed
            s2, r2 = check_crawl(domain)
            results[i] = (domain, f"{status} | {s2}", r2)
    for domain, status, ratio in sorted(results, key=lambda r: r[2]):
        mark = "OK " if ratio >= MIN_GEORGIAN else "MIX" if ratio > 0 else "BAD"
        print(f"{mark} {ratio:4.0%}  {domain:<28} {status}")
    ok = sum(r[2] >= MIN_GEORGIAN for r in results)
    mix = sum(0 < r[2] < MIN_GEORGIAN for r in results)
    print(f"\n{ok} OK, {mix} MIX (multilingual), {len(results) - ok - mix} BAD of {len(results)}")


asyncio.run(main())
