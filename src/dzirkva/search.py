"""Question -> simple queries -> wide retrieval -> feedback round -> rank by meaning.

1. Round 1: a few simple queries (as typed, corrected, dictionary forms or verb forms with OR, trusted sites).
   A spelling fix is used only when few round-1 pages use the typed word; else the page asks "did you mean"
   (confirm_fixes).
   Their job is recall (collect candidate pages), not precision.
   Search by meaning (passages.py) adds the Wikipedia paragraphs nearest to the question:
   they find answers that use other words than the question.
2. Feedback (only when round 1 is bad: fewer than ROUND1_GOOD of its top 10 contain every query word):
   read the paragraphs nearest in meaning (else the top snippets that contain every
   query word) and find the words and names that repeat there but are rare in Georgian overall
   (ბოლტი, უსწრაფესი, სპრინტერი). These are the words the answer pages use. Round 2 searches with them.
3. Rank: combine the engine ranking (RRF over all lists + trust tier + word-family match; a Wikipedia page
   counts once, with its best rank, though engines, the local index and passages all return it)
   with the meaning ranking (BGE-M3 similarity between the question and each result),
   then × trust tier × coverage (share of query words present, rare words count more:
   a metro-map page without შრიფტი drops for "თბილისის მეტროს შრიფტი").
   Pages that the matching Wikipedia articles cite get a trust bonus like tier 1; the crawled ones join
   the candidates (list "cited"). Wikipedia judges the sources instead of filling the list.
   Pages people chose for the same question before (clicks.py) get CLICK_BONUS per good click.
4. Group: the same text on many sites becomes one result with `copies`.
Only results that are mostly Georgian are kept. `kind` decides the tab (sources.kind), `tags` the filters.
"""

import asyncio
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlunparse

import httpx
import numpy as np
import yaml

from dzirkva import engines
from dzirkva.georgian import freq, georgian_ratio, latin_to_georgian, normalize, spell_candidates, typo_weight, words
from dzirkva.meaning import cached_vectors, vectors
from dzirkva.morph import OTHER_FORMS, analyze, families, family_members, genitive, other_forms
from dzirkva import archive, clicks, crawl, dictionary, iverieli, papers, passages, wiki
from dzirkva.sources import by_category, georgian_hosts, host, kind, lookup, named_sites, tags

