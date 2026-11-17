"""Search tools in the query: symbols and Georgian names only, one strict rule each. /tools explains them.

  "ილია ჭავჭავაძე"  „…“   phrase: these words, in this order, in these forms
  !ღვინოს                  this form only
  ღვინო/ჭაჭა               one of the words: / joins the one word or "phrase" on each side (spaces do not matter)
  ღვინო+ქვევრი             the words near each other: at most NEAR words apart, any form
  ~ღვინო                   the word or a word near in meaning (wordgraph): ღვინო/ლუდი/ჭაჭა …
  ქართ*                    a word that starts like this
  -ლექსი  -"…"             without this word (any form) or phrase
  #ილია                    the word in the title
  @tsu.ge @ფეისბუქი        on this site (-@ = not on it); @ვიკიპედია @ბლოგები … only this kind of source
  >2015 <2020              dated after / before the year; results with no date go

Every other word works as before: required, any form. parse() reads a query into a Query: the plain words for the
usual pipeline (spelling, intents, named sites), Terms for the indexes (wiki.any_form asks a Term for its FTS5
expression, wiki.match adds NOT for the minus terms) and filters for the merged results (keep). Nothing is guessed:
chips() says how each part was read, notes() what could not be read.
"""

import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from functools import cache

from dzirkva.georgian import freq, latin_to_georgian, normalize
from dzirkva.morph import families

NEAR = 10            # + : words at most this many words apart
NEAR_FORMS = 4       # + : forms of each word tried (FTS5 NEAR takes phrases, not OR groups)
ALT_MAX = 5          # ~ : words near in meaning added
ALT_SIM = 0.7        # ~ : word2vec similarity at least (ღვინო → ლუდი 0.79, ჭაჭა 0.79, ყურძენი 0.68)
ALT_MIN_FREQ = 20    # ~ : neighbours rarer than this are mostly typos (მასწავლებლი)
PREFIX_MIN = 3       # * : letters before the star at least (ქა* would read half the index)
PASTE_WORDS = 12     # a longer query is pasted text: its quotes and dashes are not tools
OPEN, CLOSE = "\"„“«", "\"“”»"
PREFIXES = "-!~#@<>"
TOKEN = re.compile(r"\s+|[/+]|[" + re.escape(OPEN) + r"]|[^\s/+" + re.escape(OPEN) + r"]+")
YEAR = re.compile(r"^[<>](1[0-9]{3}|20[0-9]{2})$")
DATE_FIRST = re.compile(r"^(\d{4})-\d{2}-\d{2}")
WAYBACK = re.compile(r"^https?://web\.archive\.org/web/(\d{4})\d*[^/]*/(.+)")
WORD = re.compile(r"[\wა-ჰ]+")

# @name → (label, test on a search.Result). The names are the filter chips of the results page.
SOURCES = {
    "ვიკიპედია": ("ვიკიპედია", lambda r: _host(r.url) == "ka.wikipedia.org"),
    "ვიკიწყარო": ("ვიკიწყარო", lambda r: _host(r.url) == "ka.wikisource.org"),
    "ბიბლიოთეკა": ("ეროვნული ბიბლიოთეკა (ივერიელი)", lambda r: "iverieli" in r.engines),
    "სამეცნიერო": ("სამეცნიერო ნაშრომები", lambda r: "academic" in r.tags or "papers" in r.engines),
    "ხალხი": ("ბლოგები, ფორუმები, პირადი გვერდები", lambda r: "people" in r.tags),
    "პატარავები": ("პატარა ვები: პირადი საიტები", lambda r: r.small or "small" in r.tags),
    "ძველივები": ("ძველი ვები: ინტერნეტ-არქივის ასლები", lambda r: "old" in r.tags),
    "ტექსტები": ("ტექსტები: წიგნები, ლექსები, PDF", lambda r: "texts" in r.tags),
    "ცოდნა": ("ცოდნა: ცნობარები და განმარტებები", lambda r: "knowledge" in r.tags),
    "სიახლეები": ("სიახლეები", lambda r: r.kind == "news"),
    "ვიდეო": ("ვიდეო და ფილმები", lambda r: r.kind in ("video", "film")),
}
ALIASES = {"ბლოგები": "ხალხი", "ფორუმები": "ხალხი", "ნაშრომები": "სამეცნიერო", "მეცნიერება": "სამეცნიერო",
           "ივერიელი": "ბიბლიოთეკა", "არქივი": "ძველივები", "ძველი": "ძველივები", "პატარა": "პატარავები",
           "წიგნები": "ტექსტები", "ახალიამბები": "სიახლეები", "ფილმები": "ვიდეო"}


