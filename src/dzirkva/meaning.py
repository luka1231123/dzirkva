"""Rank by meaning: local BGE-M3 embeddings (knows Georgian), free, runs on the Mac GPU.

The question and each result (title + snippet) become vectors; cosine similarity says how
close their meanings are, even when they share no words
(ვინაა ყველაზე ჩქარი მორბენალი ≈ უსეინ ბოლტი, მსოფლიოს უსწრაფესი ადამიანი).
"""

import atexit
import hashlib
import subprocess
import sys
from multiprocessing import Pipe
import os
import threading
import time
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
    else:  # fp16: 3.8× faster, same vectors (0.9998); loaded as fp16, since fp32 then .half() kept 2.3 GB of GPU memory
        m = SentenceTransformer(MODEL, device="mps", model_kwargs={"dtype": torch.float16})
    m.max_seq_length = MAX_TOKENS
    return m


def _serve(conn):
    """Only this process imports torch and owns the model. Exit releases all its memory."""
    try:
        while True:
            texts = conn.recv()
            try:
                result = _model().encode(texts, batch_size=32, normalize_embeddings=True)
                conn.send((True, result))
            except Exception as e:
                conn.send((False, f"{type(e).__name__}: {e}"))
    except EOFError:
        pass
    finally:
        conn.close()


class EmbeddingWorker:
    def __init__(self):
        self.lock = threading.Lock()
        self.process = self.conn = None
        self.last_used = 0.0
        self.idle_seconds = max(1, float(os.environ.get("MODEL_IDLE_SECONDS", "300")))
        threading.Thread(target=self._reap, daemon=True).start()
        atexit.register(self.idle)

    def _stop(self):
        if self.process is not None:
            self.conn.close()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            self.process = self.conn = None

    def idle(self):
        """Wait for any embedding call, then unload. The next call starts a fresh worker."""
        with self.lock:
            self._stop()

    def _reap(self):
        while True:
            time.sleep(min(30, self.idle_seconds))
            with self.lock:
                if self.process is not None and time.monotonic() - self.last_used >= self.idle_seconds:
                    self._stop()

    def encode(self, texts):
        with self.lock:
            if self.process is not None and self.process.poll() is not None:
                self._stop()
            if self.process is None:
                self.conn, child = Pipe()
                try:
                    # A fresh module, rather than multiprocessing spawn: callers such as build_titles.py
                    # have top-level work that must never run again in the child.
                    self.process = subprocess.Popen(
                        [sys.executable, "-m", "dzirkva.meaning", "--worker-fd", str(child.fileno())],
                        pass_fds=(child.fileno(),))
                except Exception:
                    self.conn.close()
                    self.conn = None
                    raise
                finally:
                    child.close()
            try:
                self.conn.send(texts)
                ok, result = self.conn.recv()
                if not ok:
                    raise RuntimeError(result)
                return result
            except (EOFError, BrokenPipeError, OSError):
                self._stop()
                raise RuntimeError("Embedding worker exited; retry the search") from None
            finally:
                self.last_used = time.monotonic()


@cache
def _worker():
    return EmbeddingWorker()


def idle():
    if _worker.cache_info().currsize:
        _worker().idle()


def vectors(texts: list[str]):
    """Unit vectors, computed by a process that unloads after MODEL_IDLE_SECONDS (default 300)."""
    return _worker().encode(texts)


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
    q = vectors([query])
    d = vectors(texts)
    return (d @ q.T).ravel().tolist()


if __name__ == "__main__":
    import argparse
    from urllib.request import Request, urlopen

    parser = argparse.ArgumentParser(description="Control the running web embedding worker")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--idle", action="store_true")
    mode.add_argument("--worker-fd", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker_fd is not None:
        from multiprocessing.connection import Connection
        _serve(Connection(args.worker_fd))
        sys.exit(0)
    req = Request(f"http://127.0.0.1:{os.environ.get('PORT', '8000')}/idle", method="POST",
                  headers={"X-Dzirkva-Control": "idle"})
    with urlopen(req, timeout=600) as response:
        print(response.read().decode().strip())