# Question words and function words: dropped from keyword queries and feedback terms.
STOPWORDS = set(
    "ვინ რა რას რამ რისი როგორ როგორი სად საიდან საით როდის რატომ რისთვის რომელი რომელიც რამდენი როცა როდესაც "
    "არის არიან იყო იქნება და თუ რომ ეს ის ამ იმ ეგ კი არ ვერ ნუ მაგრამ ან ანუ უნდა შეიძლება "
    "მე შენ ჩვენ თქვენ მისი მათი ჩემი შენი ჩვენი თქვენი ამის იმის აქ იქ ასე ისე ძალიან უფრო "
    "ერთი ორი სამი ყველა ყველაფერი მხოლოდ ასევე თავის თავად შემდეგ წლის წელს მიერ შესახებ "
    # page furniture that repeats in snippets but says nothing about the topic (სტატია stays: research intent;
    # too common to become a feedback term)
    "ვიკიპედია ფოტო ვიდეო ბლოგი ახალი ამბები გაიგე მეტი წაიკითხე სრულად".split()
)
CLITICS = ("აა", "ა", "ც", "ღა", "ვე")  # ვინაა = ვინ + (ა)ა "is", რაც, ესეც
INTENTS_FILE = Path(__file__).resolve().parents[2] / "config" / "intents.yaml"  # what a query wants → its sites
DEFAULT_CATEGORY = "reference"
INTENT_BONUS = 0.25     # page on a site made for what the query wants (intents.yaml): like a tier-2 source
SITES_PER_QUERY = 6
MIN_GEORGIAN = 0.3      # share of Georgian letters in title + snippet to keep a result
RRF_K = 60
TIER_BONUS = {1: 0.5, 2: 0.25, 3: 0.0}
SMALL_BONUS = 0.25     # small, non-commercial, Georgian site found by the crawl (crawl.small_site)
CITED_BONUS = 0.5      # page cited by a Wikipedia article that matches the query
NAMED_BONUS = 1.0      # page on a site the query names (ფეისბუქი → facebook.com)
NAVIGATIONAL = 0.5     # the name covers this share of the query words: search the site itself, show its home page
CITING_ARTICLES = 3    # articles read for citations: top word matches + top meaning matches (+ answer box)
CLICK_BONUS = 0.3      # × good clicks on the same question (max CLICK_MAX)
CLICK_MAX = 3
WIKI_HOSTS = {"ka.wikipedia.org": "wikipedia", "ka.wikisource.org": "wikisource"}
FAMILY_BONUS = 0.5      # × share of query word families found in title + snippet
FEEDBACK_DOCS = 15      # round-1 results read for feedback terms
FEEDBACK_PASSAGES = 10  # or: paragraphs nearest in meaning
FEEDBACK_TERMS = 3
FEEDBACK_MIN_IDF = 4.0  # ignore common words (idf of მსოფლიოში ≈ 3.9, სწრაფი ≈ 5.3, rare names ≈ 9)
MEANING_WEIGHT = 1.5    # meaning rank vs engine rank in the final fusion
MEANING_TOP = 40        # results (engine order) compared by meaning; the rest keep their engine rank
COVERAGE_FLOOR = 0.2    # score × (floor + (1 - floor) × coverage)
LOCAL_FLOOR = 0.3       # a page only our local indexes found: × (floor + (1 - floor) × title fit)
TITLE_FIT = (0.5, 0.65)   # title-query similarity: filler 0.23-0.55, the right article 0.60-1.00 → fit 0..1
LOCAL_LISTS = {"wikipedia", "wikisource", "passages", "papers", "iverieli"}  # papers and catalog records match
# on the abstract and the authors' university: ახალი ამბები found a thesis on translating news
FEEDBACK_MIN_COVERAGE = 0.99  # feedback reads only results that contain every query word
ROUND1_GOOD = 5         # round 1 is good when this many of its top 10 contain every query word: no round 2
SITE_FREE = 2           # results per site before the site penalty (თბილისი: half the page was Wikipedia)
SITE_PENALTY = 0.5      # × for each further result from the same site
SITE_MAX = 5            # results per site at most, unless the query names the site (ჩამოლაბორანტება: 25 Wikipedia pages)
VOICE_BONUS = 0.3       # × (1 + bonus × coverage) for people: small web, blogs, forums, social posts (ხალხი)
COPY_SIMILARITY = 0.6   # word overlap (Jaccard) of two snippets that makes them copies
# Question word → the shape of a text that answers it. A result with that shape gets SHAPE_BONUS.
SHAPES = {
    "why": ("რატომ რისთვის", r"რადგან|იმიტომ|ამიტომ|გამო|მიზეზ|იწვევს|გამოწვეული"),
    "how": ("როგორ", r"ჯერ |შემდეგ|ნაბიჯ|ინსტრუქცი|საჭიროა|\b\d\. "),
    "when": ("როდის", r"\b1[0-9]{3}\b|\b20[0-9]{2}\b|საუკუნ|წელს"),
    "amount": ("რამდენი ღირს", r"\d"),
}
SHAPE_BONUS = 0.3
ANSWER_TYPES = ("why", "how")  # explanation questions
ANSWER_VECTOR = 3       # why/how: results are also compared with the mean of the 3 nearest paragraphs
RELATED = 8             # related searches under the results
BRAVE_QUERY = ("corrected", "original")  # Brave API is paid per call: one per search, the corrected query if any
TYPED_USES = 3          # round-1 pages that use a typed word: a real word, so a fix is only "did you mean"
SEARXNG_SPACING = 0.3   # seconds between SearXNG requests: Google blocks fast bursts
DEEP = 3                # deep search (button): × pages, site queries, feedback terms and queries, word forms,
                        # local results and results read by meaning; Brave stays one call (paid)
