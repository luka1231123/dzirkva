"""Georgian morphology: word form -> lemma(s) and word family.

1. Lexicon (Wiktionary, data/lexicon.tsv): exact forms, suppletive verbs (ნახა -> ხედავს).
2. Noun/adjective rules: 7 cases, full and short forms, postpositions fused to the case,
   modern (-ებ-) and archaic (-ნ-, -თ-) plural, vowel truncation (დედა -> დედის),
   syncope (წყალი -> წყლის), superlative (უდიდესი -> დიდი), particles (-ც, -ვე, -ღა, -ა, -ო).
3. Verb/participle rules: slots preverb - person - version - ROOT - thematic/passive - ending.
   The root is accepted only if it leads to a known word of a verb family (რბენა, გაქცევა).

A rule result is kept only if the lexicon or the ka.wikipedia word list confirms it.
Search uses `families()` for matching and `lemmas()` for query variants.
"""

from dataclasses import dataclass
from functools import cache
from pathlib import Path

from dzirkva.georgian import freq

LEXICON_FILE = Path(__file__).resolve().parents[2] / "data" / "lexicon.tsv"
SYNONYMS_FILE = LEXICON_FILE.with_name("synonyms.tsv")
VOWELS = set("აეიოუ")
SYNCOPE_BEFORE = set("ლრნმვ")  # წყალ-ი -> წყლ-ის, ფანჯარ-ა -> ფანჯრ-ის, სოფელ-ი -> სოფლ-ის

# ---- nouns and adjectives -------------------------------------------------
# Stem kinds: C = consonant stem (lemma stem+ი), V = vowel stem (lemma = stem),
# T = truncated ა/ე stem (lemma stem+ა or stem+ე). After plural -ებ- any kind is possible.
_C_CASES = ["ი", "მა", "ს", "სა", "ის", "ისა", "ით", "ითა", "ად", "ადა", "ო"]
_GEN_POST = ["თვის", "ათვის", "ებრ", "კენ", "აკენ", "გან", "აგან", "ადმი", "თან"]
_C_POST = ["ში", "ზე", "თან", "ივით", "იდან", "ითურთ", "ამდე", "ადმდე"] + ["ის" + p for p in _GEN_POST]
_V_FORMS = ["", "მ", "ს", "სა", "დ", "ვ", "თი", "თა", "ში", "ზე", "სთან", "ვით", "სავით", "მდე", "დმდე",
            "დან", "თურთ"] + ["ს" + p for p in _GEN_POST]
_T_FORMS = ["ის", "ისა", "ით", "ითა", "იდან", "ითურთ"] + ["ის" + p for p in _GEN_POST]
_ARCHAIC_PL = ["ნი", "ნო", "თა", "თ", "თათვის", "თაგან"]

NOUN_ENDINGS: list[tuple[str, str]] = sorted(
    {(e, "C") for e in _C_CASES + _C_POST}
    | {(e, "V") for e in _V_FORMS}
    | {(e, "T") for e in _T_FORMS}
    | {("ებ" + e, "CTV") for e in _C_CASES + _C_POST}
    | {(e, "CV") for e in _ARCHAIC_PL},
    key=lambda x: -len(x[0]),
)
PARTICLES = ("მეთქი", "ვე", "ღა", "ც", "ა", "ო")

# ---- verbs and participles ------------------------------------------------
PREVERBS = ("გადმო", "ჩამო", "შემო", "გამო", "წამო", "ამო", "მიმო", "გადა", "წარ", "უკუ",
            "მი", "მო", "ჩა", "შე", "გა", "და", "წა", "ა", "")
