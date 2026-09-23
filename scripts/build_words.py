"""Count Georgian word forms in the ka.wikipedia dump. Output: data/words.tsv (word<TAB>count).

Run: uv run python scripts/build_words.py
"""

import bz2
import collections
import re
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
WORD = re.compile(r"[ა-ჰ]+")
MIN_COUNT = 3

counts = collections.Counter()
with bz2.open(DATA / "kawiki.xml.bz2", "rt", encoding="utf-8") as f:
    for i, line in enumerate(f):
        counts.update(WORD.findall(line.lower()))
        if i % 5_000_000 == 0:
            print(f"{i:,} lines, {len(counts):,} word forms")

with open(DATA / "words.tsv", "w", encoding="utf-8") as out:
    kept = 0
    for word, n in counts.most_common():
        if n < MIN_COUNT:
            break
        out.write(f"{word}\t{n}\n")
        kept += 1
print(f"kept {kept:,} word forms (count >= {MIN_COUNT})")
