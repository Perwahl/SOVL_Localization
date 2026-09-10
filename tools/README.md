# Tools

Python 3.10 or newer. No dependencies to install.

```bash
python tools/lint.py                  # check everything
python tools/seed_glossary.py         # refresh the glossary files from glossary/terms.json
```

## `lint.py`

Seven checks, in rough order of how much damage they do if ignored.

### structure

The file parses; every row has a `Key` and a `Translation`; no duplicate or empty keys.

The one to care about is **a row with no `Key` field**. The game's importer skips those
silently, so a finished translation simply never reaches players and nothing anywhere
says so. It happens when a row is edited on GitHub and the `Key` line is deleted by
accident.

### encoding

Replacement characters, mojibake, and — for the languages that use accents — a bare
`?` in the middle of a word. That last one means the file was saved through a non-UTF-8
codepage and the accented letter is *gone*: `Uppståndna` came back as `Uppst?ndna` and
there is nothing to recover it from. Files must be UTF-8 without a BOM.

### fidelity

Things that must survive translation intact:

- **Rich text tags.** `<color=#96FAFF>`, `<size=125%>`, `<b>`. TMP renders these; if one
  is dropped the markup shows up on screen as literal text.
- **Placeholders.** `{0}`, `{1}`, and forms with a format specifier such as `{1:P0}`.
  The game fills these with real values at runtime, so the translation needs the same
  set as the source — lose one and a rules number never reaches the screen, and a
  translation whose placeholders do not match falls back to English with an error in
  the log. Reordering them is fine; each is filled by its own number.
- **Digits**, reported as warnings. Every digit in a source string is a rules value and
  should appear unchanged in the translation, in the same order. Take care in languages
  that spell numbers differently: writing "1 turn" where the English says "one turn"
  introduces a digit the rules do not have, and Japanese uses kanji numerals there for
  exactly that reason.

  > This check used to describe something else entirely. Before the placeholder work,
  > the game rewrote every digit except `0` and `1` to a `3` when building the lookup
  > key and substituted the real values back afterwards, which made stray `3`s
  > load-bearing. That mechanism is gone; `{0}` is a real placeholder now.
- **Line breaks and edge whitespace.** In this project a leading or trailing space is
  usually deliberate layout (`" Icon"`, `"Battles: "`) and translators tend to trim it.

### drift

Whether each language has the same set of keys as the baseline language (German, set by
`baselineLanguage` in `glossary/terms.json`). Missing keys can never be translated;
extra keys are usually stale rows left behind by an old export.

### glossary

Rules terms use their approved translation — see [`glossary/README.md`](../glossary/README.md).

- A key that **is** a glossary term and is translated as something other than the
  approved rendering is an **error**. There is no ambiguity to allow for.
- A key that **contains** a glossary term whose approved rendering does not appear is a
  **warning**. The target language may legitimately inflect or restructure around the
  term, so this needs a human eye.

The in-sentence check matches the English case-sensitively, so "an ancient place of
power" is not mistaken for the Power stat, and it skips proper names, so
"Dwarf Flying Machine" is not read as a use of the Flying property. On the target side
it matches prefixes, so German `Angriffswurf` counts as a use of `Angriffe`.

### crossstore

The same English string translated differently in two different files — `Inferno Cannon`
as one thing in `DwarfHolds` and another in `UnitProps`. Always worth a look; the game
shows both to the same player.

### progress

What is left: strings with no translation, and strings whose translation is identical to
the English source (sometimes correct, more often never touched).

### Options

| | |
|---|---|
| `--language German` | limit to a language, repeatable |
| `--store UnitProps` | limit to a store, repeatable |
| `--check glossary` | limit to a check, repeatable |
| `--summary` | a counts table instead of the findings |
| `--format github` | `::error` annotations for CI |
| `--format json` | machine-readable findings |
| `--max-per-check 0` | show every finding rather than the first 15 |
| `--strict` | fail on warnings as well as errors |

Exit code is 1 if there are errors, 0 otherwise.

## `seed_glossary.py`

Regenerates every `<Language>/Glossary_<Language>.json` from `glossary/terms.json`.

For each term it looks for a key that is exactly that term in the existing translation
files. If every file agrees, the rendering is seeded as the approved one, with a Comment
saying where it came from. If they disagree, the row is left empty and the competing
renderings go in the Comment as a `CONFLICT` for a translator to settle.

**It never overwrites a translation that is already filled in**, so it is safe to re-run
whenever terms are added. `--dry-run` reports without writing.
