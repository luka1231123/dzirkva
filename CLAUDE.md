# dzirkva

Georgian-only search engine. Mini project. Simplicity first.
Own indexes only (no outside search engine; Brave and SearXNG archived in `scripts/archive/engines/`) + Georgian language layer + trusted source list.

## Rules
- Search time uses no LLM and no paid tokens: only code and free local models (BGE-M3 embeddings in `meaning.py`).
- No test suites. The only check is the eval script over `eval/queries.tsv` (private, gitignored).
- Commit after each working step.

## Commands
- Grammar demo: `uv run python -m dzirkva.morph <words>`; accuracy: `uv run python scripts/check_morph.py`
- Rebuild data (in `data/`, gitignored): download `kawiki-latest-pages-articles.xml.bz2` → `kawiki.xml.bz2`,
  kaikki.org Georgian JSONL → `kaikki-ka.jsonl`, unimorph/kat → `unimorph-kat.tsv`; then
  `scripts/build_words.py`, then `scripts/build_lexicon.py`, then `scripts/build_wiki_index.py` (→ `data/wiki.db`, ~1 min); Wikisource: `kawikisource-latest-pages-articles.xml.bz2` → `kawikisource.xml.bz2`, then `scripts/build_wiki_index.py wikisource` (→ `data/wikisource.db`)
  ka.wiktionary dump → `kawiktionary.xml.bz2`, then `scripts/build_dictionary.py` (→ `data/dictionary.db`, ~10 s)
  Title vectors (filler check): `scripts/build_titles.py` (→ `data/titles.npy` + `titles.tsv`, ~4 min GPU; after build_wiki_index)
  Spelling word list: `data/wordlists/` (Leipzig `kat-ge_web_2019_1M` + `kat_newscrawl_2016_1M` `*-words.txt`, gamag/ka_GE.spell `bumbeishvili.txt` + `crubadan.txt`), then `scripts/build_vocab.py` (→ `data/vocab.tsv`, ~30 s; run again after crawling)

## Layout
- `src/dzirkva/georgian.py` — normalize, Latin→Georgian, spelling (a rare word is fixed only when a sound-alike or keyboard-slip word is 20× more frequent in `vocab.tsv`), Georgian ratio
- `src/dzirkva/morph.py` — word form → lemma + word family (lexicon first, then the ka-lemma table `data/families.db`, then grammar rules); `forms_of` (Wiktionary tables; fewer than `KA_FORMS` of them, `.env`, default 10, 0 = never: plus ka-lemma forms for non-verbs, e.g. names) feeds `wiki.any_form`. First open adds an index `f_lemma` to `families.db`
- `src/dzirkva/web.py` — test page: `uv run python -m dzirkva.web` → http://127.0.0.1:8000 (`PORT` in `.env`). A thread per request; every new search runs on one worker, the main thread, because the GPU model hangs in other threads. At most `MAX_SEARCHES` (`.env`, default 3) new searches run or wait; the next visitor gets a busy page that reloads itself.
  Pages without a query: `/site?h=host` (site profile), `/discover`, `/random` (a small site), `/about`; the home page says why big engines fail Georgian and what dzirkva does (no statistics against other engines), index sizes, how to write a query (`QUERY_TIPS`), when to use ამოძირკვა, how dzirkva finds text people wrote (`SIGNS`: the result labels, meaning on hover) and 3 finds of the day; `/about` gives the exact rules and how dzirkva can grow. Site text: plain, natural Georgian, no em dashes, no example queries on the start page
  Links to other sites go through `/go` with an HMAC signature (no open redirect); access logs are off (they hold IP addresses)
- `src/dzirkva/telemetry.py` — every request is an event in `data/telemetry.db` (searches with `from`: typed/tab/filter/related/dym …, clicks with rank and block, page views, citation/panel actions by beacon `/t`, errors); no IP, 30-min anonymous session cookie, none under DNT/GPC, bots marked, 180-day retention. View: `/stats` (this computer, or `?key=` + `STATS_KEY` in `.env`)
- `src/dzirkva/discover.py` — web surfing data: site profiles (crawl pages, links in/out, similar sites by shared links, Wikipedia citations, archive copies), newest small-web posts, shelves
  Feeds: `uv run python scripts/feeds.py` (→ `data/feeds.db`, ~2 min; run daily)
- `src/dzirkva/papers.py` — Georgian journals and university repositories (OJS, DSpace, EPrints) by OAI-PMH → `data/papers.db`; search source, paper layout (authors, year, journal, PDF, citation)
  Find endpoints: `uv run python scripts/archive/find_repos.py` (~15 min, asks every Georgian host); harvest: `nohup uv run python scripts/archive/papers_collect.py > data/papers.log 2>&1 &` (resumable; run again for new or failed repositories)
  Full text: `nohup uv run python scripts/archive/papers_text.py > data/papers_text.log 2>&1 &` (PDFs → `passages.db` site `papers`), then `scripts/build_passages.py` for vectors (paused at 11,028 of 17,124)
