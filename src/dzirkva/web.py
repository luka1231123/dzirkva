"""Search page for testing. Run: uv run python -m dzirkva.web  → http://127.0.0.1:8000
Surfing pages without a query: /site?h=host (what we know about a site), /discover, /random (a small site).
/about explains dzirkva; /stats shows the telemetry (telemetry.py), for this computer or with ?key=STATS_KEY.
Visitors: each request has its own thread; one worker (the main thread) runs every new search, one at a time,
because the meaning model on the Mac GPU hangs in other threads. At most MAX_SEARCHES new searches run or wait at
a time (.env, default 3); the next visitor gets a busy page that reloads itself. Other pages never wait for it.
Every link to another site goes through /go with a signature (no open redirect), so each click is counted.

Page structure (plan.md, Session 7): a tab changes the layout, a filter narrows the sources.
Tabs: ყველა, ვიდეო, სიახლეები; the tab that fits the query words comes right after ყველა.
Filters (chips, one at a time): ცოდნა, ტექსტები, ხალხი, სამეცნიერო, ძველი ვები.
The All tab without filters shows ordinary results, max 2 per site and max 3 social posts
(Facebook …), with video, people and old-web blocks between them. With a filter it shows the plain filtered list.
"""

import hashlib
import hmac
import os
import queue
import random
import re
import secrets
import sqlite3
import threading
import time
from functools import cache
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse, urlsplit, urlunsplit

import yaml

from dzirkva.georgian import normalize
from dzirkva import archive, clicks, crawl, dictionary, discover, engines, iverieli, papers, passages, telemetry, wiki
from dzirkva.meaning import similarity
from dzirkva.morph import analyze, families
from dzirkva.georgian import KEEP_RATIO
from dzirkva.search import COPY_SIMILARITY, MIN_GEORGIAN, ROUND1_GOOD, canonical_url, intents, search
from dzirkva.sources import sources

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
                "wikipedia": "ვიკიპედია", "passages": "ვიკიპედია (აზრით)", "archive": "ძველი ვები", "crawl": "ჩვენი ინდექსი", "iverieli": "ივერიელი", "wikisource": "ვიკიწყარო", "papers": "სამეცნიერო ჟურნალები",
                "cited": "ვიკიპედიის წყაროები", "named": "დასახელებული საიტი"}
KIND_NAMES = {"knowledge": "ცოდნა", "news": "სიახლე", "web": "ვები", "forum": "ფორუმი", "social": "სოციალური ქსელი",
              "video": "ვიდეო", "film": "ფილმი"}
CATEGORY_NAMES = {"reference": "ცნობარი", "law": "სამართალი", "history": "ისტორია", "religion": "რელიგია",
                  "education": "განათლება", "government": "სახელმწიფო", "culture": "კულტურა"}
# Signs that a person wrote or chose a page: key → (label, CSS class, meaning). Results show the meaning on hover;
# the home page lists them in this order.
SIGNS = {
    "small": ("პატარა ვები", "rare", "პირადი საიტი ან ბლოგი: ავტორი პირველ პირში წერს („მე“, „ჩემი“, „მახსოვს“), "
                                     "არა კომპანიის ან სააგენტოს ენით, და მაღაზია არ აქვს"),
    "academic": ("სამეცნიერო", "", "სამეცნიერო ჟურნალი, უნივერსიტეტის რეპოზიტორია ან ეროვნული ბიბლიოთეკა"),
    "cited": ("ვიკიპედიის წყარო", "", "ამ თემის ვიკიპედიის სტატია ამ გვერდს ციტირებს: რედაქტორებმა ის წყაროდ აირჩიეს"),
    "old": ("ძველი ვები", "old", "დახურული ქართული საიტის ასლი ინტერნეტ-არქივიდან; ჭდეზე ასლის წელია "
                                 "(უმეტესობა ჩატბოტებამდეა)"),
    "trusted": ("სანდო წყარო", "", "ხელით შერჩეული საიტი: ცნობარი, მეცნიერება, კანონი, სახელმწიფო ან ჟურნალისტიკა"),
    "clicks": ("ადრე არჩეული", "", "ამავე კითხვაზე სხვებმა ეს გვერდი აირჩიეს"),
}
QUESTION = {"why": "რატომ", "how": "როგორ", "when": "როდის", "amount": "რამდენი"}
RELATED_FROM = {"wiki": "ვიკიპედიის სათაური", "feedback": "პასუხის სიტყვა", "meaning": "აზრით ახლო"}
STEP_NAMES = {"round1": "პირველი რაუნდი", "round2+rank": "მეორე რაუნდი და რიგი", "total": "სულ"}
ARCHIVE_YEAR = re.compile(r"web\.archive\.org/web/(\d{4})")
WAYBACK = re.compile(r"^https?://web\.archive\.org/web/[^/]+/")
DATE_FIRST = re.compile(r"^(\d{4}-\d{2}-\d{2})\S* · ")  # crawl snippets start with the page date
EGGS_FILE = Path(__file__).resolve().parents[2] / "config" / "easter_eggs.yaml"
LEXICON = Path(__file__).resolve().parents[2] / "data" / "lexicon.tsv"  # word forms → dictionary form (morph.py)
GO_KEY = hashlib.sha256(b"go" + (os.environ.get("SEARXNG_SECRET") or secrets.token_hex(16)).encode()).digest()
FORWARDED = ("X-Forwarded-For", "Forwarded", "Cf-Connecting-Ip", "X-Real-Ip")  # the visit came through a tunnel
MAX_SEARCHES = int(os.environ.get("MAX_SEARCHES", "3"))  # new searches running or waiting at a time
PORT = int(os.environ.get("PORT", "8000"))
BUSY_SECONDS = 10  # the busy page reloads itself after this
_cache: dict[str, tuple[dict, list, dict]] = {}
DEEP_KEY = "\x00deep"  # _cache key of a deep search: the question + this
_last_view: dict[str, float] = {}  # session → time of its last results page (time to click)
_jobs: queue.Queue = queue.Queue()  # searches for the worker: (job, done event)
_searches = threading.BoundedSemaphore(MAX_SEARCHES)
# Page actions for telemetry (/t): a citation or the "how we found it" panel opened or closed, a citation copied.
# A click on the summary, not the toggle event: details that start open fire toggle on load.
BEACON = """<script>
const q=document.querySelector("input[name=q]")?.value||"",
t=(e,o,r)=>navigator.sendBeacon(`/t?e=${e}&o=${o}&r=${r||""}&q=${encodeURIComponent(q)}`);
document.addEventListener("click",e=>{const s=e.target.closest("summary");if(!s||e.target.closest("a"))return;
const d=s.parentElement;if(d.dataset.t)t(d.dataset.t,d.open?0:1,d.dataset.r)});
document.addEventListener("copy",()=>{const c=document.getSelection()?.anchorNode?.parentElement?.closest(".cite");
if(c)t("copy",1,c.dataset.r)});
</script>"""

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
button.deep{background:transparent;color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent);padding:8px 14px}
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
.pm{font-size:13px;color:var(--ok);margin:2px 0}
.cite{font-size:12.5px;color:var(--muted);margin:6px 0} .cite summary{cursor:pointer}
.lead{font-size:16px;line-height:1.75;color:var(--text);margin:8px 0 14px}
.steps{margin:8px 0;padding-left:22px} .steps li{margin:8px 0;line-height:1.6} .steps li::marker{color:var(--accent)}
.foot{font-size:12px;color:var(--muted);margin:26px 0 40px;line-height:1.8}
.nums{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px;margin-top:10px}
.nums div{background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:8px 10px}
.nums b{display:block;font-size:20px;color:var(--ink)} .nums span{font-size:11.5px;color:var(--muted)}
.bars{width:100%;height:90px;display:block} .bars rect{fill:var(--accent)}
.blk p{margin:8px 0} .signs li{margin:10px 0} .lbl[title]{cursor:help}
.vs{width:100%;margin:4px 0;font-size:14px} .vs th,.vs td{padding:7px 12px 7px 0;border-bottom:1px solid var(--line)}
.vs th+th,.vs td+td{text-align:right;white-space:nowrap} .vs b{color:var(--ink)}
th{font-size:11px;color:var(--muted);text-align:left;font-weight:600;padding:2px 10px 2px 0}
.sess{font-size:12.5px;margin:10px 0;padding-top:8px;border-top:1px solid var(--line)} .sess div{margin:2px 0}
.cite code{display:block;margin-top:6px;padding:8px 10px;background:var(--bg);border:1px solid var(--line);
 border-radius:8px;font:12.5px/1.6 "Noto Serif Georgian",Georgia,serif;color:var(--text);user-select:all}