TRACKING = re.compile(r"^(utm_|fbclid|gclid|yclid|mc_|ref$|ref_|locale$)")  # locale: DSpace UI language, same record


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
    small: bool = False
    named: bool = False
    wanted: bool = False  # on a site made for what the query wants (intents.yaml)
    cited: bool = False
    clicks: int = 0        # good clicks on this page for the same question
    rank: int = 0          # position in the final list (1 = first)
    kind: str = "web"
    tags: set[str] = field(default_factory=set)  # filters: sources.FILTERS
    queries: set[str] = field(default_factory=set)
    engines: set[str] = field(default_factory=set)
    hits: list[tuple[str, str, int]] = field(default_factory=list)  # (query name, engine, rank)
    copies: list["Result"] = field(default_factory=list)
    vector: np.ndarray | None = None  # BGE-M3; wiki pages: their paragraph's stored vector (passages.best)

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
    """Likely typo (georgian.spell_candidates) → the candidate that appears most often with the other query words.

    Counts articles in the local Georgian Wikipedia index (any word form). Word counts alone
    choose badly (კანოები → კანონები "laws"); with აბულაძის the context chooses კინოები (53 articles).
    Score = co-occurrence / √(candidate article count), so very common words do not win by size.
    No context evidence: the first candidate (the frequency test already says the typed word is wrong).
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
        scores = {c: typo_weight(w, c) * sum(wiki.count(c, x) for x in context) / max(wiki.count(c), 1) ** 0.5
                  for c in cands}
        best = max(scores, key=scores.get)
        fixes[w] = best if scores[best] > 0 else cands[0]
    return fixes


def _lemma(word: str) -> str:
    """Dictionary form of nouns and adjectives; verbs and unknown words stay as typed."""
    for a in analyze(word):
        if a.source in ("lexicon", "noun-rule") and a.lemma in family_members(a.family):
            return a.lemma
    return word


@cache
def intents() -> dict[str, dict]:
    """config/intents.yaml: name → {ka, words (set), phrases (list), sites (hosts + the trusted category's
    + the repositories of papers.py when the intent has papers: true)}."""
    out = {}
    for name, it in yaml.safe_load(INTENTS_FILE.read_text(encoding="utf-8")).items():
        words = it["words"].split()
        sites = it["sites"].split() + (by_category(it["trusted"]) if "trusted" in it else [])
        sites += sorted(papers.hosts()) if it.get("papers") else []  # every journal and repository we harvest
        out[name] = {"ka": it["ka"], "encyclopedia": it.get("encyclopedia", True),
                     "words": {w for w in words if "_" not in w},
                     "phrases": [w.replace("_", " ") for w in words if "_" in w], "sites": list(dict.fromkeys(sites))}
    return out


def _lemmas(word: str) -> set[str]:
    """The word and its dictionary forms (guesses of the verb rules left out: შემოსილი is not მოსავს)."""
    return {word} | {a.lemma for a in analyze(word) if a.source != "verb-rule"}


@cache
def _share(word: str) -> float:
    """A word's weight: 1 / the number of intents that list it (ბილეთი: driving, trains, events)."""
    return 1 / sum(word in it["words"] for it in intents().values())


def intent(content: list[str], text: str) -> str | None:
    """What the query wants: the intent whose matching words weigh most. A word listed by several intents splits
    its weight (_share): სათხილამურო ბილეთი is travel. A phrase weighs 1 and matches the query text with its
    small words (არ მუშაობს). Ties: the first intent in the file."""
    lemmas = set().union(*map(_lemmas, content)) if content else set()
    text = f" {text} "
    score = {name: sum(map(_share, it["words"] & lemmas)) + sum(f" {p} " in text for p in it["phrases"])
             for name, it in intents().items()}
    best = max(score, key=score.get)
    return best if score[best] else None


def forms_query(content: list[str], how: bool, limit: int = OTHER_FORMS) -> str | None:
    """The question as a text says it (morph.other_forms); None when nothing changes.

    A verbal noun joins its verb forms with OR, stories tell the action: ჩამოლაბორანტება →
    (ჩამოლაბორანტება OR ჩამომალაბორანტეს OR ჩამოლაბორანტებული OR ჩამოალაბორანტა).
    A "how" question names the action: its verb becomes the verbal noun after the rest, and the last word in the
    nominative goes to the genitive. როგორ გავაკეთოთ ღვინო → ღვინის გაკეთება.
    Only these two: an engine (Yandex) mixes OR groups of every word into noise, and a verb outside a "how"
    question is often a quote (კაცი გზაზე მიდიოდა)."""
    forms = {w: other_forms(w, limit) for w in content}
    verbs = [w for w in content if forms[w][0] == "noun" and forms[w][1]]
    if how and len(verbs) == 1 and (rest := [w for w in content if w != verbs[0]]):
        nominative = [i for i, w in enumerate(rest) if w == _lemma(w) and not _is_verb(w)]
        if nominative:  # წითელი ღვინო სახლში → წითელი ღვინის სახლში
            rest[nominative[-1]] = genitive(rest[nominative[-1]])
        return " ".join(rest + forms[verbs[0]][1][:1])
    groups = [f"({' OR '.join([w, *fs])})" if kind == "verb" and fs else w for w, (kind, fs) in forms.items()]
    return " ".join(groups) if any(kind == "verb" and fs for kind, fs in forms.values()) else None


def variants_query(content: list[str], limit: int) -> str | None:
    """Deep search: every word in its forms, joined by OR (verbs: other_forms; nouns: dictionary form, genitive).
    Noisy on Yandex, so only when the visitor asks for more."""
    groups = []
    for w in content:
        forms = [w, *other_forms(w, limit)[1]] if _is_verb(w) else [w, _lemma(w), genitive(_lemma(w))]
        forms = list(dict.fromkeys(forms))
        groups.append(forms[0] if len(forms) == 1 else f"({' OR '.join(forms)})")
    return " ".join(groups) if any(g.startswith("(") for g in groups) else None


def _on(url: str, hosts: set[str]) -> bool:
    h = host(url)
    return h in hosts or any(h.endswith("." + x) for x in hosts)