def _host(url: str) -> str:
    from dzirkva.sources import host
    return host(url)


def _word(raw: str) -> str:
    """A word as the plain pipeline reads it: Georgian letters, Latin turned into Georgian."""
    w = normalize(raw)
    if w.isascii() and w.isalpha():
        return latin_to_georgian(w) or w
    return w


@cache
def neighbours(word: str) -> tuple[str, ...]:
    """~ : up to ALT_MAX words near in meaning from the word map (not its own family: any_form has those).
    Its own connection: the web pages share wordgraph's connection under a lock, a search runs on another thread."""
    from dzirkva.morph import analyze, ka_lemma
    from dzirkva.wordgraph import DB
    if not DB.exists():
        return ()
    ka = ka_lemma(word)
    with closing(sqlite3.connect(f"file:{DB}?mode=ro", uri=True)) as db:
        for lemma in (word, ka[0] if ka else None, *(a.lemma for a in analyze(word)[:3])):
            row = db.execute("SELECT near FROM g WHERE word = ?", (lemma,)).fetchone() if lemma else None
            if row:
                break
        else:
            return ()
    own = families(word) | families(lemma)
    out = [n for n, sim in json.loads(row[0]) if sim >= ALT_SIM and freq(n) >= ALT_MIN_FREQ and not families(n) & own]
    return tuple(out[:ALT_MAX])


# ---- terms: what the indexes search -------------------------------------------

@dataclass(frozen=True)
class Atom:
    """One searchable part: how = word (any form) | phrase | exact | prefix; text = normalized words."""
    how: str
    text: str

    def fts(self) -> str:
        from dzirkva.wiki import any_form
        if self.how == "word":
            return any_form(self.text)
        quoted = '"' + self.text.replace('"', "") + '"'
        return quoted + "*" if self.how == "prefix" else quoted

    def phrases(self) -> list[str]:
        """For NEAR: FTS5 phrases (most frequent forms of a word)."""
        if self.how != "word":
            return [self.fts()]
        from dzirkva.wiki import any_form
        return re.findall(r'"[^"]+"', any_form(self.text))[:NEAR_FORMS]

    def found(self, toks: list[str], fams: set[str], joined: str) -> bool:
        if self.how == "word":
            return self.text in toks or bool(families(self.text) & fams)
        if self.how == "exact":
            return self.text in toks
        if self.how == "prefix":
            return any(t.startswith(self.text) for t in toks)
        return f" {self.text} " in joined

    def label(self) -> str:
        return {"word": self.text, "exact": f"«{self.text}» (მხოლოდ ეს ფორმა)", "phrase": f"«{self.text}»",
                "prefix": f"{self.text}… (ასე დაწყებული)"}[self.how]


class Term(str):
    """A query part with a search tool. The str is its text (shown in debug and related searches); search.py keeps
    it among the content words, wiki.any_form asks it for its FTS5 expression (fts).
    join: "or" (/ and ~: any one atom) or "near" (+: all atoms close together). title: # (the title must have it).
    minus: - (an excluded term: wiki.match puts NOT before it)."""

    atoms: tuple[Atom, ...]
    join: str
    title: bool
    minus: bool

    def __new__(cls, text: str, atoms: list[Atom], join: str = "or", title: bool = False, minus: bool = False):
        t = super().__new__(cls, text)
        t.atoms, t.join, t.title, t.minus = tuple(atoms), join, title, minus
        return t

    def fts(self) -> str:
        if self.join == "near" and len(self.atoms) > 1:
            groups = [a.phrases() for a in self.atoms]
            combos = [[]]
            for g in groups:
                combos = [c + [p] for c in combos for p in g]
            return "(" + " OR ".join(f"NEAR({' '.join(c)}, {NEAR})" for c in combos) + ")"
        parts = [a.fts() for a in self.atoms]
        return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"

    def found(self, toks: list[str], fams: set[str], joined: str) -> bool:
        hits = (a.found(toks, fams, joined) for a in self.atoms)
        return all(hits) if self.join == "near" else any(hits)

    @property
    def words(self) -> list[str]:
        return [w for a in self.atoms for w in a.text.split()]

    @property
    def meaning(self) -> list[str]:
        """Words for the meaning model: ~ღვინო is ღვინო (its neighbours would pull the query toward ლუდი)."""
        return self.atoms[0].text.split() if getattr(self, "alts", False) else self.words


