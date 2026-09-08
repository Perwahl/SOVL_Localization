#!/usr/bin/env python3
"""Generate (or refresh) the per-language glossary files from glossary/terms.json.

For every term this looks through the existing translation files for a key that is
exactly that term. If every store agrees on a rendering, that rendering is seeded as
the approved one. If the stores disagree, the row is left empty and the competing
renderings are recorded in the Comment so a translator can pick a winner.

Approved translations are never overwritten: once a glossary row has a Translation,
re-running this script leaves it alone. That makes it safe to run again whenever new
terms are added to terms.json.

    python tools/seed_glossary.py            # write the files
    python tools/seed_glossary.py --dry-run  # just report what would change
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from sovl_loc import (
    REPO,
    language_dirs,
    load_json,
    same_term,
    store_files,
    stem,
    surface_forms,
    term_pattern,
)

CONTEXT = (
    "Approved translations for SOVL rules terminology. Every term in this file carries "
    "rules meaning and must be rendered the same way everywhere it appears, in every "
    "other file. Fill in the Translation field with the term you want used from now on; "
    "the linter then checks the rest of the translation files against it. Definition, "
    "Notes and ExampleUsage are reference material for the translator and are not "
    "imported into the game."
)


def existing_renderings(lang_dir: Path, term: dict) -> dict[str, list[str]]:
    """Map rendering -> the "Store:SurfaceForm" occurrences that produced it."""
    wanted = {form.casefold() for form in surface_forms(term)}
    found: dict[str, list[str]] = {}
    for path in store_files(lang_dir):
        try:
            data = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        store = path.stem.rsplit("_", 1)[0]
        for row in data.get("Translations", []):
            if not isinstance(row, dict) or "Key" not in row:
                continue
            key = (row.get("Key") or "").strip()
            translation = (row.get("Translation") or "").strip()
            if translation and key.casefold() in wanted:
                found.setdefault(translation, []).append(f"{store}:{key}")
    return found


def pick_preferred(term: dict, found: dict[str, list[str]]) -> str:
    """Of several inflections of one word, prefer the one the base English term produced."""
    base = term["en"].casefold()
    for rendering, sources in found.items():
        if any(source.split(":", 1)[1].casefold() == base for source in sources):
            return rendering
    return max(found, key=lambda r: len(found[r]))


def example_usage(lang_dir: Path, term: dict, limit: int = 2) -> list[str]:
    """A couple of real English strings containing the term, as context for the translator."""
    patterns = [term_pattern(form) for form in surface_forms(term)]
    examples: list[str] = []
    for path in store_files(lang_dir):
        try:
            data = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        for row in data.get("Translations", []):
            if not isinstance(row, dict) or "Key" not in row:
                continue
            key = (row.get("Key") or "").strip()
            if len(key) <= len(term["en"]) or len(key) > 110 or "<" in key:
                continue
            if key not in examples and any(p.search(key) for p in patterns):
                examples.append(key)
                if len(examples) >= limit:
                    return examples
    return examples


def build_row(term: dict, lang_dir: Path, previous: dict[str, dict]) -> dict:
    row = {
        "Key": term["en"],
        "Translation": "",
        "Comment": "",
        "Category": term["category"],
        "Consistency": term["consistency"],
        "Definition": term["definition"],
    }
    if term.get("aliases"):
        row["AlsoWrittenAs"] = ", ".join(term["aliases"])
    if term.get("notes"):
        row["Notes"] = term["notes"]
    if term.get("doNotTranslate"):
        row["DoNotTranslate"] = "true"
    examples = example_usage(lang_dir, term)
    if examples:
        row["ExampleUsage"] = examples

    prior = previous.get(term["en"])
    # Never clobber a decision a translator already made.
    if prior and (prior.get("Translation") or "").strip():
        row["Translation"] = prior["Translation"]
        row["Comment"] = prior.get("Comment", "")
        return row

    if term.get("doNotTranslate"):
        row["Translation"] = term["en"]
        row["Comment"] = "Do not translate - leave exactly as written in the English."
        return row

    found = existing_renderings(lang_dir, term)
    renderings = list(found)
    if len(found) > 1 and all(same_term(renderings[0], other) for other in renderings[1:]):
        # Inflections or capitalisations of one word, not a disagreement.
        preferred = pick_preferred(term, found)
        row["Translation"] = preferred
        row["Comment"] = (
            f"Seeded from the existing translation in {found[preferred][0]}. Also appears "
            f"inflected as {', '.join(repr(r) for r in renderings if r != preferred)}, which is fine."
        )
    elif len(found) == 1:
        rendering, sources = next(iter(found.items()))
        row["Translation"] = rendering
        row["Comment"] = f"Seeded from the existing translation in {sources[0]}. Confirm this is the term you want."
    elif len(found) > 1:
        options = "; ".join(
            f"{rendering!r} (used in {', '.join(sources)})" for rendering, sources in sorted(found.items())
        )
        row["Comment"] = f"CONFLICT - pick one; it will then be applied everywhere. Currently in use: {options}"
    elif prior:
        # Carry a translator's own note across, but not a stale machine-written one:
        # a CONFLICT recorded before the term list was corrected would otherwise
        # outlive the conflict.
        note = prior.get("Comment", "")
        if not note.startswith(("CONFLICT", "Seeded from", "Do not translate")):
            row["Comment"] = note
    return row


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    parser.add_argument("--repo", type=Path, default=REPO, help="repository root")
    args = parser.parse_args()

    terms = load_json(args.repo / "glossary" / "terms.json")["terms"]

    for lang_dir in language_dirs(args.repo):
        language = lang_dir.name
        out_path = lang_dir / f"Glossary_{language}.json"

        previous: dict[str, dict] = {}
        if out_path.exists():
            for row in load_json(out_path).get("Translations", []):
                if isinstance(row, dict) and "Key" in row:
                    previous[row["Key"]] = row

        rows = [build_row(term, lang_dir, previous) for term in terms]
        seeded = sum(1 for r in rows if r["Translation"])
        conflicts = sum(1 for r in rows if r["Comment"].startswith("CONFLICT"))

        payload = {"TranslationContext": CONTEXT, "Translations": rows}
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

        status = "unchanged"
        if not out_path.exists() or out_path.read_text(encoding="utf-8") != text:
            status = "would write" if args.dry_run else "wrote"
            if not args.dry_run:
                out_path.write_text(text, encoding="utf-8", newline="\n")

        print(
            f"{language:10} {status:12} {len(rows):3} terms, "
            f"{seeded:3} approved, {len(rows) - seeded:3} to decide, {conflicts:2} conflicts"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
