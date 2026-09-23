"""Barebone search page for testing. Run: uv run python -m dzirkva.web  → http://127.0.0.1:8000

Tabs like large engines (All / News / Videos …): nothing is removed, each result goes to the tab
of its kind. The All tab shows ordinary results, max 2 per site, with small video, film and
social blocks between them.
"""

import re
import time
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, quote, urlparse

from dzirkva.georgian import normalize
from dzirkva.meaning import similarity
from dzirkva.morph import families
from dzirkva.search import search

TABS = {"all": "ყველა", "knowledge": "ცოდნა", "news": "სიახლეები", "archive": "არქივი", "video": "ვიდეო",
        "film": "ფილმები", "social": "სოციალური"}
TAB_KINDS = {"knowledge": {"knowledge"}, "news": {"news"}, "archive": {"archive"}, "video": {"video"},
             "film": {"film"}, "social": {"social", "forum"}}
MAIN_KINDS = {"knowledge", "news", "web", "forum"}
BLOCKS = {3: "video", 5: "archive", 7: "film", 10: "social"}  # All tab: block after the n-th main result
PER_SITE = 2
_cache: dict[str, tuple[dict, list, dict]] = {}

PAGE = """<!doctype html><meta charset="utf-8"><title>dzirkva</title>
<style>body{{font:16px sans-serif;max-width:760px;margin:24px auto;padding:0 16px}}
input{{width:75%;font-size:18px}} .r{{margin:14px 0}} .u{{color:#070;font-size:13px}} .m{{color:#888;font-size:12px}}
.tabs a,.tabs b{{margin-right:14px}} .blk{{border:1px solid #ddd;border-radius:6px;padding:4px 12px;margin:18px 0}}
mark{{background:#fff3a0}} mark.fb{{background:#cdeaff}} .why{{color:#555;font-size:12px}} .dbg{{font:12px monospace;background:#f6f6f6;padding:8px}}
.ans{{border:1px solid #ccd;background:#f7f8ff;border-radius:6px;padding:8px 12px;margin:12px 0}}
table{{border-collapse:collapse}} td{{padding:1px 8px 1px 0;vertical-align:top}}</style>
<form><input name="q" value="{q}" autofocus> <button>ძებნა</button></form>{body}"""


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


def _highlight(text: str, marks: dict[str, str]) -> tuple[str, list[str]]:
    """Escape text and wrap words whose family matches a query word. Returns (html, matched words)."""
    out, found = [], []
    for part in re.split(r"(\w+)", text):
        fams = families(normalize(part)) if part and part[0].isalnum() else set()
        cls = next((c for f, c in marks.items() if f in fams or f == normalize(part)), None)
        if cls is None:
            out.append(escape(part))
        else:
            out.append(f"<mark class={cls}>{escape(part)}</mark>")
            found.append(part)
    return "".join(out), found


def _marks(debug: dict) -> dict[str, str]:
    """Word family -> CSS class: query words yellow, feedback words blue."""
    marks = {f: "q" for w in debug["content"] for f in families(w) | {w}}
    for term in debug["feedback"]:
        for w in term.split():
            marks.update({f: "fb" for f in families(w) | {w}} if w not in marks else {})
    return marks


def _found_by(r) -> str:
    by: dict[str, list[str]] = {}
    for name, engine, rank in r.hits:
        by.setdefault(name, []).append(f"{engine} #{rank}")
    return "; ".join(f"{escape(n)}: {', '.join(v)}" for n, v in by.items())


def _result(r, marks: dict[str, str]) -> str:
    title, t_found = _highlight(r.title, marks)
    snippet, s_found = _highlight(r.snippet, marks)
    matched = ", ".join(dict.fromkeys(escape(w) for w in t_found + s_found)) or "—"
    tier = f"tier {r.tier} · " if r.tier else "small site · " if r.small else ""
    copies = ""
    if r.copies:
        copies = f"<div class=m>also on {len(r.copies)}: " + ", ".join(
            f"<a href='{escape(c.url)}'>{escape(_host(c.url))}</a>" for c in r.copies[:6]) + "</div>"
    return (f"<div class=r><a href='{escape(r.url)}'>{title}</a>"
            f"<div class=u>{escape(r.url[:90])}</div><div>{snippet}</div>{copies}"
            f"<div class=why>matched: {matched} · {tier}{r.kind} · meaning {r.meaning:.2f} · "
            f"coverage {r.coverage:.0%} · score {r.score * 1000:.1f}</div>"
            f"<div class=m>found by: {_found_by(r)}</div></div>")


