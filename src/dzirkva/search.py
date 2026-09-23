"""Query -> Georgian query variants -> parallel engine queries -> one merged ranking.

Variants (each one finds different pages; a page found by many variants is almost always relevant):
  original    the query as typed
  corrected   Latin -> Georgian, typos fixed
  lemmas      every word in its dictionary form (ჩქარად -> ჩქარი, სოფლებში -> სოფელი)
  expanded    OR groups of word forms, family members and synonyms:
              (დარბის OR გარბის OR რბენა) ყველაზე (ჩქარად OR ჩქარი OR სწრაფი)
  site:…      the query limited to trusted sources of the matching categories
Merge: Reciprocal Rank Fusion (RRF) over all result lists, + trust tier bonus,
+ word-family match bonus (query families found in title/snippet, via morph.py).
"""

import asyncio
import re
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from dzirkva import engines
from dzirkva.georgian import GEORGIAN_WORD, georgian_ratio, latin_to_georgian, normalize, spell
from dzirkva.morph import analyze, families, family_members, synonyms
from dzirkva.sources import by_category, lookup

# Question words and function words: kept in the original query, dropped from keyword variants.
STOPWORDS = set(
    "ვინ რა რას რამ რისი როგორ როგორი სად საიდან საით როდის რატომ რისთვის რომელი რომელიც რამდენი "
    "არის არიან იყო იქნება და თუ რომ ეს ის ამ იმ ეგ კი არ ვერ ნუ მაგრამ ან ანუ უნდა შეიძლება "
    "მე შენ ჩვენ თქვენ მისი მათი ჩემი შენი ჩვენი თქვენი ამის იმის აქ იქ".split()
)
# Words that point a query to a source category (checked against each word's lemmas and families).
INTENT = {
    "law": "კანონი კოდექსი კონსტიტუცია სასამართლო დადგენილება ბრძანებულება მუხლი უფლება ჯარიმა",
    "history": "ისტორია მეფე ომი საუკუნე არქივი ძეგლი ეკლესია მონასტერი",
    "reference": "რა ვინ ბიოგრაფია ლექსიკონი განმარტება ტექსტი წიგნი ლექსი ენციკლოპედია",
    "religion": "ლოცვა ხატი წმინდა ეკლესია მონასტერი პატრიარქი ბიბლია დღესასწაული მარხვა",
    "education": "გამოცდა სკოლა უნივერსიტეტი სტუდენტი მასწავლებელი ჩარიცხვა გრანტი",
    "government": "ამინდი კურსი სტატისტიკა არჩევნები გადასახადი პირადობა პასპორტი",
    "news": "დღეს ახალი ამბები მოხდა გუშინ",
    "culture": "ფილმი მუსიკა სიმღერა პოეზია მხატვარი რეცეპტი",
}
DEFAULT_CATEGORIES = ("reference", "news")
SITES_PER_VARIANT = 6
MAX_FORMS = 4          # per word in the expanded variant
RRF_K = 60
TIER_BONUS = {1: 0.5, 2: 0.25, 3: 0.0}
FAMILY_BONUS = 0.5     # × share of query word families found in title + snippet
VARIANT_WEIGHT = {"original": 1.0, "corrected": 1.0, "lemmas": 0.8, "expanded": 1.0, "site": 0.7}
BRAVE_VARIANTS = ("corrected", "lemmas")     # Brave API: monthly quota, no OR support; "original" if no "corrected"
SEARXNG_SPACING = 0.3  # seconds between SearXNG requests: Google blocks fast bursts
TRACKING = re.compile(r"^(utm_|fbclid|gclid|yclid|mc_|ref$|ref_)")


@dataclass
class Result:
    url: str
    title: str
    snippet: str
    score: float = 0.0
    tier: int | None = None
    category: str | None = None
    georgian: float = 0.0
    variants: set[str] = field(default_factory=set)
    engines: set[str] = field(default_factory=set)


# ---- query understanding ------------------------------------------------

def _fix_word(word: str) -> str:
    if word.isascii() and word.isalpha():
        return latin_to_georgian(word) or word
    return spell(normalize(word)) if GEORGIAN_WORD.fullmatch(normalize(word)) else normalize(word)


def _lemma(word: str) -> str:
    """Dictionary form for the lemma variant; verbs and unknown words stay as typed."""
    for a in analyze(word):
        if a.source in ("lexicon", "noun-rule") and a.lemma in family_members(a.family):
            return a.lemma
    return word


def _forms(word: str) -> list[str]:
    """The word, its dictionary form, family members and synonyms (most useful first)."""
    out = [word, _lemma(word)] + synonyms(_lemma(word))[:1]
    for fam in families(word):
        out += family_members(fam)[:2]
    seen: set[str] = set()
    return [w for w in out if not (w in seen or seen.add(w))][:MAX_FORMS]


