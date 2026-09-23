"""Question -> simple queries -> wide retrieval -> feedback round -> rank by meaning.

1. Round 1: a few simple queries (original, corrected, dictionary forms, trusted sites).
   Their job is recall (collect candidate pages), not precision.
2. Feedback: read the top Georgian snippets of round 1 and find the words and names that
   repeat there but are rare in Georgian overall (ბოლტი, უსწრაფესი, სპრინტერი). These are
   the words the answer pages use. Round 2 searches with them.
3. Rank: combine the engine ranking (RRF over all lists + trust tier + word-family match)
   with the meaning ranking (BGE-M3 similarity between the question and each result),
   then × trust tier × coverage (share of query words present, rare words count more:
   a metro-map page without შრიფტი drops for "თბილისის მეტროს შრიფტი").
4. Group: the same text on many sites becomes one result with `copies`.
Only results that are mostly Georgian are kept. `kind` decides the tab (sources.kind).
"""

import asyncio
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from dzirkva import engines
from dzirkva.georgian import freq, georgian_ratio, latin_to_georgian, normalize, spell_candidates, words
from dzirkva.meaning import similarity
from dzirkva.morph import analyze, families, family_members
from dzirkva import wiki
from dzirkva.sources import by_category, kind, lookup

# Question words and function words: dropped from keyword queries and feedback terms.
STOPWORDS = set(
    "ვინ რა რას რამ რისი როგორ როგორი სად საიდან საით როდის რატომ რისთვის რომელი რომელიც რამდენი "
    "არის არიან იყო იქნება და თუ რომ ეს ის ამ იმ ეგ კი არ ვერ ნუ მაგრამ ან ანუ უნდა შეიძლება "
    "მე შენ ჩვენ თქვენ მისი მათი ჩემი შენი ჩვენი თქვენი ამის იმის აქ იქ ასე ისე ძალიან უფრო "
    "ერთი ორი სამი ყველა ყველაფერი მხოლოდ ასევე თავის თავად შემდეგ წლის წელს მიერ შესახებ "
    # page furniture that repeats in snippets but says nothing about the topic
    "ვიკიპედია ფოტო ვიდეო სტატია ბლოგი ახალი ამბები გაიგე მეტი წაიკითხე სრულად".split()
)
CLITICS = ("აა", "ა", "ც", "ღა", "ვე")  # ვინაა = ვინ + (ა)ა "is", რაც, ესეც
# Words that point a query to a source category (checked against lemmas and families).
INTENT = {
    "law": "კანონი კოდექსი კონსტიტუცია სასამართლო დადგენილება ბრძანებულება მუხლი უფლება ჯარიმა",
    "history": "ისტორია მეფე ომი საუკუნე არქივი ძეგლი ეკლესია მონასტერი",
    "religion": "ლოცვა ხატი წმინდა ეკლესია მონასტერი პატრიარქი ბიბლია დღესასწაული მარხვა",
    "education": "გამოცდა სკოლა უნივერსიტეტი სტუდენტი მასწავლებელი ჩარიცხვა გრანტი",
    "government": "ამინდი კურსი სტატისტიკა არჩევნები გადასახადი პირადობა პასპორტი",
    "culture": "ფილმი მუსიკა სიმღერა პოეზია მხატვარი რეცეპტი",
}
DEFAULT_CATEGORY = "reference"
SITES_PER_QUERY = 6
MIN_GEORGIAN = 0.3      # share of Georgian letters in title + snippet to keep a result
RRF_K = 60
TIER_BONUS = {1: 0.5, 2: 0.25, 3: 0.0}
FAMILY_BONUS = 0.5      # × share of query word families found in title + snippet
FEEDBACK_DOCS = 15      # round-1 results read for feedback terms
FEEDBACK_TERMS = 3
FEEDBACK_MIN_IDF = 4.0  # ignore common words (idf of მსოფლიოში ≈ 3.9, სწრაფი ≈ 5.3, rare names ≈ 9)
MEANING_WEIGHT = 1.5    # meaning rank vs engine rank in the final fusion
COVERAGE_FLOOR = 0.2    # score × (floor + (1 - floor) × coverage)
FEEDBACK_MIN_COVERAGE = 0.99  # feedback reads only results that contain every query word
COPY_SIMILARITY = 0.6   # word overlap (Jaccard) of two snippets that makes them copies
BRAVE_QUERIES = ("corrected", "lemmas")      # Brave API: monthly quota
SEARXNG_SPACING = 0.3   # seconds between SearXNG requests: Google blocks fast bursts
TRACKING = re.compile(r"^(utm_|fbclid|gclid|yclid|mc_|ref$|ref_)")


