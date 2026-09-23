"""Build the Georgian lexicon from Wiktionary (kaikki.org JSONL).

Output data/lexicon.tsv: form<TAB>lemma<TAB>pos<TAB>family
- lemma: the dictionary headword (nouns: nominative; verbs: 3rd person present, e.g. ხედავს)
- family: one id for a whole word family: the verb, its verbal noun (ხედვა, დანახვა),
  participles, passives, and preverb variants (წერს, ჩაწერს). Id = most frequent member.

Run: uv run python scripts/build_lexicon.py   (needs data/kaikki-ka.jsonl and data/words.tsv)
"""

import json
import re
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
SKIP_TAGS = {"table-tags", "inflection-template", "romanization"}
PREVERBS = ("გადმო", "ჩამო", "შემო", "გამო", "წამო", "ამო", "მიმო", "გადა", "წარ", "უკუ",
            "მი", "მო", "ჩა", "შე", "გა", "და", "წა", "ა")
# form_of glosses that are grammar forms of the target (row form -> lemma target)
INFLECTION = re.compile(r"nominative|ergative|dative|genitive|instrumental|adverbial|vocative|plural|singular|"
                        r"person|aorist|present|future|imperfect|perfect|optative|conditional|subjunctive|"
                        r"imperative|inflection of")
# form_of glosses that are separate words of the same family (join families)
DERIVED = re.compile(r"verbal noun|passive|participle|causative|alternative|nonstandard|obsolete|archaic|misspelling")
GEORGIAN = re.compile(r"^[ა-ჰ]+$")

freq = {}
with open(DATA / "words.tsv", encoding="utf-8") as f:
    for line in f:
        w, n = line.rstrip("\n").split("\t")
        freq[w] = int(n)

parent: dict[str, str] = {}


def find(w: str) -> str:
    parent.setdefault(w, w)
    while parent[w] != w:
        parent[w] = parent[parent[w]]
        w = parent[w]
    return w


def join(a: str, b: str) -> None:
    ra, rb = find(a), find(b)
    if ra != rb:
        parent[ra] = rb


rows: set[tuple[str, str, str]] = set()  # (form, lemma, pos)

with open(DATA / "kaikki-ka.jsonl", encoding="utf-8") as f:
    for line in f:
        d = json.loads(line)
        word, pos = d["word"], d["pos"]
        if not GEORGIAN.match(word):
            continue
        is_form_of = False
        for sense in d.get("senses", []):
            gloss = (sense.get("glosses") or [""])[0]
            for target in sense.get("form_of", []) + sense.get("alt_of", []):
                t = target.get("word", "")
                if not GEORGIAN.match(t):
                    continue
                kind = gloss.split(" of ")[0]
                if DERIVED.search(kind):
                    join(word, t)
                    # The target may have no entry of its own (რბენა -> რბის): add it.
                    rows.add((t, t, "verb" if re.search("verbal noun|participle|passive|causative", kind) else pos))
                elif INFLECTION.search(kind):
                    rows.add((word, t, pos))
                    rows.add((t, t, pos))
                    is_form_of = True
        if not is_form_of:
            rows.add((word, word, pos))
            find(word)
        for form in d.get("forms", []):
            fw, tags = form.get("form", ""), set(form.get("tags", []))
            if tags & SKIP_TAGS or not GEORGIAN.match(fw):
                continue
            if "noun-from-verb" in tags or "participle" in tags:
                join(fw, word)
            rows.add((fw, word, pos))
        for rel in d.get("related", []) + d.get("derived", []):
            r = rel.get("word", "")
            if GEORGIAN.match(r) and any(r == p + word or word == p + r for p in PREVERBS):
                join(r, word)

# Suppletive verbs that Wiktionary does not link: present <-> aorist with a different stem.
SUPPLETIVE = [("გარბის", "გაიქცა"), ("მირბის", "მიირბინა"), ("მოდის", "მოვიდა"), ("მიდის", "წავიდა"),
              ("არის", "იყო"), ("ამბობს", "თქვა"), ("ეუბნება", "უთხრა"), ("აძლევს", "მისცა"),
              ("ხედავს", "ნახა"), ("მიაქვს", "წაიღო"), ("მოაქვს", "მოიტანა"), ("კლავს", "მოკლა"),
              ("ჯდება", "დაჯდა"), ("წვება", "დაწვა"), ("დგება", "ადგა"), ("კვდება", "მოკვდა")]
lemma_of = {}
for form, lemma, _ in rows:
    lemma_of.setdefault(form, lemma)
for a, b in SUPPLETIVE:
    for w in (a, b):
        if w not in lemma_of:
            rows.add((w, w, "verb"))
            lemma_of[w] = w
    join(lemma_of[a], lemma_of[b])

# A verb with a preverb joins the same verb without it: გარბის -> რბის, ჩაწერს -> წერს.
verbs = {lemma for _, lemma, pos in rows if pos == "verb"}
for v in verbs:
    for p in PREVERBS[:-1]:  # not bare ა-: it is also a version vowel
        if v.startswith(p) and v[len(p):] in verbs:
            join(v, v[len(p):])
            break

# Family id = most frequent member (readable).
for _, lemma, _ in rows:
    find(lemma)
members: dict[str, list[str]] = {}
for w in list(parent):
    members.setdefault(find(w), []).append(w)
family_id = {root: max(ws, key=lambda w: (freq.get(w, 0), -len(w))) for root, ws in members.items()}

with open(DATA / "lexicon.tsv", "w", encoding="utf-8") as out:
    for form, lemma, pos in sorted(rows):
        out.write(f"{form}\t{lemma}\t{pos}\t{family_id[find(lemma)]}\n")

print(f"{len(rows):,} rows, {len({r[1] for r in rows}):,} lemmas, {len(members):,} families")