def round1_queries(query: str, deep: int = 1) -> tuple[dict[str, str], list[str], dict[str, str], list[tuple[str, float, str]]]:
    """Simple recall queries, the content words as typed, the spelling fixes to check, and the sites the query names.
    deep (DEEP for deep search): × site queries and word forms; dictionary forms, forms and variants all go.

    The query is always searched as typed (Latin letters turned into Georgian) and with its dictionary forms.
    Words that look like typos are also searched corrected; confirm_fixes decides after round 1."""
    typed = [_fix_word(w) for w in query.split()]
    content = [w for w in typed if not is_stop(w)] or typed
    fixes = spelling_fixes(content)
    fixed = [fixes.get(w, w) for w in content]
    lemmas, fixed_lemmas = (" ".join(_lemma(w) for w in ws) for ws in (content, fixed))
    want = intent(fixed, " ".join(fixes.get(w, w) for w in typed))
    hosts = intents()[want]["sites"] if want else by_category(DEFAULT_CATEGORY)
    forms = forms_query(fixed, question_type(query) == "how", OTHER_FORMS * deep)
    qs = {"original": query, "corrected": " ".join(fixes.get(w, w) for w in typed)}
    if not forms or deep > 1:  # else forms replace the dictionary forms: აკეთებს ღვინო is no text people write
        qs |= {"lemmas": lemmas, "lemmas:corrected": fixed_lemmas}
    if forms:
        qs["forms"] = forms
    if deep > 1 and (variants := variants_query(fixed, OTHER_FORMS * deep)):
        qs["variants"] = variants
    for i in range(deep):
        if chunk := hosts[i * SITES_PER_QUERY:(i + 1) * SITES_PER_QUERY]:
            sites = " OR ".join(f"site:{d}" for d in chunk)
            qs[f"site:{want or DEFAULT_CATEGORY}" + (f":{i + 1}" if i else "")] = f"{fixed_lemmas} ({sites})"
    named = named_sites(query.split(), fixed, fixed_lemmas.split())
    for h, share, name in named[:1]:
        if share >= NAVIGATIONAL:  # ფეისბუქი შესვლა → შესვლა site:facebook.com
            rest = [w for w in fixed if w not in name.split() and _lemma(w) not in name.split()]
            qs[f"named:{h}"] = " ".join(rest + [f"site:{h}"])
    unique: dict[str, str] = {}
    for name, q in qs.items():
        if q not in unique.values():
            unique[name] = q
    return unique, content, fixes, named


def _uses(word: str, texts: list[str]) -> int:
    """Texts that contain the word in some form (the stem without its last letter starts a token)."""
    stem = word[:-1] if len(word) > 4 else word
    return sum(any(t.startswith(stem) for t in re.findall(r"[ა-ჰ]+", text)) for text in texts)


def confirm_fixes(fixes: dict[str, str], lists: list[tuple[str, list[dict]]]) -> tuple[dict[str, str], dict[str, str]]:
    """Spelling fixes → (used, only suggested). A typed word that TYPED_USES round-1 pages of the typed query
    use is a real word: the ranking keeps it and the page only asks "did you mean". Engines that correct
    silently return pages without the typed word, so a real typo finds few uses."""
    texts = list({canonical_url(r["url"]): normalize(f"{r['title']} {r['snippet']}")
                  for name, res in lists if name in ("original", "lemmas") for r in res}.values())
    used = {w: f for w, f in fixes.items() if _uses(w, texts) < TYPED_USES}
    return used, {w: f for w, f in fixes.items() if w not in used}


# ---- feedback -----------------------------------------------------------

def _idf(word: str) -> float:
    return math.log(1 + len(words()) / (1 + freq(word)))


def _is_verb(word: str) -> bool:
    return analyze(word)[0].pos == "verb"


def coverage(text: str, content: list[str], verbs: bool = False) -> float:
    """Share of query words present in the text in any form, weighted by rarity (idf).

    Verbs do not count: answers rephrase them (დავუწიო → სიცხის დამწევი, ღირს → კურსი). A title check counts them.
    """
    content = content if verbs else [w for w in content if not _is_verb(w)] or content
    toks = re.findall(r"\w+", normalize(text))
    text_fams = {f for w in toks for f in families(w)} | set(toks)
    weights = [(_idf(w), bool(families(w) & text_fams) or w in text_fams) for w in content]
    total = sum(w for w, _ in weights)
    return sum(w for w, hit in weights if hit) / total if total else 1.0


