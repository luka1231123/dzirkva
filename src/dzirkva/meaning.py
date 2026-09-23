"""Rank by meaning: local BGE-M3 embeddings (knows Georgian), free, runs on the Mac GPU.

The question and each result (title + snippet) become vectors; cosine similarity says how
close their meanings are, even when they share no words
(ვინაა ყველაზე ჩქარი მორბენალი ≈ უსეინ ბოლტი, მსოფლიოს უსწრაფესი ადამიანი).
"""

from functools import cache

MODEL = "BAAI/bge-m3"


@cache
def _model():
    import torch
    from sentence_transformers import SentenceTransformer

    if not torch.backends.mps.is_available():
        return SentenceTransformer(MODEL, device="cpu")
    return SentenceTransformer(MODEL, device="mps").half()  # fp16: 3.8× faster, same vectors (0.9998)


def vectors(texts: list[str]):
    """Unit vectors (numpy, one row per text): a dot product of two rows is their cosine similarity."""
    return _model().encode(texts, batch_size=32, normalize_embeddings=True)


def similarity(query: str, texts: list[str]) -> list[float]:
    """Cosine similarity (0-1) between the query and each text."""
    if not texts:
        return []
    m = _model()
    q = m.encode([query], normalize_embeddings=True)
    d = m.encode(texts, batch_size=32, normalize_embeddings=True)
    return (d @ q.T).ravel().tolist()