@media (max-width:520px){.r .t{font-size:17px} .ans,.blk,.dbg{padding:12px 14px;border-radius:10px}}
"""



def _page(q: str, body: str, refresh: int = 0) -> str:
    """ამოძირკვა (deep search): the second button sends deep=1 (search.DEEP × more queries, pages, forms, feedback; slower)."""
    return (f"<!doctype html><html lang=ka><meta charset=utf-8>"
            + (f"<meta http-equiv=refresh content={refresh}>" if refresh else "") +
            f"<meta name=viewport content='width=device-width,initial-scale=1'><title>{escape(q) + ' · ' if q else ''}ძირკვა</title>"
            "<link rel=preconnect href=https://fonts.googleapis.com><link rel=stylesheet href="
            "'https://fonts.googleapis.com/css2?family=Noto+Serif+Georgian:wght@400..600&display=swap'>"
            f"<style>{CSS}</style><header><div><a class=logo href=/>{cap('ძირკვა')}</a>"
            f"<form action=/><input name=q value='{escape(q)}' autofocus><button>{cap('ძებნა')}</button>"
            f"<button class=deep name=deep value=1 title='სამჯერ მეტი მოთხოვნა, სიტყვის ფორმა და შედეგი, უფრო ნელა'>"
            f"{cap('ამოძირკვა')}</button></form></div></header>"
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
    return "; ".join(f"{escape(_query_name(n))}: {', '.join(v)}" for n, v in by.items())


def _query_name(name: str) -> str:
    """Query names from search.py (original, lemmas, site:law, feedback:…) in Georgian."""
    kind, _, arg = name.partition(":")
    fixed = {"original": "როგორც დაიწერა", "corrected": "გასწორებული", "lemmas": "ლექსიკონის ფორმები",
             "lemmas:corrected": "გასწორებულის ლექსიკონის ფორმები", "forms": "სიტყვის ფორმები",
             "variants": "ყველა ფორმა"}
    if kind == "site":  # deep search: site:reference:2 = the next sites of the list
        arg = arg.partition(":")[0]
        return f"საიტები: {intents()[arg]['ka'] if arg in intents() else CATEGORY_NAMES.get(arg, arg)}"
    if kind == "named":
        return f"დასახელებული საიტი: {arg}"
    if kind == "feedback":
        return f"პასუხის სიტყვა: {arg}"
    return fixed.get(name) or ENGINE_NAMES.get(name, name)


def _sign(key: str, extra: str = "") -> str:
    """A label with its meaning on hover (SIGNS)."""
    name, cls, meaning = SIGNS[key]
    return f"<span class='lbl {cls}' title='{escape(meaning)}'>{cap(name)}{escape(extra)}</span>"


def _labels(r) -> str:
    """Signs that a person wrote or chose the page: trusted list, small web, chosen before, cited by Wikipedia,
    research, old web with its year."""
    out = [_sign(k) for k, on in (("trusted", r.tier in (1, 2)), ("small", r.small), ("clicks", r.clicks),
                                  ("cited", r.cited), ("academic", "academic" in r.tags)) if on]
    if y := ARCHIVE_YEAR.search(r.url):
        out.append(_sign("old", f" · {y[1]}"))
    return "".join(out)


def _site(url: str) -> str:
    """host › path parts, readable: Wayback prefix removed, %-escapes decoded. The host opens the site's page."""
    url = WAYBACK.sub("", url)
    parts = [unquote(p) for p in urlparse(url).path.split("/") if p][:3]
    return (f"<a class=host href='{_site_link(_host(url), 'result')}'>{escape(_host(url))}</a>"
            + "".join(f" › {escape(p[:40].replace('_', ' '))}" for p in parts))


def _site_link(host: str, src: str = "") -> str:
    return f"/site?h={quote(host)}" + (f"&from={src}" if src else "")


def _sig(url: str) -> str:
    return hmac.new(GO_KEY, url.encode(), hashlib.sha256).hexdigest()[:16]


def _ascii_url(url: str) -> str:
    """A Location header must be ASCII: a Georgian host (ამინდი.com) becomes punycode, other letters %XX."""
    u = urlsplit(url)
    try:
        u = u._replace(netloc=u.netloc.encode("idna").decode())
    except UnicodeError:
        pass
    return quote(urlunsplit(u), safe=":/?#[]@!$&'()*+,;=%~")


def _out(url: str, where: str, **params) -> str:
    """A link to another site through /go: telemetry counts the click (where: main, video, answer, pdf,
    site:page …); the signature lets /go send the browser only to links this page showed."""
    extra = {k: v for k, v in params.items() if v not in ("", None, 0)}
    return "/go?" + urlencode({"u": url, "h": _sig(url), "w": where, **extra})


def _understood(q: str, qs: dict[str, str], debug: dict) -> str:
    """One line: how the query was read (spelling, Latin → Georgian, dictionary forms, question, extra words)."""
    parts = []
    if debug["read_as"] != normalize(q) and not debug["spelling"]:  # Latin → Georgian; spelling has its own line
        parts.append(f"<b>{escape(debug['read_as'])}</b> <span class=m>(დაწერილი: {escape(q)})</span>")
    if "lemmas" in qs and qs["lemmas"] != " ".join(debug["content"]):
        parts.append(f"ლექსიკონის ფორმა: <b>{escape(qs['lemmas'])}</b>")
    if "forms" in qs:  # (გავაკეთოთ OR გაკეთება) ღვინო → გავაკეთოთ / გაკეთება ღვინო
        parts.append(f"ფორმები: <b>{escape(qs['forms'].replace(' OR ', ' / ').replace('(', '').replace(')', ''))}</b>")
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


def _definition(d: dict, q: str) -> str:
    """Dictionary box: the word, part of speech, numbered senses, synonyms as new searches."""
    syn = ", ".join(f"<a href='{_link(w, 'all', set(), 'syn')}'>{escape(w)}</a>" for w in d["synonyms"])
    return (f"<div class='ans dict'><div class=cap>{cap('განმარტებითი ლექსიკონი')}</div>"
            f"<a class=t href='{escape(_out(d['url'], 'dict', q=q))}'>{escape(d['word'])}</a>"
            f" <span class=m>{escape(d['pos'])}</span>"
            "<ol>" + "".join(f"<li>{escape(s)}</li>" for s in d["senses"]) + "</ol>"
            + (f"<p class=syn><span class=cap>{cap('სინონიმები')}</span> {syn}</p>" if syn else "")
            + f"<div class=cap>{cap('ვიქსიკონი')}</div></div>")


def _related(related: list[tuple[str, str]]) -> str:
    if not related:
        return ""
    return (f"<div class=blk><h3>{cap('მსგავსი ძიებები')}</h3><div class=rel>" + "".join(
        f"<a href='{_link(rq, 'all', set(), 'related')}'>{escape(rq)}<span class=m>{cap(RELATED_FROM[src])}</span></a>"
        for rq, src in related) + "</div></div>")


def _result(r, marks: dict[str, str], key: str, q: str, where: str) -> str:
    """One result. Its link goes through /go: the click is logged (clicks.py ranks with it, telemetry counts it)."""
    title, t_found = _highlight(r.title, marks)
    date = DATE_FIRST.match(r.snippet)
    snippet, s_found = _highlight(r.snippet[date.end():] if date else r.snippet, marks)
    if date:
        snippet = f"<span class=date>{date[1]} · </span>{snippet}"
    matched = ", ".join(dict.fromkeys(escape(w) for w in t_found + s_found)) or "არაფერი"
    tier = f"სანდოობა {r.tier} · " if r.tier else "პატარა ვები · " if r.small else ""
    kinds = " ".join(dict.fromkeys([KIND_NAMES.get(r.kind, r.kind)] + [FILTERS.get(t, t) for t in sorted(r.tags)]))
    copies = ""
    if r.copies:
        copies = f"ასევე {len(r.copies)} საიტზე: " + ", ".join(
            f"<a href='{escape(_out(c.url, 'copy', q=q, r=r.rank))}'>{escape(_host(c.url))}</a>" for c in r.copies[:6])
    tags = f"<div class=tags>{_labels(r)}{copies}</div>" if _labels(r) or copies else ""
    paper = ""
    if m := papers.meta(r.url):  # a paper: authors, year, journal; the abstract; PDF, citation, the author's papers
        snippet = _highlight(m["description"][:320], marks)[0] if m["description"] else snippet
        who = papers.authors(m["creator"])
        info = " · ".join(escape(x) for x in ("; ".join(who[:3]), m["year"], papers.journal(m["source"]),
                                                papers.type_name(m["type"])) if x)
        pdf = f"<a href='{escape(_out(m['pdf'], 'pdf', q=q, r=r.rank))}'>PDF</a> · " if m["pdf"] else ""
        more = (f" · <a href='{_link(who[0].replace(',', ''), 'all', {'academic'}, 'author')}'>"
                f"{cap('ავტორის ნაშრომები')}</a>" if who else "")
        paper = f"<div class=pm>{info}</div>"
        tags += (f"<details class=cite data-t=cite data-r={r.rank}><summary>{pdf}{cap('ციტირება')}{more}</summary>"
                 f"<code>{escape(papers.citation(m))}</code></details>")
    go = _out(r.url, where, k=key, r=r.rank, q=q)
    return (f"<div class=r><div class=site>{_site(r.url)}</div><a class=t href='{escape(go)}'>{title}</a>"
            f"{paper}<div class=snip>{snippet}</div>{tags}"
            f"<div class=why>დაემთხვა: {matched} · {tier}{escape(kinds)} · აზრი {r.meaning:.2f} · "
            f"დაფარვა {r.coverage:.0%} · ქულა {r.score * 1000:.1f}"
            f"<br>იპოვა: {_found_by(r)}</div></div>")