@dataclass
class Result:
    url: str
    title: str
    snippet: str
    score: float = 0.0
    meaning: float = 0.0
    tier: int | None = None
    category: str | None = None
    georgian: float = 0.0
    coverage: float = 0.0
    kind: str = "web"
    queries: set[str] = field(default_factory=set)
    engines: set[str] = field(default_factory=set)
    hits: list[tuple[str, str, int]] = field(default_factory=list)  # (query name, engine, rank)
    copies: list["Result"] = field(default_factory=list)

    @property
    def text(self) -> str:
        return f"{self.title}. {self.snippet}"


# ---- query understanding ------------------------------------------------

def is_stop(word: str) -> bool:
    return word in STOPWORDS or any(word.endswith(c) and word[: -len(c)] in STOPWORDS for c in CLITICS)


def _fix_word(word: str) -> str:
    """Latin → Georgian. Typos are fixed later, from evidence in the round-1 results."""
    if word.isascii() and word.isalpha():
        return latin_to_georgian(word) or word
    return normalize(word)


def spelling_fixes(content: list[str]) -> dict[str, str]:
    """Unknown query word → the candidate that appears most often with the other query words.

    Counts articles in the local Georgian Wikipedia index (any word form). Word counts alone
    choose badly (კანოები → კანონები "laws"); with აბულაძის the context chooses კინოები (53 articles).
    Score = co-occurrence / √(candidate article count), so very common words do not win by size.
    """
    fixes = {}
    for w in content:
        cands = spell_candidates(w)
        if not cands:
            continue
        context = [c for c in content if c != w and not is_stop(c)]
        if not context:
            fixes[w] = cands[0]
            continue
        scores = {c: sum(wiki.count(c, x) for x in context) / max(wiki.count(c), 1) ** 0.5 for c in cands}
        best = max(scores, key=scores.get)
        if scores[best] > 0:
            fixes[w] = best
    return fixes


def _lemma(word: str) -> str:
    """Dictionary form of nouns and adjectives; verbs and unknown words stay as typed."""
    for a in analyze(word):
        if a.source in ("lexicon", "noun-rule") and a.lemma in family_members(a.family):
            return a.lemma
    return word


def _category(content: list[str]) -> str:
    fams = {f for w in content for f in families(w)} | set(content)
    return next((c for c, ws in INTENT.items() if any(f in fams for f in ws.split())), DEFAULT_CATEGORY)


def round1_queries(query: str) -> tuple[dict[str, str], list[str], dict[str, str]]:
    """Simple recall queries, the content words of the question, and spelling fixes."""
    corrected = [_fix_word(w) for w in query.split()]
    fixes = spelling_fixes([w for w in corrected if not is_stop(w)])
    corrected = [fixes.get(w, w) for w in corrected]
    content = [w for w in corrected if not is_stop(w)] or corrected
    lemmas = " ".join(_lemma(w) for w in content)
    cat = _category(content)
    sites = " OR ".join(f"site:{d}" for d in by_category(cat)[:SITES_PER_QUERY])
    qs = {"original": query, "corrected": " ".join(corrected), "lemmas": lemmas, f"site:{cat}": f"{lemmas} ({sites})"}
    unique: dict[str, str] = {}
    for name, q in qs.items():
        if q not in unique.values():
            unique[name] = q
    return unique, content, fixes


