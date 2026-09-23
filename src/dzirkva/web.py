"""Search page for testing. Run: uv run python -m dzirkva.web  → http://127.0.0.1:8000
Surfing pages without a query: /site?h=host (what we know about a site), /discover, /random (a small site).

Page structure (plan.md, Session 7): a tab changes the layout, a filter narrows the sources.
Tabs: ყველა, ვიდეო, სიახლეები; the tab that fits the query words comes right after ყველა.
Filters (chips, one at a time): ცოდნა, ტექსტები, ხალხი, სამეცნიერო, ძველი ვები.
The All tab without filters shows ordinary results, max 2 per site and max 3 social posts
(Facebook …), with video, people and old-web blocks between them. With a filter it shows the plain filtered list.
"""

import random
import re
import time
from functools import cache
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

import yaml

from dzirkva.georgian import normalize
from dzirkva import clicks, discover, papers, passages
from dzirkva.meaning import similarity
from dzirkva.morph import analyze, families
from dzirkva.search import canonical_url, intents, search

TABS = {"all": "ყველა", "video": "ვიდეო", "news": "სიახლეები"}
TAB_KINDS = {"video": {"video", "film"}, "news": {"news"}}
TAB_WORDS = {"video": "ფილმი სერიალი კინო მულტფილმი ვიდეო კლიპი ტრეილერი სიმღერა მუსიკა ონლაინ",
             "news": "სიახლე ამბავი დღეს გუშინ არჩევნები"}  # query word families that move the tab forward
FILTERS = {"knowledge": "ცოდნა", "texts": "ტექსტები", "people": "ხალხი", "small": "პატარა ვები", "academic": "სამეცნიერო",
           "old": "ძველი ვები"}
MAIN_KINDS = {"knowledge", "news", "web", "forum", "social"}
BLOCKS = {3: "video", 5: "people", 10: "old"}  # All tab: block after the n-th main result
PER_SITE = 2
SITE_LIMIT = {"ka.wikipedia.org": 2, "ka.wikisource.org": 1}  # local indexes must not fill the list
MAX_SOCIAL = 3  # social posts in the main list (all social sites together)
ENGINE_NAMES = {"google": "გუგლი", "yandex": "იანდექსი", "yahoo": "იაჰუ", "brave-api": "ბრეივი",
                "wikipedia": "ვიკიპედია", "passages": "ვიკიპედია (აზრით)", "archive": "ძველი ვები", "crawl": "ჩვენი ინდექსი", "iverieli": "ივერიელი", "wikisource": "ვიკიწყარო",
                "cited": "ვიკიპედიის წყაროები", "named": "დასახელებული საიტი"}
KIND_NAMES = {"knowledge": "ცოდნა", "news": "სიახლე", "web": "ვები", "forum": "ფორუმი", "social": "სოციალური ქსელი",
              "video": "ვიდეო", "film": "ფილმი"}
CATEGORY_NAMES = {"reference": "ცნობარი", "law": "სამართალი", "history": "ისტორია", "religion": "რელიგია",
                  "education": "განათლება", "government": "სახელმწიფო", "culture": "კულტურა"}
QUESTION = {"why": "რატომ", "how": "როგორ", "when": "როდის", "amount": "რამდენი"}
RELATED_FROM = {"wiki": "ვიკიპედიის სათაური", "feedback": "პასუხის სიტყვა", "meaning": "აზრით ახლო"}
STEP_NAMES = {"round1": "პირველი რაუნდი", "round2+rank": "მეორე რაუნდი და რიგი", "total": "სულ"}
ARCHIVE_YEAR = re.compile(r"web\.archive\.org/web/(\d{4})")
WAYBACK = re.compile(r"^https?://web\.archive\.org/web/[^/]+/")
DATE_FIRST = re.compile(r"^(\d{4}-\d{2}-\d{2})\S* — ")  # crawl snippets start with the page date
EGGS_FILE = Path(__file__).resolve().parents[2] / "config" / "easter_eggs.yaml"
_cache: dict[str, tuple[dict, list, dict]] = {}

