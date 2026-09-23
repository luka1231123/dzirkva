"""Clients for the outside search engines: local SearXNG and the Brave Search API."""

import asyncio
import html
import os
import re

import httpx
from dotenv import load_dotenv

load_dotenv()

SEARXNG_URL = "http://127.0.0.1:8888/search"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"


def clean(text: str) -> str:
    """Engines send snippets with HTML (<strong>, &quot;): plain text only."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", text or "")).split())


async def searxng(client: httpx.AsyncClient, query: str) -> list[dict]:
    """Google + Bing + Brave (web pages) through the local SearXNG."""
    r = await client.get(SEARXNG_URL, params={"q": query, "format": "json"}, timeout=15)
    r.raise_for_status()
    return [
        {"url": x["url"], "title": clean(x.get("title")), "snippet": clean(x.get("content")),
         "engine": "+".join(x.get("engines", []))}
        for x in r.json()["results"]
    ]


async def brave(client: httpx.AsyncClient, query: str) -> list[dict]:
    """Official Brave Search API. Uses the monthly quota, so call it sparingly."""
    r = await client.get(
        BRAVE_URL,
        params={"q": query, "count": 20},
        headers={"X-Subscription-Token": os.environ["BRAVE_API_KEY"]},
        timeout=15,
    )
    r.raise_for_status()
    return [
        {"url": x["url"], "title": clean(x.get("title")), "snippet": clean(x.get("description")),
         "engine": "brave-api"}
        for x in r.json().get("web", {}).get("results", [])
    ]


async def _demo(query: str) -> None:
    async with httpx.AsyncClient() as client:
        for name, fn in (("searxng", searxng), ("brave", brave)):
            results = await fn(client, query)
            print(f"{name}: {len(results)} results")
            for x in results[:5]:
                print(f"  [{x['engine']}] {x['title'][:60]}  {x['url'][:80]}")


if __name__ == "__main__":
    import sys
    asyncio.run(_demo(" ".join(sys.argv[1:]) or "ხაჭაპური"))
