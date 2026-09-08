#!/usr/bin/env python3
"""Lint the SOVL translation files.

Checks, roughly in order of how much damage they do if ignored:

  structure   the file parses, every row has a Key, no duplicate or empty keys
  encoding    no mojibake, no replacement characters, no accents lost to '?'
  fidelity    numbers, rich text tags, newlines and edge whitespace survive translation
  drift       the key set matches the baseline language
  glossary    rules terms use their approved translation
  crossstore  the same English string is translated the same way in every file
  progress    what is still missing or left as English

    python tools/lint.py                       # everything, human-readable
    python tools/lint.py --language German     # one language
    python tools/lint.py --check glossary      # one check (repeatable)
    python tools/lint.py --summary             # counts only
    python tools/lint.py --format github       # ::error annotations for CI
    python tools/lint.py --max-per-check 5     # trim long reports

Exit code is 1 if there are errors, 0 otherwise. Warnings never fail the build
unless --strict is passed.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from sovl_loc import (
    approved_term_present,
    MOJIBAKE_RE,
    PLACEHOLDER_RE,
    REPLACEMENT_RE,
    REPO,
    TAG_RE,
    TranslationFile,
    digits,
    is_rules_prose,
    language_dirs,
    load_json,
    same_term,
    store_files,
    surface_forms,
    term_pattern,
    trailing_ws,
)

ERROR = "error"
WARNING = "warning"

ALL_CHECKS = ("structure", "encoding", "fidelity", "drift", "glossary", "crossstore", "progress")

#: Latin languages where a bare '?' inside a word means an accented letter was lost.
ACCENTED_LATIN = {"French", "German", "Italian", "Spanish", "Swedish"}


@dataclass
class Finding:
    check: str
    level: str
    language: str
    store: str
    message: str
    path: Path | None = None
    row: int | None = None
    key: str = ""
    line: int | None = None

    def location(self) -> str:
        where = f"{self.language}/{self.store}"
        if self.line is not None:
            where += f" line {self.line}"
        elif self.row is not None:
            where += f" row {self.row}"
        return where


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, *args, **kwargs) -> None:
        self.findings.append(Finding(*args, **kwargs))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == WARNING]


def short(text: str, limit: int = 70) -> str:
    text = text.replace("\n", "\\n").replace("\r", "\\r")
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


# --------------------------------------------------------------------------- checks


def check_structure(files: list[TranslationFile], report: Report) -> None:
    for f in files:
        if f.error:
            report.add("structure", ERROR, f.language, f.store, f.error, f.path)
            continue

        seen: dict[str, int] = {}
        for index, row in enumerate(f.rows):
            if not isinstance(row, dict):
                report.add(
                    "structure", ERROR, f.language, f.store,
                    f"row is a {type(row).__name__}, expected an object", f.path, index,
                )
                continue
            if "Key" not in row:
                # This is the silent killer: the importer skips these without a word,
                # so a finished translation simply never reaches the game.
                lost = short(row.get("Translation", ""))
                report.add(
                    "structure", ERROR, f.language, f.store,
                    f"row has no 'Key' field, so the game will silently drop it "
                    f"(its Translation is {lost!r})", f.path, index,
                )
                continue
            key = row["Key"]
            if not isinstance(key, str):
                report.add(
                    "structure", ERROR, f.language, f.store,
                    f"'Key' is a {type(key).__name__}, expected a string", f.path, index,
                )
                continue
            if not key.strip():
                report.add(
                    "structure", WARNING, f.language, f.store,
                    "empty 'Key' - this row can never match anything and should be deleted",
                    f.path, index,
                )
                continue
            if key in seen:
                report.add(
                    "structure", ERROR, f.language, f.store,
                    f"duplicate key (also at row {seen[key]}); only the last one is imported: {short(key)!r}",
                    f.path, index, key,
                )
            else:
                seen[key] = index
            if "Translation" not in row:
                report.add(
                    "structure", ERROR, f.language, f.store,
                    f"row has no 'Translation' field: {short(key)!r}", f.path, index, key,
                )

        if f.has_bom:
            report.add(
                "structure", WARNING, f.language, f.store,
                "file starts with a UTF-8 BOM; save it as UTF-8 without BOM", f.path,
            )
        if not f.context.strip():
            report.add(
                "structure", WARNING, f.language, f.store,
                "empty 'TranslationContext' - translators lose the context for this file", f.path,
            )


def check_encoding(files: list[TranslationFile], report: Report) -> None:
    for f in files:
        if f.error:
            continue
        for index, key, translation, _ in f.pairs():
            if not translation:
                continue
            if REPLACEMENT_RE.search(translation):
                report.add(
                    "encoding", ERROR, f.language, f.store,
                    f"contains a Unicode replacement character - the text was corrupted on save: "
                    f"{short(translation)!r}", f.path, index, key,
                )
            if MOJIBAKE_RE.search(translation) and not MOJIBAKE_RE.search(key):
                report.add(
                    "encoding", ERROR, f.language, f.store,
                    f"looks like mojibake (UTF-8 read as a single-byte codepage): "
                    f"{short(translation)!r}", f.path, index, key,
                )
            # A '?' inside a word, in a language that has accented letters, is
            # almost always an accent that was destroyed by a non-UTF-8 save.
            if f.language in ACCENTED_LATIN and "?" not in key:
                for i, ch in enumerate(translation):
                    if ch == "?" and 0 < i < len(translation) - 1:
                        if translation[i - 1].isalpha() and translation[i + 1].isalpha():
                            report.add(
                                "encoding", ERROR, f.language, f.store,
                                f"'?' inside a word - an accented letter was lost when the file "
                                f"was saved in the wrong encoding: {short(translation)!r}",
                                f.path, index, key,
                            )
                            break


def check_fidelity(files: list[TranslationFile], report: Report) -> None:
    for f in files:
        if f.error:
            continue
        for index, key, translation, _ in f.pairs():
            if not translation.strip():
                continue

            # Rich text must survive: TMP renders it, players never see the markup.
            key_tags = collections.Counter(TAG_RE.findall(key))
            translation_tags = collections.Counter(TAG_RE.findall(translation))
            dropped = key_tags - translation_tags
            added = translation_tags - key_tags
            if dropped:
                report.add(
                    "fidelity", ERROR, f.language, f.store,
                    f"rich text tags dropped ({', '.join(sorted(dropped.elements()))}) from "
                    f"{short(key)!r} - the formatting will be wrong in game",
                    f.path, index, key,
                )
            elif added:
                report.add(
                    "fidelity", WARNING, f.language, f.store,
                    f"rich text tags added ({', '.join(sorted(added.elements()))}) to {short(key)!r} - "
                    f"usually means the English source is missing a closing tag; fix it there instead",
                    f.path, index, key,
                )

            # {0}, {1} are filled with real values at runtime. A translation that drops
            # one loses the value; one that invents an index the caller does not supply
            # makes string.Format throw, which the game catches but only by falling back
            # to English. Format specifiers are ignored here: {0:P0} and {0} are the same
            # slot, and a translator may legitimately keep or drop the specifier.
            key_slots = collections.Counter(PLACEHOLDER_RE.findall(key))
            translation_slots = collections.Counter(PLACEHOLDER_RE.findall(translation))
            if key_slots != translation_slots:
                missing = sorted((key_slots - translation_slots).elements())
                extra = sorted((translation_slots - key_slots).elements())
                detail = []
                if missing:
                    detail.append("dropped " + ", ".join("{" + m + "}" for m in missing))
                if extra:
                    detail.append("added " + ", ".join("{" + e + "}" for e in extra))
                report.add(
                    "fidelity", ERROR, f.language, f.store,
                    f"placeholders changed ({'; '.join(detail)}) - these are filled with real "
                    f"values at runtime: {short(key)!r} -> {short(translation)!r}",
                    f.path, index, key,
                )

            # Digits outside a placeholder are literal rules numbers now, so they should
            # come through unchanged. Reported as a warning: a language may legitimately
            # write a small number as a word, or reorder two numbers within the sentence.
            key_digits = digits(PLACEHOLDER_RE.sub("", key))
            translation_digits = digits(PLACEHOLDER_RE.sub("", translation))
            if sorted(key_digits) != sorted(translation_digits):
                report.add(
                    "fidelity", WARNING, f.language, f.store,
                    f"numbers changed ({key_digits or 'none'} -> {translation_digits or 'none'}) - "
                    f"check the rules meaning is intact: {short(key)!r} -> {short(translation)!r}",
                    f.path, index, key,
                )

            if key.count("\n") != translation.count("\n"):
                report.add(
                    "fidelity", WARNING, f.language, f.store,
                    f"line break count changed ({key.count(chr(10))} -> {translation.count(chr(10))}) "
                    f"in {short(key)!r}", f.path, index, key,
                )

            # Leading and trailing spaces are usually deliberate layout in this project
            # (" Icon", "Battles: "), and translators tend to trim them away.
            if bool(key[:1].isspace()) != bool(translation[:1].isspace()):
                report.add(
                    "fidelity", WARNING, f.language, f.store,
                    f"leading whitespace changed - it is layout, keep it: "
                    f"{short(key)!r} -> {short(translation)!r}", f.path, index, key,
                )
            if bool(trailing_ws(key)) != bool(trailing_ws(translation)):
                report.add(
                    "fidelity", WARNING, f.language, f.store,
                    f"trailing whitespace changed - it is layout, keep it: "
                    f"{short(key)!r} -> {short(translation)!r}", f.path, index, key,
                )


def check_drift(files: list[TranslationFile], baseline: str, report: Report) -> None:
    by_store: dict[str, dict[str, set[str]]] = collections.defaultdict(dict)
    for f in files:
        if not f.error:
            by_store[f.store][f.language] = {k for _, k, _, _ in f.pairs()}

    for store, per_language in sorted(by_store.items()):
        reference = per_language.get(baseline)
        if reference is None:
            continue
        for language, keys in sorted(per_language.items()):
            if language == baseline:
                continue
            missing, extra = reference - keys, keys - reference
            if missing:
                report.add(
                    "drift", ERROR, language, store,
                    f"{len(missing)} key(s) present in {baseline} but missing here, so they can "
                    f"never be translated (e.g. {short(sorted(missing)[0])!r})",
                )
            if extra:
                report.add(
                    "drift", WARNING, language, store,
                    f"{len(extra)} key(s) here that {baseline} does not have - probably stale, "
                    f"or exported at a different time (e.g. {short(sorted(extra)[0])!r})",
                )


def check_glossary(files: list[TranslationFile], repo: Path, report: Report) -> None:
    terms = load_json(repo / "glossary" / "terms.json")["terms"]
    by_english = {t["en"]: t for t in terms}

    by_language: dict[str, list[TranslationFile]] = collections.defaultdict(list)
    for f in files:
        if not f.error:
            by_language[f.language].append(f)

    for language, language_files in sorted(by_language.items()):
        glossary_path = repo / language / f"Glossary_{language}.json"
        if not glossary_path.exists():
            report.add(
                "glossary", WARNING, language, "Glossary",
                "no glossary file - run tools/seed_glossary.py to create it",
            )
            continue

        approved: dict[str, str] = {}
        undecided: list[str] = []
        for row in load_json(glossary_path).get("Translations", []):
            if not isinstance(row, dict) or "Key" not in row:
                continue
            value = (row.get("Translation") or "").strip()
            if value:
                approved[row["Key"]] = value
            else:
                undecided.append(row["Key"])

        if undecided:
            report.add(
                "glossary", WARNING, language, "Glossary",
                f"{len(undecided)} rules term(s) have no approved translation yet, so they are "
                f"not being checked (e.g. {undecided[0]!r})",
                glossary_path,
            )

        for f in language_files:
            for index, key, translation, _ in f.pairs():
                if not translation.strip():
                    continue
                for english, target in approved.items():
                    term = by_english.get(english)
                    if term is None:
                        continue
                    forms = surface_forms(term)

                    # A key that IS the term must be the approved rendering. When the key
                    # is the base term the match has to be exact. When it is one of the
                    # alias spellings - a plural, or the conjugated form the English uses
                    # mid sentence - the translation is expected to be inflected to match,
                    # so 'Spells' being 'Hechizos' where the term is 'Hechizo' is correct.
                    stripped = key.strip().casefold()
                    if stripped in {form.casefold() for form in forms}:
                        is_base = stripped == english.casefold()
                        matches = (
                            translation.strip().casefold() == target.casefold()
                            if is_base
                            else same_term(translation, target)
                        )
                        if not matches:
                            report.add(
                                "glossary", ERROR, f.language, f.store,
                                f"rules term {english!r} is translated as {translation.strip()!r} "
                                f"but the glossary approves {target!r}",
                                f.path, index, key,
                            )
                        continue

                    # A key that CONTAINS the term should use the approved rendering.
                    # Matched case-sensitively, because the rules terms are capitalised
                    # and the lowercase words are flavour text ('an ancient place of
                    # power' is not the Power stat). Skipped for proper names, where a
                    # rules word is part of the name ('Dwarf Flying Machine'), and for
                    # terms whose surface forms differ numerically - the fidelity check
                    # already guards D3 turning into D6. Reported as a warning: the
                    # target language may legitimately inflect or restructure.
                    if term.get("skipInSentenceCheck") or not is_rules_prose(key):
                        continue
                    if not any(term_pattern(form, ignore_case=False).search(key) for form in forms):
                        continue
                    if approved_term_present(target, translation):
                        continue
                    report.add(
                        "glossary", WARNING, f.language, f.store,
                        f"contains the rules term {english!r}, which the glossary renders as "
                        f"{target!r}, but the translation does not appear to use it: "
                        f"{short(translation)!r}",
                        f.path, index, key,
                    )


def check_crossstore(files: list[TranslationFile], report: Report) -> None:
    by_language: dict[str, dict[str, dict[str, list[str]]]] = collections.defaultdict(
        lambda: collections.defaultdict(lambda: collections.defaultdict(list))
    )
    for f in files:
        if f.error:
            continue
        for _, key, translation, _ in f.pairs():
            if translation.strip():
                by_language[f.language][key][translation.strip()].append(f.store)

    for language, keys in sorted(by_language.items()):
        for key, renderings in sorted(keys.items()):
            if len(renderings) < 2:
                continue
            detail = "; ".join(
                f"{rendering!r} in {', '.join(sorted(stores))}"
                for rendering, stores in sorted(renderings.items())
            )
            report.add(
                "crossstore", WARNING, language, "(multiple)",
                f"{short(key)!r} is translated differently in different files: {detail}",
                key=key,
            )


def check_progress(files: list[TranslationFile], report: Report) -> None:
    for f in files:
        if f.error:
            continue
        empty, untouched = [], []
        for _, key, translation, _ in f.pairs():
            if not translation.strip():
                empty.append(key)
            elif translation.strip() == key.strip() and any(c.isalpha() for c in key):
                untouched.append(key)
        if empty:
            report.add(
                "progress", WARNING, f.language, f.store,
                f"{len(empty)} string(s) not translated (e.g. {short(empty[0])!r})", f.path,
            )
        if untouched:
            report.add(
                "progress", WARNING, f.language, f.store,
                f"{len(untouched)} string(s) identical to the English source - either genuinely "
                f"the same word, or never translated (e.g. {short(untouched[0])!r})", f.path,
            )


# --------------------------------------------------------------------------- output


def print_human(report: Report, checks: tuple[str, ...], max_per_check: int) -> None:
    for check in checks:
        findings = [f for f in report.findings if f.check == check]
        if not findings:
            print(f"\n\u2713 {check}: clean")
            continue
        errors = sum(1 for f in findings if f.level == ERROR)
        warnings = len(findings) - errors
        print(f"\n\u2717 {check}: {errors} error(s), {warnings} warning(s)")
        findings.sort(key=lambda f: (f.level != ERROR, f.language, f.store, f.row or 0))
        shown = findings if max_per_check <= 0 else findings[:max_per_check]
        for f in shown:
            mark = "ERROR  " if f.level == ERROR else "warning"
            print(f"    {mark} {f.location()}: {f.message}")
        if len(findings) > len(shown):
            print(f"    ... and {len(findings) - len(shown)} more (use --max-per-check 0 for all)")


def print_summary(report: Report, checks: tuple[str, ...]) -> None:
    languages = sorted({f.language for f in report.findings})
    width = max([len(c) for c in checks] + [11])
    print(f"{'check':{width}} " + " ".join(f"{l[:6]:>7}" for l in languages) + f"{'total':>8}")
    for check in checks:
        cells = []
        for language in languages:
            hits = [f for f in report.findings if f.check == check and f.language == language]
            errors = sum(1 for f in hits if f.level == ERROR)
            cells.append(f"{errors}/{len(hits) - errors:<3}" if hits else "-")
        total = sum(1 for f in report.findings if f.check == check)
        print(f"{check:{width}} " + " ".join(f"{c:>7}" for c in cells) + f"{total:>8}")
    print("\n(cells are errors/warnings)")


def resolve_lines(report: Report, files: list[TranslationFile]) -> None:
    """Turn row indexes into file line numbers so CI annotations land on the row."""
    by_path = {f.path: f for f in files}
    for finding in report.findings:
        if finding.row is None or finding.path is None:
            continue
        source = by_path.get(finding.path)
        if source is not None:
            finding.line = source.line_of_row(finding.row)


def print_github(report: Report, repo: Path) -> None:
    for f in report.findings:
        location = ""
        if f.path is not None:
            try:
                relative = f.path.relative_to(repo).as_posix()
            except ValueError:
                relative = f.path.as_posix()
            location = f",file={relative}"
            if f.line is not None:
                location += f",line={f.line}"
        message = f.message.replace("\n", " ").replace("\r", " ")
        print(f"::{f.level}{location},title={f.check} ({f.location()})::{message}")


# --------------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo", type=Path, default=REPO, help="repository root")
    parser.add_argument("--language", action="append", help="limit to a language (repeatable)")
    parser.add_argument("--store", action="append", help="limit to a store (repeatable)")
    parser.add_argument(
        "--check", action="append", choices=ALL_CHECKS, help="run only this check (repeatable)"
    )
    parser.add_argument("--baseline", default=None, help="language to compare key sets against")
    parser.add_argument("--summary", action="store_true", help="counts only")
    parser.add_argument("--format", choices=("human", "github", "json"), default="human")
    parser.add_argument("--max-per-check", type=int, default=15, help="0 for no limit")
    parser.add_argument("--strict", action="store_true", help="fail on warnings too")
    args = parser.parse_args()

    checks = tuple(args.check) if args.check else ALL_CHECKS

    files: list[TranslationFile] = []
    for lang_dir in language_dirs(args.repo):
        if args.language and lang_dir.name not in args.language:
            continue
        for path in store_files(lang_dir):
            f = TranslationFile(path)
            if args.store and f.store not in args.store:
                continue
            files.append(f)

    if not files:
        print("no translation files found", file=sys.stderr)
        return 2

    try:
        terms_doc = load_json(args.repo / "glossary" / "terms.json")
        baseline = args.baseline or terms_doc.get("baselineLanguage", "German")
    except (OSError, json.JSONDecodeError):
        baseline = args.baseline or "German"

    report = Report()
    if "structure" in checks:
        check_structure(files, report)
    if "encoding" in checks:
        check_encoding(files, report)
    if "fidelity" in checks:
        check_fidelity(files, report)
    if "drift" in checks:
        check_drift(files, baseline, report)
    if "glossary" in checks:
        check_glossary(files, args.repo, report)
    if "crossstore" in checks:
        check_crossstore(files, report)
    if "progress" in checks:
        check_progress(files, report)

    resolve_lines(report, files)

    if args.format == "github":
        print_github(report, args.repo)
    elif args.format == "json":
        print(json.dumps(
            [
                {
                    "check": f.check, "level": f.level, "language": f.language, "store": f.store,
                    "row": f.row, "line": f.line, "key": f.key, "message": f.message,
                    "file": f.path.relative_to(args.repo).as_posix() if f.path else None,
                }
                for f in report.findings
            ],
            ensure_ascii=False, indent=2,
        ))
    elif args.summary:
        print_summary(report, checks)
    else:
        print(f"Linting {len(files)} file(s) across {len({f.language for f in files})} language(s). "
              f"Key-set baseline: {baseline}.")
        print_human(report, checks, args.max_per_check)

    if args.format == "human":
        print(f"\n{len(report.errors)} error(s), {len(report.warnings)} warning(s)")

    if report.errors:
        return 1
    return 1 if args.strict and report.warnings else 0


if __name__ == "__main__":
    sys.exit(main())