def text_parts(text: str) -> tuple[list[str], set[str], str]:
    """(tokens, their word families, tokens joined with spaces around) of a text, for Term.found."""
    toks = WORD.findall(normalize(text))
    return toks, {f for t in toks for f in families(t)}, f" {' '.join(toks)} "


# ---- the parsed query -----------------------------------------------------------

@dataclass
class Chip:
    """How one part was read, for the page: kind (CSS class), sign (the symbol), label, and the query without it."""
    kind: str
    sign: str
    label: str
    without: str = ""
    alts: list[tuple[str, str]] = field(default_factory=list)  # ~ words: (word, the query without it)


@dataclass
class Query:
    raw: str
    plain: list[tuple[int, int, str]] = field(default_factory=list)  # (start, end, word as typed) of plain words
    terms: list[Term] = field(default_factory=list)
    minus: list[Term] = field(default_factory=list)
    sites: list[str] = field(default_factory=list)
    not_sites: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    not_sources: list[str] = field(default_factory=list)
    after: int | None = None
    before: int | None = None
    chips: list[Chip] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    info: str = ""  # a long query read without tools: said on the page, changes nothing

    @property
    def active(self) -> bool:
        """The query uses a search tool: the page shows how it was read, the indexes give 3× more candidates."""
        return bool(self.terms or self.minus or self.sites or self.not_sites or self.sources or self.not_sources
                    or self.after or self.before or self.notes)

    @property
    def plain_text(self) -> str:
        return " ".join(w for _, _, w in self.plain)

    @property
    def text(self) -> str:
        """The query for the meaning model and the question type: plain words and the words of the terms."""
        return " ".join([self.plain_text] + [" ".join(t.meaning) for t in self.terms]).strip()

    @property
    def tail(self) -> str:
        """The filters as typed, kept on related searches."""
        out = [f"-{t}" for t in self.minus] + [f"@{s}" for s in self.sites + self.sources]
        out += [f"-@{s}" for s in self.not_sites + self.not_sources]
        out += [f">{self.after}"] if self.after else []
        out += [f"<{self.before}"] if self.before else []
        return " ".join(out)

    def with_fixes(self, fixes: dict[str, str]) -> str:
        """The raw query with spelling fixes applied to its plain words only."""
        out, last = [], 0
        for start, end, w in self.plain:
            out += [self.raw[last:start], fixes.get(_word(w), w)]
            last = end
        return "".join(out + [self.raw[last:]])

    def keep(self, r) -> bool:
        """A merged search.Result passes every filter: terms (the OR fallback of an index and the meaning lists
        do not have them), minus words, title words, sites, sources and years."""
        text = f"{r.title}. {r.snippet}"
        parts = text_parts(text)
        if not all(t.found(*parts) for t in self.terms):
            return False
        if any(t.found(*parts) for t in self.minus):
            return False
        title = text_parts(r.title)
        if not all(t.found(*title) for t in self.terms if t.title):
            return False
        site = result_site(r.url)
        on = lambda hosts: any(site == h or site.endswith("." + h) for h in hosts)
        if self.sites and not on(self.sites) and not any(SOURCES[s][1](r) for s in self.sources):
            return False
        if self.sources and not self.sites and not any(SOURCES[s][1](r) for s in self.sources):
            return False
        if on(self.not_sites) or any(SOURCES[s][1](r) for s in self.not_sources):
            return False
        if self.after or self.before:
            y = year(r)
            if y is None or (self.after and y <= self.after) or (self.before and y >= self.before):
                return False
        return True


def result_site(url: str) -> str:
    """The site of a result; an archive copy counts as its old site."""
    if m := WAYBACK.match(url):
        return _host(m[2] if "://" in m[2] else "http://" + m[2])
    return _host(url)


