# dzirkva handoff (2026-10-07)

Goal of the next sessions: expand dzirkva's own indexes, make search more accurate, and make the code simpler.
Read `CLAUDE.md` first (rules, commands, layout). This file holds the state and the plan.

## State

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

### Continuous expansion (implemented 2026-10-07)

See [INGESTION.md](INGESTION.md) for budgets and operations. `dzirkva-ingest.timer` runs one bounded job at a
 time under 10% CPU / 300 MB, leaving `dzirkva-crawl` at its existing 25% cap. RSS registry/validators,
 complete-feed indexing and priority excerpts, incremental OAI updates, bounded full-text PDFs, gradual
 repository/CC-host discovery, archive CDX batches and incremental personal-site scoring are implemented.
 Existing passages get a resumable FTS backfill; new text is word-searchable before offline vectors exist.
 Search-cache entries expire after 15 minutes (`SEARCH_CACHE_TTL`), so fresh ingestion reaches repeated searches.
 Bulk new dumps, Common Crawl downloads, Iverieli scans and server embeddings remain outside these jobs.

### Data (Mac copies, 2026-10-02; the live `crawl.db` is on the server and bigger)

| Index | Size |
|---|---|
| `crawl.db` | 1.41M pages, 34,664 hosts; queue 2.79M URLs; 236k known domains; 327k links (src, dst; no anchor text) |
| `wiki.db` / `wikisource.db` | 174k articles, 684k citations / 6.1k texts |
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
| Paper PDFs (6,084 left) | low | **download: measure 20 PDFs, ask** |
| Iverieli PDFs (89k left) | low | **download: ask; a slice only** |
| Small dumps: ka Wikiquote, Wikinews, Wikibooks | seconds | `build_wiki_index.py` reads the format |
| Bulk Georgian web (FineWeb-2 `kat_Geor`, Common Crawl Georgian records) | import on the Mac | **big download: ask** |

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
2. Paper and Iverieli PDF downloads: how much?
3. Keep the dictionary answer box when Wiktionary goes?
4. Push `471fec9` to GitHub?
