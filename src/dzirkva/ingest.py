"""Small shared controls for low-priority ingestion. No models or large data structures."""

import asyncio
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

DATA = Path(__file__).resolve().parents[2] / "data"


@contextmanager
def search_activity():
    """Let ingestion see active searches across the web and MCP processes."""
    DATA.mkdir(exist_ok=True)
    marker = DATA / f"search-{os.getpid()}-{uuid.uuid4().hex}.active"
    marker.touch()
    try:
        yield
    finally:
        marker.unlink(missing_ok=True)


def search_busy() -> bool:
    for marker in DATA.glob("search-*.active"):
        try:
            os.kill(int(marker.stem.split("-")[1]), 0)
            return True
        except (ProcessLookupError, ValueError, FileNotFoundError):
            marker.unlink(missing_ok=True)
        except PermissionError:
            return True
    return False


async def wait_for_host(url: str, pause: float = 1, *, background: bool = True):
    """Reserve host request slots across RSS, PDFs, archives and the slow crawler."""
    if background:
        while search_busy():
            await asyncio.sleep(2)
    host = (urlparse(url).hostname or "").removeprefix("www.")
    DATA.mkdir(exist_ok=True)
    with sqlite3.connect(DATA / "ingest.db", timeout=30) as db:
        db.execute("CREATE TABLE IF NOT EXISTS host_requests(host TEXT PRIMARY KEY,next REAL)")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT next FROM host_requests WHERE host=?", (host,)).fetchone()
        now = time.time()
        when = max(now, row[0] if row else now)
        db.execute("INSERT INTO host_requests VALUES (?,?) ON CONFLICT(host) DO UPDATE SET next=excluded.next",
                   (host, when + max(1, pause)))
    await asyncio.sleep(max(0, when - time.time()))
    if background:
        while search_busy():
            await asyncio.sleep(2)
