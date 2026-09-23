# dzirkva

Georgian-only search engine. Mini project. Simplicity first.
Meta-search (SearXNG + Brave API) + Georgian language layer + trusted source list.

## Rules
- Search time uses no LLM and no paid tokens: only code and free local models (BGE-M3 embeddings in `meaning.py`).
- Brave API has a monthly quota: use it for 1–2 query variants per search, cache everything.
- No test suites. The only check is the eval script over `eval/queries.tsv` (private, gitignored).
- Commit after each working step.

## Commands
- Start SearXNG: `./scripts/searxng.sh` (http://127.0.0.1:8888, log in `data/searxng.log`)
- Engine demo: `uv run python -m dzirkva.engines <query>`
- Grammar demo: `uv run python -m dzirkva.morph <words>`; accuracy: `uv run python scripts/check_morph.py`
- Rebuild data (in `data/`, gitignored): download `kawiki-latest-pages-articles.xml.bz2` → `kawiki.xml.bz2`,
  kaikki.org Georgian JSONL → `kaikki-ka.jsonl`, unimorph/kat → `unimorph-kat.tsv`; then
  `scripts/build_words.py`, then `scripts/build_lexicon.py`, then `scripts/build_wiki_index.py` (→ `data/wiki.db`, ~1 min); Wikisource: `kawikisource-latest-pages-articles.xml.bz2` → `kawikisource.xml.bz2`, then `scripts/build_wiki_index.py wikisource` (→ `data/wikisource.db`)
  ka.wiktionary dump → `kawiktionary.xml.bz2`, then `scripts/build_dictionary.py` (→ `data/dictionary.db`, ~10 s)
  Spelling word list: `data/wordlists/` (Leipzig `kat-ge_web_2019_1M` + `kat_newscrawl_2016_1M` `*-words.txt`, gamag/ka_GE.spell `bumbeishvili.txt` + `crubadan.txt`), then `scripts/build_vocab.py` (→ `data/vocab.tsv`, ~30 s; run again after crawling)

## Layout
- `src/dzirkva/engines.py` — SearXNG and Brave clients
- `src/dzirkva/georgian.py` — normalize, Latin→Georgian, spelling (a rare word is fixed only when a sound-alike or keyboard-slip word is 20× more frequent in `vocab.tsv`), Georgian ratio
- `src/dzirkva/morph.py` — word form → lemma + word family (lexicon first, then grammar rules), synonyms
- `src/dzirkva/web.py` — test page: `uv run python -m dzirkva.web` → http://127.0.0.1:8000. Single thread on purpose: the GPU model hangs in other threads.
- `src/dzirkva/search.py` — simple queries → engines → feedback round → rank (engine + meaning + tier + coverage) → group copies. Demo: `uv run python -m dzirkva.search <query>`
- `src/dzirkva/archive.py` + `scripts/archive_collect.py` — old web from the Internet Archive → `data/archive.db`. Run collector in background: `nohup uv run python scripts/archive_collect.py > data/archive.log 2>&1 &` (resumable)
- `src/dzirkva/dictionary.py` — Georgian word meanings from ka.wiktionary: answer box for "X რას ნიშნავს" / one-word queries
- `src/dzirkva/wiki.py` — local Georgian Wikipedia FTS5 index: article counts for spelling in context
- `src/dzirkva/crawl.py` + `scripts/crawl_sites.py` — own crawl of trusted sites + discovery of rare Georgian sites (Wikipedia-cited and linked hosts, probed, rules for commercial/academic/blog) → `data/crawl.db`. Run in background: `nohup uv run python scripts/crawl_sites.py > data/crawl.log 2>&1 &` (resumable; re-run adds new sitemap pages)
- `src/dzirkva/engines.py` answers are cached in `data/cache.db`; Google is paused 1–24 h after a CAPTCHA
- `src/dzirkva/iverieli.py` + `scripts/iverieli_collect.py` — National Library digital library catalog (601k records, OAI-PMH metadata only, most items are scans) → `data/iverieli.db`. Run in background: `nohup uv run python scripts/iverieli_collect.py > data/iverieli.log 2>&1 &` (resumable, ~4 h)
- Small web (`crawl.small_site`, filter chip პატარა ვები): personal sites by first-person voice; scores in `data/voice.db` from `uv run python scripts/score_small_web.py` (~1 min, run again after crawling)
- `src/dzirkva/meaning.py` — BGE-M3 similarity (first load ~5 s; model in ~/.cache/huggingface); result vectors cached in `data/vectors.db`
- `src/dzirkva/clicks.py` — result links go through `/go` → `data/clicks.db`; pages chosen for the same question (dictionary forms) rank higher; a click followed by another within 30 s does not count
- `config/searxng.yml` — engines: google, yandex, yahoo (tested with Georgian; reasons in the file). Google blocks fast bursts with CAPTCHA.
- `config/intents.yaml` — what a query wants (50 intents: weather, currency, jobs, films, tech help …): words → sites made for that need; the winning intent adds one `site:` query and a ranking bonus (`search.intent`)
- `config/easter_eggs.yaml` — query → one Mtavruli line above the results (აფხაზეთი → აფხაზეთი საქართველოა)
- `config/sources.yaml` + `src/dzirkva/sources.py` — trusted Georgian sites: category and tier; check with `uv run python scripts/check_sources.py`
- Named sites (`sources.named_sites`): query names a site → its pages rank higher, and a navigational query (name = half the words or more) also searches `site:` and shows the home page. Names from Wikidata: `uv run python scripts/build_sites.py` (→ `data/sites.tsv`, ~2 min, QLever endpoint); Georgian site names and languages from Common Crawl: `data/cc_hosts.db` (`scripts/cc_hosts.py`). Sites that write mostly Georgian pass the Georgian filter even with a Latin title.
- `vendor/searxng` — SearXNG source, own venv (gitignored)
- `.env` — `BRAVE_API_KEY`, `SEARXNG_SECRET` (gitignored)

## Plan
See `plan.md`. Tick each checkbox when its step works.