def _link(q: str, tab: str, filters: set[str], src: str = "", deep: bool = False) -> str:
    """A search link; src says for telemetry how the visitor came to it (tab, filter, related, dym …).
    deep: tabs and filters of a deep search stay on its results."""
    return (f"/?q={quote(q)}" + (f"&tab={tab}" if tab != "all" else "") + "".join(f"&f={f}" for f in sorted(filters))
            + ("&deep=1" if deep else "") + (f"&from={src}" if src else ""))


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


def _all_tab(q: str, results: list, marks: dict[str, str], key: str, deep: bool = False) -> str:
    main = main_list(results)
    shown = {id(r) for r in main}
    body = ""
    for i, r in enumerate(main, 1):
        body += _result(r, marks, key, q, "main")
        name = BLOCKS.get(i)
        if name is None:
            continue
        pick = (lambda x: x.kind in TAB_KINDS[name]) if name in TABS else (lambda x: name in x.tags)
        block = [x for x in results if pick(x) and id(x) not in shown][:3]
        shown |= {id(x) for x in block}
        more = _link(q, name, set(), "more", deep) if name in TABS else _link(q, "all", {name}, "more", deep)
        if block:
            body += (f"<div class=blk><h3>{cap(TABS.get(name) or FILTERS[name])}<a href='{more}'>{cap('ყველა')} →</a></h3>"
                     + "".join(_result(x, marks, key, q, name) for x in block) + "</div>")
    return body


def render(q: str, tab: str, chosen: set[str], qs: dict[str, str], results: list, debug: dict) -> str:
    """The results part of the page."""
    marks = _marks(debug)
    deep = debug.get("deep", False)
    passes = lambda r, fs: _in_tab(r, tab) and fs <= r.tags
    tab_count = {t: sum(_in_tab(r, t) and chosen <= r.tags for r in results) for t in TABS}
    body = "<nav class=tabs>" + "".join(
        f"<a class={'on' if t == tab else 'off'} href='{_link(q, t, chosen, 'tab', deep)}'>{cap(TABS[t])}"
        + (f" <span class=m>{tab_count[t]}</span>" if t != "all" else "") + "</a>"
        for t in _tab_order(debug["content"])) + "</nav>"
    body += "<nav class=chips>" + "".join(
        f"<a class={'on' if f in chosen else 'off'} href='{_link(q, tab, set() if f in chosen else {f}, 'filter', deep)}'>{cap(name)}"
        f" <span class=m>{sum(passes(r, {f}) for r in results)}</span></a>"
        for f, name in FILTERS.items()) + "</nav>"
    if debug["spelling"]:
        fixed = " ".join(debug["spelling"].get(normalize(w), w) for w in q.split())
        body += f"<p class=fix>ნაჩვენებია შედეგები: <b>{escape(fixed)}</b> <span class=m>(დაწერილი: {escape(q)})</span></p>"
    if debug.get("did_you_mean"):
        maybe = " ".join(debug["did_you_mean"].get(normalize(w), w) for w in q.split())
        body += f"<p class=fix>ხომ არ გულისხმობდით: <a href='{_link(maybe, 'all', set(), 'dym')}'><b>{escape(maybe)}</b></a></p>"
    body += _egg(q, qs) + _understood(q, qs, debug) + _sources(results)
    d = debug.get("definition")
    if d and tab == "all" and not chosen and (d["asked"] or not debug.get("answer")):
        body += _definition(d, q)
    elif (a := debug.get("answer")) and tab == "all" and not chosen:
        body += (f"<div class=ans><a class=t href='{escape(_out(a['url'], 'answer', q=q))}'>{escape(a['title'])}</a>"
                 f"<p>{escape(a['text'])}</p><div class=cap>{cap('ვიკიპედია')}</div></div>")
    body += (f"<details class=dbg open data-t=debug><summary>{cap(f"როგორ ვიპოვეთ{' · ამოძირკვა' if deep else ''} · {len(results)} შედეგი · {debug['seconds']['total']} წმ")}</summary>"
             f"<div>საძიებო სიტყვები: {escape(' · '.join(debug['content']))}</div>"
             f"<div>კითხვა: {QUESTION.get(debug['type'], 'არა')}</div>"
             f"<div>მართლწერა: {escape(', '.join(f'{a} → {b}' for a, b in debug['spelling'].items()) or 'არ შეცვლილა')}</div>"
             f"<div>პასუხის სიტყვები: <mark class=fb>{escape(' · '.join(debug['feedback']) or 'არ დასჭირდა')}</mark></div>"
             f"<div>დრო: {escape(' · '.join(f'{STEP_NAMES.get(k, k)} {v} წმ' for k, v in debug['seconds'].items()))}</div>"
             f"<div class=tbl><table>"
             + "".join(f"<tr><td>{escape(_query_name(n))}</td><td>{debug['counts'].get(n, 0)}</td><td>{escape(v)}</td></tr>"
                       for n, v in qs.items())
             + "</table></div></details>")
    if tab == "all" and not chosen:
        body += _all_tab(q, results, marks, debug["key"], deep)
    else:
        where = f"filter:{next(iter(chosen))}" if chosen else f"tab:{tab}"
        body += "".join(_result(r, marks, debug["key"], q, where) for r in results if passes(r, chosen)) or "<p class=m>ამ ფილტრით შედეგი არ არის.</p>"
    return body + _related(debug.get("related", [])) + BEACON


# ---- surfing: pages without a query -----------------------------------------

def _chips(hosts: list[str], src: str) -> str:
    return "<div class=rel>" + "".join(f"<a href='{_site_link(h, src)}'>{escape(h)}</a>" for h in hosts) + "</div>"


def _block(name: str, inner: str) -> str:
    return f"<div class=blk><h3>{cap(name)}</h3>{inner}</div>" if inner else ""


def _post(url: str, title: str, date: str, where: str, site: bool = True) -> str:
    host = f" <span class=m>· <a href='{_site_link(_host(url), 'post')}'>{escape(_host(url))}</a></span>" if site else ""
    return (f"<li><span class=m>{escape(date[:10])}</span><a href='{escape(_out(url, where))}'>{escape(title[:110])}</a>"
            f"{host}</li>")


def _paper(url: str, title: str, authors: str, year: str, journal: str) -> str:
    meta = " · ".join(x for x in (authors.split(";")[0], year, journal.split(";")[0]) if x)
    return (f"<li><a href='{escape(_out(url, 'discover:paper'))}'>{escape(title[:140])}</a><br>"
            f"<span class=m>{escape(meta[:140])}</span></li>")


def site_page(host: str) -> str:
    """What dzirkva knows about one site: kind, labels, newest pages, links in and out, similar sites, old copies."""
    s = discover.site(host)
    d = s["domain"]  # (source, state, pages, georgian, kind, inbound) or None
    labels = ""
    if s["trusted"]:
        labels += _sign("trusted", f" · {discover.CATEGORY_NAMES.get(s['trusted'][0], '')}")
    if s["small"]:
        labels += _sign("small")
    if s["repos"]:
        labels += _sign("academic")
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
    go = _out(f"https://{s['host']}/", "site:go", s=s["host"])
    body = (f"<div class='ans hero'><div class=cap>{cap('საიტი')}</div><span class=t>{escape(s['name'] or s['host'])}</span>"
            f"<div class=facts>{' · '.join(facts)}</div><div class=tags>{labels}</div>"
            f"<a class=go href='{escape(go)}'>{cap('საიტზე გადასვლა')} →</a></div>")
    body += _block("ახალი გვერდები", "<ul class=list>" + "".join(
        _post(u, t or u, dt, "site:page", False) for u, t, dt in s["pages"]) + "</ul>" if s["pages"] else "")
    body += _block("მსგავსი საიტები", _chips(s["similar"], "similar") if s["similar"] else "")
    body += _block("ამ საიტზე მიუთითებენ", _chips(s["links_in"], "links_in") if s["links_in"] else "")
    body += _block("ეს საიტი მიუთითებს", _chips(s["links_out"], "links_out") if s["links_out"] else "")
    wiki_link = lambda t: _out("https://ka.wikipedia.org/wiki/" + quote(t.replace(" ", "_")), "site:wiki", s=s["host"])
    body += _block("ვიკიპედიის სტატიები, რომლებიც მას ციტირებენ", "<ul class=list>" + "".join(
        f"<li><a href='{escape(wiki_link(t))}'>{escape(t)}</a> <span class=m>{n}</span></li>" for t, n in s["cited"])
        + "</ul>" if s["cited"] else "")
    old_link = lambda u, snap: _out(discover.archive.WAYBACK.format(snap, u), "site:old", s=s["host"])
    body += _block("ძველი ვები", "<ul class=list>" + "".join(
        f"<li><span class=m>{snap[:4]}</span><a href='{escape(old_link(u, snap))}'>{escape(t or u)}</a></li>"
        for u, snap, t in s["old"]) + "</ul>" if s["old"] else "")
    return body


