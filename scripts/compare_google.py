"""dzirkva and Google on the same Georgian queries: an internal check (the site shows no such numbers).

Usage: uv run python scripts/compare_google.py eval/stress100_dz8.jsonl
Google: eval/stress100_google.json (saved result pages); dzirkva: a run of scripts/run_stress.py (All tab rules).
Each share is of all first-ten results together; both sides are judged by the same rules.
"""

import json
import sys
from urllib.parse import urlparse

from dzirkva import crawl, papers
from dzirkva.georgian import georgian_ratio
from dzirkva.search import MIN_GEORGIAN
from dzirkva.sources import lookup, tags

MEASURES = {
    "trusted source (tier 1-2)": lambda r, t: (lookup(r["u"]) or ("", 0))[1] in (1, 2),
    "research (the academic sign)": lambda r, t: "academic" in t,
    "people: forum, blog, social, small web": lambda r, t: bool({"people", "small"} & t),
    "not Georgian (title + snippet)": lambda r, t: georgian_ratio(f"{r['t']} {r['s']}") < MIN_GEORGIAN,
}


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


def _url(shown: str) -> str:
    """Google shows 'https://host › a › b'."""
    head, *path = [p.strip() for p in shown.split("›")]
    return head.rstrip("/") + "/" + "/".join(path)


google = json.load(open("eval/stress100_google.json"))
dz = {r["q"]: r for r in map(json.loads, open(sys.argv[1]))}
queries = [q for q in google if q in dz]
sides = {"dzirkva": [r for q in queries for r in dz[q]["top"][:10]],
         "google": [dict(r, u=_url(r["u"])) for q in queries for r in google[q]["top"][:10]]}
print(f"{len(queries)} queries, first ten results: " + ", ".join(f"{s} {len(rs)}" for s, rs in sides.items()))
judged = {s: [(r, tags(r["u"], r["t"], crawl.domain_signals(r["u"]), crawl.small_site(r["u"]), papers.is_repo(r["u"])))
              for r in rs] for s, rs in sides.items()}
for name, test in MEASURES.items():
    print(f"{name:<40}" + "".join(f"{s} {sum(test(r, t) for r, t in rs) / len(rs):4.0%}   " for s, rs in judged.items()))
other = [r for q in queries for r in dz[q]["top"][:10]
         if _host(r["u"]) not in {_host(_url(g["u"])) for g in google[q]["top"][:10]}]
print(f"dzirkva results on sites Google's first ten does not show: {len(other) / len(sides['dzirkva']):.0%}")
print(f"Google AI answers: {sum(google[q]['ai'] for q in queries)} of {len(queries)} queries")
