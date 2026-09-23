# dzirkva plan

Georgian-only search engine. Meta-search (SearXNG + Brave API) + Georgian language layer + trusted source list.

Budget rules:
- Search time uses no LLM and no paid tokens: only code and free local models.
- Claude Code tokens pay only for writing code. Keep the code small (~1,000 lines).
- Brave API: 1–2 query variants per search. Cache everything.

## Session 1: Setup
- [x] git repo, `uv` project (Python 3.12), `CLAUDE.md`
- [x] SearXNG from source in `vendor/searxng` (no Docker), start script `scripts/searxng.sh`
- [x] SearXNG engines: google, bing, brave (duckduckgo removed: CAPTCHA)
- [x] Brave API client + SearXNG client in `src/dzirkva/engines.py`
- [x] Secrets in `.env` (gitignored)
- [x] Evaluation queries in `eval/queries.tsv`: 30 common + 221 from Safari history (gitignored)

## Session 2: Georgian language layer
- [x] Word list: 493k word forms with counts from ka.wikipedia (`scripts/build_words.py`)
- [x] Lexicon: Wiktionary (kaikki.org) 488k forms → lemma + word family, suppletive verbs (`scripts/build_lexicon.py`)
- [x] Normalizer: Unicode NFC, Mtavruli → Mkhedruli, AcadNusx/keyboard Latin → Georgian (`georgian.py`)
- [x] Latin → Georgian: phonetic spellings (ts, ch, kh, q) → candidates, keep real words
- [x] Spelling fix for unknown words, with Georgian confusion pairs (თ/ტ, ქ/კ/ყ, ც/წ, ჩ/ჭ, ფ/პ)
- [x] Noun rules: 7 cases (full and short), fused postpositions, -ებ- and archaic plural, truncation, syncope, superlative, particles (`morph.py`)
- [x] Verb rules: preverb, person, version, thematic, passive, screeve endings, perfect + auxiliary, participles, verbal nouns
- [x] Accuracy check vs UniMorph (`scripts/check_morph.py`): nouns 100%, adjectives 96%, verbs 88%, 23/23 hand pairs

## Session 3: Trusted source list (`config/sources.yaml`)
- [x] 101 sources chosen for information quality, 15 categories: reference (Wikipedia, Wikisource, Iverieli, NPLG, ena.ge, National Corpus, manuscripts), science, history, religion, culture, education, law, government, news, investigation, economy, community (forum.ge), sport, services
- [x] Tiers: 1 = primary/official/scholarly/reference, 2 = journalism/analysis/institutions/communities, 3 = listings, tabloid-style
- [x] Removed commercial sites (banks, telecom, pharmacies, marketplaces) and tabloids
- [x] `scripts/check_sources.py`: 88 OK, 13 multilingual (Georgian filter handles them), 0 BAD
- [x] `src/dzirkva/sources.py`: URL → (category, tier), subdomains included
- [x] Fix: SearXNG had Google and Bing disabled by default; now enabled
- [x] Found: Google blocks SearXNG with CAPTCHA after a few dozen fast queries → session 4 needs per-engine limits + Brave API fallback

## Session 4: Query variants and fan-out (`src/dzirkva/search.py`)
- [x] Variants: original, corrected (Latin → Georgian, typos), lemmas, expanded (OR groups of forms, family members, Wiktionary synonyms), `site:` by detected category
- [x] Category detection from query words (law, history, reference, religion, education, government, news, culture)
- [x] Parallel fan-out: SearXNG for all variants (0.3 s spacing), Brave API for 2 (corrected/original + lemmas)
- [x] Merge with RRF + tier bonus + word-family match bonus (query families in title/snippet)
- [x] Duplicate URLs removed (https, no www, no tracking parameters, no fragment)
- [x] Engines re-tested with Georgian: Google + Yandex + Yahoo kept; Bing (junk for Georgian), Brave scraper, DuckDuckGo, Qwant removed
- [x] Synonyms: 4,126 pairs from Wiktionary (`data/synonyms.tsv`)

## Session 5: Filter, rerank, cache
- [ ] Georgian filter: keep results with >50% Georgian letters in title + snippet
- [ ] Local reranker `bge-reranker-v2-m3` orders the top 50
- [ ] SQLite cache: full result page (1 day, news 1 hour), single engine query (7 days)
- [ ] Engine limits: max queries per engine, wait longer after a block, other engines continue

## Session 6: Web page and quality check
- [ ] FastAPI + one HTML page, interface in Georgian
- [ ] Links first; show source tier and which variants found each result
- [ ] Mark good results for 50 queries from `eval/queries.tsv` (one time)
- [ ] Eval script: share of queries with a good result in the top 10; plain Google as baseline
- [ ] One tuning pass: fix the worst queries, keep only changes that raise the score

## Later (optional)
- [ ] Own crawl of the trusted sites + local index (SQLite FTS5 + BGE-M3 vectors)
- [ ] Answer box through `claude -p` (personal use only)
- [ ] Entity names from Wikidata: თბილისი = Tbilisi = Тбилиси
- [ ] Facebook: opt-in page connect, public Telegram channels