def feedback_terms(content: list[str], texts: list[str], n: int = FEEDBACK_TERMS) -> list[str]:
    """Words and two-word names that repeat in the texts but are rare in Georgian."""
    query_fams = {f for w in content for f in families(w)}
    df: Counter[str] = Counter()
    for text in texts:
        toks = [w for w in re.findall(r"[ა-ჰ]+", normalize(text)) if len(w) > 2 and not is_stop(w)]
        new = [w for w in toks if not (families(w) & query_fams)]
        grams = set(new) | {f"{a} {b}" for a, b in zip(toks, toks[1:]) if a in new and b in new}
        df.update(grams)
    idf = {t: sum(_idf(w) for w in t.split()) / len(t.split()) for t in df}
    score = {t: n * idf[t] * len(t.split()) ** 0.5 for t, n in df.items() if n >= 2 and idf[t] >= FEEDBACK_MIN_IDF}
    best: list[str] = []
    for t in sorted(score, key=score.get, reverse=True):
        if not any(t in b or b in t for b in best):  # "უსეინ ბოლტი" replaces "ბოლტი"
            best.append(t)
        if len(best) == n:
            break
    return best


# ---- fan-out and merge --------------------------------------------------

async def _run(client: httpx.AsyncClient, name: str, q: str, engine: str, delay: float,
               pages: int = 1) -> tuple[str, list[dict]]:
    """One query; SearXNG pages 2, 3 … (deep search) join the same list, one after another (Google blocks bursts)."""
    await asyncio.sleep(delay)
    try:
        if engine == "brave-api":
            return name, await engines.brave(client, q)
        out = []
        for page in range(1, pages + 1):
            out += await engines.searxng(client, q, page)
            await asyncio.sleep(SEARXNG_SPACING if page < pages else 0)
        return name, out
    except (httpx.HTTPError, KeyError) as e:
        print(f"  ! {engine} failed for {name}: {type(e).__name__}")
        return name, []


async def fan_out(qs: dict[str, str], brave: tuple[str, ...] = (),
                  pages: dict[str, int] | None = None) -> list[tuple[str, list[dict]]]:
    pages = pages or {}
    async with httpx.AsyncClient() as client:
        jobs = [_run(client, n, q, "searxng", i * SEARXNG_SPACING, pages.get(n, 1)) for i, (n, q) in enumerate(qs.items())]
        jobs += [_run(client, n, qs[n], "brave-api", 0) for n in brave if n in qs]
        return await asyncio.gather(*jobs)


def canonical_url(url: str) -> str:
    """Same page, one key: https, no www, one percent-encoding, no tracking parameters, no fragment, no trailing slash."""
    p = urlparse(url)
    host = (p.hostname or "").removeprefix("www.")
    query = urlencode([(k, v) for k, v in parse_qsl(p.query) if not TRACKING.match(k)])
    return urlunparse(("https", host, quote(unquote(p.path)).rstrip("/") or "/", "", query, ""))


def _family_share(text: str, query_fams: list[set[str]]) -> float:
    if not query_fams:
        return 0.0
    text_fams = {f for w in re.findall(r"[ა-ჰ]+", normalize(text)) for f in families(w)}
    return sum(bool(fs & text_fams) for fs in query_fams) / len(query_fams)


def _trust(r: Result) -> float:
    return max(TIER_BONUS.get(r.tier, 0.0), SMALL_BONUS if r.small else 0.0, CITED_BONUS if r.cited else 0.0,
               NAMED_BONUS if r.named else 0.0, INTENT_BONUS if r.wanted else 0.0)


def _wiki_page(url: str) -> tuple[str, str] | None:
    """(site, article title) of a Georgian Wikipedia or Wikisource URL."""
    p = urlparse(url)
    site = WIKI_HOSTS.get(p.hostname or "")
    return (site, unquote(p.path[6:]).replace("_", " ")) if site and p.path.startswith("/wiki/") else None


def merge(lists: list[tuple[str, list[dict]]], content: list[str], cited: set[str] = frozenset(),
          clicked: Counter[str] = Counter(), named: set[str] = frozenset(),
          wanted: set[str] = frozenset()) -> list[Result]:
    """RRF over all lists (Wikipedia pages: best rank only), Georgian filter, trust tier and word-family bonuses.

    cited: canonical URLs of the pages the matching Wikipedia articles cite; clicked: good clicks per canonical URL."""
    merged: dict[str, Result] = {}
    for name, results in lists:
        for rank, r in enumerate(results):
            m = merged.setdefault(canonical_url(r["url"]), Result(r["url"], r["title"], r["snippet"]))
            if len(r["snippet"]) > len(m.snippet):
                m.snippet = r["snippet"]
            rrf = 1 / (RRF_K + rank)
            m.score = max(m.score, rrf) if urlparse(m.url).hostname in WIKI_HOSTS else m.score + rrf
            m.queries.add(name)
            m.engines.update(r["engine"].split("+"))
            m.hits += [(name, e, rank + 1) for e in r["engine"].split("+")]
    query_fams = [families(w) for w in content]
    out = []
    for m in merged.values():
        m.georgian = georgian_ratio(m.title + " " + m.snippet)
        m.named, m.wanted = _on(m.url, named), _on(m.url, wanted)
        if m.georgian < MIN_GEORGIAN and not m.named and host(m.url) not in georgian_hosts():  # Latin title, Georgian site
            continue
        m.category, m.tier = lookup(m.url) or (None, None)
        m.kind = kind(m.url)
        m.coverage = coverage(m.text, content)
        m.small = crawl.small_site(m.url)
        m.cited = canonical_url(m.url) in cited
        m.clicks = clicked[canonical_url(m.url)]
        m.tags = tags(m.url, m.title, crawl.domain_signals(m.url), m.small, papers.is_repo(m.url))
        m.score *= 1 + _trust(m) + FAMILY_BONUS * _family_share(m.text, query_fams)
        out.append(m)
    return sorted(out, key=lambda m: m.score, reverse=True)