PERSON = ("გვ", "ვ", "მ", "გ", "ჰ", "ს", "")
VERSION = ("ა", "ი", "უ", "ე", "")
PARTICIPLE_PREFIX = ("მა", "მე", "მო", "მ", "ნა", "სა", "უ", "")
# Endings after the root: thematic suffix / passive / imperfective marker / screeve + person.
_THEMATIC = ["ებ", "ობ", "ავ", "ამ", "ემ", "ოფ", "ევ", "ინებ", "ებინ", "ევინ", "ინ", "ი", "ენ", "დ", "ებოდ",
             "ოდ", "დებ", "ებულ", "ილ", "ულ", "არ", "ალ", ""]
_ENDINGS = ["ს", "თ", "ა", "ო", "ე", "ი", "ენ", "ან", "ნ", "ეს", "ნენ", "და", "დი", "დე", "დეს", "დნენ",
            "დით", "დეთ", "ით", "ეთ", "ათ", "ოთ", "ულა", "ულ", "ია", "ილა", "ოდა", "ოდე", "ოდნენ",
            "ელი", "ალი", "ული", "ილი", "არი", "ელ", "ალ", "ულ", "ილ", "ი", "ოს", "ონ", "ოთ",
            # perfect / pluperfect: participle + auxiliary (გათეთრებულ-ხარ, გამხმარ-იყოთ)
            "ხარ", "ვარ", "ვართ", "ხართ", "არიან", "იყო", "იყოს", "იყოთ", "იყავი", "იყავით", "იყვნენ",
            "ივარ", "ივართ", "იხარ", "იხართ", "ია", "იათ", "ოდეთ", "ოდით", "ოდი", ""]
VERB_ENDINGS = sorted({t + e for t in _THEMATIC for e in _ENDINGS}, key=len, reverse=True)
# Verbal noun (masdar) and 3rd person present shapes built from a root.
MASDAR = ("ა", "ება", "ობა", "ვა", "ენა", "ომა", "ოლა", "ევა", "ოდა", "ილი")
PRESENT = ("ს", "ებს", "ობს", "ავს", "ამს", "ის", "ება", "ობა", "ის")
MIN_ROOT = 2
MIN_VERB_FREQ = 5  # verb forms not in Wiktionary are accepted from the word list above this count
PARTICIPLE_SHAPE = (("მ", "მა", "მე", "მო", "ნა", "სა"), ("ელი", "ალი", "არი", "ული", "ილი", "ე"))


@dataclass(frozen=True)
class Analysis:
    lemma: str
    pos: str
    family: str
    source: str  # "lexicon" | "noun-rule" | "verb-rule" | "unknown"


@cache
def _lexicon() -> tuple[dict[str, list[tuple[str, str]]], dict[str, str], frozenset[str], frozenset[str]]:
    """form -> [(lemma, pos)], lemma -> family, all words in verb families, verb family ids."""
    forms: dict[str, list[tuple[str, str]]] = {}
    family: dict[str, str] = {}
    verb_words: set[str] = set()
    verb_fams: set[str] = set()
    if LEXICON_FILE.exists():
        with open(LEXICON_FILE, encoding="utf-8") as f:
            for line in f:
                form, lemma, pos, fam = line.rstrip("\n").split("\t")
                forms.setdefault(form, []).append((lemma, pos))
                family[lemma] = fam
        verb_fams = {family[l] for fs in forms.values() for l, p in fs if p == "verb"}
        verb_words = {w for w, fs in forms.items() if any(family[l] in verb_fams for l, _ in fs)}
    return forms, family, frozenset(verb_words), frozenset(verb_fams)


def _verb_analysis(word: str) -> Analysis:
    """Analysis for a verb-family word found by the verb rules (prefer its own verb entry)."""
    forms, family, _, verb_fams = _lexicon()
    pairs = [lp for lp in forms.get(word, []) if family.get(lp[0]) in verb_fams] or [(word, "verb")]
    lemma, pos = min(pairs, key=lambda lp: (lp[0] != word, lp[1] != "verb"))
    return Analysis(lemma, pos, family.get(lemma, lemma), "verb-rule")


def _known(word: str) -> int:
    """2 = lexicon lemma, 1 = only in the word list, 0 = unknown."""
    return 2 if word in _lexicon()[1] else 1 if freq(word) else 0