# ---- feedback -----------------------------------------------------------

def _idf(word: str) -> float:
    return math.log(1 + len(words()) / (1 + freq(word)))


def _is_verb(word: str) -> bool:
    return analyze(word)[0].pos == "verb"


def coverage(text: str, content: list[str]) -> float:
    """Share of query words present in the text in any form, weighted by rarity (idf).

    Verbs do not count: answers rephrase them (დავუწიო → სიცხის დამწევი, ღირს → კურსი).
    """
    content = [w for w in content if not _is_verb(w)] or content
    toks = re.findall(r"\w+", normalize(text))
    text_fams = {f for w in toks for f in families(w)} | set(toks)
    weights = [(_idf(w), bool(families(w) & text_fams) or w in text_fams) for w in content]
    total = sum(w for w, _ in weights)
    return sum(w for w, hit in weights if hit) / total if total else 1.0


def feedback_terms(content: list[str], results: list[Result]) -> list[str]:
    """Words and two-word names that repeat in the top results but are rare in Georgian."""
    query_fams = {f for w in content for f in families(w)}
    df: Counter[str] = Counter()
    for r in [r for r in results if r.coverage >= FEEDBACK_MIN_COVERAGE][:FEEDBACK_DOCS]:
        toks = [w for w in re.findall(r"[ა-ჰ]+", normalize(r.text)) if len(w) > 2 and not is_stop(w)]
        new = [w for w in toks if not (families(w) & query_fams)]
        grams = set(new) | {f"{a} {b}" for a, b in zip(toks, toks[1:]) if a in new and b in new}
        df.update(grams)
    idf = {t: sum(_idf(w) for w in t.split()) / len(t.split()) for t in df}
    score = {t: n * idf[t] * len(t.split()) ** 0.5 for t, n in df.items() if n >= 2 and idf[t] >= FEEDBACK_MIN_IDF}
    best: list[str] = []
    for t in sorted(score, key=score.get, reverse=True):
        if not any(t in b or b in t for b in best):  # "უსეინ ბოლტი" replaces "ბოლტი"
            best.append(t)
        if len(best) == FEEDBACK_TERMS:
            break
    return best


# ---- fan-out and merge --------------------------------------------------

async def _run(client: httpx.AsyncClient, name: str, q: str, engine: str, delay: float) -> tuple[str, list[dict]]:
    await asyncio.sleep(delay)
    try:
        fn = engines.brave if engine == "brave-api" else engines.searxng
        return name, await fn(client, q)
    except (httpx.HTTPError, KeyError) as e:
        print(f"  ! {engine} failed for {name}: {type(e).__name__}")
        return name, []


async def fan_out(qs: dict[str, str], brave: tuple[str, ...] = ()) -> list[tuple[str, list[dict]]]:
    async with httpx.AsyncClient() as client:
        jobs = [_run(client, n, q, "searxng", i * SEARXNG_SPACING) for i, (n, q) in enumerate(qs.items())]
        jobs += [_run(client, n, qs[n], "brave-api", 0) for n in brave if n in qs]
        return await asyncio.gather(*jobs)


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


def merge(lists: list[tuple[str, list[dict]]], content: list[str]) -> list[Result]:
    """RRF over all lists, Georgian filter, trust tier and word-family bonuses."""
    merged: dict[str, Result] = {}
    for name, results in lists:
        for rank, r in enumerate(results):
            m = merged.setdefault(canonical_url(r["url"]), Result(r["url"], r["title"], r["snippet"]))
            if len(r["snippet"]) > len(m.snippet):
                m.snippet = r["snippet"]
            m.score += 1 / (RRF_K + rank)
            m.queries.add(name)
            m.engines.update(r["engine"].split("+"))
            m.hits += [(name, e, rank + 1) for e in r["engine"].split("+")]
    query_fams = [families(w) for w in content]
    out = []
    for m in merged.values():
        m.georgian = georgian_ratio(m.title + " " + m.snippet)
        if m.georgian < MIN_GEORGIAN:
            continue
        m.category, m.tier = lookup(m.url) or (None, None)
        m.kind = kind(m.url)
        m.coverage = coverage(m.text, content)
        m.score *= 1 + TIER_BONUS.get(m.tier, 0.0) + FAMILY_BONUS * _family_share(m.text, query_fams)
        out.append(m)
    return sorted(out, key=lambda m: m.score, reverse=True)


