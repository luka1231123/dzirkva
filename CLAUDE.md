# dzirkva

Georgian-only search engine. Mini project. Simplicity first.
Meta-search (SearXNG + Brave API) + Georgian language layer + trusted source list.

## Rules
- Search time uses no LLM and no paid tokens: only code and free local models.
- Brave API has a monthly quota: use it for 1–2 query variants per search, cache everything.
- No test suites. The only check is the eval script over `eval/queries.tsv` (private, gitignored).
- Commit after each working step.

## Commands
- Start SearXNG: `./scripts/searxng.sh` (http://127.0.0.1:8888, log in `data/searxng.log`)
- Engine demo: `uv run python -m dzirkva.engines <query>`

## Layout
- `src/dzirkva/engines.py` — SearXNG and Brave clients
- `config/searxng.yml` — engines: google, bing, brave (duckduckgo gives CAPTCHA). SearXNG has no `ka` language.
- `vendor/searxng` — SearXNG source, own venv (gitignored)
- `.env` — `BRAVE_API_KEY`, `SEARXNG_SECRET` (gitignored)

## Plan (sessions)
1. Setup — done
2. Georgian layer: Mtavruli/AcadNusx fold, Latin→Georgian, suffix stripping, spelling
3. Source list: ~100 trusted Georgian sites by category
4. Query variants (5–8) + parallel fan-out + RRF merge + dedupe
5. Georgian filter (>50% Georgian letters) + local reranker + SQLite cache
6. Web UI + eval on 50 queries + one tuning pass