def year(r) -> int | None:
    """The year of a result: the page date (crawl, corpus), the archive copy, the paper or the catalog record."""
    if m := DATE_FIRST.match(r.snippet):
        return int(m[1])
    if m := WAYBACK.match(r.url):
        return int(m[1])
    from dzirkva import papers
    if (meta := papers.meta(r.url)) and (m := re.search(r"\d{4}", meta["year"] or "")):
        return int(m[0])
    if "iverieli" in r.engines and (m := re.search(r"\b(1[5-9]\d\d|20\d\d)\b", r.snippet.split(". ")[0])):
        return int(m[1])
    return None


def _lex(query: str) -> list[tuple[str, str, int, int]]:
    """(kind, text, start, end): word (with its prefix signs), phrase (prefix signs in front), slash, plus."""
    out, i = [], 0
    while i < len(query):
        c = query[i]
        if c.isspace():
            i += 1
        elif c in "/+":
            # a sign only between two words it touches: ბურჯანაძე / Burjanadze, 24/7, UTC+11 stay plain text
            before, after = query[i - 1:i], query[i + 1:i + 2]
            if before and after and not (before.isspace() or after.isspace() or before.isdigit() or after.isdigit()):
                out.append(("slash" if c == "/" else "plus", c, i, i + 1))
            i += 1
        elif c in OPEN:
            close = CLOSE[OPEN.index(c)]
            j = next((k for k in range(i + 1, len(query)) if query[k] in CLOSE), -1)
            end = j if j >= 0 else len(query)
            sign = ""
            if out and out[-1][0] == "word" and out[-1][3] == i and set(out[-1][1]) <= set(PREFIXES):
                sign = out.pop()[1]  # -"…", #"…"
            start = i - len(sign)
            if "@" in sign:  # @"თბილისის აეროპორტი": a site name of several words
                out.append(("word", sign + query[i + 1:end], start, min(end + 1, len(query))))
            else:
                out.append(("phrase", sign + "\x00" + query[i + 1:end], start, min(end + 1, len(query))))
            if j < 0:
                out.append(("open", c, i, i + 1))
            i = end + 1
        else:
            m = re.compile(r"[^\s/+" + re.escape(OPEN) + r"]+").match(query, i)
            out.append(("word", m[0], i, m.end()))
            i = m.end()
    return out


def _site_name(name: str) -> str | None:
    """@name → a host: a domain (tsu.ge) or a site name (ფეისბუქი, from sources.site_names)."""
    from dzirkva.sources import host, site_names
    low = name.lower()
    if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", low):
        return low.removeprefix("www.")
    url = site_names().get(low) or site_names().get(_word(low))
    return host(url) if url else None