def _finds(rng: random.Random) -> list[str]:
    """Three finds for the home page: a new post of the small web, an old-web page, a small site."""
    out = []
    if posts := discover.newest_posts(12):
        out.append(_post(*rng.choice(posts), "home:post"))
    if old := discover.old_find(rng):
        out.append(f"<li><span class=m>{cap('ძველი ვები')} · {old[2]}</span>"
                   f"<a href='{escape(_out(old[0], 'home:old'))}'>{escape(old[1])}</a></li>")
    if h := discover.random_site(rng):
        out.append(f"<li><span class=m>{cap('პატარა ვები')}</span><a href='{_site_link(h, 'find')}'>{escape(h)}</a></li>")
    return out


EXAMPLES = [("ვეფხისტყაოსანი დისერტაცია", "კვლევა"), ("kartuli anbani", "ლათინური ასოებით"),
            ("სიყვარული რას ნიშნავს", "ლექსიკონი"), ("თამარ მეფე", "ცოდნა"), ("ამინდი ბატუმში", "მართლწერა")]
STEPS = [
    "ძირკვა ჯერ შეკითხვას კითხულობს: ასწორებს მართლწერას, ლათინური ასოებით ნაწერს ქართულად კითხულობს, პოულობს "
    "სიტყვების ლექსიკონის ფორმებს და ხვდება, რა გჭირდებათ, მაგალითად ამინდი, კანონი ან კვლევა.",
    "მერე ერთდროულად ეკითხება რამდენიმე საძიებო სისტემას და ჩვენს ინდექსებს: ვიკიპედიას, ივერიელს, სამეცნიერო "
    "ჟურნალებს, ჩვენ მიერ შეგროვებულ გვერდებს და ძველ ვებს.",
    "შედეგებს აზრის მიხედვით ალაგებს: ადგილობრივი ენის მოდელი ადარებს შეკითხვასა და გვერდის ტექსტს. სანდო წყაროები "
    "და პატარა საიტები წინ იწევს, ერთ საიტს კი სიაში მხოლოდ ორი ადგილი აქვს.",
    "ყოველ შედეგთან ჩანს, რომელმა წყარომ იპოვა, რა სიტყვები დაემთხვა და რატომ დგას ამ ადგილზე.",
    "ძირკვა მხოლოდ ნაპოვნ გვერდებს აჩვენებს. რეკლამა არ არის და პასუხს ხელოვნური ინტელექტი არ წერს.",
]
PRIVACY = "ვინახავთ ძიების სიტყვებს და არჩეულ ბმულებს, IP მისამართის გარეშე."
USEFUL = [
    "ეძებთ ლექსს, მოთხრობას, ისტორიულ თემას ან სამეცნიერო ნაშრომს: ძირკვა ვიკიპედიასთან ერთად ეკითხება "
    "ეროვნული ბიბლიოთეკის „ივერიელს“, ქართულ ჟურნალებს და უნივერსიტეტების რეპოზიტორიებს.",
    "გინდათ ნახოთ, რას წერენ ადამიანები: ბლოგები, ფორუმები და პირადი საიტები სიაში წინ დგას.",
    "ქართული შრიფტი არ გაქვთ: kartuli ena და ქართული ენა ერთი და იგივე ძიებაა.",
    "სიტყვის ფორმა არ გახსოვთ: ქუთაისში, ქუთაისის და ქუთაისი ერთ სიტყვად ითვლება.",
    "გაინტერესებთ, რატომ დგას შედეგი ამ ადგილზე: ყოველ შედეგს ქვეშ უწერია, საიდან მოვიდა და რა ქულა მიიღო.",
]
HOW_TO = [
    "დაწერეთ ჩვეულებრივად, ქართულად ან ლათინური ასოებით. კითხვაც შეიძლება: „რატომ ცვივა ფოთოლი“.",
    "„სიყვარული რას ნიშნავს“ ლექსიკონის განმარტებას აჩვენებს.",
    "დაამატეთ „დისერტაცია“, „სტატია“ ან „მონოგრაფია“ და ნაშრომები პირველი იქნება. ნაშრომს ახლავს PDF და "
    "მზა ციტირება.",
    "სიის თავზე ფილტრებია: ცოდნა, ტექსტები, ხალხი, პატარა ვები, სამეცნიერო, ძველი ვები.",
    "საიტის სახელზე დააწკაპეთ და ნახავთ, რა ვიცით ამ საიტზე: ახალ გვერდებს, მსგავს საიტებს, ძველ ასლებს.",
    "„აღმოჩენა“ და „შემთხვევითი საიტი“ ძიების გარეშე გაცნობთ ქართულ ვებს.",
]
GROW = [
    "სრული ქრაული. ახლა ქრაულერი ერთი საიტიდან რამდენიმე ათას გვერდს აგროვებს. თუ ყველა ქართულ საიტს ბოლომდე "
    "წაიკითხავს, ძიებას გარე საძიებო სისტემები ნაკლებად დასჭირდება.",
    "ახალი გვერდები ყოველდღე: საიტების RSS არხები და საიტის რუკები (sitemap) ყოველ დილით, რომ ახალი სტატია "
    "იმავე დღეს იძებნებოდეს.",
    "ბიბლიოთეკისა და ჟურნალების სრული ტექსტი: ივერიელის და ქართული ჟურნალების PDF ფაილები, სკანირებული "
    "წიგნებისთვის კი ტექსტის ამოცნობა (OCR).",
    "აზრით ძიება მთელ ინდექსზე: ყოველი გვერდის ვექტორი წინასწარ დაითვლება, ამიტომ ძიებას ვიდეობარათი აღარ "
    "დასჭირდება და იაფ სერვერზეც იმუშავებს.",
    "საიტის დამატება: ბლოგის ან საიტის ავტორი თავად შეძლებს მის შეთავაზებას.",
    "ღია მონაცემები: საიტების სია, ნაშრომების კატალოგი და საიტებს შორის ბმულების რუკა სხვა პროექტებისთვისაც.",
]


@cache
def _sizes() -> dict[str, int]:
    """Index sizes for the start page, counted once per start."""
    count = lambda db, sql: db.execute(sql).fetchone()[0] if db else 0
    return {"wiki": count(wiki._db(), "SELECT count(*) FROM wiki"),
            "crawl": count(crawl._db(), "SELECT count(*) FROM pages_content"),
            "sites": count(crawl._db(), "SELECT count(*) FROM domains WHERE state = 'full'"),
            "iverieli": count(iverieli._db(), "SELECT count(*) FROM items"),
            "papers": count(papers._db(), "SELECT count(*) FROM papers"),
            "repos": count(papers._db(), "SELECT count(*) FROM repos"),
            "archive": count(archive._db(), "SELECT count(*) FROM pages"),
            "people": len(discover.people()),
            "passages": count(passages._db(), "SELECT count(*) FROM passages"),
            "dictionary": count(sqlite3.connect(dictionary.DB) if dictionary.DB.exists() else None,
                                "SELECT count(*) FROM words"),
            "forms": sum(1 for _ in open(LEXICON, encoding="utf-8")) if LEXICON.exists() else 0}


def _thousands(n: int) -> str:
    return f"{n // 1000:,} ათასი" if n >= 10000 else f"{n:,}"


def _foot() -> str:
    return (f"<p class=foot>{PRIVACY} <a href=/about>{cap('როგორ მუშაობს')}</a> · "
            f"<a href=/discover>{cap('აღმოჩენა')}</a> · <a href=/random>{cap('შემთხვევითი საიტი')}</a></p>")