# Colors are tokens: light by default, dark when the system asks for it.
CSS = """
:root{--bg:#1b1714;--card:#241f1b;--ink:#f1e9df;--text:#d6ccc0;--muted:#9a8f83;--line:#352e28;
--link:#eab676;--visited:#d1a2c4;--accent:#d9774b;--fb:#3a2c1f;
--ok-bg:#25331f;--ok:#a9cf8e;--old-bg:#3b2c1a;--old:#e2b87c;--rare-bg:#2f2638;--rare:#c7b3e6;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15.5px/1.6 "Noto Serif Georgian",Georgia,serif}
a{color:var(--link);text-decoration:none} a:hover{text-decoration:underline} a:visited{color:var(--visited)}
.cap,.logo,.tabs a,.chips a,button,.blk h3,.lbl,.dbg summary,.rel .m{letter-spacing:.04em}
header{border-bottom:1px solid var(--line);background:var(--card)}
header div,main{max-width:720px;margin:0 auto;padding:0 16px}
header div{display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px;padding-block:14px}
.logo,.logo:visited{color:var(--accent);font-weight:600;font-size:20px;letter-spacing:.08em;text-decoration:none}
form{display:flex;flex:1;min-width:240px;gap:8px}
input{flex:1;min-width:0;font:inherit;font-size:16px;color:var(--ink);background:var(--bg);
 border:1px solid var(--line);border-radius:22px;padding:8px 16px;outline:none}
input:focus{border-color:var(--link)}
button{font:inherit;font-size:12.5px;font-weight:600;border:0;border-radius:22px;padding:8px 18px;background:var(--accent);color:#1b1714;cursor:pointer}
.tabs{display:flex;gap:22px;border-bottom:1px solid var(--line);margin-top:6px}
.tabs a{padding:11px 0 8px;font-size:12.5px;color:var(--muted);border-bottom:2px solid transparent}
.tabs a.on{color:var(--ink);border-color:var(--accent);font-weight:600} .tabs a:hover{text-decoration:none;color:var(--ink)}
.chips{display:flex;gap:8px;overflow-x:auto;padding:12px 0 4px;scrollbar-width:none}
.chips a{flex:none;border:1px solid var(--line);border-radius:16px;padding:3px 12px;color:var(--text);background:var(--card);font-size:11.5px}
.chips a.on{background:var(--ink);border-color:var(--ink);color:var(--bg)} .chips a:hover{text-decoration:none;border-color:var(--muted)}
.m{color:var(--muted);font-size:11.5px}
.fix{font-size:14px;margin:14px 0 4px} .fix b{color:var(--ink)}
.und,.src{font-size:12.5px;color:var(--muted);margin:6px 0} .und b{color:var(--ink);font-weight:600}
.und .cap,.src .cap{font-size:11px;color:var(--text);margin-right:4px}
mark{background:none;color:inherit} mark.q{font-weight:600;color:var(--ink)}
mark.fb{background:var(--fb);border-radius:3px;padding:0 2px}
.t mark,.t mark.q{color:inherit;background:none;padding:0}
.ans,.blk,.dbg{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 18px;margin:18px 0}
.ans .t{font-size:19px;font-weight:500} .ans p{margin:6px 0;color:var(--text)} .ans .cap{font-size:10.5px;color:var(--muted)}
.egg{margin:18px 0 6px;font-size:21px;font-weight:600;letter-spacing:.06em;color:var(--accent)}
.dict ol{margin:8px 0;padding-left:22px} .dict li{margin:3px 0;padding-left:2px} .dict li::marker{color:var(--muted)}
.dict .syn{font-size:14px} .dict .syn .cap{margin-right:6px} .dict .t{margin-right:6px}
.blk h3{margin:0 0 6px;font-size:12.5px;font-weight:600;color:var(--ink)} .blk h3 a{font-weight:400;margin-left:10px}
.r{margin:26px 0}
.site{font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.site .host{color:var(--ink)}
.r .t{display:block;font-size:18px;line-height:1.4;font-weight:500;margin:3px 0}
.snip{margin:4px 0;color:var(--text);font-size:15px;line-height:1.7} .date{color:var(--muted)}
.tags{margin:6px 0 2px;font-size:12px;color:var(--muted)}
.lbl{display:inline-block;font-size:10.5px;border-radius:6px;padding:1px 8px;margin-right:6px;background:var(--ok-bg);color:var(--ok)}
.lbl.old{background:var(--old-bg);color:var(--old)} .lbl.rare{background:var(--rare-bg);color:var(--rare)}
.why{font-size:11.5px;color:var(--muted);line-height:1.55;margin-top:4px}
.dbg{font-size:12px;line-height:1.6;color:var(--text)} .dbg summary{cursor:pointer;color:var(--ink);font-weight:600;font-size:11.5px}
.dbg .tbl{overflow-x:auto} table{border-collapse:collapse} td{padding:1px 10px 1px 0;vertical-align:top}
.rel{display:flex;flex-wrap:wrap;gap:8px}
.rel a{border:1px solid var(--line);border-radius:16px;padding:4px 12px;background:var(--bg);font-size:14px}
.rel a .m{margin-left:8px;font-size:10px}
.site a.host,.site a.host:visited{color:var(--ink)}
.list{margin:8px 0;padding:0;list-style:none} .list li{margin:7px 0;line-height:1.5} .list .m{margin-right:8px}
.facts{font-size:13px;color:var(--muted);margin:6px 0 4px} .hero .t{font-size:24px}
.go{display:inline-block;margin-top:10px}
@media (max-width:520px){.r .t{font-size:17px} .ans,.blk,.dbg{padding:12px 14px;border-radius:10px}}
"""



