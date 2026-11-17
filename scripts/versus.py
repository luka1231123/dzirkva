"""dzirkva against Google on student queries, judged for relevance: an internal check (the site shows no numbers).

Protocol. One round = run (about 1 minute) + judging only the results no round has shown before.
1. eval/student.tsv (private): query, use, need. The need says what a student wants; the judge reads it.
2. Google, once per query set: `versus.py listen`, then open each query on google.com (hl=ka, gl=ge) in a browser
   and run scripts/google_extract.js on each page → eval/student_google.jsonl. Collect again every few months.
3. `versus.py run NAME` → eval/student_NAME.jsonl: dzirkva's All tab. Only result links are compared, no answer boxes.
4. `versus.py pool NAME` → eval/student_pool.txt: the unjudged results among the first FIVE of each side, mixed,
   without the engine name. The judge writes eval/student_grades.txt, one line per query: number, tab, one mark per
   result. 2 = gives what the need says; 1 = on the topic but partial, thin or only a list; 0 = other topic,
   not Georgian, junk; x = not a result (a spelling note). `versus.py grade` stores them in eval/student_judged.tsv.
5. `versus.py score NAME`: good@1 and good@3 (a 2 in the first one / three), quality (mean mark of the first five,
   0-100%), wins and losses per query. The losses are the work list.
Keep a change only when quality rises and no use loses a good@3 query.
Limit: the judge sees title, address and snippet, not the page.
Another query set: `SET=odd versus.py …` reads eval/odd.tsv and writes eval/odd_*.
"""

import hashlib
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

E, N, SET = Path("eval"), 5, os.environ.get("SET", "student")
GOOGLE, JUDGED, POOL, GRADES = (E / f"{SET}_{x}" for x in ("google.jsonl", "judged.tsv", "pool", "grades.txt"))


def queries() -> list[list[str]]:
    return [line.split("\t") for line in (E / f"{SET}.tsv").read_text().splitlines()[1:]]


def load(path: Path) -> dict[str, dict]:
    """Query → record; a later line replaces an earlier one."""
    return {r["q"]: r for r in map(json.loads, path.read_text().splitlines())} if path.exists() else {}


def judged() -> dict[str, int]:
    return {k: int(g) for k, g, *_ in (line.split("\t") for line in JUDGED.read_text().splitlines())} if JUDGED.exists() else {}


def key(q: str, r: dict) -> str:
    """The same page from both engines gets one mark: query + site + start of the title."""
    u = r["u"].split(" › ")[0]
    host = (urlparse(u).hostname or "").removeprefix("www.") if u.startswith("http") else u.split(" · ")[0].lower()
    return hashlib.sha1(f"{q}|{host}|{r['t'][:30]}".encode()).hexdigest()[:10]


def shown(q: str, rec: dict, marks: dict[str, int]) -> list[dict]:
    """The first results (x marks left out). The answers above the results are not compared: Google's AI
    answer comes and goes between two loads of the same query."""
    return [r for r in rec["top"] if marks.get(key(q, r)) != -1][:N]


