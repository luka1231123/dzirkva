"""One time, after the fixes of 2026-11-17 in crawl_sites.py: give wrongly rejected domains a second probe.

1. WooCommerce: the old rule rejected any site whose HTML had the word "woocommerce" (blog theme CSS).
   Rejected sites with more than 30% Georgian whose score is under COMMERCIAL without that word go back to
   `probe`: their probe pages are queued again, so the crawler collects the new signals (cart buttons) and
   decides again. Their page count and Georgian share stay: a page already stored does not count twice.
2. Dead hosts: rejected sites without pages whose robots.txt did not answer join table `dead` with a wait
   of 0, so the crawler probes them again (REVIVE an hour, .ge first).

The crawler holds every domain in memory and writes it back, so stop it first:
    sudo systemctl stop dzirkva-crawl
    uv run python scripts/recrawl_rejected.py           # counts only
    uv run python scripts/recrawl_rejected.py apply
    sudo systemctl start dzirkva-crawl
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from dzirkva.crawl import COMMERCIAL, DB, MIN_GEORGIAN, connect  # noqa: E402


def commercial(signals: set[str]) -> int:  # the new rule of crawl_sites.py, without importing its startup code
    return ("ads" in signals) + 2 * ("cart" in signals) + 2 * ("shop" in signals)


apply = sys.argv[1:] == ["apply"]
if apply and subprocess.run(["systemctl", "is-active", "--quiet", "dzirkva-crawl"], check=False).returncode == 0:
    sys.exit("dzirkva-crawl runs: stop it first (sudo systemctl stop dzirkva-crawl)")
db = connect() if apply else sqlite3.connect(f"file:{DB}?mode=ro", uri=True)  # counts only: no write next to the crawler
if apply:
    db.execute("CREATE TABLE IF NOT EXISTS dead (host TEXT PRIMARY KEY, tries INT, next REAL)")

woo = []
for host, signals in db.execute("SELECT host, signals FROM domains WHERE state='rejected' AND pages>0 AND georgian>? "
                                "AND signals LIKE '%woocommerce%'", (MIN_GEORGIAN,)):
    rest = set(signals.split(",")) - {"woocommerce", ""}
    if commercial(rest) < COMMERCIAL:
        woo.append((host, ",".join(sorted(rest)), commercial(rest)))
dead = db.execute("SELECT count(DISTINCT q.host), count(DISTINCT CASE WHEN q.host LIKE '%.ge' THEN q.host END) "
                  "FROM queue q JOIN domains d ON d.host=q.host WHERE q.status='dead' AND d.state='rejected' "
                  "AND d.pages=0").fetchone()
print(f"WooCommerce: {len(woo):,} domains back to probe ({sum(h.endswith('.ge') for h, *_ in woo):,} .ge)")
print(f"dead: {dead[0]:,} domains to probe again ({dead[1]:,} .ge)")
if not apply:
    print("counts only; run with 'apply' to change crawl.db")
    sys.exit()

pages = 0
for host, signals, score in woo:
    db.execute("UPDATE domains SET state='probe', signals=?, commercial=? WHERE host=?", (signals, score, host))
    pages += db.execute("UPDATE queue SET status='todo' WHERE host=? AND status!='todo'", (host,)).rowcount
db.execute("INSERT OR IGNORE INTO dead SELECT DISTINCT q.host, 1, 0 FROM queue q JOIN domains d ON d.host=q.host "
           "WHERE q.status='dead' AND d.state='rejected' AND d.pages=0")
db.commit()
print(f"done: {pages:,} probe pages queued again; table dead has {db.execute('SELECT count(*) FROM dead').fetchone()[0]:,} hosts")
