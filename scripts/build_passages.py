"""Meaning index: Georgian Wikipedia paragraphs + one BGE-M3 vector each → data/passages.db.

Step 1 (~1 min): cut every article of data/wiki.db into passages of ~600 characters.
Step 2 (~2 h on the Mac GPU, fp16): a vector for each passage that has none. Resumable: run again after a stop.
Run in background: nohup uv run python scripts/build_passages.py > data/passages.log 2>&1 &
"""

import sqlite3
import time

import numpy as np
import torch

from dzirkva.meaning import _model
from dzirkva.passages import chunks, connect, embed_text
from dzirkva.wiki import DB as WIKI

BATCH = 2048
MIN_CHARS = 100  # shorter articles are empty stubs

db = connect()
if not db.execute("SELECT 1 FROM passages LIMIT 1").fetchone():
    wiki = sqlite3.connect(WIKI)
    for title, body in wiki.execute("SELECT title, body FROM wiki"):
        if len(body) >= MIN_CHARS:
            db.executemany("INSERT INTO passages (title, text) VALUES (?, ?)", [(title, c) for c in chunks(body)])
    db.commit()
total = db.execute("SELECT count(*) FROM passages").fetchone()[0]
print(f"{total} passages", flush=True)

model = _model()
t0 = time.time()
done = done0 = db.execute("SELECT count(*) FROM passages WHERE v IS NOT NULL").fetchone()[0]
last = 0
while rows := db.execute("SELECT id, title, text FROM passages WHERE id > ? AND v IS NULL ORDER BY id LIMIT ?",
                         (last, BATCH)).fetchall():
    vecs = model.encode([embed_text(t, x) for _, t, x in rows], batch_size=32, normalize_embeddings=True)
    db.executemany("UPDATE passages SET v = ? WHERE id = ?",
                   [(v.astype(np.float16).tobytes(), i) for v, (i, _, _) in zip(vecs, rows)])
    db.commit()
    if torch.backends.mps.is_available():
        torch.mps.empty_cache()  # MPS keeps freed buffers; without this the process grows into swap and stalls
    last, done = rows[-1][0], done + len(rows)
    rate = (done - done0) / (time.time() - t0)
    print(f"{done}/{total}  {rate:.0f}/s  {(total - done) / rate / 60:.0f} min left", flush=True)
print("done")