def parse(query: str) -> Query:
    q = Query(query)
    if len(query.split()) > PASTE_WORDS:
        q.plain = [(m.start(), m.end(), m[0]) for m in re.finditer(r"\S+", query)]
        if re.search(r"[\"„“«/+~*#@<>!]|(^|\s)-\w", query):
            q.info = f"შეკითხვაში {PASTE_WORDS} სიტყვაზე მეტია: ჩასმულ ტექსტად წავიკითხეთ, ნიშნების გარეშე."
        return q
    items = _lex(query)
    if any(k == "open" for k, *_ in items):
        q.notes.append("ბრჭყალი არ დაიხურა: ფრაზად წავიკითხეთ ბოლომდე.")
    items = [x for x in items if x[0] != "open"]
    # groups: atoms joined by / or + (spaces around the sign do not matter)
    groups: list[list] = []
    joiner = None
    for kind, text, start, end in items:
        if kind in ("slash", "plus"):
            if not groups or joiner:
                q.notes.append(f"«{text}» ორ სიტყვას შორის უნდა იდგეს.")
                continue
            joiner = kind
            groups[-1][1].add(kind)
            groups[-1][3] = end
            continue
        if joiner and groups:
            groups[-1][0].append((kind, text))
            groups[-1][3] = end
        else:
            groups.append([[(kind, text)], set(), start, end])
        joiner = None
    if joiner:
        q.notes.append("«/» ან «+» ორ სიტყვას შორის უნდა იდგეს: ბოლოში დარჩა.")

    def without(start: int, end: int, put: str = "") -> str:
        return re.sub(r"\s+", " ", (query[:start] + put + query[end:])).strip()

    for parts, joins, start, end in groups:
        if len(joins) > 1:
            q.notes.append("ერთ ჯგუფში «/» და «+» ერთად არ მუშაობს: «+» წავიკითხეთ როგორც «/».")
        join = "near" if joins == {"plus"} else "or"
        atoms, minus, title, alts_of = [], False, False, None
        for i, (kind, text) in enumerate(parts):
            if kind == "phrase":
                sign, _, body = text.partition("\x00")
                words = [_word(w) for w in WORD.findall(body)]
                if i == 0:
                    minus, title = "-" in sign, "#" in sign
                if words:
                    atoms.append(Atom("phrase" if len(words) > 1 or sign != "!" else "exact", " ".join(words)))
                else:
                    q.notes.append("ცარიელი ბრჭყალები გამოვტოვეთ.")
                continue
            sign = re.match(r"[" + re.escape(PREFIXES) + r"]*", text)[0]
            body = text[len(sign):]
            if len(parts) == 1 and sign in ("@", "-@"):
                if not body:
                    q.notes.append("«@» მარტო დარჩა: დაწერეთ საიტი ან წყარო, მაგალითად @ვიკიპედია.")
                    break
                src = ALIASES.get(normalize(body), normalize(body))
                if src in SOURCES:
                    (q.not_sources if sign == "-@" else q.sources).append(src)
                    q.chips.append(Chip("site", sign, ("არა: " if sign == "-@" else "მხოლოდ: ") + SOURCES[src][0],
                                        without(start, end)))
                elif h := _site_name(body):
                    (q.not_sites if sign == "-@" else q.sites).append(h)
                    q.chips.append(Chip("site", sign, ("არა საიტზე: " if sign == "-@" else "მხოლოდ საიტზე: ") + h,
                                        without(start, end)))
                else:
                    q.notes.append(f"«@{body}» ვერ ვიცანით: დაწერეთ საიტის მისამართი (tsu.ge) ან წყარო: "
                                   + ", ".join("@" + s for s in SOURCES) + ".")
                break
            if len(parts) == 1 and YEAR.match(text):
                y = int(text[1:])
                if text[0] == ">":
                    q.after = y
                    q.chips.append(Chip("year", ">", f"{y + 1} წლიდან (თარიღის გარეშე შედეგები არ ჩანს)", without(start, end)))
                else:
                    q.before = y
                    q.chips.append(Chip("year", "<", f"{y - 1} წლამდე ჩათვლით (თარიღის გარეშე შედეგები არ ჩანს)",
                                        without(start, end)))
                break
            if set(sign) - set("-!~#") or len(sign) > 2:
                q.notes.append(f"«{text}»: ნიშანი «{sign}» აქ არ მუშაობს. სიტყვად წავიკითხეთ.")
                sign = ""
            if i == 0:
                minus, title = "-" in sign, "#" in sign
            elif "-" in sign or "#" in sign:
                q.notes.append(f"«{text}»: «-» და «#» ჯგუფის დასაწყისში იწერება, მთელ ჯგუფზე.")
            if sign == "-" and body[:1].isdigit():  # -5 გრადუსი: a number, not an excluded word
                sign, minus = "", False
            words = [_word(w) for w in WORD.findall(body)]
            if not words:  # a sign alone is punctuation (Nobody Else - The Movie), as before
                continue
            w = " ".join(words)
            if body.endswith("*"):
                if len(w) < PREFIX_MIN:
                    q.notes.append(f"«{text}»: ვარსკვლავამდე მინიმუმ {PREFIX_MIN} ასო უნდა იყოს. მთელ სიტყვად წავიკითხეთ.")
                    atoms.append(Atom("word", w))
                else:
                    atoms.append(Atom("prefix", w))
            elif "!" in sign:
                atoms.append(Atom("exact", w))
            elif "~" in sign:
                near = neighbours(w)
                if not near:
                    q.notes.append(f"«~{w}»: ახლო მნიშვნელობის სიტყვები არ ვიცით. მხოლოდ ეს სიტყვა ვეძებეთ.")
                atoms += [Atom("word", w)] + [Atom("word", n) for n in near]
                alts_of = (w, near) if near else None
            else:
                atoms.append(Atom("word", w))
        else:
            if not atoms:
                continue
            if join == "near" and len(atoms) < 2:
                join = "or"
            if len(atoms) == 1 and atoms[0].how == "word" and not (minus or title) and alts_of is None:
                q.plain.append((start, end, query[start:end]))  # as typed: the usual pipeline reads it
                continue
            text = query[start:end].strip()
            if minus:
                text = text.lstrip("-")
            term = Term(text, atoms, join, title, minus)
            term.alts = alts_of is not None
            (q.minus if minus else q.terms).append(term)
            q.chips.append(_chip(term, without(start, end), alts_of,
                                 lambda put: without(start, end, put)))
    # ~ never brings back what - takes away: ~ღვინო -ლუდი has no ლუდი
    gone = {f for t in q.minus for w in t.words for f in families(w) | {w}}
    for i, t in enumerate(q.terms):
        if getattr(t, "alts", False) and gone:
            keep = [a for a in t.atoms[1:] if not (families(a.text) | {a.text}) & gone]
            if len(keep) < len(t.atoms) - 1:
                q.terms[i] = nt = Term(str(t), [t.atoms[0], *keep], t.join, t.title, t.minus)
                nt.alts = True
                chip = next(c for c in q.chips if c.kind == "alt" and c.label.startswith(t.atoms[0].text + " "))
                chip.alts = [(w, without) for w, without in chip.alts if w in {a.text for a in keep}]
    if q.active and not (q.plain or q.terms):
        q.notes.append("საძიებო სიტყვა არ არის: დაწერეთ მინიმუმ ერთი სიტყვა ნიშნის გარეშე ან ფრაზა.")
    if q.active:  # plain words are chips too, so the whole reading is on the page
        q.chips[:0] = [Chip("word", "", f"{' '.join(map(_word, WORD.findall(w)))} · ყველა ფორმით", without(s, e))
                       for s, e, w in q.plain if WORD.search(w)]
    return q