def _categories(words: list[str]) -> list[str]:
    fams = {f for w in words for f in families(w)} | set(words)
    hits = [c for c, ws in INTENT.items() if any(f in fams for f in ws.split())]
    return hits or list(DEFAULT_CATEGORIES)


def variants(query: str) -> dict[str, str]:
    """Named query variants, duplicates removed."""
    raw = query.split()
    corrected = [_fix_word(w) for w in raw]
    content = [w for w in corrected if w not in STOPWORDS] or corrected
    expanded = " ".join("(" + " OR ".join(forms) + ")" if len(forms := _forms(w)) > 1 else w for w in content)
    out = {
        "original": " ".join(raw),
        "corrected": " ".join(corrected),
        "lemmas": " ".join(_lemma(w) for w in content),
        "expanded": expanded,
    }
    for cat in _categories(content)[:2]:
        sites = " OR ".join(f"site:{d}" for d in by_category(cat)[:SITES_PER_VARIANT])
        out[f"site:{cat}"] = f"{expanded} ({sites})"
    unique: dict[str, str] = {}
    for name, q in out.items():
        if q not in unique.values():
            unique[name] = q
    return unique


# ---- fan-out ------------------------------------------------------------

async def _run(client: httpx.AsyncClient, name: str, q: str, engine: str, delay: float) -> tuple[str, str, list[dict]]:
    await asyncio.sleep(delay)
    try:
        fn = engines.brave if engine == "brave-api" else engines.searxng
        return name, engine, await fn(client, q)
    except (httpx.HTTPError, KeyError) as e:
        print(f"  ! {engine} failed for {name}: {type(e).__name__}")
        return name, engine, []


async def fan_out(vs: dict[str, str]) -> list[tuple[str, str, list[dict]]]:
    async with httpx.AsyncClient() as client:
        jobs = [_run(client, n, q, "searxng", i * SEARXNG_SPACING) for i, (n, q) in enumerate(vs.items())]
        brave = [n for n in BRAVE_VARIANTS if n in vs] or ["original"]
        if "corrected" not in vs and "original" not in brave:
            brave = ["original"] + brave[:1]
        jobs += [_run(client, n, vs[n], "brave-api", 0) for n in brave]
        return await asyncio.gather(*jobs)


# ---- merge --------------------------------------------------------------

def canonical_url(url: str) -> str:
    """Same page, one key: https, no www, no tracking parameters, no fragment, no trailing slash."""
    p = urlparse(url)
    host = (p.hostname or "").removeprefix("www.")
    query = urlencode([(k, v) for k, v in parse_qsl(p.query) if not TRACKING.match(k)])
    return urlunparse(("https", host, p.path.rstrip("/") or "/", "", query, ""))


def _family_share(text: str, query_fams: list[set[str]]) -> float:
    if not query_fams:
        return 0.0
    text_fams = {f for w in re.findall(r"[ა-ჰ]+", normalize(text)) for f in families(w)}
    return sum(bool(fs & text_fams) for fs in query_fams) / len(query_fams)


def merge(lists: list[tuple[str, str, list[dict]]], query_words: list[str]) -> list[Result]:
    merged: dict[str, Result] = {}
    for name, engine, results in lists:
        weight = VARIANT_WEIGHT["site" if name.startswith("site:") else name]
        for rank, r in enumerate(results):
            key = canonical_url(r["url"])
            m = merged.setdefault(key, Result(r["url"], r["title"], r["snippet"]))
            if len(r["snippet"]) > len(m.snippet):
                m.snippet = r["snippet"]
            m.score += weight / (RRF_K + rank)
            m.variants.add(name)
            m.engines.update(r["engine"].split("+"))
    query_fams = [families(w) for w in query_words if w not in STOPWORDS]
    for m in merged.values():
        m.category, m.tier = lookup(m.url) or (None, None)
        m.georgian = georgian_ratio(m.title + " " + m.snippet)
        m.score *= 1 + TIER_BONUS.get(m.tier, 0.0) + FAMILY_BONUS * _family_share(m.title + " " + m.snippet, query_fams)
    return sorted(merged.values(), key=lambda m: m.score, reverse=True)


def search(query: str) -> tuple[dict[str, str], list[Result]]:
    vs = variants(query)
    lists = asyncio.run(fan_out(vs))
    words = [_fix_word(w) for w in query.split()]
    return vs, merge(lists, words)


if __name__ == "__main__":
    import sys

    t = time.time()
    vs, results = search(" ".join(sys.argv[1:]))
    for name, q in vs.items():
        print(f"{name:>16}: {q}")
    print(f"\n{len(results)} results in {time.time() - t:.1f}s\n")
    for r in results[:15]:
        tier = f"t{r.tier}" if r.tier else "  "
        print(f"{r.score * 1000:5.1f} {tier} {r.georgian:4.0%} v{len(r.variants)} {r.title[:60]}")
        print(f"                     {r.url[:100]}")
