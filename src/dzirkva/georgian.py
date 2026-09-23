"""Georgian text layer: normalize, Latin→Georgian, spelling. Grammar is in morph.py.

The engines (Google, Bing, Brave) match surface forms, so this module produces
real Georgian words that can go into query variants. The word list from
ka.wikipedia (data/words.tsv) decides which generated forms are real.
"""

import itertools
import re
import unicodedata
from functools import cache
from pathlib import Path

WORDS_FILE = Path(__file__).resolve().parents[2] / "data" / "words.tsv"

ALPHABET = "აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ"
GEORGIAN_WORD = re.compile(r"[ა-ჰ]+")

# Standard Georgian keyboard layout. AcadNusx and similar pre-Unicode fonts use the same table.
KEYBOARD = dict(zip("abcdefghijklmnopqrstuvwxyzCJRSTWZ", "აბცდეფგჰიჯკლმნოპქრსტუვწხყზჩჟღშთჭძ"))

# Informal Latin spelling ("tsqali", "chveni"). Several letters are ambiguous.
PHONETIC = {
    "sh": "შ", "ch": "ჩჭ", "ts": "ცწ", "tz": "ცწ", "dz": "ძ", "kh": "ხ", "gh": "ღ", "zh": "ჟ",
    "th": "თ", "ph": "ფ", "dj": "ჯ",
    "a": "ა", "b": "ბ", "c": "ცწჩ", "d": "დ", "e": "ე", "f": "ფ", "g": "გ", "h": "ჰ", "i": "ი",
    "j": "ჯჟ", "k": "კქ", "l": "ლ", "m": "მ", "n": "ნ", "o": "ო", "p": "პფ", "q": "ქყ", "r": "რ",
    "s": "ს", "t": "ტთ", "u": "უ", "v": "ვ", "w": "წ", "x": "ხ", "y": "ყ", "z": "ზ",
}
MAX_CANDIDATES = 4096

# Letters Georgians often mix up when typing.
CONFUSION: dict[str, str] = {}
for _a, _b in ("თტ", "ქკ", "ქყ", "კყ", "ცწ", "ჩჭ", "ფპ", "ძზ", "ღგ", "ხქ"):
    CONFUSION[_a] = CONFUSION.get(_a, "") + _b
    CONFUSION[_b] = CONFUSION.get(_b, "") + _a

@cache
def words() -> dict[str, int]:
    """Georgian word form -> count in ka.wikipedia. Empty if the list is not built yet."""
    if not WORDS_FILE.exists():
        return {}
    with open(WORDS_FILE, encoding="utf-8") as f:
        return {w: int(n) for w, n in (line.rstrip("\n").split("\t") for line in f)}


def freq(word: str) -> int:
    return words().get(word, 0)


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
    best = max(candidates, key=freq)
    return best if freq(best) else None


def _edits(word: str) -> set[str]:
    splits = [(word[:i], word[i:]) for i in range(len(word) + 1)]
    return (
        {a + b[1:] for a, b in splits if b}
        | {a + b[1] + b[0] + b[2:] for a, b in splits if len(b) > 1}
        | {a + c + b[1:] for a, b in splits if b for c in ALPHABET}
        | {a + c + b for a, b in splits for c in ALPHABET}
    )


def spell_candidates(word: str, limit: int = 12) -> list[str]:
    """Known words one edit away (typing mix-ups first), most frequent first. Empty if the word is known."""
    if freq(word) or not GEORGIAN_WORD.fullmatch(word):
        return []
    confused = {word[:i] + r + word[i + 1:] for i, c in enumerate(word) for r in CONFUSION.get(c, "")}
    known = sorted({w for w in confused | _edits(word) if freq(w)}, key=lambda w: (w not in confused, -freq(w)))
    return known[:limit]


def spell(word: str) -> str:
    """Fix a typo if the word is unknown. Mix-ups from CONFUSION rank first."""
    if freq(word) or not GEORGIAN_WORD.fullmatch(word):
        return word
    confused = {word[:i] + r + word[i + 1:] for i, c in enumerate(word) for r in CONFUSION.get(c, "")}
    for group in (confused, _edits(word)):
        known = [w for w in group if freq(w)]
        if known:
            return max(known, key=freq)
    return word


if __name__ == "__main__":
    import sys

    for w in sys.argv[1:]:
        if w.isascii():  # keep case: "T" = თ on the keyboard layout
            print(f"{w}: latin -> {latin_to_georgian(w)} | keyboard -> {from_keyboard(w)}")
        else:
            w = normalize(w)
            print(f"{w}: spell {spell(w)} | freq {freq(w)}")