def _page(q: str, body: str) -> str:
    return (f"<!doctype html><html lang=ka><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'><title>{escape(q) + ' · ' if q else ''}ძირკვა</title>"
            "<link rel=preconnect href=https://fonts.googleapis.com><link rel=stylesheet href="
            "'https://fonts.googleapis.com/css2?family=Noto+Serif+Georgian:wght@400..600&display=swap'>"
            f"<style>{CSS}</style><header><div><a class=logo href=/>{cap('ძირკვა')}</a>"
            f"<form><input name=q value='{escape(q)}' autofocus><button>{cap('ძებნა')}</button></form></div></header>"
            f"<main>{body}</main>")


def cap(text: str) -> str:
    """Mtavruli (Georgian capitals) for short labels. CSS text-transform leaves Georgian unchanged."""
    return text.upper()


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
        by.setdefault(name, []).append(f"{ENGINE_NAMES.get(engine, engine)} #{rank}")
    return "; ".join(f"{escape(_query_name(n))} — {', '.join(v)}" for n, v in by.items())


def _query_name(name: str) -> str:
    """Query names from search.py (original, lemmas, site:law, feedback:…) in Georgian."""
    kind, _, arg = name.partition(":")
    fixed = {"original": "როგორც დაიწერა", "corrected": "გასწორებული", "lemmas": "ლექსიკონის ფორმები",
             "lemmas:corrected": "გასწორებულის ლექსიკონის ფორმები"}
    if kind == "site":
        return f"საიტები: {intents()[arg]['ka'] if arg in intents() else CATEGORY_NAMES.get(arg, arg)}"
    if kind == "named":
        return f"დასახელებული საიტი: {arg}"
    if kind == "feedback":
        return f"პასუხის სიტყვა: {arg}"
    return fixed.get(name) or ENGINE_NAMES.get(name, name)


def _labels(r) -> str:
    """What dzirkva knows about the source: trusted list, rare site, cited by Wikipedia, old web with its year."""
    out = []
    if r.tier in (1, 2):
        out.append(f"<span class=lbl>{cap('სანდო წყარო')}</span>")
    if r.small:
        out.append(f"<span class='lbl rare'>{cap('პატარა ვები')}</span>")
    if r.clicks:
        out.append(f"<span class=lbl>{cap('ადრე არჩეული')}</span>")
    if r.cited:
        out.append(f"<span class=lbl>{cap('ვიკიპედიის წყარო')}</span>")
    if y := ARCHIVE_YEAR.search(r.url):
        out.append(f"<span class='lbl old'>{cap('ძველი ვები')} · {y[1]}</span>")
    return "".join(out)


