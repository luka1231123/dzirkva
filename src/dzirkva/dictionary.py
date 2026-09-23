"""Georgian explanatory dictionary from ka.wiktionary (ვიქსიკონი): data/dictionary.db.

Answer box for "სახლი რას ნიშნავს", "პურის განმარტება", "გამარჯობა მნიშვნელობა", and for
one-word queries ("პური", "პური რა არის") without a Wikipedia article. Built by scripts/build_dictionary.py.
"""

import json
import re
import sqlite3
from functools import cache
from pathlib import Path
from urllib.parse import quote

from dzirkva.georgian import normalize
from dzirkva.morph import analyze

DB = Path(__file__).resolve().parents[2] / "data" / "dictionary.db"
URL = "https://ka.wiktionary.org/wiki/"
# Words that ask for a meaning (prefixes, any form): განმარტება, მნიშვნელობა, ნიშნავს, ლექსიკონი, სიტყვის
ASK = ("განმარტ", "მნიშვნელობ", "ნიშნავ", "ლექსიკონ", "სიტყვ")
ASK_STOP = {"რას", "რა", "რის", "რისი", "არის", "ანუ", "ქართულად", "ქართული"}
MAX_SENSES = 4

# ---- parse one wiki page ------------------------------------------------

# Three page formats: "== ქართული ==" sections (also "== [[ქართული]] =="), old "{{-ka-}}" pages,
# and the {{აღწერა … |ენა = ქართული |მნიშვნელობა = …}} template.
GEORGIAN = re.compile(r"^==\s*\[*ქართული\]*\s*==\s*$(.*?)(?=^==[^=]|\Z)", re.M | re.S)
POS = re.compile(r"\{\{კატეგორია\|ქართული\|([^}|]+)\}\}|\{\{მნ\|([^}|]+)\|ქართული\}\}"
                 r"|\{\{-(noun|verb|adj|adv|pron|num)-\}\}|მეტყველების ნაწილი1?\s*=\s*([^\n|]+)")
OLD_POS = {"noun": "არსებითი სახელი", "verb": "ზმნა", "adj": "ზედსართავი სახელი", "adv": "ზმნიზედა",
           "pron": "ნაცვალსახელი", "num": "რიცხვითი სახელი"}
TEMPLATE_SENSE = re.compile(r"^\s*\|\s*მნიშვნელობა\d*\s*=(.*)$", re.M)
SYNONYMS = re.compile(r"\{\{სინონიმები\|([^}]*)\}\}")
SENSE = re.compile(r"^#(?![:*#])\s*(.+)$", re.M)
CLEAN = [
    (re.compile(r"\{\{კ\|\s*([^}|]+?)\s*\}\}"), r"(\1)"),   # {{კ|გადატანით}} -> (გადატანით)
    (re.compile(r"\{\{[^{}]*\}\}"), ""),                    # other templates (inner first; run twice)
    (re.compile(r"\{\{[^{}]*\}\}"), ""),
    (re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]"), r"\1"),  # [[target|text]] -> text
    (re.compile(r"<br\s*/?>.*$"), ""),                       # an example follows <br />
    (re.compile(r"<[^>]+>|'''?"), ""),
]


def _clean(text: str) -> str:
    for rx, rep in CLEAN:
        text = rx.sub(rep, text)
    return " ".join(text.split()).strip(" ;,")


def _body(wikitext: str) -> tuple[str, list[str]] | None:
    """The Georgian part of the page and its raw senses."""
    if m := GEORGIAN.search(wikitext):
        return m.group(1), SENSE.findall(m.group(1))
    if "{{-ka-}}" in wikitext:
        body = wikitext.split("{{-ka-}}", 1)[1]
        return body, SENSE.findall(body)
    if "{{აღწერა" in wikitext and re.search(r"ენა\s*=\s*ქართული", wikitext):
        return wikitext, TEMPLATE_SENSE.findall(wikitext)
    return None


def parse(wikitext: str) -> dict | None:
    """The Georgian entry of one page: {pos, senses, synonyms}, or None without senses."""
    found = _body(wikitext)
    if not found:
        return None
    body, raw = found
    senses = [s for s in map(_clean, raw) if len(s.strip(".… ")) > 1]
    if not senses:
        return None
    pos = next((g for m in POS.finditer(body) for g in m.groups() if g and g.strip()), "").strip()
    synonyms = [w.strip() for m in SYNONYMS.finditer(body) for w in m.group(1).split("|")]
    return {"pos": OLD_POS.get(pos, pos), "senses": senses,
            "synonyms": list(dict.fromkeys(w for w in synonyms if w and "=" not in w))}


# ---- lookup ----------------------------------------------------------------

@cache
def _db() -> sqlite3.Connection | None:
    return sqlite3.connect(DB, check_same_thread=False) if DB.exists() else None


def lookup(word: str) -> dict | None:
    """Entry for a word in any form: the form itself first, then its dictionary forms (morph)."""
    db = _db()
    if db is None:
        return None
    word = normalize(word)
    for cand in dict.fromkeys([word] + [a.lemma for a in analyze(word)] if " " not in word else [word]):
        row = db.execute("SELECT entry FROM words WHERE word = ?", (cand,)).fetchone()
        if row:
            return {"word": cand, "url": URL + quote(cand), **json.loads(row[0])}
    return None


def define(query: str) -> dict | None:
    """Dictionary answer for the query, with asked=True when the query asks for a meaning.

    "სახლი რას ნიშნავს" → სახლი (asked); "პური რა არის" → პური (one word, not asked).
    """
    words = [w for w in normalize(query).split() if not w.isascii()]
    ask = [w for w in words if w.startswith(ASK)]
    rest = [w for w in words if w not in ask and w not in ASK_STOP]
    if not rest or (not ask and len(rest) > 1):  # "პური რა არის" counts as one word
        return None
    entry = lookup(" ".join(rest)) or (lookup(rest[0]) if len(rest) == 1 else None)
    if entry:
        entry["senses"] = entry["senses"][:MAX_SENSES]
        entry["asked"] = bool(ask)
    return entry
