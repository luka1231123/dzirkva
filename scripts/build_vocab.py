"""Count Georgian word forms in all local Georgian text. Output: data/vocab.tsv (word<TAB>count).

Sources: ka.wikipedia (data/words.tsv), Wikisource, own crawl, old web archive, Wiktionary forms (lexicon.tsv),
and data/wordlists/: Leipzig web 2019 + news 2016 word counts (downloads.wortschatz-leipzig.de/corpora/
kat-ge_web_2019_1M.tar.gz, kat_newscrawl_2016_1M.tar.gz: keep *-words.txt), gamag/ka_GE.spell words/
bumbeishvili.txt + crubadan.txt.
Spelling uses it: a word people write is a word, even if Wikipedia never uses it (ლარებში, ჩავიდე).
Run after build_words.py and crawling: uv run python scripts/build_vocab.py (~30 s)
"""

import collections
import re
import sqlite3
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
LISTS = DATA / "wordlists"
WORD = re.compile(r"[ა-ჰ]+")
MIN_COUNT = 2
TEXTS = [("wikisource.db", "SELECT title, body FROM wiki"),
         ("crawl.db", "SELECT title, text FROM pages"),
         ("archive.db", "SELECT title, text FROM pages")]

counts = collections.Counter()
with open(DATA / "words.tsv", encoding="utf-8") as f:
    for line in f:
        w, n = line.rstrip("\n").split("\t")
        counts[w] += int(n)
for db, sql in TEXTS:
    if not (DATA / db).exists():
        continue
    con = sqlite3.connect(DATA / db)
    for i, (title, text) in enumerate(con.execute(sql)):
        counts.update(WORD.findall(f"{title} {text}".lower()))
        if i % 20_000 == 0:
            print(f"{db}: {i:,} texts, {len(counts):,} word forms", flush=True)
for path in LISTS.glob("*/*-words.txt"):  # Leipzig corpora: id<TAB>word<TAB>count
    with open(path, encoding="utf-8") as f:
        for line in f:
            _, w, n = line.rstrip("\n").split("\t")
            if WORD.fullmatch(w.lower()):
                counts[w.lower()] += int(n)
kept = {w: n for w, n in counts.items() if n >= MIN_COUNT}
known = [DATA / "lexicon.tsv", LISTS / "bumbeishvili.txt", LISTS / "crubadan.txt"]  # real words, no counts
for path in filter(Path.exists, known):
    with open(path, encoding="utf-8") as f:
        for line in f:
            w = line.split("\t", 1)[0].strip()
            if WORD.fullmatch(w):
                kept[w] = max(kept.get(w, 0), counts[w], 1)

with open(DATA / "vocab.tsv", "w", encoding="utf-8") as out:
    for w, n in sorted(kept.items(), key=lambda x: -x[1]):
        out.write(f"{w}\t{n}\n")
print(f"kept {len(kept):,} word forms")