- `src/dzirkva/search.py` — query words (any form) → every local index + passages by meaning (the crawl also keeps `PEOPLE_PAGES` = 20 pages of people's sites beyond its top 20: `search.people`, home pages and date archives excluded) → feedback round (one term, crawl only, only when fewer than 3 of the top 10 have every query word) → rank (index position + meaning + tier + coverage) → group copies. Demo: `uv run python -m dzirkva.search [--deep] <query>`
  ამოძირკვა (button, `deep=1`): `DEEP` = 3 × local results and results read by meaning
- `src/dzirkva/archive.py` + `scripts/archive_collect.py` — old web from the Internet Archive → `data/archive.db`. Run collector in background: `nohup uv run python scripts/archive_collect.py > data/archive.log 2>&1 &` (resumable)
- `src/dzirkva/dictionary.py` — Georgian word meanings from ka.wiktionary: answer box for "X რას ნიშნავს" / one-word queries
- `src/dzirkva/wiki.py` — local Georgian Wikipedia FTS5 index: article counts for spelling in context
- `src/dzirkva/bulk.py` — read-only FineWeb-2 Georgian corpus (`data/bulk.db`, 3.5M documents), FTS5 word search and stored text with capture provenance; live crawl matches take priority. Build on the Mac with `scripts/download_fineweb.py` and `scripts/import_fineweb.py`; verified copy/stream operations and attribution are in `BULK.md`. The server never reads the Parquet files or builds corpus embeddings.
- `src/dzirkva/crawl.py` + `scripts/crawl_sites.py` — own crawl of trusted sites + discovery of rare Georgian sites → `data/crawl.db`. On the server it always runs, slow (systemd `dzirkva-crawl`, `CRAWL_SLOW=1`, 25% of one core, 1.2 GB); it exits when stalled or done and systemd starts it again. The server copy of `crawl.db` is the live one.
- `src/dzirkva/wordgraph.py` — word map `/words?w=` (header link სიტყვები): family by ka-lemma level + 16 nearest words by word2vec, SVG + JS on the page; data `data/wordgraph.db` from `uv run --project ~/Programming/ka-lemma python scripts/build_word_graph.py` (20 s; after a new `families.db`)
- `families.db` comes from the ka-lemma project (`~/Programming/ka-lemma/data/families.tsv` imported into SQLite, table `f`)
- `src/dzirkva/iverieli.py` + `scripts/archive/iverieli_collect.py` — National Library digital library catalog (601k records, OAI-PMH metadata only, most items are scans) → `data/iverieli.db`. Run in background: `nohup uv run python scripts/archive/iverieli_collect.py > data/iverieli.log 2>&1 &` (resumable, ~4 h)
  Text: `nohup uv run python scripts/archive/iverieli_text.py > data/iverieli_text.log 2>&1 &` — PDFs ≤ 5 MB (text layer; big ones are scans) → `data/passages.db` site `iverieli`, then `scripts/build_passages.py` for vectors (resumable; paused at 1,820 of 90,804 items: the user limits downloads)
- Small web (`crawl.small_site`, filter chip პატარა ვები): personal sites by first-person voice; scores in `data/voice.db` from `uv run python scripts/score_small_web.py` (~1 min, run again after crawling)
- `src/dzirkva/meaning.py` — BGE-M3 in a spawned worker (lazy load, exit after `MODEL_IDLE_SECONDS`, default 300; manual idle: `uv run python -m dzirkva.meaning --idle`, localhost web control, drains queued searches and clears cache). Web search cache: LRU, `SEARCH_CACHE_SIZE` default 32, 0 disables. BGE-M3 similarity (first load ~5 s; model in ~/.cache/huggingface); result vectors cached in `data/vectors.db`; `MEANING_WEB=0` (`.env`, slow CPU server) gives no new result vectors, only stored ones (stress100: top-10 overlap 8.9/10, same #1 in 70 of 100)
- `src/dzirkva/clicks.py` — result links go through `/go` → `data/clicks.db`; pages chosen for the same question (dictionary forms) rank higher; a click followed by another within 30 s does not count
- `config/intents.yaml` — what a query wants (50 intents: weather, currency, jobs, films, tech help …): words → sites made for that need; the winning intent adds one crawl search limited to its sites (`crawl.search(hosts=)`) and a ranking bonus (`search.intent`)
- `config/easter_eggs.yaml` — query → one Mtavruli line above the results (აფხაზეთი → აფხაზეთი საქართველოა)
- `config/sources.yaml` + `src/dzirkva/sources.py` — trusted Georgian sites: category and tier; check with `uv run python scripts/check_sources.py`
- Named sites (`sources.named_sites`): query names a site → its pages rank higher, and a navigational query (name = half the words or more) also searches the crawl on that site (the other words) and shows the home page. Names from Wikidata: `uv run python scripts/build_sites.py` (→ `data/sites.tsv`, ~2 min, QLever endpoint); Georgian site names and languages from Common Crawl: `data/cc_hosts.db` (`scripts/cc_hosts.py`). Sites that write mostly Georgian pass the Georgian filter even with a Latin title.
- `.env` — `GO_SECRET` (signs `/go` links; the old name `SEARXNG_SECRET` still works); `MCP_PORT`/`MCP_PUBLIC_URL` start the MCP listener inside the web process (`MCP.md`) (gitignored)

## Plan
See `plan.md`. Tick each checkbox when its step works.