def home_page() -> str:
    """The start page: what dzirkva is, how it tells a person's text, when it helps, how to use it, finds of the day."""
    n, k = _sizes(), _thousands
    examples = "".join(f"<a href='{_link(q, 'all', set(), 'example')}'>{escape(q)}<span class=m>{cap(what)}</span></a>"
                       for q, what in EXAMPLES)
    index = (f"ინდექსში: {k(n['wiki'])} ვიკიპედიის სტატია · {k(n['crawl'])} გვერდი {n['sites']:,} საიტიდან · "
             f"{k(n['papers'])} სამეცნიერო ნაშრომი · {k(n['iverieli'])} ბიბლიოთეკის ჩანაწერი · "
             f"{n['people']} პირადი საიტი · {n['archive']:,} ძველი ვების გვერდი")
    signs = "".join(f"<li>{_sign(key, ' · 2010' if key == 'old' else '')} {escape(meaning)}</li>"
                    for key, (_, _, meaning) in SIGNS.items())
    today = _finds(random.Random(time.strftime("%Y-%m-%d")))  # the same finds all day
    return (f"<div class='ans hero'><div class=cap>{cap('ქართული ვების საძიებო')}</div>"
            "<span class=t>ადამიანების დაწერილი ქართული ვები</span>"
            "<p class=lead>ძირკვა ეძებს მხოლოდ ქართულ გვერდებს და ჯერ იმას აჩვენებს, რაც ადამიანებმა დაწერეს: "
            "ბლოგებს, ფორუმებს, მეცნიერთა ნაშრომებს, ბიბლიოთეკის ფონდებს და ძველ ვებს. რეკლამა არ არის და პასუხს "
            f"ხელოვნური ინტელექტი არ წერს.</p><div class=rel>{examples}</div><p class=facts>{index}</p></div>"
            + _block("როგორ ცნობს ადამიანის ტექსტს",
                     "<p>ძირკვა არ ცდილობს გამოიცნოს, რომელი ტექსტი დაწერა მანქანამ. ის ეძებს ნიშანს, რომ გვერდი "
                     "ადამიანმა დაწერა ან შეარჩია, და ასეთ გვერდებს სიის თავში აყენებს. შედეგებთან ეს ნიშნები ასე ჩანს:</p>"
                     f"<ul class='list signs'>{signs}</ul>"
                     "<p>ერთი და იგივე ტექსტი ბევრ საიტზე ერთ შედეგად ჩანს („ასევე 5 საიტზე“), ერთ საიტს სიაში "
                     "მხოლოდ ორი ადგილი აქვს, ხოლო გვერდი, რომელზეც ქართული ასოები ცოტაა, საერთოდ არ ჩანს. ჩვენი "
                     "ქრაულერი ონლაინ-მაღაზიებს არ აგროვებს. ასე კოპირებული სტატიები და ერთი საიტის ათასობით "
                     "გვერდი სიას ვერ ავსებს.</p>")
            + _block("როდის გამოგადგებათ",
                     "<p>დიდი საძიებო სისტემები ყველა ენაზე ეძებენ. ძირკვა მხოლოდ ქართულ ვებს ეძებს, ამიტომ მისი სია "
                     "ქართული გვერდებით ივსება. ის გამოგადგებათ, როცა:</p><ul class=list>"
                     + "".join(f"<li>{escape(x)}</li>" for x in USEFUL) + "</ul>"
                     "<p>ამინდისთვის, რუკისთვის ან ყიდვისთვის დიდი საძიებო სისტემები უფრო მოსახერხებელია. ძირკვა "
                     "სწავლისთვის და ქართული ვების აღმოსაჩენად არის გაკეთებული.</p>")
            + _block("როგორ გამოვიყენოთ", "<ul class=list>" + "".join(f"<li>{escape(x)}</li>" for x in HOW_TO) + "</ul>"
                     f"<a class=go href=/about>{cap('როგორ მუშაობს')} →</a>")
            + _block("დღის მიგნებები", "<ul class=list>" + "".join(today) + "</ul>"
                     f"<a class=go href=/discover>{cap('აღმოაჩინე მეტი')} →</a>" if today else "")
            + _foot())


def busy_page(path: str) -> str:
    """Shown when MAX_SEARCHES new searches already run or wait; the page reloads itself (BUSY_SECONDS)."""
    return (f"<div class='ans hero'><div class=cap>{cap('დატვირთულია')}</div>"
            "<span class=t>ძირკვა ახლა სხვის ძიებებს ამუშავებს</span>"
            f"<p class=lead>ერთდროულად მხოლოდ {MAX_SEARCHES} ძიებას ვამუშავებთ. "
            f"გვერდი თავად განახლდება {BUSY_SECONDS} წამში.</p>"
            f"<a class=go href='{escape(path)}'>{cap('სცადეთ ახლავე')} →</a></div>")


def _details() -> list[tuple[str, str, list[str]]]:
    """How dzirkva works, step by step: (heading, text, example queries). Numbers come from the code and the indexes."""
    n, k = _sizes(), _thousands
    kd = lambda x: k(x).removesuffix("ი")  # before a dative noun: 508 ათას გვერდს
    return [
        ("შეკითხვის წაკითხვა",
         "ლათინური ასოებით დაწერილს ძირკვა ქართულად კითხულობს: kartuli anbani იგივეა, რაც ქართული ანბანი. სიტყვას "
         f"ასწორებს მხოლოდ მაშინ, თუ მსგავსი სიტყვა ქართულ ტექსტებში {KEEP_RATIO}-ჯერ უფრო ხშირია და პირველი შედეგებიც "
         "ამას ადასტურებს. თუ დაწერილი სიტყვაც ნამდვილია, შედეგებს არ ცვლის და მხოლოდ გკითხავთ: „ხომ არ "
         "გულისხმობდით“.", ["kartuli anbani", "ამინდი ბატუმში"]),
        ("სიტყვის ყველა ფორმა",
         f"ქართულ სიტყვას ბევრი ფორმა აქვს. ძირკვა {k(n['forms'])} ფორმის ლექსიკონით და გრამატიკის წესებით პოულობს "
         "ყოველი სიტყვის ლექსიკონის ფორმას და ეძებს ორივეთი, როგორც დაწერეთ და ლექსიკონის ფორმით. ამიტომ ქუთაისში, "
         "ქუთაისის და ქუთაისი ერთ სიტყვად ითვლება, შედეგში კი ყველა ფორმა ყვითლად ინიშნება.", ["ქუთაისის ისტორია"]),
        ("რა გჭირდებათ",
         f"ძირკვას {len(intents())} საჭიროების სია აქვს: ამინდი, კანონი, სკოლა, კვლევა, წიგნები, რელიგია და სხვა. "
         "თითოეულს თავისი საიტები აქვს, და ძირკვა ამ საიტებზე ცალკეც ეძებს. თუ შეკითხვაში „დისერტაცია“ ან "
         "„სტატია“ წერია, ნაშრომები წინ დგას და ამ ტიპის ნაშრომები პირველია.", ["ვეფხისტყაოსანი დისერტაცია"]),
        ("სად ეძებს",
         "ერთდროულად ეკითხება რამდენიმე საძიებო სისტემას და საკუთარ ინდექსებს: ქართულ ვიკიპედიას "
         f"({k(n['wiki'])} სტატია), ვიკიწყაროს, ჩვენ მიერ შეგროვებულ {kd(n['crawl'])} გვერდს {n['sites']:,} საიტიდან, "
         f"„ივერიელის“ {kd(n['iverieli'])} ჩანაწერს, {kd(n['papers'])} სამეცნიერო ნაშრომს და ძველი ვების ასლებს. "
         "საძიებო სისტემების პასუხები ერთი დღე ინახება, ამიტომ იგივე ძიება მეორედ ბევრად სწრაფად ჩნდება.", []),
        ("აზრით ძიება",
         f"ადგილობრივი ენის მოდელი (BGE-M3) {kd(n['passages'])} აბზაცს ინახავს რიცხვების სახით: ვიკიპედიის, "
         "ვიკიწყაროს, ნაშრომებისა და ბიბლიოთეკის ტექსტებს. შეკითხვაც ასეთ რიცხვებად იქცევა, და ძირკვა პოულობს "
         "აბზაცს, რომელიც იგივეს სხვა სიტყვებით ამბობს. მოდელი ამ კომპიუტერზე მუშაობს და ფასიანი სერვისი არ "
         "სჭირდება.", ["რატომ წითლდება მზე როცა ბნელდება"]),
        ("რიგი",
         "ყოველი შედეგი ქულას იღებს. ქულა იზრდება, თუ გვერდს რამდენიმე წყარო პოულობს, თუ აზრით ახლოსაა "
         "შეკითხვასთან, თუ შეკითხვის ყველა სიტყვა აქვს (იშვიათი სიტყვა მეტს ითვლის), თუ სანდო საიტზეა, თუ ვიკიპედია "
         f"მას ციტირებს, ან თუ პირადი საიტი ან ბლოგია. ერთ საიტს მთავარ სიაში {PER_SITE} ადგილი აქვს, ერთი და იგივე "
         "ტექსტი ბევრ საიტზე კი ერთ შედეგად ჩანს.", ["თამარ მეფე"]),
        ("მეორე რაუნდი",
         f"თუ პირველი ათი შედეგიდან {ROUND1_GOOD}-ზე ნაკლებს აქვს შეკითხვის ყველა სიტყვა, ძირკვა საუკეთესო "
         "შედეგებიდან იღებს იშვიათ სიტყვებს, რომლებსაც პასუხის გვერდები იყენებს, და მათით მეორედ ეძებს. ეს სიტყვები "
         "შედეგებში ცისფრად ინიშნება.", []),
        ("პასუხის ყუთები",
         f"„X რას ნიშნავს“ ვიქსიკონის {n['dictionary']:,} სიტყვიდან განმარტებას და სინონიმებს აჩვენებს. თუ "
         "შეკითხვა ვიკიპედიის სტატიის სახელია, სიის თავზე სტატიის პირველი წინადადებები ჩანს. ნაშრომს ახლავს "
         "ავტორი, წელი, ჟურნალი, PDF და მზა ციტირება.", ["სიყვარული რას ნიშნავს"]),
        ("ყველაფერი ჩანს",
         "ყოველი შედეგის ქვეშ წერია, რომელმა წყარომ იპოვა და რომელ ადგილზე, რა სიტყვები დაემთხვა და რა ქულა მიიღო. "
         "„როგორ ვიპოვეთ“ ველში ჩანს ყველა შეკითხვა, რაც ძირკვამ გაგზავნა, და თითოეულის დრო.", []),
    ]