def _site(url: str) -> str:
    """host › path parts, readable: Wayback prefix removed, %-escapes decoded. The host opens the site's page."""
    url = WAYBACK.sub("", url)
    parts = [unquote(p) for p in urlparse(url).path.split("/") if p][:3]
    return (f"<a class=host href='{_site_link(_host(url))}'>{escape(_host(url))}</a>"
            + "".join(f" › {escape(p[:40].replace('_', ' '))}" for p in parts))


def _site_link(host: str) -> str:
    return f"/site?h={quote(host)}"


def _understood(q: str, qs: dict[str, str], debug: dict) -> str:
    """One line: how the query was read (spelling, Latin → Georgian, dictionary forms, question, extra words)."""
    parts = []
    if debug["read_as"] != normalize(q) and not debug["spelling"]:  # Latin → Georgian; spelling has its own line
        parts.append(f"<b>{escape(debug['read_as'])}</b> <span class=m>(დაწერილი: {escape(q)})</span>")
    if "lemmas" in qs and qs["lemmas"] != " ".join(debug["content"]):
        parts.append(f"ლექსიკონის ფორმა: <b>{escape(qs['lemmas'])}</b>")
    dropped = [w for w in debug["read_as"].split() if w not in debug["content"]]
    if dropped:
        parts.append(f"გამოტოვებული: {escape(' '.join(dropped))}")
    if debug["type"]:
        parts.append(f"კითხვა: {QUESTION[debug['type']]}")
    if debug["feedback"]:
        parts.append(f"დამატებითი სიტყვები: <mark class=fb>{escape(', '.join(debug['feedback']))}</mark>")
    return f"<p class=und><span class=cap>{cap('გავიგეთ')}</span> {' · '.join(parts)}</p>" if parts else ""


def _sources(results: list) -> str:
    """Result count per engine and the number of different sites."""
    count: dict[str, int] = {}
    for r in results:
        for e in r.engines:
            count[e] = count.get(e, 0) + 1
    engines = " · ".join(f"{escape(ENGINE_NAMES.get(e, e))} {n}" for e, n in sorted(count.items(), key=lambda x: -x[1]))
    return f"<p class=src><span class=cap>{cap('წყაროები')}</span> {engines} · {len({_host(r.url) for r in results})} საიტი</p>"


@cache
def _eggs() -> dict[str, str]:
    """Query -> easter egg line (config/easter_eggs.yaml)."""
    return {normalize(w): e["say"] for e in yaml.safe_load(EGGS_FILE.read_text(encoding="utf-8")) for w in e["when"]}


def _egg(q: str, qs: dict[str, str]) -> str:
    for text in (q, qs.get("corrected", q)):
        key = normalize(re.sub(r"[^\w\s%.]", " ", text)).strip(". ")  # keep the dot in mail.ru
        lemmas = [a.lemma for a in analyze(key)] if key and " " not in key else []
        if say := next((_eggs()[k] for k in [key] + lemmas if k in _eggs()), None):
            return f"<p class=egg>{escape(cap(say))}</p>"
    return ""


def _definition(d: dict) -> str:
    """Dictionary box: the word, part of speech, numbered senses, synonyms as new searches."""
    syn = ", ".join(f"<a href='{_link(w, 'all', set())}'>{escape(w)}</a>" for w in d["synonyms"])
    return (f"<div class='ans dict'><div class=cap>{cap('განმარტებითი ლექსიკონი')}</div>"
            f"<a class=t href='{escape(d['url'])}'>{escape(d['word'])}</a> <span class=m>{escape(d['pos'])}</span>"
            "<ol>" + "".join(f"<li>{escape(s)}</li>" for s in d["senses"]) + "</ol>"
            + (f"<p class=syn><span class=cap>{cap('სინონიმები')}</span> {syn}</p>" if syn else "")
            + f"<div class=cap>{cap('ვიქსიკონი')}</div></div>")