def listen() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # the page goes to http://127.0.0.1:8765/?d=<JSON> (Google's page rules block fetch)
            rec = json.loads(parse_qs(urlparse(self.path).query)["d"][0])
            with GOOGLE.open("a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(len(rec["top"]), rec["q"], flush=True)
            self.send_response(204)
            self.end_headers()

        def log_message(self, *a):
            pass

    HTTPServer(("127.0.0.1", 8765), Handler).serve_forever()


def run(name: str) -> None:
    from dzirkva.search import search
    from dzirkva.web import main_list

    with (E / f"{SET}_{name}.jsonl").open("w") as f:
        for q, use, _ in queries():
            t = time.time()
            _, results, d = search(q)
            box, word = d["answer"], d["definition"]
            direct = (f"{word['word']}: {'; '.join(word['senses'])}" if word and (word["asked"] or not box)
                      else f"{box['title']}: {box['text']}" if box else "")
            top = [{"t": r.title[:90], "u": r.url, "s": r.snippet[:200]} for r in main_list(results)[:10]]
            f.write(json.dumps({"q": q, "use": use, "direct": direct[:300], "top": top}, ensure_ascii=False) + "\n")
            print(f"{time.time() - t:5.1f}s {q}", flush=True)


def pool(name: str) -> None:
    sides, marks, order, text = [load(GOOGLE), load(E / f"{SET}_{name}.jsonl")], judged(), {}, []
    for n, (q, _, need) in enumerate(queries(), 1):
        new = {key(q, r): r for side in sides if q in side for r in shown(q, side[q], marks) if key(q, r) not in marks}
        if new:
            order[n] = sorted(new)  # hash order mixes the two engines
            text.append(f"\n## {n}. {q}: {need}")
            text += [f"{i}. {new[k]['u'].removeprefix('https://')[:70]} | {new[k]['t'][:70]} | {new[k]['s'][:130]}"
                     for i, k in enumerate(order[n], 1)]
    POOL.with_suffix(".json").write_text(json.dumps(order))
    POOL.with_suffix(".txt").write_text("\n".join(text) + "\n")
    print(f"{sum(map(len, order.values()))} results to judge in {len(order)} queries: {POOL}.txt")


def grade() -> None:
    order, qs = json.loads(POOL.with_suffix(".json").read_text()), queries()
    with JUDGED.open("a") as f:
        for line in GRADES.read_text().split("\n"):
            if line.strip():
                n, marks = line.split()
                assert len(marks) == len(order[n]), f"query {n}: {len(order[n])} results, {len(marks)} marks"
                for k, m in zip(order[n], marks):
                    f.write(f"{k}\t{-1 if m == 'x' else int(m)}\t{qs[int(n) - 1][0]}\n")
    GRADES.unlink()


def score(name: str) -> None:
    sides, marks = {"dzirkva": load(E / f"{SET}_{name}.jsonl"), "google": load(GOOGLE)}, judged()
    rows, missing = [], 0
    for q, use, _ in queries():
        row = {"q": q, "use": use}
        for side, recs in sides.items():
            got = [marks.get(key(q, r)) for r in shown(q, recs[q], marks)] if q in recs else []
            missing += got.count(None)
            g = [m or 0 for m in got]
            row[side] = (2 in g[:1], 2 in g[:3], sum(g) / (2 * N))
        rows.append(row)
    if missing:
        print(f"{missing} results have no mark (counted as 0): run pool and grade")
    print(f"{'use':<10}{'n':>3}   dzirkva @1 @3 quality   google @1 @3 quality")
    for use in [*dict.fromkeys(r["use"] for r in rows), "ALL"]:
        part = [r for r in rows if use in (r["use"], "ALL")]
        cells = ["{:9d} {:2d} {:6.0%}".format(sum(r[s][0] for r in part), sum(r[s][1] for r in part),
                                             sum(r[s][2] for r in part) / len(part)) for s in sides]
        print(f"{use:<10}{len(part):>3}   {cells[0]}   {cells[1]}")
    gap = sorted(rows, key=lambda r: r["dzirkva"][2] - r["google"][2])
    print(f"per query: dzirkva better {sum(r['dzirkva'][2] > r['google'][2] for r in rows)}, "
          f"same {sum(r['dzirkva'][2] == r['google'][2] for r in rows)}, Google better {sum(r['dzirkva'][2] < r['google'][2] for r in rows)}")
    print("work list (dzirkva quality, Google quality):")
    for r in gap[:12]:
        if r["dzirkva"][2] < r["google"][2] or not r["dzirkva"][1]:
            print(f"  {r['dzirkva'][2]:4.0%} {r['google'][2]:4.0%}  {'  ' if r['dzirkva'][1] else 'no good@3 '}{r['q']}  [{r['use']}]")


if __name__ == "__main__":
    {"listen": listen, "run": run, "pool": pool, "grade": grade, "score": score}[sys.argv[1]](*sys.argv[2:])
