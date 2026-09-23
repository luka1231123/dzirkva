"""Georgian text layer: normalize, Latin→Georgian, spelling. Grammar is in morph.py.

The engines (Google, Bing, Brave) match surface forms, so this module produces
real Georgian words that can go into query variants. Two word lists:
data/words.tsv (ka.wikipedia counts) measures how rare a word is; data/vocab.tsv (all Georgian text we have:
Wikipedia, own crawl, Leipzig web + news corpora, spelling lists) decides which forms people really write.
"""

import itertools
import re
import unicodedata
from functools import cache
from pathlib import Path

WORDS_FILE = Path(__file__).resolve().parents[2] / "data" / "words.tsv"
VOCAB_FILE = WORDS_FILE.with_name("vocab.tsv")

ALPHABET = "აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ"
GEORGIAN_WORD = re.compile(r"[ა-ჰ]+")

# Standard Georgian keyboard layout. AcadNusx and similar pre-Unicode fonts use the same table.
KEYBOARD = dict(zip("abcdefghijklmnopqrstuvwxyzCJRSTWZ", "აბცდეფგჰიჯკლმნოპქრსტუვწხყზჩჟღშთჭძ"))

# Informal Latin spelling ("tsqali", "chveni"). Several letters are ambiguous.
PHONETIC = {
    "sh": "შ", "ch": "ჩჭ", "ts": "ცწ", "tz": "ცწ", "dz": "ძ", "kh": "ხ", "gh": "ღ", "zh": "ჟ",
    "th": "თ", "ph": "ფ", "dj": "ჯ",
    "a": "ა", "b": "ბ", "c": "ცწჩ", "d": "დ", "e": "ე", "f": "ფ", "g": "გღ", "h": "ჰ", "i": "ი",
    "j": "ჯჟ", "k": "კქ", "l": "ლ", "m": "მ", "n": "ნ", "o": "ო", "p": "პფ", "q": "ქყ", "r": "რ",
    "s": "ს", "t": "ტთ", "u": "უ", "v": "ვ", "w": "წ", "x": "ხ", "y": "ყ", "z": "ზ",
}
MAX_CANDIDATES = 4096

# Letters Georgians mix up: each consonant series (voiced, aspirated, ejective: ბ ფ პ, დ თ ტ ...) sounds alike,
# word ends lose voicing (იაფად → იაფათ), and a few more pairs sound or look close.
CONFUSION: dict[str, str] = {}
for _group in ("ბფპ", "დთტ", "გქკ", "ძცწ", "ჯჩჭ", "ქყ", "კყ", "ძზ", "ღგ", "ხქ", "ზს"):
    for _a, _b in itertools.permutations(_group, 2):
        CONFUSION[_a] = CONFUSION.get(_a, "") + _b
MAX_CONFUSED = 2    # letters mixed up in one word (აპტიაქი → აფთიაქი)
# A word written KNOWN_COUNT times or more is a real word (სართული, ჩვენო, ბოლტი: never "fixed").
# A rarer word is fixed when a candidate is KEEP_RATIO times more frequent (after the typo weight):
# ტბილისი 23 vs თბილისი 85,040 is fixed.
KNOWN_COUNT = 50
KEEP_RATIO = 20

# Key position (row, column) of each letter; Shift letters (თ ჭ ღ შ ჟ ძ ჩ) share the key of their base letter.
KEY_POS = {KEYBOARD[c]: (r, i + (0, 0.25, 0.75)[r]) for r, row in enumerate(("qwertyuiop", "asdfghjkl", "zxcvbnm"))
           for i, c in enumerate(row)}
KEY_POS.update({KEYBOARD[c]: KEY_POS[KEYBOARD[c.lower()]] for c in "CJRSTWZ"})
# How probable a typo is, by kind (rough typing statistics: neighbor keys and missed Shift dominate).
TYPO = {"shift": 1.0, "confusion": 0.8, "neighbor": 0.6, "swap": 0.5, "double": 0.4, "drop": 0.3, "far": 0.1}


def _near(a: str, b: str) -> bool:
    (r1, c1), (r2, c2) = KEY_POS.get(a, (9, 99)), KEY_POS.get(b, (-9, -99))
    return abs(r1 - r2) <= 1 and abs(c1 - c2) <= 1.25


def typo_weight(typed: str, fixed: str) -> float:
    """How probable it is that someone who meant `fixed` typed `typed` (one edit), from the keyboard."""
    if len(typed) == len(fixed):
        diff = [i for i, (x, y) in enumerate(zip(typed, fixed)) if x != y]
        if len(diff) == 2 and typed[diff[0]] == fixed[diff[1]] and typed[diff[1]] == fixed[diff[0]]:
            return TYPO["swap"]
        if len(diff) <= MAX_CONFUSED and all(fixed[i] in CONFUSION.get(typed[i], "") for i in diff):
            return TYPO["confusion"] ** len(diff)
        if len(diff) != 1:
            return TYPO["far"]
        x, y = typed[diff[0]], fixed[diff[0]]
        if KEY_POS.get(x) == KEY_POS.get(y):
            return TYPO["shift"]
        return TYPO["confusion"] if y in CONFUSION.get(x, "") else TYPO["neighbor"] if _near(x, y) else TYPO["far"]
    if len(typed) == len(fixed) + 1:  # extra letter: the same key twice or a neighbor key pressed too
        i = next((i for i, (x, y) in enumerate(zip(typed, fixed)) if x != y), len(fixed))
        around = fixed[max(i - 1, 0): i + 1]
        return TYPO["double"] if any(typed[i] == c or _near(typed[i], c) for c in around) else TYPO["far"]
    return TYPO["drop"]


