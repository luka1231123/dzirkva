"""Quick accuracy check of morph.py (seconds, not a test suite).

1. Hand pairs: two forms that search must treat as the same word family.
2. UniMorph Georgian (verbs verified by native speakers): is each form in its lemma's family?
   "rules only" hides the exact form from the lexicon, so it measures the grammar rules.

Run: uv run python scripts/check_morph.py
"""

import collections
import random
from pathlib import Path

from dzirkva.morph import analyze, same_family

PAIRS = [
    ("მორბენალი", "დარბის"), ("ჩქარად", "ჩქარი"), ("გარბის", "გაიქცა"), ("რბენა", "დარბის"),
    ("წყლის", "წყალი"), ("ფანჯრის", "ფანჯარა"), ("სახლებისთვის", "სახლი"), ("სოფლებში", "სოფელი"),
    ("უდიდესი", "დიდი"), ("უმაღლესი", "მაღალი"), ("ნახა", "ხედავს"), ("თქვა", "ამბობს"),
    ("მოვიდა", "მოდის"), ("დაწერილი", "წერა"), ("მწერალი", "წერს"), ("ქართველთა", "ქართველი"),
    ("მეგობრებისთვის", "მეგობარი"), ("ხვალამდე", "ხვალ"), ("საქართველოსთვის", "საქართველო"),
    ("სახლშია", "სახლი"), ("დედამ", "დედა"), ("მდინარის", "მდინარე"), ("წერილს", "წერილი"),
]
failed = [(a, b) for a, b in PAIRS if not same_family(a, b)]
print(f"hand pairs: {len(PAIRS) - len(failed)}/{len(PAIRS)}", "failed:", failed or "none")

rows = [l.rstrip("\n").split("\t") for l in open(Path(__file__).parent.parent / "data" / "unimorph-kat.tsv", encoding="utf-8")]
rows = [r for r in rows if len(r) == 3 and r[0] and " " not in r[1]]
random.seed(0)
sample = random.sample(rows, 3000)
for exact in (True, False):
    ok, total, bad = collections.Counter(), collections.Counter(), collections.defaultdict(list)
    for lemma, form, feats in sample:
        pos = feats.split(";")[0]
        total[pos] += 1
        fam_form = {a.family for a in analyze(form, exact)}
        if fam_form & {a.family for a in analyze(lemma)}:
            ok[pos] += 1
        else:
            bad[pos].append(f"{form}→{lemma}")
    label = "lexicon + rules" if exact else "rules only    "
    print(label, "  ".join(f"{p} {ok[p] / total[p]:.0%} ({total[p]})" for p in sorted(total)))
    if not exact:
        for p in sorted(bad):
            print(f"   {p} misses:", ", ".join(bad[p][:8]))
