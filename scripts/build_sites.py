"""Names → official websites, from Wikidata (via the QLever endpoint). Output: data/sites.tsv (name<TAB>url<TAB>sitelinks).

Items that have a Georgian name (label or alias) and an official website (P856); names in Georgian and English
(ფეისბუქი, Facebook → facebook.com; მაგთიკომი, MagtiCom → magticom.ge). Search uses it for queries that
name a site (sources.named_sites). A profile page on a social site is not the item's own site: dropped.
Run: uv run python scripts/build_sites.py (~2-5 min)
"""

import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dzirkva.georgian import normalize  # noqa: E402
from dzirkva.sources import kind  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "data" / "sites.tsv"
ENDPOINT = "https://qlever.dev/api/wikidata"
PREFIX = """PREFIX wdt: <http://www.wikidata.org/prop/direct/> PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#> PREFIX wikibase: <http://wikiba.se/ontology#>"""
# Plain triple patterns only: the endpoint refuses (429) the same query with BIND or a predicate variable.
GEORGIAN = PREFIX + """ SELECT ?item ?site ?name WHERE {
  ?item wdt:P856 ?site . ?item %s ?name . FILTER(lang(?name) = "ka") }"""
ENGLISH = PREFIX + """ SELECT ?item ?name ?links WHERE { VALUES ?item { %s }
  ?item %s ?name . FILTER(lang(?name) = "en") OPTIONAL { ?item wikibase:sitelinks ?links } }"""
LABEL, ALIAS = "rdfs:label", "skos:altLabel"
BATCH = 3000
MIN_NAME = 3


def rows(query: str) -> list[list[str]]:
    for wait in (5, 15, 45, 120, 0):  # the endpoint answers 429 to fast series of queries
        r = httpx.post(ENDPOINT, data={"query": query}, headers={"Accept": "text/tab-separated-values"}, timeout=180)
        if r.status_code != 429 or not wait:
            break
        time.sleep(wait)
    r.raise_for_status()
    return [line.split("\t") for line in r.text.splitlines()[1:]]


def literal(s: str) -> str:
    return s.rsplit("@", 1)[0].strip('"').replace('\\"', '"')


def own_site(url: str) -> bool:
    """A home page (or one language folder), not a deep page or a profile on a social or video site."""
    path = [p for p in urlparse(url).path.split("/") if p]
    return urlparse(url).hostname is not None and len(path) <= 1 and not (path and kind(url) in ("social", "video"))


sites: dict[str, set[str]] = {}
names: dict[str, dict[str, bool]] = {}  # item → name → is the main name (label), not an alias
for kind_, main in ((ALIAS, False), (LABEL, True)):
    for item, site, name in rows(GEORGIAN % kind_):
        url = site.strip("<>")
        if own_site(url):
            sites.setdefault(item, set()).add(url)
            names.setdefault(item, {})[literal(name)] = main
print(f"{len(sites):,} items with a Georgian name and an own site")
links: dict[str, int] = {}
items = sorted(sites)
for kind_, main in ((ALIAS, False), (LABEL, True)):
    for i in range(0, len(items), BATCH):
        time.sleep(2)
        for item, name, n in rows(ENGLISH % (" ".join(items[i:i + BATCH]), kind_)):
            names[item][literal(name)] = main
            links[item] = int(literal(n)) if n else 0

# A shared name goes to the item whose main name it is (სილქნეტი: Silknet, not Geocell's alias), then the
# best-known item (most Wikipedia language versions). Site: https first, a Georgian page first, then shortest.
best: dict[str, tuple[tuple[bool, int], str, int]] = {}
for item, urls in sites.items():
    url = min(urls, key=lambda u: (not u.startswith("https"), "/ka" not in u.lower() and "/ge" not in u.lower(), len(u)))
    for name, main in names[item].items():
        name, rank = normalize(name), (main, links.get(item, 0))
        if len(name) >= MIN_NAME and not name.isdigit() and rank > best.get(name, ((False, -1),))[0]:
            best[name] = (rank, url, links.get(item, 0))
with open(OUT, "w", encoding="utf-8") as out:
    for name, (_, url, n) in sorted(best.items()):
        out.write(f"{name}\t{url}\t{n}\n")
print(f"{len(best):,} names → {OUT}")