def question_type(query: str) -> str | None:
    """why / how / when / amount from the question word, clitics removed (რამდენია = რამდენი + ა)."""
    for w in map(normalize, query.split()):
        for t, (qwords, _) in SHAPES.items():
            if any(w == q or w in (q + c for c in CLITICS) for q in qwords.split()):
                return t
    return None


def answer_vector(near: list[dict]):
    """Questions and answers read differently (რატომ წითლდება ≠ მიმოფანტვის გამო): the mean of the
    nearest paragraphs is a vector of the answer, like HyDE but with real paragraphs, no LLM."""
    v = vectors([p["snippet"] for p in near[:ANSWER_VECTOR]]).mean(0)
    return v / np.linalg.norm(v)


def wiki_snippets(results: list[Result], qv, content: list[str]) -> list[Result]:
    """Wikipedia and Wikisource results show their paragraph nearest to the question, not the engine's snippet,
    and keep its stored vector: two thirds of the results need no new embedding."""
    for r in results:
        if page := _wiki_page(r.url):
            if hit := passages.best(page[1], qv, page[0]):
                r.snippet, r.vector = hit
                r.coverage = coverage(r.text, content)
    return results


def _title_fit(r: Result, content: list[str], qv) -> float:
    """0-1: is the article about the query? Its title vector against the query vector (wiki.title_similarity);
    word coverage of the title if the title has no vector. ქართულათ ფილმები fits ქართული ფილმი (0.75),
    not ჯეიმზ ბონდის ფილმების სია (0.37)."""
    page = _wiki_page(r.url)
    sim = wiki.title_similarity(*page, qv) if page else None
    if sim is None:
        return coverage(r.title, content, verbs=True)
    low, high = TITLE_FIT
    return min(max((sim - low) / (high - low), 0.0), 1.0)


def rank_by_meaning(query: str, results: list[Result], content: list[str], qv, answer=None,
                    encyclopedia: bool = True, topic: list[str] | None = None, top: int = MEANING_TOP) -> list[Result]:
    """Final order: fusion of the engine rank and the meaning rank.

    Meaning = similarity to the question; with an answer vector, the mean of both. Only the top
    MEANING_TOP results in engine order get a vector (cached, meaning.cached_vectors; wiki pages have their
    paragraph's vector already); results below rank 40 seldom reach the top 10, so they keep their engine rank
    as their meaning rank.
    A Wikipedia or Wikisource page that only our local indexes found needs its title to be about the query
    (_title_fit): the body of a long article mentions every word somewhere (აფთიაქი ღამის → აღდგომის კუნძული).
    A query that wants a service (encyclopedia=False: a pharmacy, a flat) gets no such page high, however close
    its title (აფთიაქი ღამის → პოლარული ღამე). topic: the query words without research words (დისერტაცია):
    the title of a thesis on ვეფხისტყაოსანი fits ვეფხისტყაოსანი დისერტაცია.
    """
    qtype = question_type(query)
    shape = re.compile(SHAPES[qtype][1]) if qtype else None
    top = results[:top]
    new = [r for r in top if r.vector is None]
    for r, v in zip(new, cached_vectors([r.text for r in new]) if new else []):
        r.vector = v
    for r in top:
        r.meaning = float(r.vector @ qv)
        if answer is not None:
            r.meaning = (r.meaning + float(r.vector @ answer)) / 2
    by_meaning = {id(r): i for i, r in enumerate(sorted(top, key=lambda r: r.meaning, reverse=True))}
    for i, r in enumerate(results):  # results are in engine order here
        fused = 1 / (RRF_K + i) + MEANING_WEIGHT / (RRF_K + by_meaning.get(id(r), i))
        r.score = fused * (1 + _trust(r)) * (COVERAGE_FLOOR + (1 - COVERAGE_FLOOR) * r.coverage)
        if shape and shape.search(r.snippet):
            r.score *= 1 + SHAPE_BONUS
        r.score *= 1 + CLICK_BONUS * min(r.clicks, CLICK_MAX)
        if r.queries <= LOCAL_LISTS:  # no web engine found it: a long text mentions every word somewhere
            r.score *= LOCAL_FLOOR + (1 - LOCAL_FLOOR) * (_title_fit(r, topic or content, qv) if encyclopedia else 0.0)
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


