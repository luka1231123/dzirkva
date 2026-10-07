# dzirkva (ძირკვა)

**[ქართული ვერსია ქვემოთაა / Georgian version below](#ძირკვა-dzirkva)**

dzirkva is a Georgian-language search engine for learning and for discovering the Georgian web. It shows only Georgian results and favors text written by people: personal blogs, academic papers, sources cited by Wikipedia, archived copies of Georgian sites that no longer exist, and a hand-picked list of trusted sites. Search runs without an LLM or paid tokens. It uses code plus one free local embedding model (BGE-M3).

## What it does

- **Metasearch plus its own indexes.** Queries Google, Yandex and Yahoo through a local SearXNG instance and the Brave Search API, and merges them with dzirkva's own indexes.
- **A Georgian language layer.** Context-aware spelling correction, Latin-to-Georgian transliteration (`kartuli` to `ქართული`), morphological analysis (7 noun cases, postpositions, verb preverbs, person, version and screeve), synonyms, and dictionary answers.
- **Transparent ranking.** Score = engine rank (reciprocal rank fusion) + semantic similarity (BGE-M3) + source trust tier + query term coverage + human-text signals + past clicks. Every result shows why it scored the way it did, and a "how we found this" panel shows each step.
- **Research search.** Papers come with authors, year, journal, abstract, PDF link and a citation line.
- **Privacy.** No IP addresses stored, a 30-minute random session cookie, Do Not Track and GPC respected, records deleted after 180 days, outbound links signed with HMAC.

## Indexes

| Source | Size |
|---|---|
| Georgian Wikipedia (SQLite FTS5) | 174k articles |
| Own crawler | 360k pages across 864 sites |
| Iverieli (National Library of Georgia) | 601k records |
| Academic papers (OAI-PMH) | 29k papers from 29 journals and repositories |
| Passage embeddings (BGE-M3) | about 872k passages |
| Wikisource, Wiktionary, Internet Archive, RSS feeds | smaller indexes |

Morphological analyzer accuracy measured against UniMorph: nouns 100%, adjectives 96%, verbs 88%.

## Stack

Python 3.12, uv, SQLite (FTS5), BGE-M3, SearXNG, Brave Search API. Licensed under AGPL-3.0.

Setup, data building and configuration are documented in Georgian below.

---

# ძირკვა (dzirkva)

ძირკვა ქართული საძიებო სისტემაა. ის მხოლოდ ქართულ გვერდებს აჩვენებს და პირველ რიგში ადამიანის დაწერილ ტექსტებს
გთავაზობთ: ბლოგებს, სამეცნიერო ნაშრომებს, ვიკიპედიის წყაროებს და დახურული საიტების ძველ ასლებს.

ძიებისას ენობრივი მოდელები (LLM) არ გამოიყენება. მუშაობს მხოლოდ კოდი და ერთი ლოკალური მოდელი, BGE-M3.

საიტი: https://dzirkva.ge

## როგორ ეძებს

1. ჯერ შეკითხვას ამუშავებს: ლათინურით აკრეფილ ტექსტს ქართულად აქცევს (`kartuli` → `ქართული`), ასწორებს
   შეცდომებს და სიტყვებს საწყის ფორმამდე დაჰყავს (`morph.py`, `data/families.db`).
2. შემდეგ ერთდროულად ეძებს Brave-ში (თითო ძიებაზე ერთი ფასიანი მოთხოვნა) და საკუთარ ბაზებში.
3. ბოლოს შედეგებს ალაგებს. ითვალისწინებს, რომელ ადგილზე იყო გვერდი Brave-ში, რამდენად ახლოსაა მისი შინაარსი
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
| `wordgraph.db` | სიტყვების რუკა: 75 ათასი სიტყვის ოჯახი და მნიშვნელობით ახლო სიტყვები |

სანდო საიტების სია `config/sources.yaml` ფაილშია.

## გაშვება

საჭიროა Python 3.12 და [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run python -m dzirkva.web
```

გვერდი გაიხსნება მისამართზე http://127.0.0.1:8000. `.env` ფაილში უნდა მიუთითოთ `BRAVE_API_KEY` და
`SEARXNG_SECRET`. მეორე ნებისმიერი გრძელი სტრიქონია, რომელიც `/go` ბმულებს იცავს. პირველი გაშვებისას BGE-M3
მოდელი დაახლოებით 2 GB-ს ჩამოტვირთავს.

`.env` ფაილის სხვა ცვლადები:

| ცვლადი | ნაგულისხმევად | რას აკეთებს |
|---|---|---|
| `BRAVE_DAILY_LIMIT`, `BRAVE_MONTHLY_LIMIT` | 20, 300 | Brave-ის მოთხოვნების დღიური და თვიური ლიმიტი; `0` თიშავს Brave-ს |
| `MAX_SEARCHES` | 3 | რამდენი ძიება შეიძლება მიმდინარეობდეს ერთდროულად |
| `MEANING_WEB` | 1 | `0`: ნელ პროცესორზე ახალი ვექტორები აღარ ითვლება |
| `SEARXNG` | 0 | `1` რთავს Yandex-სა და Yahoo-ს SearXNG-ის მეშვეობით |
| `STATS_KEY` | | `/stats` გვერდის გახსნა სხვა კომპიუტერიდან |

## სერვერი

ძირკვა სახლის სერვერზე მუშაობს (Ubuntu, 7 GB ოპერატიული მეხსიერება, ვიდეობარათის გარეშე) და ინტერნეტს
Cloudflare Tunnel-ით უკავშირდება. systemd-ის ფაილები `config/systemd/` საქაღალდეშია:

- `dzirkva-web`: საძიებო გვერდი.
- `dzirkva-crawl`: გვერდების შემგროვებელი. გამუდმებით მუშაობს, მაგრამ ნელა: იყენებს პროცესორის ერთი ბირთვის
  მეოთხედს და 1,2 GB მეხსიერებას. ახალ გვერდებს თავისით ამატებს.
- `searxng`: გამორთულია.

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