def _related(related: list[tuple[str, str]]) -> str:
    if not related:
        return ""
    return (f"<div class=blk><h3>{cap('მსგავსი ძიებები')}</h3><div class=rel>" + "".join(
        f"<a href='{_link(rq, 'all', set())}'>{escape(rq)}<span class=m>{cap(RELATED_FROM[src])}</span></a>"
        for rq, src in related) + "</div></div>")


def _go(r, key: str) -> str:
    """Result link through /go: the click is logged (clicks.py), then the browser goes to the page."""
    return f"/go?k={quote(key)}&r={r.rank}&u={quote(r.url)}"


def _result(r, marks: dict[str, str], key: str) -> str:
    title, t_found = _highlight(r.title, marks)
    date = DATE_FIRST.match(r.snippet)
    snippet, s_found = _highlight(r.snippet[date.end():] if date else r.snippet, marks)
    if date:
        snippet = f"<span class=date>{date[1]} — </span>{snippet}"
    matched = ", ".join(dict.fromkeys(escape(w) for w in t_found + s_found)) or "—"
    tier = f"სანდოობა {r.tier} · " if r.tier else "პატარა ვები · " if r.small else ""
    kinds = " ".join(dict.fromkeys([KIND_NAMES.get(r.kind, r.kind)] + [FILTERS.get(t, t) for t in sorted(r.tags)]))
    copies = ""
    if r.copies:
        copies = f"ასევე {len(r.copies)} საიტზე: " + ", ".join(
            f"<a href='{escape(c.url)}'>{escape(_host(c.url))}</a>" for c in r.copies[:6])
    tags = f"<div class=tags>{_labels(r)}{copies}</div>" if _labels(r) or copies else ""
    return (f"<div class=r><div class=site>{_site(r.url)}</div><a class=t href='{escape(_go(r, key))}'>{title}</a>"
            f"<div class=snip>{snippet}</div>{tags}"
            f"<div class=why>დაემთხვა: {matched} · {tier}{escape(kinds)} · აზრი {r.meaning:.2f} · "
            f"დაფარვა {r.coverage:.0%} · ქულა {r.score * 1000:.1f}"
            f"<br>იპოვა: {_found_by(r)}</div></div>")


def _link(q: str, tab: str, filters: set[str]) -> str:
    return f"?q={quote(q)}" + (f"&tab={tab}" if tab != "all" else "") + "".join(f"&f={f}" for f in sorted(filters))


def _tab_order(content: list[str]) -> list[str]:
    """ყველა first, then the tab whose words are in the query, then the rest."""
    fams = {f for w in content for f in families(w)} | set(content)
    hit = [t for t, ws in TAB_WORDS.items() if fams & set(ws.split())]
    return ["all"] + hit + [t for t in TABS if t != "all" and t not in hit]


def _in_tab(r, tab: str) -> bool:
    return tab == "all" or r.kind in TAB_KINDS[tab]


def main_list(results: list, size: int = 30) -> list:
    """All tab: the main results, max PER_SITE per site (SITE_LIMIT for some), MAX_SOCIAL social posts."""
    main, per_site = [], {}
    for r in results:
        host = _host(r.url)
        site, limit = ("social", MAX_SOCIAL) if r.kind == "social" else (host, SITE_LIMIT.get(host, PER_SITE))
        if r.kind in MAIN_KINDS and per_site.get(site, 0) < limit:
            per_site[site] = per_site.get(site, 0) + 1
            main.append(r)
    return main[:size]


def _all_tab(q: str, results: list, marks: dict[str, str], key: str) -> str:
    main = main_list(results)
    shown = {id(r) for r in main}
    body = ""
    for i, r in enumerate(main, 1):
        body += _result(r, marks, key)
        name = BLOCKS.get(i)
        if name is None:
            continue
        pick = (lambda x: x.kind in TAB_KINDS[name]) if name in TABS else (lambda x: name in x.tags)
        block = [x for x in results if pick(x) and id(x) not in shown][:3]
        shown |= {id(x) for x in block}
        more = _link(q, name, set()) if name in TABS else _link(q, "all", {name})
        if block:
            body += (f"<div class=blk><h3>{cap(TABS.get(name) or FILTERS[name])}<a href='{more}'>{cap('ყველა')} →</a></h3>"
                     + "".join(_result(x, marks, key) for x in block) + "</div>")
    return body