def _site(url: str) -> str:
    """Site of a result for diversify: Wikipedia and Wikisource are one site, an archive copy is its old site."""
    h = host(url)
    if h == "web.archive.org" and (m := re.match(r"https?://web\.archive\.org/web/[^/]+/(.+)", url)):
        h = host(m[1] if "://" in m[1] else "http://" + m[1])
    return "wiki" if h in WIKI_HOSTS else h


def diversify(results: list[Result], named_hosts: set[str] = frozenset()) -> list[Result]:
    """Many sites, not one: after SITE_FREE results from a site, each further one gets × SITE_PENALTY,
    and after SITE_MAX the rest go (not for a site the query names). People (small web, blogs, forums) get
    VOICE_BONUS when they use the query words: institutions fill the top otherwise."""
    seen: Counter[str] = Counter()
    for r in results:
        if r.small or "people" in r.tags:
            r.score *= 1 + VOICE_BONUS * r.coverage
        site = _site(r.url)
        r.score *= SITE_PENALTY ** max(0, seen[site] - SITE_FREE + 1)
        seen[site] += 1
    results = sorted(results, key=lambda r: r.score, reverse=True)
    kept: Counter[str] = Counter()
    out = []
    for r in results:
        site = _site(r.url)
        kept[site] += 1
        if kept[site] <= SITE_MAX or host(r.url) in named_hosts:
            out.append(r)
    return out


def related(content: list[str], base: str, terms: list[str], wiki_hits: list[dict], near: list[dict],
            answer: dict | None) -> list[tuple[str, str]]:
    """Related searches without an LLM: (query, source).

    wiki: narrower Wikipedia titles with every query word (თბილისის მეტრო → ღრმაღელე (თბილისის მეტრო));
    feedback: the query + a word the answer pages use; meaning: articles nearest in meaning (passages.py).
    """
    strip = lambda t: t.removesuffix(" · ვიკიპედია")
    cands = [(strip(h["title"]), "wiki") for h in wiki_hits if coverage(h["title"], content) >= FEEDBACK_MIN_COVERAGE][:4]
    cands += [(f"{base} {t}", "feedback") for t in terms]
    cands += [(strip(p["title"]), "meaning") for p in near if p["title"].endswith(" · ვიკიპედია")]  # not papers
    seen = {normalize(" ".join(content)), normalize(answer["title"]) if answer else ""}
    out = []
    for q, source in cands:
        if normalize(q) not in seen:
            seen.add(normalize(q))
            out.append((q, source))
    return out[:RELATED]


def click_key(content: list[str]) -> str:
    """The question for clicks.py: content words in dictionary form, sorted."""
    return " ".join(sorted(_lemma(w) for w in content))


