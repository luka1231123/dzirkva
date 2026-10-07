"""Word graph data for the /words page → data/wordgraph.db. Search time then needs one lookup per word.

For every word seen at least MIN_COUNT times that is a lemma (data/families.db, level 5) or a word of its own in a
family (level 1-3: derived word, other preverb, verbal noun: აღწერა, დაწერა; not an inflection):
- family: its forms from the ka-lemma table, by level (4 inflection, 3 aspect preverb or verbal noun,
  2 other preverb, 1 derived word); levels 1-2 only when word2vec agrees (similarity ≥ MIN_FAMILY_SIM),
  because the table links some look-alikes (შესახებ → სახის)
- near: the NEAR lemmas closest in meaning (ka-lemma word2vec, whole words, cosine)

Needs the ka-lemma project (word2vec in its data/vectors.kv); run with its environment, ~30 s on the Mac GPU:
    uv run --project ~/Programming/ka-lemma python scripts/build_word_graph.py [~/Programming/ka-lemma]
"""

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch
from gensim.models import KeyedVectors

ROOT = Path(__file__).resolve().parents[1]
KA = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / "Programming" / "ka-lemma").expanduser()
FAMILIES = ROOT / "data" / "families.db"
OUT = ROOT / "data" / "wordgraph.db"
MIN_COUNT = 20
NEAR = 24
MIN_FAMILY_SIM = 0.25
PER_LEVEL = {4: 10, 3: 8, 2: 8, 1: 8}  # forms kept per level, most frequent first

kv = KeyedVectors.load(str(KA / "data" / "vectors.kv"), mmap="r")
db = sqlite3.connect(FAMILIES)
counts = {w: c for w, c in db.execute("SELECT form, count FROM f WHERE level = 5 AND count >= ?", (MIN_COUNT,))}
# a family word gets its own map only when it is a dictionary form itself: other forms have it as their lemma
# (აღწერა ← აღწერების), not an inflection that the table links to the base word (აღწერას → წერს)
heads = {w for (w,) in db.execute("SELECT DISTINCT lemma FROM f WHERE level = 4")}
rows = db.execute("SELECT form, lemma, preverb, count FROM f WHERE level BETWEEN 1 AND 3 AND count >= ?",
                  (MIN_COUNT,)).fetchall()
base = {w: (lemma, preverb) for w, lemma, preverb, c in rows if w in heads}
counts |= {w: c for w, lemma, preverb, c in rows if w in heads}
lemmas = [w for w in counts if w in kv.key_to_index]
print(f"{len(lemmas):,} words with a vector ({sum(w in base for w in lemmas):,} of them family words)", flush=True)

vecs = np.stack([kv[w] for w in lemmas]).astype(np.float32)
vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
device = "mps" if torch.backends.mps.is_available() else "cpu"
V = torch.from_numpy(vecs).to(device)
near: dict[str, list] = {}
for start in range(0, len(lemmas), 4096):
    sims, idx = (V[start:start + 4096] @ V.T).topk(NEAR + 1)
    for row, (s, i) in enumerate(zip(sims.cpu().tolist(), idx.cpu().tolist())):
        w = lemmas[start + row]
        near[w] = [[lemmas[j], round(x, 3)] for x, j in zip(s, i) if lemmas[j] != w][:NEAR]

family: dict[str, list] = defaultdict(list)
for form, lemma, level, count in db.execute(
        "SELECT form, lemma, level, count FROM f WHERE level BETWEEN 1 AND 4 AND form != lemma ORDER BY count DESC"):
    if lemma not in near or sum(1 for f in family[lemma] if f[1] == level) >= PER_LEVEL[level]:
        continue
    sim = float(kv.similarity(form, lemma)) if form in kv.key_to_index else None
    if level <= 2 and (sim is None or sim < MIN_FAMILY_SIM):
        continue
    family[lemma].append([form, level, count, round(sim, 3) if sim is not None else None])

# a family word (აღწერა): its base word and the forms that share its preverb (აღწერს, აღწერილი)
for w, (lemma, preverb) in base.items():
    if w not in near:
        continue
    family[w].insert(0, [lemma, 2, counts.get(lemma, 0), round(float(kv.similarity(w, lemma)), 3)
                         if lemma in kv.key_to_index else None])
    if len(family[w]) == 1:  # no forms of its own in the table: the forms that share its preverb (აღწერს, აღწერილი)
        for form, count in db.execute("SELECT form, count FROM f WHERE lemma = ? AND preverb = ? AND form != ? "
                                      "ORDER BY count DESC LIMIT ?", (lemma, preverb, w, PER_LEVEL[4])):
            if preverb and form.startswith(preverb):
                family[w].append([form, 4, count, None])

OUT.unlink(missing_ok=True)
out = sqlite3.connect(OUT)
out.execute("CREATE TABLE g (word TEXT PRIMARY KEY, count INT, family TEXT, near TEXT) WITHOUT ROWID")
out.executemany("INSERT INTO g VALUES (?, ?, ?, ?)",
                ((w, counts[w], json.dumps(family[w], ensure_ascii=False), json.dumps(near[w], ensure_ascii=False))
                 for w in lemmas))
out.commit()
out.execute("VACUUM")
print(f"{len(lemmas):,} words → {OUT} ({OUT.stat().st_size / 1e6:.0f} MB)")
