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
  `scripts/build_words.py`, then `scripts/build_lexicon.py`, then `scripts/build_wiki_index.py` (→ `data/wiki.db`, ~1 min)
  ka.wiktionary dump → `kawiktionary.xml.bz2`, then `scripts/build_dictionary.py` (→ `data/dictionary.db`, ~10 s)

## Layout
- `src/dzirkva/engines.py` — SearXNG and Brave clients
- `src/dzirkva/georgian.py` — normalize, Latin→Georgian, spelling, Georgian ratio
- `src/dzirkva/morph.py` — word form → lemma + word family (lexicon first, then grammar rules), synonyms
- `src/dzirkva/web.py` — test page: `uv run python -m dzirkva.web` → http://127.0.0.1:8000. Single thread on purpose: the GPU model hangs in other threads.
- `src/dzirkva/search.py` — simple queries → engines → feedback round → rank (engine + meaning + tier + coverage) → group copies. Demo: `uv run python -m dzirkva.search <query>`
- `src/dzirkva/archive.py` + `scripts/archive_collect.py` — old web from the Internet Archive → `data/archive.db`. Run collector in background: `nohup uv run python scripts/archive_collect.py > data/archive.log 2>&1 &` (resumable)
- `src/dzirkva/dictionary.py` — Georgian word meanings from ka.wiktionary: answer box for "X რას ნიშნავს" / one-word queries
- `src/dzirkva/wiki.py` — local Georgian Wikipedia FTS5 index: article counts for spelling in context
- `src/dzirkva/crawl.py` + `scripts/crawl_sites.py` — own crawl of trusted sites + discovery of rare Georgian sites (Wikipedia-cited and linked hosts, probed, rules for commercial/academic/blog) → `data/crawl.db`. Run in background: `nohup uv run python scripts/crawl_sites.py > data/crawl.log 2>&1 &` (resumable; re-run adds new sitemap pages)
- `src/dzirkva/engines.py` answers are cached in `data/cache.db`; Google is paused 1–24 h after a CAPTCHA
- `src/dzirkva/iverieli.py` + `scripts/iverieli_collect.py` — National Library digital library catalog (601k records, OAI-PMH metadata only, most items are scans) → `data/iverieli.db`. Run in background: `nohup uv run python scripts/iverieli_collect.py > data/iverieli.log 2>&1 &` (resumable, ~4 h)
- `src/dzirkva/meaning.py` — BGE-M3 similarity (first load ~5 s; model in ~/.cache/huggingface)
- `config/searxng.yml` — engines: google, yandex, yahoo (tested with Georgian; reasons in the file). Google blocks fast bursts with CAPTCHA.
- `config/easter_eggs.yaml` — query → one Mtavruli line above the results (აფხაზეთი → აფხაზეთი საქართველოა)
- `config/sources.yaml` + `src/dzirkva/sources.py` — trusted Georgian sites: category and tier; check with `uv run python scripts/check_sources.py`
- `vendor/searxng` — SearXNG source, own venv (gitignored)
- `.env` — `BRAVE_API_KEY`, `SEARXNG_SECRET` (gitignored)

## Plan
See `plan.md`. Tick each checkbox when its step works.
