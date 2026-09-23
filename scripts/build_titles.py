"""One BGE-M3 vector per Wikipedia and Wikisource title. Output: data/titles.npy (fp16) + data/titles.tsv (site<TAB>title).

Search uses it for the filler check (wiki.title_similarity): a page only the local indexes found must have a title
close in meaning to the query. Run after build_wiki_index.py: uv run python scripts/build_titles.py (~4 min, GPU)
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from dzirkva.meaning import vectors  # noqa: E402
from dzirkva.wiki import SITES, TITLES, TITLE_VECTORS, _db  # noqa: E402

BATCH = 4096
rows = [(site, title) for site in SITES if _db(site) for (title,) in _db(site).execute("SELECT title FROM wiki")]
titles = [t for _, t in rows]
vecs = np.vstack([vectors(titles[i:i + BATCH]) for i in range(0, len(titles), BATCH)]).astype(np.float16)
np.save(TITLE_VECTORS, vecs)
TITLES.write_text("".join(f"{site}\t{title}\n" for site, title in rows), encoding="utf-8")
print(f"{len(rows):,} titles → {TITLE_VECTORS}")
