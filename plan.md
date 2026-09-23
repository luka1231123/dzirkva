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

## Session 2: Georgian language layer (`src/dzirkva/georgian.py`)
- [ ] Normalizer: Unicode NFC, Mtavruli → Mkhedruli, AcadNusx/LitNusx → Unicode
- [ ] Latin → Georgian: keyboard-layout table (`T` = თ, `W` = ჭ)
- [ ] Latin → Georgian: phonetic spellings (ts, ch, kh, q) → candidates, keep real words
- [ ] Suffix stripper: case endings, plural, postpositions (-ის, -ით, -ად, -მა, -ში, -ზე, -დან, -თვის, -ებ-)
- [ ] Spelling fix for unknown words, with Georgian confusion pairs (თ/ტ, ქ/კ/ყ, ც/წ, ჩ/ჭ, ფ/პ)
- [ ] Word list for spelling and candidates: frequency list from ka.wikipedia

## Session 3: Trusted source list (`config/sources.yaml`)
- [ ] ~100 Georgian sites in categories: news, government, law, education, health, encyclopedia, culture, business
- [ ] Trust tier per site (1 = best, 3 = lowest)
- [ ] Script checks that each site is online and has Georgian pages

## Session 4: Query variants and fan-out (`src/dzirkva/search.py`)
- [ ] 5–8 variants per query: original, normalized, stem form, Latin fix, spelling fix, `site:` variants by category
- [ ] Send all variants in parallel: SearXNG for most, Brave API for 1–2
- [ ] Merge with RRF: pages found by many variants rank higher
- [ ] Bonus for trusted-source tier
- [ ] Remove duplicate URLs (normalize `www`, `http/https`, tracking parameters)

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