def search(query: str, deep: bool = False) -> tuple[dict[str, str], list[Result], dict]:
    """Returns the queries sent, the ranked results, and debug information (with the answer box).
    deep: DEEP × more of everything (pages, queries, feedback, forms, local results); slower."""
    t0 = time.time()
    m = DEEP if deep else 1
    qs, typed, fixes, named = round1_queries(query, m)
    pages = {n: m for n in ("original", "corrected")}
    lists = asyncio.run(fan_out(qs, (next(n for n in BRAVE_QUERY if n in qs),), pages))
    named_hosts = {h for h, _, _ in named}
    lists.append(("named", [{"url": f"https://{h}/", "title": name, "snippet": "", "engine": "named"}
                            for h, share, name in named if share >= NAVIGATIONAL]))
    fixes, suggested = confirm_fixes(fixes, lists)
    content = [fixes.get(w, w) for w in typed]
    want = intent(content, " ".join(fixes.get(w, w) for w in map(_fix_word, query.split())))
    wanted = set(intents()[want]["sites"]) if want else set()
    lists.append(("wikipedia", wiki.search(content, 20 * m)))   # local Georgian Wikipedia, every search
    lists.append(("wikisource", wiki.search(content, 10 * m, "wikisource")))  # classic texts: poems, prose, laws
    lists.append(("archive", archive.search(content, 20 * m)))  # old Georgian web, local index
    lists.append(("crawl", crawl.search(content, 20 * m)))  # trusted sites, own crawl
    lists.append(("iverieli", iverieli.search(content, 10 * m)))  # National Library catalog: books, journals, press
    # Georgian journals and university repositories; research words (დისერტაცია, სტატია) name the kind of text,
    # not its topic: an abstract seldom says them. დისერტაცია puts theses first.
    topic = [w for w in content if not _lemmas(w) & intents()["research"]["words"]] or content
    kinds = {k for w in content for k in _lemmas(w) if k in papers.KIND_WORDS}
    lists.append(("papers", papers.search(topic, 10 * m, kinds=kinds)))
    qv = vectors([query])[0]
    near = passages.search(query, 20 * m, qv=qv)     # Wikipedia paragraphs nearest in meaning
    lists.append(("passages", near))
    answer = wiki.article(content)
    wiki_hits = next(res for name, res in lists if name == "wikipedia")
    articles = [answer["title"]] if answer else []
    articles += [t for _, t in filter(None, map(_wiki_page, [h["url"] for h in wiki_hits[:CITING_ARTICLES * m]]))]
    articles += [t for site, t in filter(None, map(_wiki_page, [p["url"] for p in near])) if site == "wikipedia"][
        :CITING_ARTICLES * m]
    cites = wiki.cites(articles)
    cited = {canonical_url(u) for u in cites}
    key = click_key(content)
    clicked = clicks.good(key)
    lists.append(("cited", crawl.search(content, 10 * m, cites) if cites else []))  # crawled pages the articles cite
    # explanations (why/how): answer vector and feedback from the nearest paragraphs; names and facts: from
    # the covered snippets (paragraphs about "the fastest" drift to cars and trains, the snippets name ბოლტი)
    explain = question_type(query) in ANSWER_TYPES and bool(near)
    answer_v = answer_vector(near) if explain else None
    merged = merge(lists, content, cited, clicked, named_hosts, wanted)
    encyclopedia = intents()[want]["encyclopedia"] if want else True
    first = rank_by_meaning(query, wiki_snippets(merged, qv, content), content, qv, answer_v, encyclopedia, topic,
                            MEANING_TOP * m)
    t1 = time.time()
    covered = [r.text for r in first if r.coverage >= FEEDBACK_MIN_COVERAGE][:FEEDBACK_DOCS * m]
    terms = feedback_terms(content, [p["snippet"] for p in near[:FEEDBACK_PASSAGES * m]] if explain else covered,
                           FEEDBACK_TERMS * m)
    # verbs stay out: ბნელდება would bring back the eclipse pages (დაბნელება)
    base = " ".join(_lemma(w) for w in content if not _is_verb(w)) or " ".join(_lemma(w) for w in content)
    more = {}
    bad = sum(r.coverage >= FEEDBACK_MIN_COVERAGE for r in first[:10]) < ROUND1_GOOD
    if terms and (bad or deep):  # deep: always, one query per term for the first DEEP terms
        for t in terms[:m]:
            more[f"feedback:{t}"] = f"{base} {t}"
        if len(terms) > 1:
            more[f"feedback:{terms[1]}" if m == 1 else f"feedback:{terms[0]} {terms[1]}"] = " ".join(terms[:2])
    if more:
        qs.update(more)
        lists += asyncio.run(fan_out(more))
        merged = merge(lists, content, cited, clicked, named_hosts, wanted)
        first = rank_by_meaning(query, wiki_snippets(merged, qv, content), content, qv, answer_v, encyclopedia, topic,
                                MEANING_TOP * m)
    results = diversify(group_copies(first), named_hosts)
    for i, r in enumerate(results, 1):
        r.rank = i
    debug = {
        "content": content, "deep": deep, "key": key, "type": question_type(query), "spelling": fixes, "did_you_mean": suggested, "named": [h for h, _, _ in named], "intent": want, "read_as": " ".join(fixes.get(w, w) for w in map(_fix_word, query.split())), "feedback": terms if more else [], "answer": answer,
        "related": related(content, base, terms, wiki_hits, near, answer),
        "definition": dictionary.define(qs.get("corrected", query)),  # "სახლი რას ნიშნავს"
        "counts": {name: len(res) for name, res in lists}, "cites": len(cites),
        "seconds": {"round1": round(t1 - t0, 1), "round2+rank": round(time.time() - t1, 1)},
    }
    return qs, results, debug


if __name__ == "__main__":
    import sys

    t = time.time()
    deep = "--deep" in sys.argv
    qs, results, debug = search(" ".join(a for a in sys.argv[1:] if a != "--deep"), deep)
    print(debug)
    for name, q in qs.items():
        print(f"{name:>22}: {q}")
    print(f"\n{len(results)} results in {time.time() - t:.1f}s\n")
    for r in results[:15]:
        tier = f"t{r.tier}" if r.tier else "  "
        print(f"{r.score * 1000:5.1f} m{r.meaning:.2f} {tier} q{len(r.queries)} {r.title[:60]}")
        print(f"                       {r.url[:100]}")