def about_page() -> str:
    """How dzirkva works in detail: the signs of a person's text and their rules, what it pushes away, how it can grow,
    sources and their licenses, what telemetry stores."""
    origins = [
        "საძიებო სისტემები: იანდექსი, იაჰუ, გუგლი და ბრეივი. მათ შედეგებს ვინახავთ ერთი დღით (ბრეივისას ერთი კვირით).",
        "ვიკიპედია, ვიკიწყარო და ვიქსიკონი: ტექსტები CC BY-SA 4.0 ლიცენზიით; ყოველ ამონარიდს ახლავს ბმული სტატიაზე.",
        "ივერიელი: ეროვნული ბიბლიოთეკის ციფრული ბიბლიოთეკის კატალოგი.",
        "სამეცნიერო ჟურნალები და უნივერსიტეტების რეპოზიტორიები: ნაშრომების აღწერები მათი OAI-PMH არხებიდან; "
        "ბმულები ორიგინალზე მიდის.",
        "ჩვენი ქრაულერი: სანდო ქართული საიტები და პატარა ვები. იცავს robots.txt-ს და ერთ საიტს წამში ერთხელ მიმართავს.",
        "ძველი ვები: დახურული საიტების ასლები ინტერნეტ-არქივიდან (Wayback Machine).",
    ]
    stored = [
        "ყოველ ძიებას, ჩანართს, ფილტრს და დაწკაპებას: სიტყვებს, დროს, შედეგის ადგილს, მოწყობილობის ტიპს "
        "(ტელეფონი ან კომპიუტერი) და ბრაუზერის ენას.",
        "IP მისამართს არ ვინახავთ.",
        "ერთი ვიზიტის ნაბიჯებს აკავშირებს შემთხვევითი ნომერი (ქუქი), რომელიც ბოლო მოქმედებიდან 30 წუთში ქრება. "
        "თუ ბრაუზერი აგზავნის Do Not Track ან GPC სიგნალს, ნომერს არ ვქმნით.",
        f"ჩანაწერები {telemetry.KEEP_DAYS} დღეში იშლება.",
        "რატომ: ვხედავთ, რას ვერ პოულობს ძირკვა და სად უნდა გაუმჯობესდეს.",
    ]
    n = _sizes()
    rules = {  # SIGNS: the exact rule of each
        "small": f"1000 სიტყვაზე მინიმუმ {crawl.MIN_VOICE} პირველი პირის სიტყვა („მე“, „ჩემი“, „ვფიქრობ“, „მახსოვს“ …), "
                 f"{crawl.MAX_CORPORATE}-ზე ნაკლები კომპანიის სიტყვა („შპს“, „მომსახურება“, „ფასი“ …) და "
                 f"{crawl.MAX_REPORTING}-ზე ნაკლები სააგენტოს სიტყვა („განაცხადა“, „ცნობით“ …). არ არის სახელმწიფო, "
                 "ახალი ამბების, ტელევიზიის, რადიოს, სპორტის, სკოლის ან სასამართლოს საიტი, მაღაზია არ აქვს და მასზე "
                 f"მაქსიმუმ {crawl.SMALL_INBOUND} საიტი ან ვიკიპედიის სტატია მიუთითებს (ერთი ადამიანის ბლოგზე "
                 "რამდენიც არ უნდა იყოს).",
        "academic": "ჟურნალის ან უნივერსიტეტის რეპოზიტორია (OAI-PMH), .edu ან .ac.ge საიტი, ან ეროვნული ბიბლიოთეკის "
                    f"„ივერიელი“. ინდექსში {n['papers']:,} ნაშრომია {n['repos']} ჟურნალიდან და რეპოზიტორიიდან.",
        "cited": "გვერდს ციტირებს ვიკიპედიის სტატია, რომელიც შეკითხვას სიტყვებით ან აზრით ყველაზე ახლოს დგას.",
        "old": "ვიკიპედიაში ციტირებული, ახლა დახურული ქართული საიტების ასლები ინტერნეტ-არქივიდან.",
        "trusted": f"{sum(t in (1, 2) for _, t in sources().values())} საიტი ხელით შედგენილი სიიდან: ოფიციალური, "
                   "სამეცნიერო და საცნობარო საიტები, ჟურნალისტიკა და ინსტიტუტები.",
        "clicks": f"დაწკაპება ითვლება, თუ იმავე კითხვაზე {clicks.POGO} წამში სხვა დაწკაპება არ მოჰყვა.",
    }
    against = [
        f"თუ ორ ამონარიდს სიტყვების {COPY_SIMILARITY:.0%} საერთო აქვს, ეს ერთი ტექსტია და ერთ შედეგად ჩანს.",
        f"ერთი საიტი მთავარ სიაში მაქსიმუმ {PER_SITE}-ჯერ ჩანს.",
        f"თუ სათაურსა და ამონარიდში ქართული ასოები {MIN_GEORGIAN:.0%}-ზე ნაკლებია, შედეგი არ ჩანს (გარდა საიტებისა, "
        "რომლებიც ძირითადად ქართულად წერენ).",
        "ქრაულერი ახალ საიტს მთლიანად მხოლოდ მაშინ აგროვებს, თუ მისი გვერდები ქართულია და ის მაღაზია არ არის.",
    ]
    return (f"<div class='ans hero'><div class=cap>{cap('ძირკვის შესახებ')}</div><span class=t>როგორ მუშაობს</span></div>"
            + "".join(_block(head, f"<p>{escape(text)}</p>" + ("<div class=rel>" + "".join(
                f"<a href='{_link(q, 'all', set(), 'example')}'>{escape(q)}</a>" for q in examples) + "</div>"
                if examples else "")) for head, text, examples in _details())
            + _block("ადამიანის ტექსტის ნიშნები", "<ul class='list signs'>" + "".join(
                f"<li>{_sign(k)} {escape(t)}</li>" for k, t in rules.items())
                     + "</ul><p>ყოველი ნიშანი ქულას ზრდის; ქულა და მიზეზი ჩანს ყოველი შედეგის ქვეშ.</p>")
            + _block("რას აშორებს", "<ul class=list>" + "".join(f"<li>{escape(s)}</li>" for s in against) + "</ul>")
            + _block("როგორ შეიძლება გაიზარდოს", "<ul class=list>" + "".join(f"<li>{escape(x)}</li>" for x in GROW)
                     + "</ul>")
            + _block("წყაროები", "<ul class=list>" + "".join(f"<li>{escape(s)}</li>" for s in origins) + "</ul>")
            + _block("რას ვინახავთ", "<ul class=list>" + "".join(f"<li>{escape(s)}</li>" for s in stored) + "</ul>")
            + _foot())


def discover_page() -> str:
    rng = random.Random()
    body = (f"<div class='ans hero'><div class=cap>{cap('აღმოჩენა')}</div><span class=t>ქართული ვები ძიების გარეშე</span>"
            f"<div class=facts>ახალი პოსტები პატარა ვებში, ძველი ვები, ახალი ნაშრომები, საიტები თემების მიხედვით</div>"
            f"<a class=go href=/random>{cap('შემთხვევითი საიტი')} →</a></div>")
    posts = discover.newest_posts()
    body += _block("ახალი პოსტები პატარა ვებში", "<ul class=list>" + "".join(
        _post(*p, "discover:post") for p in posts) + "</ul>" if posts else "")
    old = [f for f in (discover.old_find(rng) for _ in range(5)) if f]
    body += _block("ძველი ვებიდან", "<ul class=list>" + "".join(
        f"<li><span class=m>{y}</span><a href='{escape(_out(u, 'discover:old'))}'>{escape(t)}</a></li>"
        for u, t, y in old) + "</ul>" if old else "")
    new = discover.new_papers()
    body += _block("ახალი ნაშრომები", "<ul class=list>" + "".join(_paper(*p) for p in new) + "</ul>" if new else "")
    for name, hosts in discover.shelves():
        body += _block(name, _chips(hosts, "shelf") if hosts else "")
    return body


# ---- telemetry page ---------------------------------------------------------

STAT_NAMES = {"searches": "ძიება", "tab_filter_views": "ჩანართის ან ფილტრის ცვლა", "computed": "ახლად გამოთვლილი",
              "clicks": "დაწკაპება, ყველა", "result_clicks": "დაწკაპება შედეგზე", "sessions": "ვიზიტი",
              "searches_per_session": "ძიება ერთ ვიზიტზე", "clicked_share": "ძიება, რომელსაც დაწკაპება მოჰყვა",
              "zero_results": "ძიება შედეგის გარეშე", "median_sec": "დრო, მედიანა (წმ)", "p90_sec": "დრო, 90% (წმ)",
              "mobile_share": "ტელეფონით ან პლანშეტით", "local_share": "ამ კომპიუტერიდან",
              "busy": "დატვირთვის გვერდი ნახეს"}
FEATURE_NAMES = {"spelling fixed": "მართლწერა გასწორდა", "did you mean shown": "„ხომ არ გულისხმობდით“",
                 "answer box": "ვიკიპედიის პასუხი", "dictionary box": "ლექსიკონი", "feedback round": "მეორე რაუნდი",
                 "papers in top 10": "ნაშრომი პირველ ათეულში", "citation opened": "ციტირება გაიხსნა",
                 "citation copied": "ციტირება დაკოპირდა", "how-we-found panel toggled": "„როგორ ვიპოვეთ“ გაიხსნა ან დაიხურა"}