def render(q: str, tab: str, chosen: set[str], qs: dict[str, str], results: list, debug: dict) -> str:
    """The results part of the page."""
    marks = _marks(debug)
    passes = lambda r, fs: _in_tab(r, tab) and fs <= r.tags
    tab_count = {t: sum(_in_tab(r, t) and chosen <= r.tags for r in results) for t in TABS}
    body = "<nav class=tabs>" + "".join(
        f"<a class={'on' if t == tab else 'off'} href='{_link(q, t, chosen)}'>{cap(TABS[t])}"
        + (f" <span class=m>{tab_count[t]}</span>" if t != "all" else "") + "</a>"
        for t in _tab_order(debug["content"])) + "</nav>"
    body += "<nav class=chips>" + "".join(
        f"<a class={'on' if f in chosen else 'off'} href='{_link(q, tab, set() if f in chosen else {f})}'>{cap(name)}"
        f" <span class=m>{sum(passes(r, {f}) for r in results)}</span></a>"
        for f, name in FILTERS.items()) + "</nav>"
    if debug["spelling"]:
        fixed = " ".join(debug["spelling"].get(normalize(w), w) for w in q.split())
        body += f"<p class=fix>ნაჩვენებია შედეგები: <b>{escape(fixed)}</b> <span class=m>(დაწერილი: {escape(q)})</span></p>"
    if debug.get("did_you_mean"):
        maybe = " ".join(debug["did_you_mean"].get(normalize(w), w) for w in q.split())
        body += f"<p class=fix>ხომ არ გულისხმობდით: <a href='{_link(maybe, 'all', set())}'><b>{escape(maybe)}</b></a></p>"
    body += _egg(q, qs) + _understood(q, qs, debug) + _sources(results)
    d = debug.get("definition")
    if d and tab == "all" and not chosen and (d["asked"] or not debug.get("answer")):
        body += _definition(d)
    elif (a := debug.get("answer")) and tab == "all" and not chosen:
        body += (f"<div class=ans><a class=t href='{escape(a['url'])}'>{escape(a['title'])}</a>"
                 f"<p>{escape(a['text'])}</p><div class=cap>{cap('ვიკიპედია')}</div></div>")
    body += (f"<details class=dbg open><summary>{cap(f"როგორ ვიპოვეთ · {len(results)} შედეგი · {debug['seconds']['total']} წმ")}</summary>"
             f"<div>საძიებო სიტყვები: {escape(' · '.join(debug['content']))}</div>"
             f"<div>კითხვა: {QUESTION.get(debug['type'], '—')}</div>"
             f"<div>მართლწერა: {escape(', '.join(f'{a} → {b}' for a, b in debug['spelling'].items()) or '—')}</div>"
             f"<div>პასუხის სიტყვები: <mark class=fb>{escape(' · '.join(debug['feedback']) or '—')}</mark></div>"
             f"<div>დრო: {escape(' · '.join(f'{STEP_NAMES.get(k, k)} {v} წმ' for k, v in debug['seconds'].items()))}</div>"
             f"<div class=tbl><table>"
             + "".join(f"<tr><td>{escape(_query_name(n))}</td><td>{debug['counts'].get(n, 0)}</td><td>{escape(v)}</td></tr>"
                       for n, v in qs.items())
             + "</table></div></details>")
    if tab == "all" and not chosen:
        body += _all_tab(q, results, marks, debug["key"])
    else:
        body += "".join(_result(r, marks, debug["key"]) for r in results if passes(r, chosen)) or "<p>—</p>"
    return body + _related(debug.get("related", []))


# ---- surfing: pages without a query -----------------------------------------

