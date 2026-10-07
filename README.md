# dzirkva (ძირკვა)

**[ქართული ვერსია ქვემოთაა / Georgian version below](#ძირკვა-dzirkva)**

dzirkva is a search engine for the Georgian web. It shows only Georgian pages and puts text written by people
first: blogs, academic papers, sources cited by Wikipedia, old copies of Georgian sites that are gone. Search uses
no LLM: only code and one local embedding model, BGE-M3. Live at https://dzirkva.ge.

## What it does

- **Search.** Only dzirkva's own indexes, no outside search engine. Results are ranked by index position,
  meaning (BGE-M3), source trust and how many query words the text contains; each result says why it ranks where
  it does.
- **Georgian language layer.** Latin-to-Georgian (`kartuli` → `ქართული`), spelling correction, and word forms
  reduced to their lemma (a grammar and a 1.65M-form table from the ka-lemma project).
- **Word map** (`/words`). Type a word and see its forms, related words and the words closest in meaning
  (word2vec trained on our own corpus). Click any word to open its map.
- **Research.** Papers come with authors, year, journal, PDF link and a citation line.
- **Privacy.** No IP addresses, a 30-minute random session cookie, Do Not Track and GPC respected, records
  deleted after 180 days.

## Indexes

| Source | Size |
|---|---|
| Georgian Wikipedia and Wikisource (SQLite FTS5) | 174k articles |
| Own crawler (runs all the time, slowly) | pages from trusted and small Georgian sites |
| Passage embeddings (BGE-M3) | about 870k passages |
| Academic papers (OAI-PMH) and the National Library catalog | 29k papers, 601k records |
| Word forms and word map | 1.65M forms, 83k words |

## Stack

Python 3.12, uv, SQLite, BGE-M3. It runs on a home server (7 GB RAM, no GPU) behind a Cloudflare Tunnel.
Licensed under AGPL-3.0.

Setup and data building are described in Georgian below.

## AI access (MCP)

AI assistants can search Georgian sources, read source text and document passages, inspect
word families and definitions, and discover related sites through Dzirkva's MCP server.
Normal search is the default; `deep: true` enables ამოძირკვა.
Run `uv run --extra mcp dzirkva-mcp` for stdio, or add
`--transport streamable-http` for `http://127.0.0.1:8001/mcp`.
See [MCP.md](MCP.md) for client configuration, tools and hosting.
Set `MCP_PORT=8001` when running the website with the `mcp` extra to share its search backend.

---

# ძირკვა (dzirkva)

ძირკვა ქართული საძიებო სისტემაა. ის მხოლოდ ქართულ გვერდებს აჩვენებს და პირველ რიგში ადამიანის დაწერილ ტექსტებს
გთავაზობთ: ბლოგებს, სამეცნიერო ნაშრომებს, ვიკიპედიის წყაროებს და დახურული საიტების ძველ ასლებს.

ძიებისას ენობრივი მოდელები (LLM) არ გამოიყენება. მუშაობს მხოლოდ კოდი და ერთი ლოკალური მოდელი, BGE-M3.

საიტი: https://dzirkva.ge

## როგორ ეძებს

1. ჯერ შეკითხვას ამუშავებს: ლათინურით აკრეფილ ტექსტს ქართულად აქცევს (`kartuli` → `ქართული`), ასწორებს
   შეცდომებს და სიტყვებს საწყის ფორმამდე დაჰყავს (`morph.py`, `data/families.db`).
2. შემდეგ ეძებს საკუთარ ბაზებში. გარე საძიებო სისტემებს არ იყენებს.
3. ბოლოს შედეგებს ალაგებს. ითვალისწინებს, რომელ ადგილზე იყო გვერდი ბაზაში, რამდენად ახლოსაა მისი შინაარსი
   შეკითხვასთან (BGE-M3), რამდენად სანდოა წყარო და შეკითხვის რამდენი სიტყვა გვხვდება ტექსტში. თითოეული შედეგის
   ქვეშ წერია, რატომ დადგა ამ ადგილას.

კოდი: `src/dzirkva/search.py`. ტერმინალში ასე გაეშვება: `uv run python -m dzirkva.search ქართული ანბანი`.

## სიტყვების რუკა

გვერდზე `/words` ჩაწერეთ სიტყვა და ნახავთ მის ფორმებს, მონათესავე სიტყვებს და მნიშვნელობით ახლო სიტყვებს.
ნებისმიერ სიტყვაზე დაჭერით მისი რუკა იხსნება. კოდი: `src/dzirkva/wordgraph.py`.

## ბაზები

ყველა ფაილი `data/` საქაღალდეში ინახება და git-ში არ შედის.

| ფაილი | რა ინახება |
|---|---|
| `wiki.db`, `wikisource.db` | ქართული ვიკიპედია და ვიკიწყარო |
| `crawl.db` | ჩვენ მიერ შეგროვებული გვერდები სანდო და ნაკლებად ცნობილი ქართული საიტებიდან |
| `passages.db` | დაახლოებით 870 000 აბზაცი ვიკიპედიიდან, ნაშრომებიდან და „ივერიელიდან“, თითოეული BGE-M3 ვექტორით |
| `papers.db`, `iverieli.db` | სამეცნიერო ნაშრომები (OAI-PMH) და ეროვნული ბიბლიოთეკის კატალოგი |
| `archive.db` | დახურული ქართული საიტების ასლები Internet Archive-დან |
| `families.db` | 1,65 მილიონი სიტყვის ფორმა და მათი საწყისი ფორმები (პროექტ ka-lemma-დან) |
| `dictionary.db` | სიტყვების განმარტებები ვიქსიკონიდან |
| `wordgraph.db` | სიტყვების რუკა: 83 ათასი სიტყვის ოჯახი და მნიშვნელობით ახლო სიტყვები |

სანდო საიტების სია `config/sources.yaml` ფაილშია.

## გაშვება

საჭიროა Python 3.12 და [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run python -m dzirkva.web
```

გვერდი გაიხსნება მისამართზე http://127.0.0.1:8000. `.env` ფაილში უნდა მიუთითოთ `GO_SECRET`:
ნებისმიერი გრძელი სტრიქონი, რომელიც `/go` ბმულებს იცავს. პირველი გაშვებისას BGE-M3
მოდელი დაახლოებით 2 GB-ს ჩამოტვირთავს.

`.env` ფაილის სხვა ცვლადები:

| ცვლადი | ნაგულისხმევად | რას აკეთებს |
|---|---|---|
| `MAX_SEARCHES` | 3 | რამდენი ძიება შეიძლება მიმდინარეობდეს ერთდროულად |
| `MEANING_WEB` | 1 | `0`: ნელ პროცესორზე ახალი ვექტორები აღარ ითვლება |
| `STATS_KEY` | | `/stats` გვერდის გახსნა სხვა კომპიუტერიდან |

## სერვერი

ძირკვა სახლის სერვერზე მუშაობს (Ubuntu, 7 GB ოპერატიული მეხსიერება, ვიდეობარათის გარეშე) და ინტერნეტს
Cloudflare Tunnel-ით უკავშირდება. systemd-ის ფაილები `config/systemd/` საქაღალდეშია:

- `dzirkva-web`: საძიებო გვერდი.
- `dzirkva-crawl`: გვერდების შემგროვებელი. გამუდმებით მუშაობს, მაგრამ ნელა: იყენებს პროცესორის ერთი ბირთვის
  მეოთხედს და 1,2 GB მეხსიერებას. ახალ გვერდებს თავისით ამატებს.

The embedding model runs in a separate process, loaded on the first search. It exits after
five minutes without embedding requests; the next search reloads it. Set `MODEL_IDLE_SECONDS`
to change the delay. The in-memory search cache keeps the 32 most recently used searches;
`SEARCH_CACHE_SIZE=0` disables it. These settings are environment variables.

To unload the model manually and clear the search cache on the running server:

```bash
ssh rexvopc 'cd ~/dzirkva && ~/.local/bin/uv run python -m dzirkva.meaning --idle'
```

This waits for queued searches to finish. New searches wake the model again. The control
endpoint accepts only local requests with the control header, excluding tunnel requests.

კოდის განახლება: `git ls-files -z | rsync -t --files-from=- --from0 . rexvopc:dzirkva/`. ამის შემდეგ
გადატვირთეთ `dzirkva-web` სერვისი.

## მონაცემების მომზადება

თითოეული სკრიპტის თავში წერია, რა სჭირდება მას და რამდენ ხანს გრძელდება. სკრიპტები ამ თანმიმდევრობით გაუშვით:

1. ენა და ვიკიპედია: `scripts/build_words.py`, `build_lexicon.py`, `build_wiki_index.py`, `build_dictionary.py`,
   `build_titles.py`, `build_vocab.py`.
2. საიტები: `scripts/build_sites.py`, `cc_hosts.py`, `crawl_sites.py`, `score_small_web.py`, `feeds.py`,
   `archive_collect.py`.
3. აბზაცების ვექტორები: `scripts/build_passages.py`.
4. სიტყვების რუკა: `uv run --project ~/Programming/ka-lemma python scripts/build_word_graph.py` (დაახლოებით
   20 წამი). სკრიპტს ka-lemma პროექტის ვექტორები სჭირდება.

„ივერიელისა“ და სამეცნიერო ნაშრომების შემგროვებელი სკრიპტები `scripts/archive/` საქაღალდეშია გადატანილი. მათ
აღარ ვიყენებთ, თუმცა უკვე შეგროვებული მონაცემები ძიებაში კვლავ მონაწილეობს.

## კონფიდენციალურობა

IP მისამართებს არ ვინახავთ. ერთი ვიზიტის მოქმედებებს ერთმანეთთან ანონიმური ქუქი-ფაილი აკავშირებს, რომელიც
30 წუთში ქრება. თუ ბრაუზერში ჩართულია Do Not Track ან GPC, ქუქი-ფაილი საერთოდ არ იქმნება. ჩანაწერები
(`data/telemetry.db`) 180 დღის შემდეგ იშლება.

## ლიცენზია

კოდი ვრცელდება [GNU AGPL-3.0](LICENSE) ლიცენზიით, © 2026 Luka Rekhviashvili. ვიკიპედიის, ვიკიწყაროსა და
ვიქსიკონის ტექსტები: CC BY-SA 4.0; ყველა ამონარიდს თან ახლავს ბმული წყაროზე. BGE-M3: MIT.