FROM_NAMES = {"typed": "აკრიფა", "tab": "ჩანართი", "filter": "ფილტრი", "more": "ბლოკის „ყველა“", "related": "მსგავსი ძიება",
              "dym": "„ხომ არ გულისხმობდით“", "syn": "სინონიმი", "author": "ავტორის ნაშრომები", "example": "მაგალითი"}
PAGE_NAMES = {"home": "მთავარი გვერდი", "about": "როგორ მუშაობს", "discover": "აღმოჩენა", "site": "საიტის გვერდი",
              "random": "შემთხვევითი საიტი"}
PERIODS = {1: "დღე", 7: "კვირა", 30: "თვე", 0: "ყველა"}
DEVICE_NAMES = {"desktop": "კომპიუტერი", "mobile": "ტელეფონი", "tablet": "პლანშეტი"}
WHERE_NAMES = {"main": "მთავარი სია", "video": "ვიდეოს ბლოკი", "people": "ხალხის ბლოკი", "old": "ძველი ვების ბლოკი",
               "answer": "ვიკიპედიის პასუხი", "dict": "ლექსიკონი", "copy": "იგივე ტექსტი სხვა საიტზე", "pdf": "PDF",
               "site:go": "საიტის გვერდი: საიტზე გადასვლა", "site:page": "საიტის გვერდი: ახალი გვერდები",
               "site:wiki": "საიტის გვერდი: ვიკიპედია", "site:old": "საიტის გვერდი: ძველი ვები",
               "discover:post": "აღმოჩენა: პოსტი", "discover:old": "აღმოჩენა: ძველი ვები", "discover:paper": "აღმოჩენა: ნაშრომი",
               "home:post": "მთავარი გვერდი: პოსტი", "home:old": "მთავარი გვერდი: ძველი ვები"}


def _where(w: str) -> str:
    """Where on a page a link was clicked (telemetry `where`), in Georgian."""
    kind, _, name = w.partition(":")
    if kind == "tab":
        return f"ჩანართი: {TABS.get(name, name)}"
    if kind == "filter":
        return f"ფილტრი: {FILTERS.get(name, name)}"
    return WHERE_NAMES.get(w, w)


