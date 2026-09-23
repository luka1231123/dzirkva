"""Rank by meaning: local BGE-M3 embeddings (knows Georgian), free, runs on the Mac GPU.

The question and each result (title + snippet) become vectors; cosine similarity says how
close their meanings are, even when they share no words
(ვინაა ყველაზე ჩქარი მორბენალი ≈ უსეინ ბოლტი, მსოფლიოს უსწრაფესი ადამიანი).
"""

import hashlib
import sqlite3
from functools import cache
from pathlib import Path

import numpy as np

MODEL = "BAAI/bge-m3"
CACHE = Path(__file__).resolve().parents[2] / "data" / "vectors.db"
MAX_TOKENS = 512  # default 8192: one 9,000-character paragraph pads the whole batch (search 11.9 s → 2.9 s)


@cache
def _model():
    import torch
    from sentence_transformers import SentenceTransformer

    if not torch.backends.mps.is_available():
        m = SentenceTransformer(MODEL, device="cpu")
    else:
        m = SentenceTransformer(MODEL, device="mps").half()  # fp16: 3.8× faster, same vectors (0.9998)
    m.max_seq_length = MAX_TOKENS
    return m


def vectors(texts: list[str]):
    """Unit vectors (numpy, one row per text): a dot product of two rows is their cosine similarity."""
    return _model().encode(texts, batch_size=32, normalize_embeddings=True)


@cache
def _cache() -> sqlite3.Connection:
    db = sqlite3.connect(CACHE, check_same_thread=False)
    db.execute("CREATE TABLE IF NOT EXISTS vectors (key BLOB PRIMARY KEY, v BLOB)")
    return db


def cached_vectors(texts: list[str]):
    """Like vectors(), but each text is embedded once: data/vectors.db (fp16, key = SHA-1 of the text)."""
    keys = [hashlib.sha1(t.encode()).digest() for t in texts]
    db = _cache()
    found = dict(db.execute(f"SELECT key, v FROM vectors WHERE key IN ({','.join('?' * len(keys))})", keys))
    todo = [i for i, k in enumerate(keys) if k not in found]
    if todo:
        new = [(keys[i], v.astype(np.float16).tobytes()) for i, v in zip(todo, vectors([texts[i] for i in todo]))]
        db.executemany("INSERT OR REPLACE INTO vectors VALUES (?, ?)", new)
        db.commit()
        found.update(new)
    return np.stack([np.frombuffer(found[k], dtype=np.float16) for k in keys]).astype(np.float32)


def similarity(query: str, texts: list[str]) -> list[float]:
    """Cosine similarity (0-1) between the query and each text."""
    if not texts:
        return []
    m = _model()
    q = m.encode([query], normalize_embeddings=True)
    d = m.encode(texts, batch_size=32, normalize_embeddings=True)
    return (d @ q.T).ravel().tolist()
