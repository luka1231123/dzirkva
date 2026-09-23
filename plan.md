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

## Session 5: Ranking by meaning, tabs, grouping
- [x] Georgian filter: keep results with >30% Georgian letters in title + snippet
- [x] Meaning ranking: local BGE-M3 embeddings (`meaning.py`), fused with engine rank
- [x] Feedback round: rare terms from top results that contain all query words → second search
- [x] Coverage: rare query nouns count more; verbs do not count (answers rephrase them)
- [x] Trust tier kept in the final score
- [x] Copies grouped: same text on many sites → one result + "also on N"
- [x] Tabs: ყველა, ცოდნა, სიახლეები, ვიდეო, ფილმები, სოციალური; All tab: max 2 per site, video/film/social blocks
- [x] Spelling in context: candidates one edit away, chosen by co-occurrence with the other query words in local Wikipedia (`wiki.py`, `data/wiki.db`, 174k articles)
- [x] Web page: highlighted matched words (yellow = query, blue = feedback), why-line and 'found by' per result, debug panel, 'showing results for'
- [x] Manual review of 14 queries (Claude read and rated results): 9 good, 3 mixed, 2 fail
- [x] Wikipedia answer box: the query names an article title exactly (any word form) → first sentences + link (`wiki.article`)
- [ ] Answer boxes with live data: currency (NBG API), weather — like Google's widgets
- [x] SQLite cache of engine answers (`data/cache.db`): SearXNG 24 h, Brave 7 days; empty answers are not cached
- [x] Google back-off: after a CAPTCHA, Google is left out for 1, 2, 4, 8, 24 h (block in a row); Yandex + Yahoo continue
- Known limit: questions that need world knowledge (ვინაა ყველაზე ჩქარი მორბენალი → უსეინ ბოლტი) fail

## Session 6: Web page and quality check
- [ ] FastAPI + one HTML page, interface in Georgian
- [ ] Links first; show source tier and which variants found each result
- [ ] Mark good results for 50 queries from `eval/queries.tsv` (one time)
- [ ] Eval script: share of queries with a good result in the top 10; plain Google as baseline
- [ ] One tuning pass: fix the worst queries, keep only changes that raise the score

## Session 5b: Old Georgian web (Internet Archive)
- [x] Seeds: 36,380 .ge links cited in Georgian Wikipedia; 668 sites are dead → 1,604 pages + home pages
- [x] Collector `scripts/archive_collect.py`: Wayback raw copies, 1 req/s, resumable, same-site links (depth 2, 60 pages/site)
- [x] Old fonts: AcadNusx/LitNusx text (font tags, inline styles, CSS classes) → Unicode Georgian
- [x] Local index `data/archive.db` (FTS5), search source `archive`, tab არქივი + block in ყველა
- [ ] 8-bit encodings Georgian-PS / Georgian-Academy (need verified tables)
- [ ] More seeds: old directories (top.ge, Open Directory), live .ge sites' old versions

## Session 7: Page structure (tabs, filters, sections, Discover)
Evidence: author's Safari history, 1,912 queries (Jul–Sep 2026). Georgian: entity 50%, word meaning 23%, texts 18%.
English: entity + fact 47%, media 12%, local/shop/site/quick 22%. No browsing queries. Models: Google tabs, Kagi lenses,
Naver sections, Kagi Small Web + Marginalia Explore pages.

Rule: separate page = no query; tab = the layout changes; filter = source type, same list; section = intent, automatic.
- [x] Tabs: ყველა (sections), ვიდეო (thumbnail grid, films included), სიახლეები (newest first, date shown)
- [x] Tab order by intent: film/music words → ვიდეო first; news words → სიახლეები first
- [x] Filters (chips, combine): ცოდნა, ტექსტები, ხალხი (forums + blogs + social), სამეცნიერო, იშვიათი (small web), ძველი ვები (archive)
- [x] Remove tabs ფილმები, სოციალური, არქივი, ცოდნა (now tab ვიდეო or filters)
- [ ] Section: dictionary meaning for a one-word Georgian query (ena.ge / Wiktionary)
- [ ] Section: texts (ტექსტი, ლექსი, სიმღერა, ნოტები, ლოცვა, pdf)
- [ ] Sections: people (forums, blogs), video row, news row, საინტერესო მიგნებები (2–3 small sites); weather + currency from Session 5
- [ ] Discover page `/discover`: new posts from Georgian blogs and small sites (RSS), random rare site, by topic
- [ ] Discover: similar sites (shared inbound links), old-web gallery from `data/archive.db`
- [ ] Home page: 3 "today's finds" under the search box

## Later (optional)
- [x] Own crawl of the trusted sites (`scripts/crawl_sites.py` → `data/crawl.db`, FTS5): robots.txt, sitemaps newest first, 1 req/s per site, main text by trafilatura; search source `crawl`
- [x] Discovery of rare Georgian sites (same crawler): seeds = 121k hosts cited in ka.wikipedia + hosts linked from crawled pages (`links`); new domain = robots + home + 5 pages, full budget only if >30% Georgian and not commercial
- [x] Domain rules, no ML (`domains` table): commercial score (ads 1, WooCommerce 2, shop words 2), kind academic / blog / other, inbound count
- [x] Search: small (≤100 inbound), non-commercial, Georgian domains get a bonus like tier 2 (`crawl.small_site`)
- [ ] Answer box through `claude -p` (personal use only)
- [ ] Entity names from Wikidata: თბილისი = Tbilisi = Тбилиси
- [ ] Facebook: opt-in page connect, public Telegram channels
