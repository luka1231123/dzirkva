# dzirkva handoff (2026-11-17; the state of 2026-10-08 follows below)

Goal of the next sessions: expand dzirkva's own indexes, make search more accurate, and make the code simpler.
Read `CLAUDE.md` first (rules, commands, layout). This file holds the state and the plan.

## Session 2026-11-16/17: audit for the student goal

The user asked: finish dzirkva, compare it with Google, find niches, decide what to add, make good and discard,
and prefer one simple mechanism to many patches. The audit stopped early at the usage limit. Its findings are below.
Plan for students: `plan.md` Session 9.

### Done
- `0bed028`: the same run twice gives the same lists. `wiki.any_form` cut its forms in set order, which changes per
  process. Check: two runs of the 40 student queries, 0 lists differ.
- `6d2877b`: `SET=odd uv run python scripts/versus.py run|pool|grade|score NAME` reads `eval/odd.tsv` and writes
  `eval/odd_*`. Use it for the second query set.
- Deployed 2026-11-17 (rsync of the changed files, `dzirkva-web` restarted): round 1 (intents, sources), the
  determinism fix and the punctuation fix. Live check: `რა არის ალიტერაცია?` puts the article first.
- Search tools (`28d4abd`, `6ecb996`; `ops.py`, `/tools`, MCP `query_tools`) deployed 2026-11-17: rsync of the 14
  changed files (server copies matched git before), `dzirkva-web` restarted. `CLAUDE.md` not sent (server copy
  differs). Live check: `"ილია ჭავჭავაძე" -ლექსი @სამეცნიერო` 37 results in 3.4 s with 3 chips; MCP
  `ტყემალი/ბაჟე -მაღაზია` returns `query_tools`. `dzirkva-crawl` was stopped at 11:09 by another change (SIGTERM),
  not started again by this deploy.
- **The server has code that git does not have:** `scripts/crawl_sites.py` (FineWeb-2 hosts as crawl seeds,
  `bulk_hosts.tsv`) and `CLAUDE.md`, both edited on the server on 2026-10-19. The deploy left them alone. Copy
  them into git before the next full `git ls-files | rsync`, or that rsync deletes the feature.
- Baseline after the fix (`eval/student_d1.jsonl`, Mac index): dzirkva good@1 16, good@3 26, quality 43%.
  Google: 29, 33, 76%. One new result has no mark yet (`versus.py pool d1`).

### Facts measured
- **Almost no person uses the search box.** Live `telemetry.db` (copied from the server on 2026-11-16): 937,930
  events, most from bots. The bot flag misses many bots: "human" browsers had language en 18,327, zh 1,826, ka 4.
  Since the gate page (22 Oct): 20 human searches in total; /discover 8 views, /random 2, citation and panel
  actions 13 ever. Real typed queries before the scrapers (24 Sep - 7 Oct, 91): person names (often "Surname Name"
  in Latin letters), word meanings, how-to, one thesis search, history, weather, slang.
- **Visitors come from Google to pages, not to the search.** 65 visits from Google: 45 to `/site` pages (they
  stopped after 23 Oct, when `/site` became noindex), 9 to `/words` pages, 5 to result pages for paper authors in
  Latin letters (Nardaia Ada, Geldiashvili Nunu), 6 to the home page.
- **Cold search on the server is slow.** Live median 1.3 s while bots kept the cache warm; the rare human search
  since 22 Oct took 5-23 s. On the Mac the word search of all indexes takes about 0.3 s per query (40 queries:
  crawl 7.3 s, bulk 4.0 s, the rest under 1.2 s each). So the cause is on the server: cold disk cache, RAM taken
  by the crawler in max mode, the model. Not measured on the server yet.
- **Google snapshots need a normal browser.** The built-in browser pane gets non-Georgian results from Google
  (რა არის ფოტოსინთეზი gave Armenian blogs). Collect set 2 in the user's own Chrome (about 10 minutes).