def _strip_particles(word: str) -> list[str]:
    out = [word]
    for p in PARTICLES:
        if word.endswith(p) and len(word) - len(p) >= MIN_ROOT:
            rest = word[: -len(p)]
            out.append(rest)
            out += [rest[: -len(q)] for q in PARTICLES if rest.endswith(q) and len(rest) - len(q) >= MIN_ROOT]
    return out


def _unsyncopate(stem: str) -> list[str]:
    """წყლ -> წყალ, წყელ, წყოლ (only if the last letter allows syncope)."""
    if len(stem) >= 3 and stem[-1] in SYNCOPE_BEFORE and stem[-2] not in VOWELS:
        return [stem[:-1] + v + stem[-1] for v in "აეო"]
    return []


def _stem_lemmas(stem: str, kind: str, stems: list[str]) -> set[str]:
    out: set[str] = set()
    if "C" in kind and stem[-1] not in VOWELS:
        out.update(s + "ი" for s in stems)
        out.update(s for s in stems if _known(s) == 2)  # words without -ი: ხვალ, დღეს
    if "T" in kind:
        out.update(s + v for s in stems for v in "აე")
    if "V" in kind and stem[-1] in VOWELS:
        out.add(stem)
    return out


def _noun_lemmas(word: str) -> list[str]:
    candidates: set[str] = set()
    for w in _strip_particles(word):
        for ending, kind in NOUN_ENDINGS:
            if not w.endswith(ending):
                continue
            stem = w[: len(w) - len(ending)]
            if not stem:
                continue
            found = _stem_lemmas(stem, kind, [stem])
            # Put a lost vowel back only if the plain stem gives no lexicon word (სახლ ≠ სახელ).
            if not any(_known(c) == 2 for c in found):
                found |= _stem_lemmas(stem, kind, _unsyncopate(stem))
            if len(stem) < MIN_ROOT:  # one-letter stems (ჭა, ხე): lexicon words only
                found = {c for c in found if _known(c) == 2}
            candidates.update(found)
    # Superlative უ-X-ეს-ი -> X-ი (უდიდესი -> დიდი, უმაღლესი -> მაღალი).
    for c in list(candidates):
        if c.startswith("უ") and c.endswith("ესი") and len(c) > 5:
            root = c[1:-3]
            candidates.update(s + "ი" for s in [root] + _unsyncopate(root))
    return [c for c in candidates if _known(c)]


def _verb_lemmas(word: str) -> list[str]:
    """Roots from slot stripping -> known words of verb families, best first."""
    verb_words = _lexicon()[2]
    w0 = word[:-1] if word.endswith("ო") and len(word) > 4 else word  # quotative -ო
    hits: dict[str, tuple[int, int]] = {}
    for w in {word, w0}:
        for pv in PREVERBS:
            if not w.startswith(pv):
                continue
            rest1 = w[len(pv):]
            for pre in PERSON + PARTICIPLE_PREFIX:
                if not rest1.startswith(pre):
                    continue
                rest2 = rest1[len(pre):]
                for ver in VERSION:
                    if not rest2.startswith(ver):
                        continue
                    rest3 = rest2[len(ver):]
                    for end in VERB_ENDINGS:
                        if not rest3.endswith(end) or len(rest3) - len(end) < MIN_ROOT:
                            continue
                        root = rest3[: len(rest3) - len(end)]
                        for r in [root] + _unsyncopate(root):
                            shapes = ([r + m for m in MASDAR] + ["სი" + r + "ილი", "სი" + r + "ულე"]
                                      + [v + r + p for v in VERSION for p in PRESENT])
                            for s in shapes:
                                for cand in (pv + s, s) if pv else (s,):
                                    tier = 2 if cand in verb_words else 1 if freq(cand) >= MIN_VERB_FREQ else 0
                                    if tier:
                                        score = (tier, len(r) + (2 if pv and cand.startswith(pv) else 0), freq(cand))
                                        hits[cand] = max(hits.get(cand, score), score)
    best = max((h[0] for h in hits.values()), default=0)
    return sorted((c for c in hits if hits[c][0] == best), key=lambda c: hits[c], reverse=True)


