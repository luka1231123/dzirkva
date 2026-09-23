"""Barebone search page for testing. Run: uv run python -m dzirkva.web  → http://127.0.0.1:8000

Tabs like large engines (All / News / Videos …): nothing is removed, each result goes to the tab
of its kind. The All tab shows ordinary results, max 2 per site, with small video, film and
social blocks between them.
"""

import time
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

from dzirkva.meaning import similarity
from dzirkva.search import search

TABS = {"all": "ყველა", "knowledge": "ცოდნა", "news": "სიახლეები", "video": "ვიდეო", "film": "ფილმები", "social": "სოციალური"}
TAB_KINDS = {"knowledge": {"knowledge"}, "news": {"news"}, "video": {"video"}, "film": {"film"}, "social": {"social", "forum"}}
MAIN_KINDS = {"knowledge", "news", "web", "forum"}
BLOCKS = {3: "video", 6: "film", 9: "social"}  # All tab: block of that tab after the n-th main result
PER_SITE = 2
_cache: dict[str, tuple[dict, list, float]] = {}

PAGE = """<!doctype html><meta charset="utf-8"><title>dzirkva</title>
<style>body{{font:16px sans-serif;max-width:760px;margin:24px auto;padding:0 16px}}
input{{width:75%;font-size:18px}} .r{{margin:14px 0}} .u{{color:#070;font-size:13px}} .m{{color:#888;font-size:12px}}
.tabs a,.tabs b{{margin-right:14px}} .blk{{border:1px solid #ddd;border-radius:6px;padding:4px 12px;margin:18px 0}}</style>
<form><input name="q" value="{q}" autofocus> <button>ძებნა</button></form>{body}"""


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


def _result(r) -> str:
    tier = f"tier {r.tier} · " if r.tier else ""
    copies = ""
    if r.copies:
        copies = f"<div class=m>also on {len(r.copies)}: " + ", ".join(
            f"<a href='{escape(c.url)}'>{escape(_host(c.url))}</a>" for c in r.copies[:6]) + "</div>"
    return (f"<div class=r><a href='{escape(r.url)}'>{escape(r.title)}</a>"
            f"<div class=u>{escape(r.url[:90])}</div><div>{escape(r.snippet)}</div>{copies}"
            f"<div class=m>{tier}{r.kind} · meaning {r.meaning:.2f} · coverage {r.coverage:.0%} · "
            f"{len(r.queries)} queries · {escape(', '.join(sorted(r.engines)))}</div></div>")


def _all_tab(q: str, results: list) -> str:
    main, per_site = [], {}
    for r in results:
        if r.kind in MAIN_KINDS and per_site.get(_host(r.url), 0) < PER_SITE:
            per_site[_host(r.url)] = per_site.get(_host(r.url), 0) + 1
            main.append(r)
    body = ""
    for i, r in enumerate(main[:30], 1):
        body += _result(r)
        tab = BLOCKS.get(i)
        block = [x for x in results if x.kind in TAB_KINDS.get(tab, ())][:3]
        if block:
            body += (f"<div class=blk><p><b>{TABS[tab]}</b> · <a href='?q={quote(q)}&tab={tab}'>ყველა →</a></p>"
                     + "".join(_result(x) for x in block) + "</div>")
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
                qs, results = search(q)
                _cache[q] = (qs, results, time.time() - t)
            qs, results, secs = _cache[q]
            counts = {t: sum(r.kind in k for r in results) for t, k in TAB_KINDS.items()}
            body = "<p class=tabs>" + "".join(
                (f"<b>{name}</b>" if t == tab else f"<a href='?q={quote(q)}&tab={t}'>{name}</a>")
                + (f" <span class=m>{counts[t]}</span>" if t in counts else "")
                for t, name in TABS.items()) + "</p>"
            body += f"<p class=m>{len(results)} results, {secs:.1f}s</p><details><summary class=m>queries</summary>"
            body += "".join(f"<div class=m>{escape(n)}: {escape(v)}</div>" for n, v in qs.items()) + "</details>"
            if tab == "all":
                body += _all_tab(q, results)
            else:
                body += "".join(_result(r) for r in results if r.kind in TAB_KINDS.get(tab, ())) or "<p>—</p>"
        html = PAGE.format(q=escape(q), body=body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html)


if __name__ == "__main__":
    similarity("გამარჯობა", ["გამარჯობა"])  # load the meaning model once, before the first search
    print("http://127.0.0.1:8000", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8000), Handler).serve_forever()