### Failures seen on 26 new school queries (Mac index, judged by Claude, not blind)
1. **Punctuation broke every query (fixed 2026-11-17, `search.query_words`).** `understand` splits on spaces, so `ალიტერაცია?` and
   `"მერანი"` keep the mark. Coverage of every result becomes 0.0 (`coverage('ალიტერაცია არის …',
   ['ალიტერაცია?'])` = 0.0, without `?` = 1.0). Fix: words by `re.findall(r"\w+", query)` in `understand` and
   `question_type`. The student set has no punctuation, so the score cannot show this fix.
2. **One-word intent triggers misfire.** `intent()` gives `law` for ომის კანონი (Ohm's law), ნიუტონის მესამე
   კანონი, ავთანდილის ანდერძი ტექსტი (Avtandil's testament, a poem). 50 intents in `config/intents.yaml`, most
   for services (no more tuning by the 2026-09-23 focus). Test: student set with only research, books and school
   intents; keep the cut if the score does not drop.
3. **A work named in the query does not lead.** The Wikisource text ranks 29-46: ოთარაანთ ქვრივი (33),
   ვეფხისტყაოსანი/ანდერძი ავთანდილისა (42), the quote სჯობს სიცოცხლესა ნაძრახსა… (no text in the top 30).
   "მერანი" ანალიზი gives მათემატიკური ანალიზი. `wiki.article` matches only the whole query. One general
   mechanism: look up every 1-4 word part of the query in the Wikipedia and Wikisource titles; a hit joins the
   candidates with full title fit. This also covers plan Stage 2 "work title + შინაარსი / ანალიზი / ტექსტი".
4. **Crawl titles are the site name.** mastsavlebeli.ge pages show the title "mastsavlebeli.ge", so neither the
   judge nor a visitor sees the topic. Fix the title choice in `crawl.py` (og:title or h1 when the title is the host).
5. **Blogs and lesson plans fill the top of study queries** (გაკვეთილის გეგმა at 4-5 for ალიტერაცია and მიტოზი).
   Plan Stage 2 already lists it: no people bonus for study queries.
6. Known limits: fact questions (ყველაზე გრძელი მდინარე: blogs first, the list article third); words outside the
   8k-word dictionary (მიჯნური რას ნიშნავს: no box); textbooks as PDF (me 10 klasis istoriis wigni pdf).
   დისკრიმინანტის ფორმულა puts the article დისკრიმინაცია first with coverage 0: check the word family in morph.

### Next steps, in order
1. Done: fix 1 (punctuation). The student lists did not change; `რა არის ალიტერაცია?` now gives the same list as
   the query without `?`, and `"მერანი" ანალიზი` shows `მერანი - ანალიზი` at 3 (before: no result on the poem).
2. Done another way: no intent was cut. The source bonus (tier, small site, cited, intent) now counts × the
   page's topic cover (`search._trust`), so a law page for Ohm's law gets no lift. good@1 16 → 18, good@3 26,
   quality 43% → 44%. Law pages left the top 5 of ომის კანონი and ნიუტონის მესამე კანონი; დისკრიმინაცია fell
   from 1 to 4 for დისკრიმინანტის ფორმულა. Measured on the 107 known queries with an intent: 9 misfires
   (law 4, driving 1, statistics 1, translation 1, banking 1, history 1).
   Deployed 2026-11-17. **Like the server (`MEANING_WEB=0`) the change is neutral: 10 / 23 / 42% before and after**
   (`eval/student_s3`, `student_s4`). The server gives 10 good@1 where the Mac gives 16-18: without new vectors only
   Wikipedia, Wikisource and papers have a meaning score, so a Wikipedia article off the topic still wins
   (დისკრიმინაცია stays first on the server). The biggest lever for what visitors get is accuracy step 1 below:
   stored vectors for crawl pages, built on the Mac GPU.
3. Stored vectors for crawl pages: pilot first, then the full build (plan below). The user approved the plan
   on 2026-11-17; ask again before the 1 GB and 5.4 GB copies.
4. Build fix 3 (title parts as a source), measure; it should repair text and study queries.
5. Plan Stage 1 (coverage) stays the biggest lever: of Google's 110 good results, 46 are on sites we crawl but the
   page is missing, 47 are on sites we do not have.
6. Second query set: 40 queries in `eval/odd.tsv` (query, use, need), Google collected by the user in Chrome.
   Telemetry has too few clean human queries for it (see plan.md Stage 0).

### Plan: stored vectors for crawl pages (measured 2026-11-17)

Why: on the server only Wikipedia, Wikisource and papers have a meaning score. Like the server (`MEANING_WEB=0`) the
student set gives 10 / 23 / 42%; with a vector for every result (Mac) 18 / 26 / 44%. Crawl pages are 2/3 of the list.

Measured: 2,533,767 live crawl pages on the server (sample of 3,000 ids, 96% live); title + first 600 characters =
1,536 bytes per page, 380 compressed: export about 1 GB. BGE-M3 on the Mac GPU, fp16: 74 pages/s = about 9.5 h for
all (4,096-page test, the GPU shared with another session). Vectors back: fp16 1024 = 2 KB per page, about 5.4 GB,
about 25 min over the LAN. Server disk: 126 GB free. Server RAM: nothing more (40 indexed reads per search). The
crawl grew 1.41M → 2.63M pages in 45 days: about 27k new pages a day.

Design (one table, one lookup, no new rule in the ranking):
- `data/page_vectors.db`: `v (url TEXT PRIMARY KEY, v BLOB)`; url = `search.canonical_url`, v = fp16 unit vector of
  `passages.embed_text(title, text[:600])`, the same text shape as the Wikipedia passages.
- `search.rank_by_meaning`: results in the top `MEANING_TOP` without a vector first read their stored page vector
  by URL; only then `MEANING_WEB` computes new ones. About 10 lines. The Mac then ranks like the server.
- `scripts/build_page_vectors.py`, three steps, resumable: `export` on the server (read-only, `nice -n 19`, rows with
  id > the last exported id → `data/pages_export.tsv.zst`); `embed` on the Mac (foreground or nohup like
  `build_passages.py`, no daemon); then rsync `page_vectors.db` to the server. Weekly after that: only new ids
  (about 190k pages, 45 min on the GPU, about 400 MB back).

Steps:
1. Pilot (no big copy): vectors only for the crawl pages in the student set's candidate lists (about 2-3k pages,
   from the Mac `crawl.db`, 1 min on the GPU). Run `versus.py` with `MEANING_WEB=0`; mark new results. Go on only if
   good@1 rises from 10 toward 18.
2. Export on the server, copy about 1 GB to the Mac (ask first: download budget).
3. Embed on the Mac (about 9.5 h; at night). Copy about 5.4 GB to the server (ask first). Restart `dzirkva-web`.
4. Live check: დისკრიმინანტის ფორმულა must not start with დისკრიმინაცია; student set on the server index.
5. Later, only if needed: the same vectors for `bulk.db` pages (3.5M, about 13 h), and search by meaning over the
   crawl (1-bit copy in RAM, about 324 MB: the server RAM is tight).

Wait first: the session "Search bar customization tools" has uncommitted edits in `search.py`, `web.py`, `crawl.py`
and others (search operators, `ops.py`). Start step 1 after it commits, so the two do not edit `search.py` at once.

### Plan: faster search on the server (measured 2026-11-17)

Measured on the server (read-only, a capped process): the web process holds 4.7 GB (MemoryMax 5.8 GB); the page
cache has 4.4 GB for about 74 GB of databases (bulk 40, crawl 27, passages 4, wiki 1.3). Word search of every index:
first query 13.3 s (language tables 8.0 s: loaded once at web start; crawl 4.7 s cold), later queries 0.5-1.7 s
(crawl 0.2-0.6 s, papers up to 0.9 s). The crawl FTS index has 24 segments (2 of 374k and 582k leaf pages + 4 of
374k at level 8); bulk and wiki have 1. The disk scheduler is `none`, so `IOSchedulingClass=idle` of the crawler
and ingest units does nothing: their reads and writes compete with search on equal terms.

Steps (each measured alone; none takes CPU from the other jobs):
1. Time every step of a real search (indexes, query vector, passages, wiki snippets, citations, rank) in
   `debug["seconds"]` and telemetry; a day of data shows the slow step. About 10 lines.
2. Disk priority: scheduler `mq-deadline` for nvme0n1 (a udev rule, the user's sudo), so the idle IO class of the
   crawler and ingest takes effect. Search reads then go first; the jobs lose nothing while nobody searches.
3. Merge the crawl FTS segments (24 → a few) in small `merge` steps while the crawler is stopped or idle, under
   `nice -n 19` and idle IO; fewer segments = fewer cold disk reads per word. Measure a cold query before and after.
4. Papers search (0.9 s warm on a 0.2 GB file): find the slow query.
5. Only if 1-4 are not enough: less RAM in the web process (what holds the 4.7 GB?) so more of the index stays in
   the page cache.

### Questions for the user
1. People reach dzirkva from Google on word and site pages, not by the search box. Should the next work make more
   such pages for students (texts, authors, word pages), with search second?
2. `/site` pages brought 45 of 65 Google visits before noindex. Open them again as cached static pages?
3. Cut the 50 intents to research, books and school, if the score agrees?
4. On the server, crawl speed or search speed first? The crawler in max mode is the likely cause of 5-23 s searches.
5. Will you collect Google for the second set in your Chrome (about 10 minutes)?

### The stopped audit
Two read-only workflow runs (8 agents: ranking audit, feature audit, Google losses 1-20 and 21-40, four user
lenses) stopped at the usage limit with no reports. Script to run again:
`~/.claude/projects/-Users-rexvlapt-Programming-dzirkva/dcee13aa-da4d-43e9-a62c-7443bbe506c7/workflows/scripts/dzirkva-understand-2.js`
(set its `SCRATCH` constant to the new session's scratchpad; the school lens is done, see above). Run it only when
the user asks for agents again.

## State (2026-10-08)

- **No outside search engine.** Brave and SearXNG are archived in `scripts/archive/engines/` (commit `471fec9`,
  deployed to `rexvopc`, SearXNG service disabled, not pushed to GitHub yet). Search uses only local indexes.
- Search per query: understand (Latin → Georgian, spelling, word forms, intent, named site) → word search in every
  local index (`wiki.any_form` = up to 40 forms of the lemma) + meaning search over `passages.db` → feedback round
  on the crawl (only when fewer than 3 of the top 10 have every query word) → rank (RRF + meaning + tier +
  coverage + clicks) → group copies. Code: `src/dzirkva/search.py`.
- `confirm_fixes` now uses `crawl.count` (typed word next to the other query words vs the fix, 20× rule).

- **Embedding worker and cache:** BGE-M3 loads lazily in a spawned process and exits after 300 seconds without
  embedding requests (`MODEL_IDLE_SECONDS`). Manual unload and web cache clear:
  `ssh rexvopc 'cd ~/dzirkva && ~/.local/bin/uv run python -m dzirkva.meaning --idle'`.
  The web result cache is an LRU capped at 32 searches (`SEARCH_CACHE_SIZE`, 0 disables).
  The next uncached search reloads the model; language tables and the 1-bit passage index stay in the web process.

### Continuous expansion (deployed 2026-10-07)

See [INGESTION.md](INGESTION.md) for budgets and operations. `dzirkva-ingest.timer` runs one bounded job at a
 time under 10% CPU / 300 MB, leaving `dzirkva-crawl` at its existing 25% cap. RSS registry/validators,
 complete-feed indexing and priority excerpts, incremental OAI updates, bounded full-text PDFs, gradual
 repository/CC-host discovery, archive CDX batches and incremental personal-site scoring are implemented.
 Existing passages get a resumable FTS backfill; new text is word-searchable before offline vectors exist.
 Search-cache entries expire after 15 minutes (`SEARCH_CACHE_TTL`), so fresh ingestion reaches repeated searches.

Live checks: RSS added 80 entries and indexed 53 complete texts (4.86 MB, 70.3 MiB peak); 20,000 of 872,172
existing passages were backfilled and word search returned results; 2,000 historical pages were scored; the
crawler imported the first 500 Common Crawl discovery rows. One new paper PDF produced searchable full text.
Its slow passage replacement exposed a missing URL index, now built under the cap and verified with an indexed
query plan. The timer is enabled and active; live searches correctly defer ingestion until a later tick.
 Bulk new dumps, Common Crawl downloads, Iverieli scans and server embeddings remain outside these jobs.

### Bulk Georgian corpus (deployed 2026-10-08)

The user explicitly authorized downloading and adding a large existing corpus. FineWeb-2's complete Georgian
slice (three verified Parquet files, 6.85 GB) was downloaded and indexed on the Mac. `data/bulk.db` now adds
**3,505,415 documents** to live word search on `rexvopc`; it preserves original URLs, capture dates and provenance.
These are historical web captures. Current crawler matches keep their newer title and snippet in every search round.
Paper results retain matched body text instead of replacing it with an abstract.

The finished index passed SQLite structural and FTS integrity checks on the Mac. The server copy matches the Mac's
BLAKE3 hash; stored-text pagination, provenance and word search passed there. Transfer/unpack held the ingestion
lock and stayed at 10% CPU / 300 MB with zero swap. Homepage and About show the new coverage and attribution.
No corpus embeddings or index build ran on the server. See [BULK.md](BULK.md) for pinned source, checksums and operations.

### Data (Mac copies, 2026-10-02; the live `crawl.db` is on the server and bigger)

| Index | Size |
|---|---|
| `crawl.db` | 1.41M pages, 34,664 hosts; queue 2.79M URLs; 236k known domains; 327k links (src, dst; no anchor text) |
| `wiki.db` / `wikisource.db` | 174k articles, 684k citations / 6.1k texts |
| `bulk.db` (Mac and server, 2026-10-08) | 3,505,415 Georgian FineWeb-2 documents; 39.96 GB SQLite / 11.34 GB compressed |
| `passages.db` | 872k paragraphs, **all with vectors**: wikipedia 667k, papers 97k, wikisource 77k, iverieli 32k |
| `papers.db` | 29.4k papers; full text read for 11,040 of 17,124 PDFs |
| `iverieli.db` | 246k records; text of 1,859 of 90.8k small PDFs |
| `archive.db` | 770 pages; queue 7.5k |
| `cc_hosts.db` | 300 of 21,897 Georgian hosts checked |
| `families.db` (ka-lemma) | 1.65M forms → lemma, level, preverb, pos, count |

Unused now: `data/cache.db` (483 MB of old engine answers), `vendor/searxng`, `data/searxng.log`. Delete only after
the user says yes.

### Eval: local only vs the last Brave run (`eval/stress100_local.jsonl` vs `eval/stress100_dz8.jsonl`)

0.9 s per query (was 1.6 s). Top-10 overlap with the old lists 1.9/10. Trusted 47% → 39%, research 8% → 11%,
people 8% → 23%, non-Georgian 3% → 0%. No query has marked good results, so "better or worse" is a hand check:
weather, train, Tamar, photosynthesis fine; police fines, "ქართული ანბანი რამდენი ასოა", "ფილმი მიმინო" worse.

## User rules for this work

- CPU on the server matters more than downloads. Server: i3, 7.1 GB RAM; search uses ~5.3 GB, crawler 1.2 GB
  (CPUQuota 25%). Run each extra job alone with `systemd-run --user -p CPUQuota=10% -p MemoryMax=300M nice -n 19 …`.
- No BGE-M3 work on the server (`MEANING_WEB=0`). Vectors are built on the Mac GPU in one foreground run, then
  copied. No services or daemons on the Mac.
- Big downloads (PDFs, dumps, Common Crawl): give count × size and wait for a yes (memory: download-budget).
- SSH needs the Cloudflare login in the browser; the user types sudo (`ssh -t rexvopc 'sudo …'`).
  Deploy: `git ls-files -z | rsync -t --files-from=- --from0 . rexvopc:dzirkva/`, then restart `dzirkva-web`.
- The user wants to focus on ka-lemma and replace Wiktionary in the end.

## 1. Accuracy (do first)

1. **Every candidate needs a meaning score.** A result with no vector keeps its index position, so pages that only
   repeat the query words rank high (`rank_by_meaning`). On the server no crawl or archive page has a vector.
   Fix: vectors for crawl pages (title + first ~600 characters), built on the Mac GPU (1.4M pages, ~3–4 h,
   resumable like `build_passages.py`), stored like `passages.db`; search reads the stored vector by URL.
   1-bit copy in RAM ≈ 180 MB. Then the same pages can also be found by meaning.
2. **Done (code, not yet checked with the eval).** **Names find all their forms.** `morph.forms_of` reads only Wiktionary tables: მამარდაშვილის, ტრამპის,
   ბიტკოინის get 0 forms, so `any_form` searches only 2. Add: no Wiktionary forms → ka-lemma forms
   (`SELECT form FROM f WHERE lemma=? AND level>=4 ORDER BY count DESC`). Not for verbs (ka gives forms without
   the preverb: ჩავწერე → წერს).
3. **Done (code, not yet checked with the eval; measure the time of intents with many sites).** **Intent and named sites as a host filter.** The `site:` queries went with SearXNG; now an intent only gives a
   bonus. Add a host filter to `crawl.search` and search the intent's sites and a named site with it.
4. **Coverage floor vs meaning answers.** why/how answers use other words, so `COVERAGE_FLOOR` (0.2) pushes them
   down. Check with the eval after step 1.
5. **Site authority.** PageRank over `crawl.db` links, weekly, seconds of CPU; a prior in `_trust`.

Check each step alone with `uv run python scripts/run_stress.py eval/<name>.jsonl` and
`uv run python scripts/compare_google.py eval/<name>.jsonl`. Better: first mark good results for 50 queries
(plan.md item), so the eval measures relevance.

## 2. Simplicity

1. **Done.** **Dead code in `morph.py` (~80 of 374 lines):** `other_forms`, `EBA_FORMS`, `OTHER_FORMS`, `genitive`,
   `synonyms` + `synonyms.tsv` loading, `lemmas()`, the `KA_LEMMA` switch. Nothing calls them since the engine
   queries went (`git grep` before deleting).
2. **Noun rules (~60 lines):** they keep only lemmas the word list confirms, and the ka-lemma table covers every
   corpus word. Remove if `scripts/check_morph.py` does not get worse.
3. **Wiktionary → ka-lemma** (section 4): `morph.py` becomes table lookups, ~60 lines; `build_words.py`,
   `build_lexicon.py`, kaikki and UniMorph data (~180 MB) go.
4. `LOCAL_FLOOR` in `search.py` was made for "no web engine found it"; now every result is local. Keep the
   title-fit idea for long Wikipedia articles, but check the rule with the eval.

## 3. Expansion (low CPU first)

| Step | CPU on server | Note |
|---|---|---|
| Crawl queue priority: state sites (matsne.gov.ge: 1,631 pages, 3,359 queued), tier 1–2, Georgian share, inbound links | none extra | order only |
| Finish `scripts/cc_hosts.py` (21,597 hosts left) | low, waits on network | new Georgian sites for the crawler |
| RSS for every crawled site with a feed (`domains.signals` has `rss`); hourly for news | low | freshness: Brave gave it before |
| `archive_collect.py` (7.5k queued) | low | run capped |
| FTS5 index on `passages.text` | minutes, once | PDF text without vectors becomes word-searchable; then new PDF text needs no vectors |
| Paper PDFs (6,084 initially left) | low | approved: at most 20 attempts and 25 MiB per daily batch |
| Iverieli PDFs (89k left) | low | **download: ask; a slice only** |
| Small dumps: ka Wikiquote, Wikinews, Wikibooks | seconds | `build_wiki_index.py` reads the format |
| Bulk Georgian web (FineWeb-2 `kat_Geor`) | built on the Mac; read-only on server | **done:** 3.5M documents; explicit user download approval |

## 4. Morphology: Wiktionary → ka-lemma

Now: Wiktionary lexicon first (exact forms, suppletion, inflection tables), then the ka-lemma table for words
Wiktionary lacks, then noun and verb rules. ka-lemma levels 1–3 and the `preverb` column are unused.

Accuracy of the ka-lemma table alone on UniMorph forms seen ≥ 5 times in the corpus: **nouns 88%, adjectives 71%,
verbs 40%**; hand pairs 16/23 (current hybrid 23/23, but the lexicon contains UniMorph, so its score is too high).

Noun errors (279 of 2,204, sample seed 1). Some are UniMorph errors (კასეტი, ჰაუბიცი):
- 73: the dictionary form itself maps elsewhere (ათეისტი → ათეისტებს, მონარქისტი → ანარქისტის, აღმზრდელი → მზრდელი).
- 74: form not reduced: archaic plural -ნი/-თა (მეცნიერნი, ნაგებობანი, კონფედერატთა), -ად, vocative, rare forms.
- 48: lemma is a longer inflected form (ვირუსების → ვირუსებს, შეტყობინებებს → შეტყობინებებსა).
- 84: other lemma (თხზულების → თხზული, დისკოს → დისკი, მოდები → ედების).
Likely causes in ka-lemma `relate.py`: the lemma must be a frequent corpus word, so a frequent plural or particle
form beats a rarer nominative; noun readings strip derivation (აღ-, შე-) like verb preverbs; archaic endings are
missing. Fix in the ka-lemma project; its rules apply (no outside data, held-out sets, a fix stays only if no set
gets worse). Its own HANDOFF lists the verb work (ablation, levels 2 vs 3, future with ი-, ა- version vs preverb).

Steps: (1) ka-lemma nouns ≥ 95%, verbs ≥ 85% on held-out corpus sets; (2) dzirkva `morph.py` → table lookups
(analyze, forms, family, verbal noun by level 3 + preverb); (3) monthly refresh on the Mac: crawl → ka-lemma
corpus → vectors (40 min) → `families.py --build` (3 min) → copy `families.db`; (4) user decision: keep
`dictionary.db` (Wiktionary meanings, answer box) or drop the box; ka-lemma cannot give definitions.

**Synonyms:** no code uses them. ka-lemma has nearest words (`wordgraph.db`), not synonyms: მანქანა → ავტომობილი
0.81 (good), ცხელი → ცივი 0.73 (opposite), ექიმი → ქირურგი (same kind). Use only in ამოძირკვა, as a separate
list, only mutual neighbors with score ≥ 0.75.

## 5. Query expansion: when to use what (research)

| Method | Normal search | ამოძირკვა |
|---|---|---|
| Word forms (any_form) | always | more forms |
| Wikipedia title match → title as query (EQFE, Dalton 2014) | exact title only | yes |
| Feedback word from passages, searched in the crawl (Diaz & Metzler 2006; Xu & Croft 1996) | only when weak | always |
| Passage title as a query (entity feedback) | no: off-topic danger | top 1–2, only with a high meaning score or 2+ passages from the same article |
| Vector feedback: question + mean of nearest passages, search passages again (ANCE-PRF; Li et al. 2023) | no | yes |
| Synonyms / nearest words (Voorhees 1994; Diaz, Mitra & Craswell 2016) | no | filtered, separate list |
| Links of the top results (snowballing) | no | top 5 |
| PageRank, anchor text (Brin & Page 1998; Craswell et al. 2001) | yes, offline | yes |
Never: LLM rewrites at search time (HyDE, query2doc), doc2query, many feedback words in one query (Cao 2008).

## Open decisions for the user

1. Delete `cache.db`, `vendor/searxng`, `data/searxng.log`?
2. Iverieli PDF downloads: how much? Paper ingestion now has an approved bounded daily budget.
3. Keep the dictionary answer box when Wiktionary goes?
4. Push `471fec9` to GitHub?