def _table(rows: list, heads: list[str]) -> str:
    if not rows:
        return "<p class=m>ჯერ არაფერია.</p>"
    return ("<div class=tbl><table><tr>" + "".join(f"<th>{h}</th>" for h in heads) + "</tr>"
            + "".join("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in row) + "</tr>" for row in rows)
            + "</table></div>")


def _bars(days: list[tuple[str, int]], clicks: dict[str, int]) -> str:
    """Searches per day, days without searches included."""
    if not days:
        return "<p class=m>ჯერ არაფერია.</p>"
    first, last = (time.mktime(time.strptime(d, "%Y-%m-%d")) for d in (days[0][0], days[-1][0]))
    count = dict(days)
    dates = [time.strftime("%Y-%m-%d", time.localtime(first + i * 86400)) for i in range(int((last - first) / 86400) + 1)]
    top = max(count.values())
    bars = "".join(f"<rect x={i * 14} y={60 - 56 * count.get(d, 0) / top:.1f} width=11 "
                   f"height={56 * count.get(d, 0) / top:.1f}><title>{d}: {count.get(d, 0)} ძიება, "
                   f"{clicks.get(d, 0)} დაწკაპება</title></rect>" for i, d in enumerate(dates))
    return (f"<svg class=bars viewBox='0 0 {len(dates) * 14} 60' preserveAspectRatio=none>{bars}</svg>"
            f"<p class=m>{dates[0]}-დან {dates[-1]}-მდე · დღეში მაქსიმუმ {top} ძიება</p>")


def _step(e: dict) -> str:
    """One step of a visit on the telemetry page."""
    k = e["kind"]
    if k == "search":
        what = f"ძიება «{escape(e['q'])}» · {FROM_NAMES.get(e.get('from', 'typed'), escape(str(e.get('from'))))}"
        what += f" · {TABS.get(e.get('tab'), '')}" if e.get("tab", "all") != "all" else ""
        what += f" · {FILTERS.get(e['f'], e['f'])}" if e.get("f") else ""
        what += f" · {e.get('n', 0)} შედეგი" + (f" · {e.get('sec')} წმ" if e.get("fresh") else "")
    elif k == "click":
        rank = f"#{e['rank']} " if e.get("rank") else ""
        what = f"→ {rank}{escape(e.get('host', ''))} <span class=m>{escape(_where(e.get('where', '')))}</span>"
    elif k == "ui":
        what = f"მოქმედება: {escape(e.get('what', ''))}" + (" · გაიხსნა" if e.get("open") else "")
    elif k == "error":
        what = f"შეცდომა: {escape(e.get('error', ''))}"
    else:
        what = PAGE_NAMES.get(k, k) + (f": {escape(e['host'])}" if e.get("host") else "")
    return f"<div><span class=m>{time.strftime('%H:%M:%S', time.localtime(e['time']))}</span> {what}</div>"


def stats_page(days: int, local: bool, key: str) -> str:
    """How people use dzirkva (telemetry.stats), for the period and with or without this computer's visits."""
    s = telemetry.stats(telemetry.events(days, local))
    link = lambda d, loc: f"/stats?d={d}" + ("" if loc else "&local=0") + (f"&key={quote(key)}" if key else "")
    nav = " · ".join(f"<b>{name}</b>" if d == days else f"<a href='{link(d, local)}'>{name}</a>"
                     for d, name in PERIODS.items())
    nav += " · " + (f"<a href='{link(days, False)}'>ამ კომპიუტერის გარეშე</a>" if local
                    else f"<a href='{link(days, True)}'>ამ კომპიუტერითაც</a>")
    nums = "".join((f"<div><b>{v:.0%}</b>" if k.endswith("_share") else f"<div><b>{v}</b>")
                   + f"<span>{STAT_NAMES[k]}</span></div>" for k, v in s["numbers"].items())
    intent_name = lambda i: intents()[i]["ka"] if i in intents() else "ზოგადი" if i == "none" else i
    today, month = engines.brave_used()
    brave = (f"ბრეივის API (ფასიანი): დღეს {today} / {engines.BRAVE_DAILY_LIMIT}, ამ თვეში {month} / "
             f"{engines.BRAVE_MONTHLY_LIMIT} · ერთდროულად {MAX_SEARCHES} ძიება")
    body = (f"<div class='ans hero'><div class=cap>{cap('ტელემეტრია')}</div><span class=t>როგორ იყენებენ ძირკვას</span>"
            f"<p class=facts>{nav}</p><p class=facts>{brave}</p><div class=nums>{nums}</div></div>")
    body += _block("ძიება დღეების მიხედვით", _bars(s["days"], s["click_days"]))
    body += _block("ხშირი ძიებები", _table(s["top_queries"], ["ძიება", "რამდენჯერ", "დაწკაპება"]))
    body += _block("ძიება შედეგის გარეშე", _table(s["zero"], ["ძიება", "რამდენჯერ"]))
    body += _block("ძიება დაწკაპების გარეშე", _table(s["no_click"], ["ძიება", "ვიზიტი"]))
    body += _block("შეკითხვის შეცვლა (დაწკაპების გარეშე)", _table([(a, b, n) for (a, b), n in s["refine"]],
                                                                  ["ჯერ", "მერე", "რამდენჯერ"]))
    body += _block("დაწკაპების ადგილი სიაში", _table([("11+" if r == 11 else r, n) for r, n in s["ranks"]],
                                                    ["ადგილი", "დაწკაპება"]))
    body += _block("სად დააწკაპეს", _table([(_where(w), n) for w, n in s["where"]], ["ადგილი გვერდზე", "დაწკაპება"]))
    body += _block("საიტები, სადაც წავიდნენ", _table(s["hosts"], ["საიტი", "დაწკაპება"]))
    body += _block("რა სახის შედეგები აირჩიეს", _table([(KIND_NAMES.get(k, k), n) for k, n in s["kinds"]]
                                                     + [(f"ფილტრი: {FILTERS.get(t, t)}", n) for t, n in s["tags"]],
                                                     ["სახე", "დაწკაპება"]))
    body += _block("როგორ მივიდნენ ძიებამდე", _table([(FROM_NAMES.get(f, f), n) for f, n in s["from"]], ["გზა", "ძიება"]))
    body += _block("ჩანართები და ფილტრები", _table([(TABS.get(t, t), n) for t, n in s["tabs"]]
                                                   + [(FILTERS.get(f, f), n) for f, n in s["filters"]], ["", "ნახვა"]))
    body += _block("რა სჭირდებათ", _table([(intent_name(i), n) for i, n in s["intents"]]
                                         + [(f"კითხვა: {QUESTION.get(t, t)}", n) for t, n in s["types"] if t != "none"],
                                         ["საჭიროება", "ძიება"]))
    body += _block("ფუნქციები", _table([(FEATURE_NAMES.get(k, k), n) for k, n in s["features"].items()], ["", "რამდენჯერ"]))
    body += _block("გვერდები", _table([(PAGE_NAMES.get(k, k), n) for k, n in s["pages"]] + s["sites"], ["გვერდი", "ნახვა"]))
    body += _block("მოწყობილობა, ენა, საიდან მოვიდნენ", _table(
        [(DEVICE_NAMES.get(d, d), n) for d, n in s["devices"]] + [(f"ენა: {'უცნობი' if lang == 'none' else lang}", n) for lang, n in s["langs"]]
        + [(f"საიდან: {r}", n) for r, n in s["refs"]], ["", "რამდენჯერ"]))
    body += _block("ყველაზე ნელი ძიებები", _table(s["slow"], ["წამი", "ძიება"]))
    body += _block("შეცდომები", _table([(time.strftime("%m-%d %H:%M", time.localtime(t)), w, q, e)
                                        for t, w, q, e in s["errors"]], ["დრო", "გვერდი", "ძიება", "შეცდომა"]))
    body += _block("ბოლო ვიზიტები", "".join(
        f"<div class=sess><div class=m>{time.strftime('%m-%d %H:%M', time.localtime(seq[0]['time']))} · "
        f"{DEVICE_NAMES.get(seq[0].get('device', ''), '')} · {len(seq)} ნაბიჯი</div>" + "".join(_step(e) for e in seq[:40]) + "</div>"
        for _, seq in reversed(s["recent"])))
    return body


def _on_worker(fn):
    """fn() on the worker (the main thread, see __main__), after the searches before it; its value or its error."""
    done, box = threading.Event(), {}

    def job() -> None:
        try:
            box["value"] = fn()
        except Exception as e:
            box["error"] = e
    _jobs.put((job, done))
    done.wait()
    if "error" in box:
        raise box["error"]
    return box["value"]


class Handler(BaseHTTPRequestHandler):
    """Request threads; pages are built one at a time on the worker. Every request is a telemetry event."""

    def log_message(self, format: str, *args) -> None:
        pass  # access logs hold IP addresses; telemetry keeps what we need without them

    def _begin(self) -> tuple[str, dict]:
        """Session id (cookie, none under Do Not Track or GPC), local or not, the visitor's client class."""
        self.t0 = time.time()
        url = urlparse(self.path)
        private = self.headers.get("DNT") == "1" or self.headers.get("Sec-GPC") == "1"
        m = re.search(r"(?:^|;\s*)s=([0-9a-f]{12})(?:;|$)", self.headers.get("Cookie", ""))
        self.session = "" if private else m[1] if m else secrets.token_hex(6)
        host = self.headers.get("Host", "").split(":")[0]
        self.local = host in ("127.0.0.1", "localhost") and not any(h in self.headers for h in FORWARDED)
        ref = urlparse(self.headers.get("Referer", "")).hostname or ""
        self.who = telemetry.client(self.headers.get("User-Agent", ""), self.headers.get("Accept-Language", ""),
                                    "" if ref == host else ref, self.local)
        return url.path, parse_qs(url.query)

    def _log(self, kind: str, q: str = "", **data) -> None:
        telemetry.log(kind, self.session, q, int((time.time() - self.t0) * 1000), **self.who, **data)

    def do_GET(self) -> None:
        """A new search needs a free place (MAX_SEARCHES), else the visitor gets the busy page at once."""
        route, params = self._begin()
        q = params.get("q", [""])[0].strip()
        deep = params.get("deep", [""])[0] == "1"
        new_search = route == "/" and bool(q) and (q + DEEP_KEY if deep else q) not in _cache
        if new_search and not _searches.acquire(blocking=False):
            self._log("busy", q)
            return self._send(_page("", busy_page(self.path), BUSY_SECONDS), 503)
        try:
            self._safe(route, params)
        finally:
            if new_search:
                _searches.release()

    def _safe(self, route: str, params: dict) -> None:
        try:
            self._route(route, params)
        except Exception as e:  # the visitor gets a page; the error goes to telemetry
            self._log("error", params.get("q", [""])[0], where=route, error=f"{type(e).__name__}: {e}"[:300])
            self._send(_page("", "<p class=fix>ეს გვერდი ახლა ვერ გაიხსნა. სცადეთ ცოტა ხანში.</p>"), 500)

    def do_POST(self) -> None:  # navigator.sendBeacon posts
        route, params = self._begin()
        if route == "/t":
            self._beacon(params)
        else:
            self.send_error(405)

    def _route(self, route: str, params: dict) -> None:
        p = lambda k, default="": params.get(k, [default])[0].strip()
        if route == "/go":
            return self._go(params)
        if route == "/t":
            return self._beacon(params)
        if route == "/random":  # surfing: a random small site, straight to it
            host = discover.random_site()
            self._log("random", host=host)
            return self._redirect(f"https://{host}/")
        if route == "/site" and p("h"):
            host = p("h").lower().removeprefix("www.")
            body = site_page(host)
            self._log("site", host=host, **{"from": p("from")})
            return self._send(_page("", body))
        if route in ("/site", "/discover"):
            body = discover_page()
            self._log("discover")
            return self._send(_page("", body))
        if route == "/about":
            self._log("about")
            return self._send(_page("", about_page()))
        if route == "/stats":
            key = os.environ.get("STATS_KEY", "")
            if not (self.local or key and hmac.compare_digest(p("key"), key)):
                return self.send_error(404)
            return self._send(_page("", stats_page(int(p("d", "7")) if p("d", "7").isdigit() else 7,
                                                   p("local") != "0", p("key"))))
        if route != "/":
            return self.send_error(404)
        q = p("q")
        if not q:
            body = home_page()
            self._log("home")
            return self._send(_page("", body))
        tab = p("tab", "all") if p("tab", "all") in TABS else "all"
        chosen = {f for f in params.get("f", [])[:1] if f in FILTERS}  # one filter at a time
        deep = p("deep") == "1"
        key = q + DEEP_KEY if deep else q
        fresh = key not in _cache
        if fresh:
            t = time.time()
            qs, results, debug = _on_worker(lambda: search(q, deep))  # the meaning model runs on the worker only
            debug["seconds"]["total"] = round(time.time() - t, 1)  # with the wait for searches before it
            _cache[key] = (qs, results, debug)
        qs, results, debug = _cache[key]
        body = render(q, tab, chosen, qs, results, debug)
        if self.session:
            _last_view[self.session] = time.time()
        top = main_list(results)[:10]
        self._log("search", q, fresh=fresh, deep=deep, tab=tab, f=next(iter(chosen), ""), n=len(results),
                  sec=debug["seconds"]["total"], intent=debug.get("intent"), type=debug.get("type"),
                  fixed=bool(debug["spelling"]), dym=bool(debug.get("did_you_mean")), answer=bool(debug.get("answer")),
                  definition=bool(debug.get("definition")), feedback=bool(debug["feedback"]),
                  papers=any("papers" in r.queries for r in top), top=[_host(r.url) for r in top],
                  **{"from": p("from", "typed")})
        self._send(_page(q, body))

    def _go(self, params: dict) -> None:
        """A click on a link to another site: only links this server signed; results also go to clicks.py."""
        p = lambda k: params.get(k, [""])[0]
        url, q = p("u"), p("q")
        if not url.startswith(("http://", "https://")) or not hmac.compare_digest(p("h"), _sig(url)):
            return self.send_error(400)
        rank = int(p("r")) if p("r").isdigit() else 0
        info = {}
        if p("k") and rank:
            clicks.log(p("k"), canonical_url(url), rank)
            hit = next((r for r in _cache[q][1] if r.rank == rank), None) if q in _cache else None
            if hit:
                info = {"result_kind": hit.kind, "tags": sorted(hit.tags), "tier": hit.tier,
                        "paper": "papers" in hit.queries}
            _cache.clear()  # the next search of the question ranks with this click
        since = _last_view.get(self.session)
        self._log("click", q, where=p("w"), rank=rank, host=_host(url), site=p("s"),
                  after_ms=int((time.time() - since) * 1000) if since else None, **info)
        self._redirect(url)

    def _beacon(self, params: dict) -> None:
        """A page action (BEACON): citation or panel opened or closed, citation copied."""
        p = lambda k: params.get(k, [""])[0]
        self._log("ui", p("q"), what=p("e")[:20], open=p("o") == "1", rank=int(p("r")) if p("r").isdigit() else 0)
        self.send_response(204)
        self._cookie()
        self.end_headers()

    def _cookie(self) -> None:
        if self.session:
            self.send_header("Set-Cookie", f"s={self.session}; Max-Age={telemetry.SESSION_MINUTES * 60}; Path=/; "
                                           "HttpOnly; SameSite=Lax")

    def _send(self, html: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._cookie()
        self.end_headers()
        self.wfile.write(html.encode())

    def _redirect(self, url: str) -> None:
        self.send_response(302)
        self.send_header("Location", _ascii_url(url))
        self._cookie()
        self.end_headers()


if __name__ == "__main__":
    similarity("გამარჯობა", ["გამარჯობა"])  # load the meaning model once, before the first search
    passages._index()  # ~35 s: load the 743k paragraph vectors before the first search, not during it
    home_page()  # ~10 s: index sizes and the newest posts, counted before the first visitor
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"http://127.0.0.1:{PORT}", flush=True)
    # The worker: every new search runs here, one at a time. The meaning model on the Mac GPU hangs when called
    # from other threads; the request threads build the pages and wait only for their search.
    while True:
        job, done = _jobs.get()
        job()  # catches its own errors
        done.set()
