# dzirkva (ძირკვა)

A search engine for the Georgian web, made for study and for exploring. It shows only Georgian results.
Search time uses no LLM and no paid tokens: only code and a free local model (BGE-M3).

## What it does

- **Search**: meta-search (Google, Yandex and Yahoo through a local SearXNG; the Brave API) plus our own indexes:
  Georgian Wikipedia (174k articles), Wikisource (6k texts), our own crawl (360k pages on 864 Georgian sites),
  the old web from the Internet Archive, the National Library catalog Iverieli (246k records) and 29k papers
  from 29 Georgian journals and university repositories.
- **Georgian language layer**: spelling in context, Latin letters to Georgian (`kartuli` → `ქართული`),
  word forms and word families, a dictionary box for "X რას ნიშნავს".
- **Ranking**: engine rank + meaning (BGE-M3) + trusted sources (`config/sources.yaml`) + word coverage.
  What a query wants (`config/intents.yaml`) adds sites made for that need.
- **Research**: papers show authors · year · journal, the abstract, a PDF link and a citation line.
  `დისერტაცია`, `სტატია` or `მონოგრაფია` in a query puts that type of paper first. Filter chip: სამეცნიერო.
- **Surfing**: `/site?h=host` (what a site is, its newest pages, similar sites, links in and out, old copies),
  `/discover` (new posts of the small web, old-web finds, new papers, sites by topic), `/random` (a small site).
  Three finds of the day on the home page.

## Run

```bash
./scripts/searxng.sh                 # SearXNG at http://127.0.0.1:8888 (engines: google, yandex, yahoo)
uv run python -m dzirkva.web         # the search page at http://127.0.0.1:8000
```

`.env` needs `BRAVE_API_KEY` and `SEARXNG_SECRET`. All data lives in `data/` (not in git).
`CLAUDE.md` lists every build step and module.

## Data pipeline

| Data | Built by | Run again |
|---|---|---|
| Wikipedia, Wikisource indexes | `scripts/build_wiki_index.py` | after a new dump |
| Meaning vectors (`passages.db`) | `scripts/build_passages.py` | after new passages |
| Own crawl (`crawl.db`) | `scripts/crawl_sites.py` (runs all the time) | resumable |
| Small web (`voice.db`), feeds (`feeds.db`) | `scripts/score_small_web.py`, `scripts/feeds.py` | after crawling; feeds daily |
| Papers (`papers.db`) | `scripts/find_repos.py`, then `scripts/papers_collect.py` | new repositories: both |
| Paper full text | `scripts/papers_text.py`, then `build_passages.py` | resumable |
| Iverieli catalog and text | `scripts/iverieli_collect.py`, `scripts/iverieli_text.py` | resumable |
| Old web (`archive.db`) | `scripts/archive_collect.py` | resumable |

## State (2026-09-23)

- Running: the crawler (`scripts/crawl_sites.py`).
- Paused to limit downloads, resumable with the same command:
  - paper PDFs: 11,028 of 17,124 read (`nohup uv run python scripts/papers_text.py > data/papers_text.log 2>&1 &`)
  - Iverieli PDFs: 1,820 of 90,804 items read (`nohup uv run python scripts/iverieli_text.py > data/iverieli_text.log 2>&1 &`)
- Check (private, not in git): `eval/` holds 100 test queries with saved Google results; `scripts/run_stress.py`
  runs them through dzirkva.
