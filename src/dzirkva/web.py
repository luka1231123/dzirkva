"""Barebone search page for testing. Run: uv run python -m dzirkva.web  → http://127.0.0.1:8000

Page structure (plan.md, Session 7): a tab changes the layout, a filter narrows the sources.
Tabs: ყველა, ვიდეო, სიახლეები; the tab that fits the query words comes right after ყველა.
Filters (chips, combined with AND): ცოდნა, ტექსტები, ხალხი, სამეცნიერო, იშვიათი, ძველი ვები.
The All tab without filters shows ordinary results, max 2 per site, with video, people,
small-site and old-web blocks between them. With a filter it shows the plain filtered list.
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

TABS = {"all": "ყველა", "video": "ვიდეო", "news": "სიახლეები"}
TAB_KINDS = {"video": {"video", "film"}, "news": {"news"}}
TAB_WORDS = {"video": "ფილმი სერიალი კინო მულტფილმი ვიდეო კლიპი ტრეილერი სიმღერა მუსიკა ონლაინ",
             "news": "სიახლე ამბავი დღეს გუშინ არჩევნები"}  # query word families that move the tab forward
FILTERS = {"knowledge": "ცოდნა", "texts": "ტექსტები", "people": "ხალხი", "academic": "სამეცნიერო",
           "small": "იშვიათი", "old": "ძველი ვები"}
MAIN_KINDS = {"knowledge", "news", "web", "forum"}
BLOCKS = {3: "video", 5: "people", 7: "small", 10: "old"}  # All tab: block after the n-th main result
PER_SITE = 2
_cache: dict[str, tuple[dict, list, dict]] = {}

PAGE = """<!doctype html><meta charset="utf-8"><title>dzirkva</title>
<style>body{{font:16px sans-serif;max-width:760px;margin:24px auto;padding:0 16px}}
input{{width:75%;font-size:18px}} .r{{margin:14px 0}} .u{{color:#070;font-size:13px}} .m{{color:#888;font-size:12px}}
.tabs a,.tabs b{{margin-right:14px}}
.chips a{{display:inline-block;border:1px solid #bbb;border-radius:12px;padding:0 9px;margin:0 6px 6px 0;text-decoration:none;color:#333}}
.chips a.on{{background:#333;color:#fff}} .blk{{border:1px solid #ddd;border-radius:6px;padding:4px 12px;margin:18px 0}}
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
            f"<div class=why>matched: {matched} · {tier}{r.kind} {' '.join(sorted(r.tags))} · meaning {r.meaning:.2f} · "
            f"coverage {r.coverage:.0%} · score {r.score * 1000:.1f}</div>"
            f"<div class=m>found by: {_found_by(r)}</div></div>")


def _link(q: str, tab: str, filters: set[str]) -> str:
    return f"?q={quote(q)}" + (f"&tab={tab}" if tab != "all" else "") + "".join(f"&f={f}" for f in sorted(filters))


def _tab_order(content: list[str]) -> list[str]:
    """ყველა first, then the tab whose words are in the query, then the rest."""
    fams = {f for w in content for f in families(w)} | set(content)
    hit = [t for t, ws in TAB_WORDS.items() if fams & set(ws.split())]
    return ["all"] + hit + [t for t in TABS if t != "all" and t not in hit]


def _in_tab(r, tab: str) -> bool:
    return tab == "all" or r.kind in TAB_KINDS[tab]


def _all_tab(q: str, results: list, marks: dict[str, str]) -> str:
    main, per_site = [], {}
    for r in results:
        if r.kind in MAIN_KINDS and per_site.get(_host(r.url), 0) < PER_SITE:
            per_site[_host(r.url)] = per_site.get(_host(r.url), 0) + 1
            main.append(r)
    main = main[:30]
    shown = {id(r) for r in main}
    body = ""
    for i, r in enumerate(main, 1):
        body += _result(r, marks)
        name = BLOCKS.get(i)
        if name is None:
            continue
        pick = (lambda x: x.kind in TAB_KINDS[name]) if name in TABS else (lambda x: name in x.tags)
        block = [x for x in results if pick(x) and id(x) not in shown][:3]
        shown |= {id(x) for x in block}
        more = _link(q, name, set()) if name in TABS else _link(q, "all", {name})
        if block:
            body += (f"<div class=blk><p><b>{TABS.get(name) or FILTERS[name]}</b> · <a href='{more}'>ყველა →</a></p>"
                     + "".join(_result(x, marks) for x in block) + "</div>")
    return body


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        params = parse_qs(urlparse(self.path).query)
        q = params.get("q", [""])[0].strip()
        tab = params.get("tab", ["all"])[0]
        tab = tab if tab in TABS else "all"
        chosen = {f for f in params.get("f", []) if f in FILTERS}
        body = ""
        if q:
            if q not in _cache:
                t = time.time()
                qs, results, debug = search(q)
                debug["seconds"]["total"] = round(time.time() - t, 1)
                _cache[q] = (qs, results, debug)
            qs, results, debug = _cache[q]
            marks = _marks(debug)
            passes = lambda r, fs: _in_tab(r, tab) and fs <= r.tags
            tab_count = {t: sum(_in_tab(r, t) and chosen <= r.tags for r in results) for t in TABS}
            body = "<p class=tabs>" + "".join(
                (f"<b>{TABS[t]}</b>" if t == tab else f"<a href='{_link(q, t, chosen)}'>{TABS[t]}</a>")
                + (f" <span class=m>{tab_count[t]}</span>" if t != "all" else "")
                for t in _tab_order(debug["content"])) + "</p>"
            body += "<p class=chips>" + "".join(
                f"<a class={'on' if f in chosen else 'off'} href='{_link(q, tab, chosen ^ {f})}'>{name}"
                f" <span class=m>{sum(passes(r, chosen | {f}) for r in results)}</span></a>"
                for f, name in FILTERS.items()) + "</p>"
            if debug["spelling"]:
                fixed = " ".join(debug["spelling"].get(normalize(w), w) for w in q.split())
                body += f"<p>ნაჩვენებია შედეგები: <b>{escape(fixed)}</b> <span class=m>(typed: {escape(q)})</span></p>"
            if (a := debug.get("answer")) and tab == "all" and not chosen:
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
            if tab == "all" and not chosen:
                body += _all_tab(q, results, marks)
            else:
                body += "".join(_result(r, marks) for r in results if passes(r, chosen)) or "<p>—</p>"
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
