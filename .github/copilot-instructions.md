# Copilot Instructions

## Project Guidelines
- This is a localization project for SOVL:Fantasy Warfare.

- SOVL: Fantasy Warfare is a fantasy wargame. Translations should match the style and tone of a 
classic fantasy wargame.

- Some text is rules text and needs consistent terminology. The rules vocabulary is
listed in `glossary/terms.json`, and each language's approved translation of every term
is in `<Language>/Glossary_<Language>.json`. Before translating a string that contains a
rules term, check the glossary and use the approved term. If the glossary row is empty,
decide on a term, fill it in there, and then use it everywhere.

- Run `python tools/lint.py` before opening a pull request. It checks that rich text tags
and number placeholders survived translation, that the encoding is intact, and that rules
terms match the glossary. See `tools/README.md`.

- Numbers in the "Key" field are placeholders that the game fills in at runtime. A
translation must contain the same digits as its key, in the same order. Do not spell a
number out as a word and do not drop one.

- Each JSON file has a "TranslationContext" field that provides context for the translations within that file.

- The "Key" Field in the JSON files contain the English string that needs to be translated. The "Translation" field should contain the translation in the target language. The "Key" field should never be modified.

- The "Comment" field in JSON translation files should be used to add translator notes about the translation itself, including tone and word choice decisions. The comments should be in English.