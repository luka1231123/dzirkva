"""Voice scores of crawled domains for small-web detection (crawl.small_site) → data/voice.db.

Per 1,000 words of all pages of a domain: first-person words (voice), company words, newsroom words.
Domains with fewer than 2,000 words stay unscored (NULL = not small web). ~1 min; run again after crawling.
Run: uv run python scripts/score_small_web.py
"""

import re
import sqlite3
from collections import defaultdict

from dzirkva.crawl import CORPORATE_WORDS, DB, I_WORDS, REPORTING_WORDS, VOICE_DB, domain_of

MIN_WORDS = 2000

db = sqlite3.connect(DB, timeout=60)
full = {h for (h,) in db.execute("SELECT host FROM domains WHERE state='full'")}
counts = defaultdict(lambda: [0, 0, 0, 0])  # words, first person, corporate, reporting
for url, text in db.execute("SELECT url, text FROM pages"):
    host = domain_of(url)
    if host not in full:
        continue
    c = counts[host]
    for t in re.findall(r"[ა-ჰ]+|₾", text):
        c[0] += 1
        c[1] += t in I_WORDS
        c[2] += t in CORPORATE_WORDS
        c[3] += t in REPORTING_WORDS
rows = [(1000 * i / n, 1000 * c / n, 1000 * r / n, h) for h, (n, i, c, r) in counts.items() if n >= MIN_WORDS]
out = sqlite3.connect(VOICE_DB)
out.executescript("DROP TABLE IF EXISTS voice; CREATE TABLE voice (voice REAL, corporate REAL, reporting REAL, "
                  "host TEXT PRIMARY KEY)")
out.executemany("INSERT INTO voice VALUES (?, ?, ?, ?)", rows)
out.commit()
print(f"{len(rows)} domains scored")