def _all_tab(q: str, results: list, marks: dict[str, str]) -> str:
    main, per_site = [], {}
    for r in results:
        if r.kind in MAIN_KINDS and per_site.get(_host(r.url), 0) < PER_SITE:
            per_site[_host(r.url)] = per_site.get(_host(r.url), 0) + 1
            main.append(r)
    body = ""
    for i, r in enumerate(main[:30], 1):
        body += _result(r, marks)
        tab = BLOCKS.get(i)
        block = [x for x in results if x.kind in TAB_KINDS.get(tab, ())][:3]
        if block:
            body += (f"<div class=blk><p><b>{TABS[tab]}</b> · <a href='?q={quote(q)}&tab={tab}'>ყველა →</a></p>"
                     + "".join(_result(x, marks) for x in block) + "</div>")
    return body


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        params = parse_qs(urlparse(self.path).query)
        q = params.get("q", [""])[0].strip()
        tab = params.get("tab", ["all"])[0]
        body = ""
        if q:
            if q not in _cache:
                t = time.time()
                qs, results, debug = search(q)
                debug["seconds"]["total"] = round(time.time() - t, 1)
                _cache[q] = (qs, results, debug)
            qs, results, debug = _cache[q]
            marks = _marks(debug)
            counts = {t: sum(r.kind in k for r in results) for t, k in TAB_KINDS.items()}
            body = "<p class=tabs>" + "".join(
                (f"<b>{name}</b>" if t == tab else f"<a href='?q={quote(q)}&tab={t}'>{name}</a>")
                + (f" <span class=m>{counts[t]}</span>" if t in counts else "")
                for t, name in TABS.items()) + "</p>"
            if debug["spelling"]:
                fixed = " ".join(debug["spelling"].get(normalize(w), w) for w in q.split())
                body += f"<p>ნაჩვენებია შედეგები: <b>{escape(fixed)}</b> <span class=m>(typed: {escape(q)})</span></p>"
            if (a := debug.get("answer")) and tab == "all":
                body += (f"<div class=ans><a href='{escape(a['url'])}'><b>{escape(a['title'])}</b></a>"
                         f"<div>{escape(a['text'])}</div><div class=m>ვიკიპედია</div></div>")
            body += (f"<details class=dbg><summary>debug · {len(results)} results · {debug['seconds']['total']}s</summary>"
                     f"<div>content words: {escape(' · '.join(debug['content']))}</div>"
                     f"<div>spelling: {escape(str(debug['spelling']) if debug['spelling'] else '—')}</div>"
                     f"<div>feedback terms: <mark class=fb>{escape(' · '.join(debug['feedback']) or '—')}</mark></div>"
                     f"<div>seconds: {escape(str(debug['seconds']))}</div><table>"
                     + "".join(f"<tr><td>{escape(n)}</td><td>{debug['counts'].get(n, 0)}</td><td>{escape(v)}</td></tr>"
                               for n, v in qs.items())
                     + "</table></details>")
            if tab == "all":
                body += _all_tab(q, results, marks)
            else:
                body += "".join(_result(r, marks) for r in results if r.kind in TAB_KINDS.get(tab, ())) or "<p>—</p>"
        html = PAGE.format(q=escape(q), body=body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html)


if __name__ == "__main__":
    similarity("გამარჯობა", ["გამარჯობა"])  # load the meaning model once, before the first search
    print("http://127.0.0.1:8000", flush=True)
    # One thread: the meaning model on the Mac GPU hangs when called from other threads.
    HTTPServer(("127.0.0.1", 8000), Handler).serve_forever()
