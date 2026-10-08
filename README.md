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
| Georgian web corpus (FineWeb-2, Common Crawl captures 2013–2024) | 3.5M documents |
| Word forms and word map | 1.65M forms, 83k words |

## Stack

Python 3.12, uv, SQLite, BGE-M3. It runs on a home server (7 GB RAM, no GPU) behind a Cloudflare Tunnel.
Licensed under AGPL-3.0.

Setup and data building are described in Georgian below.

## AI access (MCP)

Any AI assistant that speaks the [Model Context Protocol](https://modelcontextprotocol.io) can use dzirkva
as a tool. The public server is **`https://mcp.dzirkva.ge/mcp`** (Streamable HTTP, read-only, no login).
It has 8 tools: `search_georgian`, `fetch_source`, `read_passages`, `analyze_word`, `word_family`,
`define_word`, `site_profile`, `list_sources`. Tools, limits and self-hosting are in [MCP.md](MCP.md).

### Connect a client

Clients that accept a server URL:

| Client | How |
|---|---|
| Claude Code | `claude mcp add --transport http dzirkva https://mcp.dzirkva.ge/mcp` |
| Claude Desktop / claude.ai | Settings → Connectors → Add custom connector → the URL above |
| Cursor (`~/.cursor/mcp.json`), LM Studio (Program → Install → Edit `mcp.json`) | `{"mcpServers": {"dzirkva": {"url": "https://mcp.dzirkva.ge/mcp"}}}` |
| VS Code (`.vscode/mcp.json`) | `{"servers": {"dzirkva": {"type": "http", "url": "https://mcp.dzirkva.ge/mcp"}}}` |

Clients that start only local (stdio) servers can use the `mcp-remote` bridge (needs Node.js):

```json
{"mcpServers": {"dzirkva": {"command": "npx", "args": ["-y", "mcp-remote", "https://mcp.dzirkva.ge/mcp"]}}}
```

### Local AI (Ollama, LM Studio, Open WebUI)

A local model needs a chat app that is an MCP client; Ollama itself only runs the model.

- **LM Studio**: add the `mcpServers` entry above to `mcp.json`, then load a model with tool support.
- **Open WebUI** (with Ollama): Admin Settings → External Tools → add an MCP (Streamable HTTP) server
  with the URL above. Older versions can use [mcpo](https://github.com/open-webui/mcpo):
  `uvx mcpo --port 8100 -- npx -y mcp-remote https://mcp.dzirkva.ge/mcp`, then add `http://localhost:8100`
  as an OpenAPI tool server.
- **Any other MCP client** (Jan, AnythingLLM, mcphost and others): use the URL, or the `mcp-remote` bridge.

Choose a model that calls tools reliably and reads Georgian, because dzirkva returns Georgian text and
needs Georgian queries; English queries are not translated. Larger models
follow the tool flow much better than small ones. To run everything offline, run the MCP server from a
checkout with its `data/` folder: `uv run --extra mcp dzirkva-mcp` (stdio; see [MCP.md](MCP.md)).

### Skill

[`skills/dzirkva/SKILL.md`](skills/dzirkva/SKILL.md) teaches an assistant to use the tools well:
Georgian queries, the order search → read → cite, filters, and dates. For Claude Code, copy the folder
to `~/.claude/skills/dzirkva/`; in claude.ai, upload it as a skill. For other assistants, paste the file
into the system prompt.

### Best uses

- **Research with Georgian sources**: papers with authors and citation lines, Wikipedia, National Library
  records, and the full text of papers when it is stored.
- **Fact-checking from Georgian evidence**: the assistant reads the stored text and cites the URL.
- **Gone or old Georgian websites**: Internet Archive copies and 2013–2024 web captures.
- **Georgian language questions**: lemma and part of speech, all forms of a word, word family,
  dictionary meaning, words near in meaning.
- **Judging a Georgian source**: trust tier, Wikipedia citations, archive history, related sites.
- **What Georgians write themselves**: with `tag: "people"` (blogs, forums, personal sites) or
  `tag: "small"` (personal sites in the first person). Each search keeps up to 20 such pages beyond
  its top results, from about 25,000 personal sites and blogs. General search engines seldom show them:
  experiences, opinions, local history, recipes, everyday language.

Not a good fit: today's news, prices, weather, non-Georgian topics, and English-only queries.
dzirkva searches its own indexes, which can be weeks or years old; it does not search the live web.

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
| `bulk.db` | 3,5 მილიონი ქართული ვებგვერდის ტექსტი FineWeb-2-დან (Common Crawl, 2013–2024); იხ. [BULK.md](BULK.md) |
| `families.db` | 1,65 მილიონი სიტყვის ფორმა და მათი საწყისი ფორმები (პროექტ ka-lemma-დან) |
| `dictionary.db` | სიტყვების განმარტებები ვიქსიკონიდან |
| `wordgraph.db` | სიტყვების რუკა: 83 ათასი სიტყვის ოჯახი და მნიშვნელობით ახლო სიტყვები |

სანდო საიტების სია `config/sources.yaml` ფაილშია.

## ხელოვნური ინტელექტისთვის (MCP)

AI ასისტენტებს ძირკვის გამოყენება ხელსაწყოდ შეუძლიათ [Model Context Protocol](https://modelcontextprotocol.io)-ით.
საჯარო სერვერი: **`https://mcp.dzirkva.ge/mcp`**. ის მხოლოდ კითხულობს და შესვლა არ სჭირდება.
ხელსაწყოები ეძებს ქართულ წყაროებს, კითხულობს მათ ტექსტს, აანალიზებს სიტყვის ფორმებს, პოულობს მნიშვნელობას
და აფასებს საიტს. დეტალები: [MCP.md](MCP.md).

როგორ დავუკავშიროთ:

- **Claude Code**: `claude mcp add --transport http dzirkva https://mcp.dzirkva.ge/mcp`
- **Claude Desktop / claude.ai**: Settings → Connectors → Add custom connector და ზემოთ მოცემული მისამართი.
- **Cursor, LM Studio**: `mcp.json` ფაილში
  `{"mcpServers": {"dzirkva": {"url": "https://mcp.dzirkva.ge/mcp"}}}`
- **ლოკალური მოდელები (Ollama)**: საჭიროა MCP-ის მხარდამჭერი პროგრამა, მაგალითად LM Studio ან Open WebUI
  (Admin Settings → External Tools). პროგრამებს, რომლებიც მხოლოდ ლოკალურ სერვერს უშვებენ, დაეხმარება
  `npx -y mcp-remote https://mcp.dzirkva.ge/mcp`.

აირჩიეთ მოდელი, რომელიც ხელსაწყოებს კარგად იყენებს და ქართული ესმის: ძირკვა ქართულ ტექსტს აბრუნებს და
შეკითხვაც ქართულად უნდა დაიწეროს. ინგლისურ შეკითხვას ძირკვა არ თარგმნის.

[`skills/dzirkva/SKILL.md`](skills/dzirkva/SKILL.md) ასისტენტს ასწავლის, როგორ ეძებოს, წაიკითხოს და
წყარო მიუთითოს. Claude Code-ისთვის ეს საქაღალდე დააკოპირეთ `~/.claude/skills/dzirkva/`-ში, სხვა
ასისტენტებისთვის ფაილის ტექსტი სისტემურ ინსტრუქციაში ჩასვით.

რისთვის გამოდგება ყველაზე კარგად:

- კვლევა ქართული წყაროებით: სამეცნიერო ნაშრომები ავტორებით და ციტირებით, ვიკიპედია, ეროვნული ბიბლიოთეკა.
- ფაქტის შემოწმება ქართული ტექსტით და წყაროს ბმულით.
- დახურული ან ძველი ქართული საიტები: Internet Archive-ის ასლები და 2013–2024 წლების გვერდები.
- ენის კითხვები: სიტყვის ფუძე, ფორმები, ოჯახი, მნიშვნელობა, აზრით ახლო სიტყვები.
- ქართული საიტის შეფასება: სანდოობა, ვიკიპედიის ციტირებები, ძველი ასლები.
- რას წერენ თავად ადამიანები: ფილტრი `tag: "people"` (ბლოგები, ფორუმები, პირადი საიტები) ან
  `tag: "small"` (პირადი საიტები, სადაც ავტორი პირველ პირში წერს). ყოველი ძიება ასეთ 20 გვერდამდე
  ინახავს დაახლოებით 25 000 პირადი საიტიდან და ბლოგიდან. დიდი საძიებო სისტემები მათ იშვიათად აჩვენებს.

არ გამოდგება დღევანდელი ამბებისთვის, ფასებისთვის, ამინდისთვის და არაქართული თემებისთვის: ძირკვა საკუთარ
ინდექსებში ეძებს და ცოცხალ ინტერნეტს არ ათვალიერებს.

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
