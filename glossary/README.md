# Glossary

SOVL is a wargame, so a lot of its text is rules text. If `Damage Save` is called one
thing in a unit's ability description and something else in the tooltip that explains
the rule, players cannot connect the two. This folder is where we decide, once per
language, what each rules term is called.

## The two files

**`terms.json`** is the canonical English list. Each entry is a term that carries rules
meaning, with what it means in the game and what to watch out for when translating it.
It is the same for every language and only changes when the rules change.

**`<Language>/Glossary_<Language>.json`** is one file per language holding the approved
translation of every term. This is the file translators fill in. It has the same shape
as every other translation file, but the game does **not** import it: there is no
`Glossary` store in the project, and there does not need to be. Its job is to constrain
the other nineteen files, which the linter does. Nothing here reaches a player directly.

## Filling in a glossary

Open your language's `Glossary_<Language>.json` and work down it. Each row gives you:

| field | what it is |
|---|---|
| `Key` | the English term |
| `Translation` | **the approved term — this is what you fill in** |
| `Comment` | your notes, and any conflict the tooling found |
| `Definition` | what the term means in the rules |
| `Notes` | traps: terms it must stay distinct from, spellings that vary in the English |
| `AlsoWrittenAs` | other English spellings that mean the same term |
| `ExampleUsage` | real strings from the game that contain the term |

Only `Translation` and `Comment` are imported into the game. The rest is reference
material for you and is regenerated from `terms.json`.

Some rows arrive pre-filled from a translation that already existed, with a Comment
saying where it came from — change it if it is not the term you want. Rows whose
Comment starts with **`CONFLICT`** are the interesting ones: the term is currently
translated two or more different ways in different files. Pick one, and the linter
will then flag everywhere the other one is still used.

## Three things worth knowing

**Keep a term distinct from its neighbours.** Several `Notes` say things like "must be
distinct from". `Flying` (the property, meaning the unit flies) and `Flight Move` (a
rout move made by a unit that is running away) share a word in English but mean
opposite things. If they share a stem in your language, players will misread the rules.

**Compounds should be built from their parts.** If `Charge` is X and `Phase` is Y, then
`Charge Phase` should be recognisably X + Y. The rulebook builds terms this way and it
is how players learn the vocabulary.

**Sets should read as sets.** `Front` / `Flank` / `Rear`, and the five army sizes from
`Warband` up to `Unrestricted`, are ladders. Translate them together so the ordering
and the parallel structure survive.

## Adding or changing a term

Add it to `terms.json`, then run:

```bash
python tools/seed_glossary.py
```

That adds the row to every language's glossary without touching any translation that
is already filled in. Then `python tools/lint.py --check glossary` to see where the
new term is not yet being used consistently.
