"""Telemetry: how people use dzirkva (data/telemetry.db), and the numbers for the /stats page.

Every request is one event: a page (home, about, discover, site, random), a search (typed, or a tab, filter,
related search, "did you mean" … on the same query: `from` says which), a click through /go (a result with its
rank and block, or a surfing link), a UI action from the page (citation opened or copied, the "how we found it"
panel), an error. No IP address is stored. The session id is random, in a cookie that lives SESSION_MINUTES after
the last request: it links the steps of one visit (query → click → new query). A browser that sends Do Not Track
or GPC gets no session id. Bots (by user agent) are marked and left out of the numbers. Local visits (this
computer, not through a tunnel) are marked too. Events older than KEEP_DAYS are deleted.
"""

import json
import re
import sqlite3
import time
from collections import Counter, defaultdict
from functools import cache
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "telemetry.db"
SESSION_MINUTES = 30
KEEP_DAYS = 180
REFINE_SECONDS = 300  # a new query this soon after another, with no click between, is a reformulation
BOT = re.compile(r"(?i)bot|crawl|spider|slurp|curl|wget|python|httpx|headless|preview|scan|monitor")
SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, time REAL, session TEXT, kind TEXT, q TEXT, data TEXT,
                                   ms INT);
CREATE INDEX IF NOT EXISTS events_time ON events(time);
"""


@cache
def _db() -> sqlite3.Connection:
    db = sqlite3.connect(DB, check_same_thread=False, timeout=10)
    db.executescript(SCHEMA)
    db.execute("DELETE FROM events WHERE time < ?", (time.time() - KEEP_DAYS * 86400,))
    db.commit()
    return db


def client(user_agent: str, language: str, referrer_host: str, local: bool) -> dict:
    """What we keep about the visitor: device class, bot or not, first language, the site that linked here."""
    ua = user_agent or ""
    device = "tablet" if "iPad" in ua or ("Android" in ua and "Mobile" not in ua) else \
        "mobile" if re.search(r"Mobi|Android|iPhone", ua) else "desktop"
    lang = re.split(r"[-,;]", language or "", maxsplit=1)[0].strip().lower()
    out = {"device": device, "lang": lang, "local": local}
    if BOT.search(ua) or not ua:
        out["bot"] = True
    if referrer_host:
        out["ref"] = referrer_host
    return out


def log(kind: str, session: str, q: str = "", ms: int = 0, **data) -> None:
    """One event. Telemetry must never break a page: errors are ignored."""
    try:
        _db().execute("INSERT INTO events (time, session, kind, q, data, ms) VALUES (?, ?, ?, ?, ?, ?)",
                      (time.time(), session, kind, q, json.dumps(data, ensure_ascii=False), ms))
        _db().commit()
    except sqlite3.Error:
        pass


def events(days: float, local: bool = True) -> list[dict]:
    """Events of the last `days` (0: all), bots left out; local=False leaves out visits from this computer."""
    since = time.time() - days * 86400 if days else 0
    out = []
    for t, session, kind, q, data, ms in _db().execute(
            "SELECT time, session, kind, q, data, ms FROM events WHERE time >= ? ORDER BY id", (since,)):
        d = json.loads(data or "{}")
        if not d.get("bot") and (local or not d.get("local")):
            out.append({**d, "time": t, "session": session, "kind": kind, "q": q, "ms": ms})
    return out


def _norm(q: str) -> str:
    return " ".join(q.lower().split())


def stats(evs: list[dict]) -> dict:
    """All the numbers of the /stats page."""
    by = defaultdict(list)
    for e in evs:
        by[e["kind"]].append(e)
    searches = by["search"]
    typed = [e for e in searches if e.get("from", "typed") not in ("tab", "filter", "more")]
    fresh = [e for e in searches if e.get("fresh")]
    clicks = by["click"]
    result_clicks = [c for c in clicks if c.get("rank")]
    clicked = {(c["session"], _norm(c["q"])) for c in clicks if c["session"] and c["q"]}
    asked = {(e["session"], _norm(e["q"])) for e in typed if e["session"]}
    sessions = {e["session"] for e in evs if e["session"]}
    day = lambda e: time.strftime("%Y-%m-%d", time.localtime(e["time"]))
    seconds = sorted(e.get("sec", 0) for e in fresh)

    # reformulations: in a session, a typed query followed by another within REFINE_SECONDS, no click between
    refine: Counter[tuple[str, str]] = Counter()
    for s, seq in _sessions(evs).items():
        last = None
        for e in seq:
            if e["kind"] == "click":
                last = None
            elif e["kind"] == "search" and e.get("from", "typed") not in ("tab", "filter", "more"):
                if last and _norm(last["q"]) != _norm(e["q"]) and e["time"] - last["time"] <= REFINE_SECONDS:
                    refine[(last["q"], e["q"])] += 1
                last = e

    top = Counter(_norm(e["q"]) for e in typed)
    clicks_per_q = Counter(_norm(c["q"]) for c in result_clicks)
    return {
        "numbers": {
            "searches": len(typed), "tab_filter_views": len(searches) - len(typed), "computed": len(fresh),
            "clicks": len(clicks), "result_clicks": len(result_clicks), "sessions": len(sessions),
            "searches_per_session": round(len(typed) / len(sessions), 1) if sessions else 0,
            "clicked_share": round(len(asked & clicked) / len(asked), 2) if asked else 0,
            "zero_results": sum(e.get("n", 1) == 0 for e in typed),
            "median_sec": seconds[len(seconds) // 2] if seconds else 0,
            "p90_sec": seconds[int(len(seconds) * 0.9)] if seconds else 0,
            "mobile_share": round(sum(e.get("device") != "desktop" for e in typed) / len(typed), 2) if typed else 0,
            "local_share": round(sum(bool(e.get("local")) for e in evs) / len(evs), 2) if evs else 0,
            "busy": len(by["busy"]),
        },
        "days": sorted(Counter(day(e) for e in typed).items()),
        "click_days": dict(Counter(day(c) for c in clicks)),
        "top_queries": [(q, n, clicks_per_q[q]) for q, n in top.most_common(30)],
        "zero": Counter(_norm(e["q"]) for e in typed if e.get("n", 1) == 0).most_common(20),
        "no_click": Counter(q for s, q in asked - clicked).most_common(20),
        "refine": refine.most_common(20),
        "ranks": sorted(Counter(min(c["rank"], 11) for c in result_clicks).items()),
        "where": Counter(c.get("where", "") for c in clicks).most_common(),
        "hosts": Counter(c.get("host", "") for c in clicks).most_common(20),
        "kinds": Counter(c.get("result_kind", "") for c in result_clicks if c.get("result_kind")).most_common(),
        "tags": Counter(t for c in result_clicks for t in c.get("tags", [])).most_common(),
        "from": Counter(e.get("from", "typed") for e in searches).most_common(),
        "tabs": Counter(e.get("tab", "all") for e in searches).most_common(),
        "filters": Counter(e.get("f") for e in searches if e.get("f")).most_common(),
        "intents": Counter(e.get("intent") or "none" for e in fresh).most_common(15),
        "types": Counter(e.get("type") or "none" for e in fresh).most_common(),
        "features": {
            "spelling fixed": sum(bool(e.get("fixed")) for e in fresh),
            "did you mean shown": sum(bool(e.get("dym")) for e in fresh),
            "answer box": sum(bool(e.get("answer")) for e in fresh),
            "dictionary box": sum(bool(e.get("definition")) for e in fresh),
            "feedback round": sum(bool(e.get("feedback")) for e in fresh),
            "papers in top 10": sum(bool(e.get("papers")) for e in fresh),
            "citation opened": sum(u.get("what") == "cite" and u.get("open") for u in by["ui"]),
            "citation copied": sum(u.get("what") == "copy" for u in by["ui"]),
            "how-we-found panel toggled": sum(u.get("what") == "debug" for u in by["ui"]),
        },
        "pages": Counter(e["kind"] for e in evs if e["kind"] in ("home", "about", "discover", "site", "random")).most_common(),
        "sites": Counter(e.get("host", "") for e in by["site"]).most_common(15),
        "devices": Counter(e.get("device", "") for e in evs if e["kind"] in ("search", "home")).most_common(),
        "langs": Counter(e.get("lang") or "none" for e in evs if e["kind"] in ("search", "home")).most_common(8),
        "refs": Counter(e["ref"] for e in evs if e.get("ref")).most_common(10),
        "slow": sorted(((e.get("sec", 0), e["q"]) for e in fresh), reverse=True)[:10],
        "errors": [(e["time"], e.get("where", ""), e["q"], e.get("error", "")) for e in by["error"]][-20:],
        "recent": list(_sessions(evs).items())[-15:],
    }


def _sessions(evs: list[dict]) -> dict[str, list[dict]]:
    """Events per session, in time order; sessions in order of their first event."""
    out: dict[str, list[dict]] = {}
    for e in evs:
        if e["session"]:
            out.setdefault(e["session"], []).append(e)
    return out
