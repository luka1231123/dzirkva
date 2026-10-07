"""Run one bounded expansion job at a time. Intended for the resource-capped systemd timer."""

import argparse
import fcntl
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from dzirkva.ingest import DATA, search_busy

ROOT = Path(__file__).resolve().parents[1]
JOBS = {
    "feeds": (1800, ["scripts/feeds.py", "--discover-limit", "10", "--poll-limit", "30"]),
    "passage_words": (600, ["scripts/build_passage_words.py", "--limit", "20000"]),
    "small_web": (3600, ["scripts/score_small_web.py", "--limit", "2000"]),
    "paper_metadata": (3600, ["scripts/archive/papers_collect.py", "--limit", "1"]),
    "paper_discovery": (86400, ["scripts/archive/find_repos.py", "--limit", "20"]),
    "paper_text": (86400, ["scripts/archive/papers_text.py", "--limit", "20", "--max-bytes", "26214400",
                            "--discover-missing"]),
    "archives": (86400, ["scripts/archive_collect.py", "--limit", "20", "--max-mb", "20"]),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", choices=JOBS, help="force one named job")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()
    DATA.mkdir(exist_ok=True)
    if args.status:
        with sqlite3.connect(DATA / "ingest.db", timeout=30) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='jobs'").fetchone():
                print("[]")
            else:
                print(json.dumps(db.execute("SELECT name,next,last,exit_code,runs FROM jobs ORDER BY name").fetchall()))
        return
    with (DATA / "ingest.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("ingestion already running", flush=True)
            return
        with sqlite3.connect(DATA / "ingest.db", timeout=30) as db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs(name TEXT PRIMARY KEY,next REAL DEFAULT 0,"
                       "last REAL DEFAULT 0,exit_code INT,runs INT DEFAULT 0)")
            db.executemany("INSERT OR IGNORE INTO jobs(name) VALUES (?)", [(name,) for name in JOBS])
            db.commit()
            if search_busy():
                print("search active; expansion waits for the next timer tick", flush=True)
                return
            due = [row[0] for row in db.execute("SELECT name FROM jobs WHERE next<=? ORDER BY next,rowid",
                                                (time.time(),)) if row[0] in JOBS]
            name = args.job or (due[0] if due else None)
            if name is None:
                print("no expansion job due", flush=True)
                return
            interval, command = JOBS[name]
            start = time.time()
            print(f"ingestion {name}: start", flush=True)
            try:
                code = subprocess.run([sys.executable, *command], cwd=ROOT, timeout=1200).returncode
            except subprocess.TimeoutExpired:
                code = 124
                print(f"ingestion {name}: timed out; bounded checkpoint resumes next run", flush=True)
            if name == "passage_words" and not code:
                with sqlite3.connect(f"file:{DATA / 'passages.db'}?mode=ro", uri=True) as pdb:
                    last, target = pdb.execute("SELECT last,target FROM words_progress WHERE id=1").fetchone()
                if last >= target:
                    interval = 7 * 86400
            db.execute("UPDATE jobs SET last=?,next=?,exit_code=?,runs=runs+1 WHERE name=?",
                       (start, time.time() + (interval if not code else 3600), code, name))
            db.commit()
            print(f"ingestion {name}: exit={code}, seconds={time.time()-start:.1f}", flush=True)
            if code:
                raise SystemExit(code)


if __name__ == "__main__":
    main()
