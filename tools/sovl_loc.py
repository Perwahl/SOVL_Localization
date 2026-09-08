"""Shared helpers for the SOVL localization tools.

Both `lint.py` and `seed_glossary.py` build on this so that "what counts as a
translation file", "what counts as an occurrence of a glossary term", and the
rules for reading the JSON are defined in exactly one place.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Directories that live beside the language folders and are not languages.
NON_LANGUAGE_DIRS = {"glossary", "tools", "node_modules"}

#: Rich text tags: <b>, </color>, <size=133%>, <sprite=0>, <indent=33%> ...
TAG_RE = re.compile(r"<[^<>]*>")

#: Characters that mean the file was written through a non-UTF-8 codepage.
REPLACEMENT_RE = re.compile(r"�")

#: Classic UTF-8-read-as-cp1252 mojibake.
MOJIBAKE_RE = re.compile(r"Ã[-¿]|â€[-¿]?|Ð[-¿]")


class TranslationFile:
    """One `<Store>_<Language>.json` file."""

    def __init__(self, path: Path):
        self.path = path
        self.language = path.stem.rsplit("_", 1)[-1]
        self.store = path.stem.rsplit("_", 1)[0]
        self.raw_bytes = path.read_bytes()
        self.error: str | None = None
        self.context: str = ""
        self.rows: list[dict] = []
        self._row_lines: list[int] | None = None
        try:
            data = json.loads(self.raw_bytes.decode("utf-8-sig"))
        except UnicodeDecodeError as exc:
            self.error = f"not valid UTF-8: {exc}"
            return
        except json.JSONDecodeError as exc:
            self.error = f"not valid JSON: {exc}"
            return
        if isinstance(data, list):
            # Legacy shape: a bare array of rows.
            self.rows = [row for row in data if isinstance(row, dict)]
            return
        if not isinstance(data, dict):
            self.error = f"expected an object at the top level, found {type(data).__name__}"
            return
        self.context = data.get("TranslationContext") or ""
        translations = data.get("Translations")
        if not isinstance(translations, list):
            self.error = "missing a 'Translations' array"
            return
        self.rows = translations

    @property
    def has_bom(self) -> bool:
        return self.raw_bytes.startswith(b"\xef\xbb\xbf")

    def line_of_row(self, index: int) -> int | None:
        """1-based line number of a row, so CI annotations land where the problem is.

        Rows are objects in a pretty-printed array, so the nth `"Key"` line is the nth
        row. Rows missing a Key are exactly the ones we most want to point at, so fall
        back to counting opening braces at the row indent when the count comes up short.
        """
        if self._row_lines is None:
            text = self.raw_bytes.decode("utf-8-sig", errors="replace")
            key_lines, brace_lines = [], []
            in_translations = False
            for number, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if not in_translations:
                    if stripped.startswith('"Translations"'):
                        in_translations = True
                    continue
                if stripped.startswith("{"):
                    brace_lines.append(number)
                elif stripped.startswith('"Key"'):
                    key_lines.append(number)
            self._row_lines = brace_lines if len(brace_lines) >= len(key_lines) else key_lines
        if 0 <= index < len(self._row_lines):
            return self._row_lines[index]
        return None

    def pairs(self):
        """Yield (index, key, translation, comment) for every well-formed row."""
        for index, row in enumerate(self.rows):
            if not isinstance(row, dict) or "Key" not in row:
                continue
            yield (
                index,
                row.get("Key") or "",
                row.get("Translation") or "",
                row.get("Comment") or "",
            )


def language_dirs(repo: Path = REPO) -> list[Path]:
    """Every language folder in the repository, in a stable order."""
    return sorted(
        d
        for d in repo.iterdir()
        if d.is_dir()
        and not d.name.startswith((".", "_"))
        and d.name not in NON_LANGUAGE_DIRS
        and any(d.glob("*.json"))
    )


def store_files(lang_dir: Path, include_glossary: bool = False) -> list[Path]:
    """Translation files for a language. The glossary is excluded by default:
    it is the source of the terminology rules, not something to check against them."""
    return sorted(
        p
        for p in lang_dir.glob("*.json")
        if include_glossary or not p.name.startswith("Glossary_")
    )


def load_json(path: Path):
    with path.open(encoding="utf-8-sig") as handle:
        return json.load(handle)


def load_terms(repo: Path = REPO) -> list[dict]:
    return load_json(repo / "glossary" / "terms.json")["terms"]


def surface_forms(term: dict) -> list[str]:
    """Every English spelling that means this term."""
    return [term["en"], *term.get("aliases", [])]


def term_pattern(surface: str, ignore_case: bool = True) -> re.Pattern:
    """Match a term as a whole word, tolerating any run of whitespace between its
    words so that 'Damage  Save' and 'Damage\\nSave' still count as occurrences.

    English rules terms are capitalised in this project, so matching case-sensitively
    is what separates the Power stat from the word 'power' in flavour text."""
    body = r"\s+".join(re.escape(word) for word in surface.split())
    flags = re.IGNORECASE if ignore_case else 0
    return re.compile(rf"(?<![0-9A-Za-z]){body}(?![0-9A-Za-z])", flags)


def stem(word: str) -> str:
    """A crude prefix stem, enough to see past inflection and compounding.

    German 'Angriff' should count as a use of 'Angriffe', and 'Angriffswurf' as a
    compound built on it. Scripts without inflection (Chinese, Japanese) are short
    enough to be returned unchanged, where plain substring matching already works."""
    return word if len(word) <= 4 else word[: max(4, len(word) - 2)]


def _content_stems(text: str) -> set[str]:
    """Short stems of the words that carry meaning, for comparing two renderings."""
    return {w[:5] for w in re.findall(r"\w+", text.casefold()) if len(w) >= 4}


def same_term(a: str, b: str) -> bool:
    """Are these two renderings the same word rather than a real disagreement?

    An English term and its plural, or a button label and the verb form used mid
    sentence, produce different strings meaning the same thing: Hechizo/Hechizos,
    Aktivieren/aktiviert, Activer/s'active, Armure Lourde/Armure lourde. Reporting
    those as conflicts buries the handful of places where translators genuinely
    reached for different words.

    Inflection is a prefix change in the languages here, so comparing short stems
    catches it. Chinese and Japanese do not inflect and their terms are shorter than
    the stem window, so containment does the same job there: 行動 inside が行動します
    is one term, while スペル and 呪文 are two real choices."""
    a, b = a.strip().casefold(), b.strip().casefold()
    if a == b:
        return True
    if a and b and (a in b or b in a):
        return True
    stems = _content_stems(a)
    return bool(stems) and stems == _content_stems(b)


def approved_term_present(approved: str, translation: str) -> bool:
    """Is the approved rendering used in this translation, allowing for inflection?"""
    haystack = translation.casefold()
    if len(approved) <= 3:
        # Short terms like 'XP' or 'D6' would match inside unrelated words, so require
        # a whole-word hit and do not try to stem them. Only for terms left in Latin
        # script: a two-character Chinese term sits directly against its neighbours
        # ("+1攻击"), where a word boundary would never match.
        if approved.isascii():
            return bool(term_pattern(approved).search(translation))
        return approved.casefold() in haystack
    if approved.casefold() in haystack:
        return True
    words = [w for w in re.findall(r"\w+", approved) if len(w) > 2]
    return bool(words) and all(stem(w).casefold() in haystack for w in words)


#: A key with sentence punctuation, or one that carries a numeric modifier such as
#: "+1 Ranged Attack", is rules prose and its terminology is worth checking. A short
#: key without either is almost always a proper name - a unit, an ability, a level-up
#: title - where a rules word is part of the name rather than a use of the term.
_PROSE_PUNCTUATION = frozenset(".,:;()")


def is_rules_prose(key: str) -> bool:
    if any(ch in _PROSE_PUNCTUATION for ch in key):
        return True
    if any(ch.isdigit() or ch in "+-" for ch in key):
        return True
    return len(key.split()) > 3


def digits(text: str) -> str:
    """The digits of a string in order. Used to check that numbers survive translation."""
    return "".join(ch for ch in text if ch.isdigit())


def strip_tags(text: str) -> str:
    return TAG_RE.sub("", text)


def leading_ws(text: str) -> str:
    return text[: len(text) - len(text.lstrip())]


def trailing_ws(text: str) -> str:
    stripped = text.rstrip()
    return text[len(stripped):]