def _chip(term: Term, without: str, alts_of, rewrite) -> Chip:
    """The chip of a term. ~ lists its words: removing one rewrites ~ღვინო as ღვინო/ჭაჭა/… without it."""
    if term.minus:
        return Chip("minus", "-", "არა: " + " / ".join(a.label() for a in term.atoms), without)
    if alts_of:
        w, near = alts_of
        chip = Chip("alt", "~", f"{w} ან მსგავსი:", without)
        chip.alts = [(n, rewrite("/".join([w] + [x for x in near if x != n]))) for n in near]
        return chip
    sign = "#" if term.title else ""
    if term.join == "near":
        return Chip("near", sign + "+", " + ".join(a.label() for a in term.atoms) + f" (≤{NEAR} სიტყვის მანძილზე)",
                    without)
    kind = "or" if len(term.atoms) > 1 else term.atoms[0].how
    label = " ან ".join(a.label() for a in term.atoms)
    return Chip(kind, sign + {"or": "/", "phrase": "„“", "exact": "!", "prefix": "*"}.get(kind, ""),
                ("სათაურში: " if term.title else "") + label, without)


# ---- the explainer (/tools) and the MCP description share this list ---------------

GUIDE = [  # (sign, name, rule, example)
    ('"…"', "ზუსტი ფრაზა", "ეს სიტყვები, ამ რიგით და ამ ფორმით. „…“ და «…» ბრჭყალებიც მუშაობს.", '"ილია ჭავჭავაძე"'),
    ("!", "მხოლოდ ეს ფორმა", "სიტყვის სხვა ფორმებს არ ეძებს: !ღვინოს პოულობს მხოლოდ „ღვინოს“, არა „ღვინო“ ან „ღვინის“.", "!ქართველთა"),
    ("/", "ერთ-ერთი", "აერთებს ერთ სიტყვას ან ფრაზას თითო მხარეს, ჰარის გარეშე. მეტი სიტყვისთვის "
                      "გამოიყენეთ ბრჭყალები.",
     "ტყემალი/ბაჟე"),
    ("+", "ახლოს", f"ორივე სიტყვა, ნებისმიერი ფორმით, ერთმანეთიდან მაქსიმუმ {NEAR} სიტყვის მანძილზე.", "ქვევრი+ღვინო"),
    ("~", "მსგავსი", f"სიტყვა ან მასთან მნიშვნელობით ახლო სიტყვა (მაქსიმუმ {ALT_MAX}, სიტყვების რუკიდან). "
                     "გვერდზე ჩანს, რომელი, და ზედმეტს × მოაშორებს.", "~მასწავლებელი"),
    ("*", "იწყება", f"ნებისმიერი სიტყვა, რომელიც ასე იწყება (მინიმუმ {PREFIX_MIN} ასო).", "ვეფხისტყაოს*"),
    ("-", "გარდა", "შედეგებში ეს სიტყვა არცერთი ფორმით არ არის. ფრაზაც შეიძლება: -\"…\".", "ჯაზი -ფესტივალი"),
    ("#", "სათაურში", "სიტყვა გვერდის სათაურში უნდა იყოს.", "#ლექსი ბარათაშვილი"),
    ("@", "საიტი ან წყარო", "მხოლოდ ეს საიტი (@tsu.ge, @ფეისბუქი) ან წყარო (@ვიკიპედია, @ხალხი …). "
                          "-@ ამ საიტს ან წყაროს აშორებს.", "ქუთაისი @ხალხი"),
    (">  <", "წელი", ">2015 ნიშნავს 2016 წლიდან, <2000 ნიშნავს 1999 წლამდე ჩათვლით. "
                     "თარიღის გარეშე შედეგები არ ჩანს.", "არჩევნები >2019"),
]
RULES = [
    "ნიშნის გარეშე სიტყვა სავალდებულოა და ნებისმიერი ფორმით მოიძებნება, როგორც ყოველთვის.",
    "«/» და «+» აერთებს ზუსტად ერთ სიტყვას ან ბრჭყალებში ჩასმულ ფრაზას თითო მხარეს და ორივეს უნდა ეხებოდეს, "
    "ჰარის გარეშე: ღვინო/ჭაჭა. ჰარით (ღვინო / ჭაჭა) ან ციფრებთან (24/7) ის ჩვეულებრივი სასვენი ნიშანია.",
    "ძველი ღვინო/ჭაჭა ნიშნავს: ძველი და (ღვინო ან ჭაჭა). „ძველი ღვინო“/ჭაჭა ნიშნავს: ფრაზა ან ჭაჭა.",
    "«/» და «+» ერთ ჯგუფში ერთად არ იწერება.",
    "«-» და «#» ჯგუფის დასაწყისში დგას და მთელ ჯგუფს ეხება: -ღვინო/ჭაჭა აშორებს ორივეს.",
    "«-», «@», «#» და წელი მხოლოდ აშორებს შედეგებს, ახალს არ ამატებს.",
    "მარტო მდგარი ნიშანი (ტირე ჰარებს შორის) და -5 მსგავსი რიცხვი ჩვეულებრივი ტექსტია.",
    f"{PASTE_WORDS} სიტყვაზე გრძელი შეკითხვა ჩასმული ტექსტია: მასში ნიშნები არ იკითხება, და ეს შედეგების ზემოთ ჩანს.",
    "რასაც ვერ წავიკითხავთ, წითლად ჩანს შედეგების ზემოთ. ჩუმად არაფერი იკარგება.",
]
SOURCE_HELP = "@" + ", @".join(SOURCES)


def mcp_help() -> str:
    """The search tools for the MCP description, in English for the AI client."""
    return (
        "Query tools (symbols only; no AND/OR words; every plain word is required and matches any form): "
        '"phrase" = exact words in order (also „…“); !word = this form only; a/b = a or b (/ joins one word or '
        '"phrase" on each side and must touch both, no spaces: "a / b" and 24/7 are plain text); a+b = both words '
        'within 10 words, any form; ~word = word or up to 5 words near in '
        "meaning; prefix* = words starting with prefix (3+ letters); -word or -\"phrase\" = exclude; #word = in the "
        "title; @domain.ge or @site-name = only that site, -@ = not that site; " + SOURCE_HELP + " = only that "
        "source kind; >2015 = dated 2016 or later, <2000 = dated 1999 or earlier (undated results are dropped). "
        f"Queries longer than {PASTE_WORDS} words are pasted text: no tools. "
        "The response field query_tools shows how each part was read and query_notes what could not be read.")