@cache
def words() -> dict[str, int]:
    """Georgian word form -> count in ka.wikipedia. Empty if the list is not built yet."""
    if not WORDS_FILE.exists():
        return {}
    with open(WORDS_FILE, encoding="utf-8") as f:
        return {w: int(n) for w, n in (line.rstrip("\n").split("\t") for line in f)}


def freq(word: str) -> int:
    return words().get(word, 0)


@cache
def vocab() -> dict[str, int]:
    """Georgian word form -> count in all our Georgian text (scripts/build_vocab.py). Falls back to words()."""
    if not VOCAB_FILE.exists():
        return words()
    with open(VOCAB_FILE, encoding="utf-8") as f:
        return {w: int(n) for w, n in (line.rstrip("\n").split("\t") for line in f)}


def count(word: str) -> int:
    return vocab().get(word, 0)


def normalize(text: str) -> str:
    """Unicode NFC, Mtavruli capitals -> normal letters, one space between words."""
    return " ".join(unicodedata.normalize("NFC", text).lower().split())


def georgian_ratio(text: str) -> float:
    """Share of letters in the text that are Georgian (0.0-1.0)."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum("Ⴀ" <= c <= "ჿ" or "Ა" <= c <= "Ჿ" for c in letters) / len(letters)


def from_keyboard(text: str) -> str:
    """Latin typed on the Georgian keyboard layout (or AcadNusx font text) -> Georgian."""
    return "".join(KEYBOARD.get(c, KEYBOARD.get(c.lower(), c)) if c.isascii() and c.isalpha() else c for c in text)


def _phonetic_options(word: str) -> list[str]:
    options, i = [], 0
    while i < len(word):
        if word[i:i + 2] in PHONETIC:
            options.append(PHONETIC[word[i:i + 2]])
            i += 2
        else:
            options.append(PHONETIC.get(word[i], word[i]))
            i += 1
    return options


def latin_to_georgian(word: str) -> str | None:
    """Best real Georgian word for one Latin word ("kari" -> "ქარი"), or None."""
    candidates = {from_keyboard(word)}
    options = _phonetic_options(word.lower())
    total = 1
    for o in options:
        total *= len(o)
    if total <= MAX_CANDIDATES:
        candidates.update("".join(p) for p in itertools.product(*options))
    best = max(candidates, key=count)
    return best if count(best) else None


def _edits(word: str) -> set[str]:
    splits = [(word[:i], word[i:]) for i in range(len(word) + 1)]
    return (
        {a + b[1:] for a, b in splits if b}
        | {a + b[1] + b[0] + b[2:] for a, b in splits if len(b) > 1}
        | {a + c + b[1:] for a, b in splits if b for c in ALPHABET}
        | {a + c + b for a, b in splits for c in ALPHABET}
    )


def _confused(word: str) -> set[str]:
    """The word with one or two letters swapped for their sound-alikes."""
    out, frontier = set(), {word}
    for _ in range(MAX_CONFUSED):
        frontier = {w[:i] + r + w[i + 1:] for w in frontier for i, c in enumerate(w) for r in CONFUSION.get(c, "")}
        out |= frontier
    return out - {word}


def spell_candidates(word: str, limit: int = 12) -> list[str]:
    """Likely intended words, best first; empty if the typed word is likely right.

    Rare words are checked too, not only unknown ones (ტბილისი is on the web). A candidate must be a plausible typo
    (sound-alike letters or a keyboard slip; no far keys: პხალი is ფხალი, never ახალი) and, after the typo
    weight, KEEP_RATIO times more frequent than the typed word.
    """
    if not GEORGIAN_WORD.fullmatch(word) or count(word) >= KNOWN_COUNT:
        return []
    floor = KEEP_RATIO * max(count(word), 1)
    score = {c: w for c in _confused(word) | _edits(word)
             if count(c) and (w := typo_weight(word, c) * count(c)) > floor and typo_weight(word, c) > TYPO["far"]}
    return sorted(score, key=score.get, reverse=True)[:limit]


def spell(word: str) -> str:
    """Fix a likely typo; keep the word otherwise."""
    return next(iter(spell_candidates(word)), word)


if __name__ == "__main__":
    import sys

    for w in sys.argv[1:]:
        if w.isascii():  # keep case: "T" = თ on the keyboard layout
            print(f"{w}: latin -> {latin_to_georgian(w)} | keyboard -> {from_keyboard(w)}")
        else:
            w = normalize(w)
            print(f"{w}: spell {spell(w)} | count {count(w)} | candidates {spell_candidates(w)[:5]}")