@cache
def analyze(word: str, exact: bool = True) -> tuple[Analysis, ...]:
    """All analyses for one normalized Georgian word, best first.

    exact=False skips the exact form lookup (to measure the rules alone).
    """
    forms, family, _, _ = _lexicon()
    out: list[Analysis] = []
    if exact and word in forms:
        pairs = sorted(set(forms[word]), key=lambda lp: (lp[0] == word and len(forms[word]) > 1, -freq(lp[0])))
        out = [Analysis(l, p, family.get(l, l), "lexicon") for l, p in pairs]
        # Participles (მწერალი, მასწავლებელი) are often separate entries: link them to their verb.
        if word.startswith(PARTICIPLE_SHAPE[0]) and word.endswith(PARTICIPLE_SHAPE[1]):
            out += [_verb_analysis(v) for v in _verb_lemmas(word)[:2] if v in forms]
        return tuple(out)
    for lemma in sorted(_noun_lemmas(word), key=lambda c: (-_known(c), -freq(c))):
        out.append(Analysis(lemma, "noun", family.get(lemma, lemma), "noun-rule"))
    if not out or _known(out[0].lemma) < 2:
        out += [_verb_analysis(v) for v in _verb_lemmas(word)[:3]]
    if freq(word) and all(a.lemma != word for a in out):
        # A real word form the rules could not reduce: keep it as its own lemma.
        out.append(Analysis(word, "?", word, "unknown"))
    # Lexicon-confirmed lemmas first, then by frequency; drop repeats of the same family.
    out.sort(key=lambda a: (-_known(a.lemma), -freq(a.lemma)))
    seen: set[str] = set()
    out = [a for a in out if not (a.family in seen or seen.add(a.family))]
    return tuple(out) or (Analysis(word, "?", word, "unknown"),)


@cache
def _members() -> dict[str, list[str]]:
    """family -> its lemmas, most frequent first."""
    out: dict[str, list[str]] = {}
    for lemma, fam in _lexicon()[1].items():
        out.setdefault(fam, []).append(lemma)
    return {f: sorted(ls, key=freq, reverse=True) for f, ls in out.items()}


@cache
def _forms_by_lemma() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for form, pairs in _lexicon()[0].items():
        for lemma, _ in pairs:
            out.setdefault(lemma, []).append(form)
    return out


def forms_of(lemma: str) -> list[str]:
    """All known inflected forms of a lemma (from Wiktionary tables)."""
    return _forms_by_lemma().get(lemma, [])


def family_members(family: str) -> list[str]:
    return _members().get(family, [])


@cache
def _synonyms() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    if SYNONYMS_FILE.exists():
        with open(SYNONYMS_FILE, encoding="utf-8") as f:
            for line in f:
                a, b = line.rstrip("\n").split("\t")
                out.setdefault(a, []).append(b)
    return out


def synonyms(lemma: str) -> list[str]:
    """Wiktionary synonyms of a lemma, most frequent first."""
    return sorted(_synonyms().get(lemma, []), key=freq, reverse=True)


def lemmas(word: str) -> list[str]:
    return [a.lemma for a in analyze(word)]


def families(word: str) -> set[str]:
    return {a.family for a in analyze(word)}


def same_family(a: str, b: str) -> bool:
    return bool(families(a) & families(b))


if __name__ == "__main__":
    import sys

    from dzirkva.georgian import normalize

    for raw in sys.argv[1:]:
        w = normalize(raw)
        print(w)
        for a in analyze(w)[:4]:
            print(f"   {a.lemma:<16} {a.pos:<6} family={a.family:<14} {a.source}")