def _chips(hosts: list[str]) -> str:
    return "<div class=rel>" + "".join(f"<a href='{_site_link(h)}'>{escape(h)}</a>" for h in hosts) + "</div>"


def _block(name: str, inner: str) -> str:
    return f"<div class=blk><h3>{cap(name)}</h3>{inner}</div>" if inner else ""


def _post(url: str, title: str, date: str, site: bool = True) -> str:
    host = f" <span class=m>· <a href='{_site_link(_host(url))}'>{escape(_host(url))}</a></span>" if site else ""
    return f"<li><span class=m>{escape(date[:10])}</span><a href='{escape(url)}'>{escape(title[:110])}</a>{host}</li>"


def _paper(url: str, title: str, authors: str, year: str, journal: str) -> str:
    meta = " · ".join(x for x in (authors.split(";")[0], year, journal.split(";")[0]) if x)
    return f"<li><a href='{escape(url)}'>{escape(title[:140])}</a><br><span class=m>{escape(meta[:140])}</span></li>"


def site_page(host: str) -> str:
    """What dzirkva knows about one site: kind, labels, newest pages, links in and out, similar sites, old copies."""
    s = discover.site(host)
    d = s["domain"]  # (source, state, pages, georgian, kind, inbound) or None
    labels = ""
    if s["trusted"]:
        labels += f"<span class=lbl>{cap('სანდო წყარო')} · {discover.CATEGORY_NAMES.get(s['trusted'][0], '')}</span>"
    if s["small"]:
        labels += f"<span class='lbl rare'>{cap('პატარა ვები')}</span>"
    if s["repos"]:
        labels += f"<span class=lbl>{cap('სამეცნიერო')}</span>"
    facts = [f"{s['page_count']:,} გვერდი ჩვენს ინდექსში"]
    if d:
        facts.append(f"{d[3]:.0%} ქართული")
    facts.append(f"{s['in_count']} საიტი მიუთითებს")
    if s["cited_count"]:
        facts.append(f"ვიკიპედია ციტირებს {s['cited_count']}-ჯერ")
    if s["old_count"]:
        facts.append(f"ძველ ვებში {s['old_count']} გვერდი")
    for base, name, records in s["repos"]:
        facts.append(f"{escape(name or base)}: {records:,} ნაშრომი")
    body = (f"<div class='ans hero'><div class=cap>{cap('საიტი')}</div><span class=t>{escape(s['name'] or s['host'])}</span>"
            f"<div class=facts>{' · '.join(facts)}</div><div class=tags>{labels}</div>"
            f"<a class=go href='https://{escape(s['host'])}/'>{cap('საიტზე გადასვლა')} →</a></div>")
    body += _block("ახალი გვერდები", "<ul class=list>" + "".join(_post(u, t or u, dt, False) for u, t, dt in s["pages"])
                   + "</ul>" if s["pages"] else "")
    body += _block("მსგავსი საიტები", _chips(s["similar"]) if s["similar"] else "")
    body += _block("ამ საიტზე მიუთითებენ", _chips(s["links_in"]) if s["links_in"] else "")
    body += _block("ეს საიტი მიუთითებს", _chips(s["links_out"]) if s["links_out"] else "")
    wiki_link = lambda t: "https://ka.wikipedia.org/wiki/" + quote(t.replace(" ", "_"))
    body += _block("ვიკიპედიის სტატიები, რომლებიც მას ციტირებენ", "<ul class=list>" + "".join(
        f"<li><a href='{wiki_link(t)}'>{escape(t)}</a> <span class=m>{n}</span></li>" for t, n in s["cited"])
        + "</ul>" if s["cited"] else "")
    body += _block("ძველი ვები", "<ul class=list>" + "".join(
        f"<li><span class=m>{snap[:4]}</span><a href='{discover.archive.WAYBACK.format(snap, u)}'>{escape(t or u)}</a></li>"
        for u, snap, t in s["old"]) + "</ul>" if s["old"] else "")
    return body