def rank_by_meaning(query: str, results: list[Result], known: dict[str, float] | None = None) -> list[Result]:
    """Final order: fusion of the engine rank and the meaning rank. `known` caches scores by URL."""
    known = {} if known is None else known
    todo = [r for r in results if r.url not in known]
    known.update(zip((r.url for r in todo), similarity(query, [r.text for r in todo])))
    for r in results:
        r.meaning = known[r.url]
    by_meaning = {id(r): i for i, r in enumerate(sorted(results, key=lambda r: r.meaning, reverse=True))}
    for i, r in enumerate(results):  # results are in engine order here
        fused = 1 / (RRF_K + i) + MEANING_WEIGHT / (RRF_K + by_meaning[id(r)])
        r.score = fused * (1 + TIER_BONUS.get(r.tier, 0.0)) * (COVERAGE_FLOOR + (1 - COVERAGE_FLOOR) * r.coverage)
    return sorted(results, key=lambda r: r.score, reverse=True)


def group_copies(results: list[Result]) -> list[Result]:
    """The same text on several sites: keep the best-ranked one, attach the rest as copies."""
    kept: list[tuple[Result, set[str]]] = []
    for r in results:
        toks = {w for w in re.findall(r"[ა-ჰ]{3,}", normalize(r.snippet))}
        for k, ktoks in kept:
            if len(toks) >= 8 and len(toks & ktoks) / len(toks | ktoks) >= COPY_SIMILARITY:
                k.copies.append(r)
                break
        else:
            kept.append((r, toks))
    return [k for k, _ in kept]


def search(query: str) -> tuple[dict[str, str], list[Result], dict]:
    """Returns the queries sent, the ranked results, and debug information."""
    t0 = time.time()
    qs, content, fixes = round1_queries(query)
    lists = asyncio.run(fan_out(qs, BRAVE_QUERIES if "corrected" in qs else ("original", "lemmas")))
    known: dict[str, float] = {}
    first = rank_by_meaning(query, merge(lists, content), known)
    t1 = time.time()
    terms = feedback_terms(content, first)
    base = " ".join(_lemma(w) for w in content)
    more = {}
    if terms:
        more[f"feedback:{terms[0]}"] = f"{base} {terms[0]}"
        if len(terms) > 1:
            more[f"feedback:{terms[1]}"] = " ".join(terms[:2])
    if more:
        qs.update(more)
        lists += asyncio.run(fan_out(more))
    results = group_copies(rank_by_meaning(query, merge(lists, content), known))
    debug = {
        "content": content, "spelling": fixes, "feedback": terms,
        "counts": {name: len(res) for name, res in lists},
        "seconds": {"round1": round(t1 - t0, 1), "round2+rank": round(time.time() - t1, 1)},
    }
    return qs, results, debug


if __name__ == "__main__":
    import sys

    t = time.time()
    qs, results, debug = search(" ".join(sys.argv[1:]))
    print(debug)
    for name, q in qs.items():
        print(f"{name:>22}: {q}")
    print(f"\n{len(results)} results in {time.time() - t:.1f}s\n")
    for r in results[:15]:
        tier = f"t{r.tier}" if r.tier else "  "
        print(f"{r.score * 1000:5.1f} m{r.meaning:.2f} {tier} q{len(r.queries)} {r.title[:60]}")
        print(f"                       {r.url[:100]}")
