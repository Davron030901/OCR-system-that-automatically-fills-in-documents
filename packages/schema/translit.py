"""Uzbek transliteration and text normalisation.

`normalize_apostrophes` is small but load-bearing. The official Uzbek Latin
character for Oʻ and Gʻ is U+02BB MODIFIER LETTER TURNED COMMA, but in
practice six different characters get typed interchangeably. Without
normalisation the same person's name is stored inconsistently, cross-checks
between MRZ and visual zone fail, and PDF/DOCX output shows missing glyphs.

Apply it to every extracted value and every value written into a document.
"""

from __future__ import annotations

APOSTROPHE_VARIANTS = (
    "\u0027",  # ' APOSTROPHE
    "\u2018",  # ' LEFT SINGLE QUOTATION MARK
    "\u2019",  # ' RIGHT SINGLE QUOTATION MARK
    "\u0060",  # ` GRAVE ACCENT
    "\u00B4",  # ´ ACUTE ACCENT
    "\u02BC",  # ʼ MODIFIER LETTER APOSTROPHE
    "\u2032",  # ′ PRIME
)

TURNED_COMMA = "\u02BB"  # ʻ — the official character

_APOSTROPHE_MAP = {ord(c): TURNED_COMMA for c in APOSTROPHE_VARIANTS}


def normalize_apostrophes(text: str | None) -> str | None:
    """Normalise every apostrophe-like character to U+02BB."""
    if text is None:
        return None
    return text.translate(_APOSTROPHE_MAP)


# --------------------------------------------------------------------------
# Latin -> Cyrillic
# --------------------------------------------------------------------------
# Order matters: multi-character sequences must be tried before single ones.

_LAT2CYR_MULTI = [
    ("SHCH", "Щ"), ("Shch", "Щ"), ("shch", "щ"),
    ("O" + TURNED_COMMA, "Ў"), ("o" + TURNED_COMMA, "ў"),
    ("G" + TURNED_COMMA, "Ғ"), ("g" + TURNED_COMMA, "ғ"),
    ("CH", "Ч"), ("Ch", "Ч"), ("ch", "ч"),
    ("SH", "Ш"), ("Sh", "Ш"), ("sh", "ш"),
    ("YO", "Ё"), ("Yo", "Ё"), ("yo", "ё"),
    ("YU", "Ю"), ("Yu", "Ю"), ("yu", "ю"),
    ("YA", "Я"), ("Ya", "Я"), ("ya", "я"),
    ("TS", "Ц"), ("Ts", "Ц"), ("ts", "ц"),
    ("NG", "НГ"), ("Ng", "Нг"), ("ng", "нг"),
]

_LAT2CYR_SINGLE = {
    "A": "А", "B": "Б", "D": "Д", "E": "Е", "F": "Ф", "G": "Г", "H": "Ҳ",
    "I": "И", "J": "Ж", "K": "К", "L": "Л", "M": "М", "N": "Н", "O": "О",
    "P": "П", "Q": "Қ", "R": "Р", "S": "С", "T": "Т", "U": "У", "V": "В",
    "X": "Х", "Y": "Й", "Z": "З",
    "a": "а", "b": "б", "d": "д", "e": "е", "f": "ф", "g": "г", "h": "ҳ",
    "i": "и", "j": "ж", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о",
    "p": "п", "q": "қ", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в",
    "x": "х", "y": "й", "z": "з",
    "'": "ъ", TURNED_COMMA: "ъ",
}

# --------------------------------------------------------------------------
# Cyrillic -> Latin
# --------------------------------------------------------------------------

_CYR2LAT = {
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Ғ": "G" + TURNED_COMMA,
    "Д": "D", "Е": "E", "Ё": "Yo", "Ж": "J", "З": "Z", "И": "I", "Й": "Y",
    "К": "K", "Қ": "Q", "Л": "L", "М": "M", "Н": "N", "О": "O",
    "Ў": "O" + TURNED_COMMA, "П": "P", "Р": "R", "С": "S", "Т": "T",
    "У": "U", "Ф": "F", "Х": "X", "Ҳ": "H", "Ц": "Ts", "Ч": "Ch",
    "Ш": "Sh", "Щ": "Shch", "Ъ": TURNED_COMMA, "Ь": "", "Э": "E",
    "Ю": "Yu", "Я": "Ya", "Ы": "I",
    "а": "a", "б": "b", "в": "v", "г": "g", "ғ": "g" + TURNED_COMMA,
    "д": "d", "е": "e", "ё": "yo", "ж": "j", "з": "z", "и": "i", "й": "y",
    "к": "k", "қ": "q", "л": "l", "м": "m", "н": "n", "о": "o",
    "ў": "o" + TURNED_COMMA, "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "x", "ҳ": "h", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch", "ъ": TURNED_COMMA, "ь": "", "э": "e",
    "ю": "yu", "я": "ya", "ы": "i",
}


def latin_to_cyrillic(text: str | None) -> str | None:
    if text is None:
        return None
    s = normalize_apostrophes(text) or ""
    for lat, cyr in _LAT2CYR_MULTI:
        s = s.replace(lat, cyr)
    return "".join(_LAT2CYR_SINGLE.get(ch, ch) for ch in s)


def cyrillic_to_latin(text: str | None) -> str | None:
    if text is None:
        return None
    return "".join(_CYR2LAT.get(ch, ch) for ch in text)


def mrz_to_display(name: str | None) -> str | None:
    """Convert an MRZ name field ('ALIYEV<<SHOHRUH<AKMAL') to display form."""
    if name is None:
        return None
    return " ".join(part for part in name.replace("<", " ").split() if part)


def to_mrz_name(surname: str, given: str = "", patronymic: str = "") -> str:
    """Build the MRZ name field. Non-ASCII is folded, spaces become filler."""
    def fold(s: str) -> str:
        s = (cyrillic_to_latin(s) or s).upper()
        s = s.replace(TURNED_COMMA, "").replace("'", "")
        return "".join(ch if ch.isalnum() else "<" for ch in s)

    givens = "<".join(f for f in (fold(given), fold(patronymic)) if f)
    return f"{fold(surname)}<<{givens}" if givens else fold(surname)


def title_case_name(text: str | None) -> str | None:
    """Title-case a name without breaking Oʻ / Gʻ digraphs."""
    if text is None:
        return None
    s = normalize_apostrophes(text) or ""
    out = []
    for word in s.split():
        if not word:
            continue
        out.append(word[0].upper() + word[1:].lower())
    return " ".join(out)