def _finds(rng: random.Random) -> list[str]:
    """Three finds for the home page: a new post of the small web, an old-web page, a small site."""
    out = []
    if posts := discover.newest_posts(12):
        out.append(_post(*rng.choice(posts)))
    if old := discover.old_find(rng):
        out.append(f"<li><span class=m>{cap('ძველი ვები')} · {old[2]}</span><a href='{escape(old[0])}'>{escape(old[1])}</a></li>")
    if h := discover.random_site(rng):
        out.append(f"<li><span class=m>{cap('პატარა ვები')}</span><a href='{_site_link(h)}'>{escape(h)}</a></li>")
    return out


def home_page() -> str:
    finds = _finds(random.Random(time.strftime("%Y-%m-%d")))  # the same finds all day
    return _block("დღის მიგნებები", "<ul class=list>" + "".join(finds) + "</ul>"
                  f"<a class=go href=/discover>{cap('აღმოაჩინე მეტი')} →</a>" if finds else "")


def discover_page() -> str:
    rng = random.Random()
    body = (f"<div class='ans hero'><div class=cap>{cap('აღმოჩენა')}</div><span class=t>ქართული ვები ძიების გარეშე</span>"
            f"<div class=facts>ახალი პოსტები პატარა ვებში, ძველი ვები, ახალი ნაშრომები, საიტები თემების მიხედვით</div>"
            f"<a class=go href=/random>{cap('შემთხვევითი საიტი')} →</a></div>")
    posts = discover.newest_posts()
    body += _block("ახალი პოსტები პატარა ვებში", "<ul class=list>" + "".join(_post(*p) for p in posts) + "</ul>"
                   if posts else "")
    old = [f for f in (discover.old_find(rng) for _ in range(5)) if f]
    body += _block("ძველი ვებიდან", "<ul class=list>" + "".join(
        f"<li><span class=m>{y}</span><a href='{escape(u)}'>{escape(t)}</a></li>" for u, t, y in old) + "</ul>"
        if old else "")
    new = discover.new_papers()
    body += _block("ახალი ნაშრომები", "<ul class=list>" + "".join(_paper(*p) for p in new) + "</ul>" if new else "")
    for name, hosts in discover.shelves():
        body += _block(name, _chips(hosts) if hosts else "")
    return body


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        path = urlparse(self.path)
        params = parse_qs(path.query)
        if path.path == "/go":
            url = params.get("u", [""])[0]
            if not url.startswith(("http://", "https://")):
                self.send_error(400)
                return
            clicks.log(params.get("k", [""])[0], canonical_url(url), int(params.get("r", ["0"])[0] or 0))
            _cache.clear()  # the next search of the question ranks with this click
            self.send_response(302)
            self.send_header("Location", url)
            self.end_headers()
            return
        if path.path == "/random":  # surfing: a random small site, straight to it
            self.send_response(302)
            self.send_header("Location", f"https://{discover.random_site()}/")
            self.end_headers()
            return
        if path.path in ("/site", "/discover"):
            h = params.get("h", [""])[0].strip()
            body = site_page(h) if path.path == "/site" and h else discover_page()
            self._send(_page("", body))
            return
        q = params.get("q", [""])[0].strip()
        tab = params.get("tab", ["all"])[0]
        tab = tab if tab in TABS else "all"
        chosen = {f for f in params.get("f", [])[:1] if f in FILTERS}  # one filter at a time
        body = ""
        if q:
            if q not in _cache:
                t = time.time()
                qs, results, debug = search(q)
                debug["seconds"]["total"] = round(time.time() - t, 1)
                _cache[q] = (qs, results, debug)
            body = render(q, tab, chosen, *_cache[q])
        self._send(_page(q, body or home_page()))

    def _send(self, html: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())


if __name__ == "__main__":
    similarity("გამარჯობა", ["გამარჯობა"])  # load the meaning model once, before the first search
    passages._index()  # ~35 s: load the 743k paragraph vectors before the first search, not during it
    print("http://127.0.0.1:8000", flush=True)
    # One thread: the meaning model on the Mac GPU hangs when called from other threads.
    HTTPServer(("127.0.0.1", 8000), Handler).serve_forever()
