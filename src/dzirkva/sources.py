"""Trusted Georgian sources from config/sources.yaml: domain -> (category, tier)."""

from functools import cache
from pathlib import Path
from urllib.parse import urlparse

import yaml

SOURCES_FILE = Path(__file__).resolve().parents[2] / "config" / "sources.yaml"


@cache
def sources() -> dict[str, tuple[str, int]]:
    data = yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8"))
    return {domain: (category, tier) for category, sites in data.items() for domain, tier in sites.items()}


def lookup(url: str) -> tuple[str, int] | None:
    """(category, tier) for a URL on a trusted domain or its subdomain, else None."""
    host = (urlparse(url).hostname or "").removeprefix("www.")
    while host:
        if host in sources():
            return sources()[host]
        host = host.partition(".")[2]
    return None


def by_category(category: str) -> list[str]:
    """Domains of one category, best tier first."""
    return sorted((d for d, (c, _) in sources().items() if c == category), key=lambda d: sources()[d][1])
