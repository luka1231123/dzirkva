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
  `scripts/build_words.py`, then `scripts/build_lexicon.py`

## Layout
- `src/dzirkva/engines.py` — SearXNG and Brave clients
- `src/dzirkva/georgian.py` — normalize, Latin→Georgian, spelling, Georgian ratio
- `src/dzirkva/morph.py` — word form → lemma + word family (lexicon first, then grammar rules), synonyms
- `src/dzirkva/web.py` — barebone test page: `uv run python -m dzirkva.web` → http://127.0.0.1:8000
- `src/dzirkva/search.py` — simple queries → engines → feedback round → rank (engine + meaning + tier + coverage) → group copies. Demo: `uv run python -m dzirkva.search <query>`
- `src/dzirkva/meaning.py` — BGE-M3 similarity (first load ~5 s; model in ~/.cache/huggingface)
- `config/searxng.yml` — engines: google, yandex, yahoo (tested with Georgian; reasons in the file). Google blocks fast bursts with CAPTCHA.
- `config/sources.yaml` + `src/dzirkva/sources.py` — trusted Georgian sites: category and tier; check with `uv run python scripts/check_sources.py`
- `vendor/searxng` — SearXNG source, own venv (gitignored)
- `.env` — `BRAVE_API_KEY`, `SEARXNG_SECRET` (gitignored)

## Plan
See `plan.md`. Tick each checkbox when its step works.
