# Curriculum config

Curriculum outcomes (*yêu cầu cần đạt*), word lists and grammar scope are data, not code. They live in versioned YAML so that when the Ministry revises the programme (a revision is expected during the 2026–2027 school year), the change is a config update, not a rewrite.

## Layout

```
curriculum/
  gdpt-2018/                      ← curriculum version
    README.md
    tieng-anh/
      lop-3.yaml
      lop-4.yaml
      lop-5.yaml
  gdpt-2018-rev2027/              ← future revision; same shape, new IDs where content changed
```

The loader (`src/classdeck/curriculum/loader.py`) reads one `(version, grade)` file, validates it with Pydantic, and exposes lookup by `unit_id` / `outcome_id`, the grade word list, the grammar scope, and a simple keyword search used by the outliner.

## File format

```yaml
version: gdpt-2018
subject: tieng-anh
grade: 3
language: en
period_minutes: 35

vocabulary:
  level: pre-A1
  words:                          # the grade's expected word list; units extend it below
    - hello
    - hi
    - goodbye
    - name
    - …
  classroom_language:             # instruction language pupils hear every lesson (not taught as new words)
    - listen
    - point
    - repeat
    - partner
    - picture
    - …

grammar_scope:
  allowed:                        # documentation for the prompt; not machine-checked
    - "present simple with be and have"
    - "imperatives"
    - "this / that"
    - "What's …? / How …?"
  forbidden:                      # regexes checked by english.grammar_scope
    - { id: perfect,   pattern: '\b(has|have|had)\s+\w+(ed|en)\b',          note: "perfect tenses" }
    - { id: passive,   pattern: '\b(is|are|was|were)\s+\w+(ed|en)\s+by\b',  note: "passive voice" }
    - { id: past,      pattern: '\b(was|were|did|went|saw|had)\b',           note: "past simple (grade 5 only)" }

textbooks:                        # optional; maps known books to units when one is uploaded
  - series: global-success
    volume: 1

units:
  - id: ta3.u1
    title: "Hello"
    textbook_units: [ { series: global-success, unit: 1 } ]
    lessons: 3
    outcomes:
      - id: ta3.u1.o1
        text: Greet and respond to greetings using Hello / Hi.
        skills: [listening, speaking]
        keywords: [greet, hello, hi]
      - id: ta3.u1.o2
        text: Introduce oneself using "I'm …".
        skills: [speaking]
        keywords: [introduce, name]
      - id: ta3.u1.o3
        text: Say goodbye using Goodbye / Bye.
        skills: [speaking]
    vocabulary: [hello, hi, goodbye, bye, nice, meet]
    patterns:
      - { pattern: "Hello, I'm ___.", function: introducing oneself }
      - { pattern: "Nice to meet you.", function: responding to an introduction }
    phonics:
      - { sound: "/b/", letters: "b", words: [Bill, bye] }
      - { sound: "/h/", letters: "h", words: [hello, hi] }
```

## ID policy

- IDs are stable strings, never renumbered: `ta<grade>.u<unit>.o<outcome>` — `ta3.u1.o2`.
- A revision that changes the meaning of an outcome gets a **new ID** in the new version directory; an unchanged outcome keeps its ID. Lessons store `{version, outcome_ids}`, so a lesson built against `gdpt-2018` stays valid and a migration report can list which of its outcomes changed in the new version.

## How the pipeline uses it

| stage | use |
|---|---|
| Planning | Outliner receives the unit's outcomes, patterns and vocabulary; must assign ≥ 1 outcome to each section; `objective` blocks are derived from outcome text. |
| Drafting | `grammar_scope.allowed` and the grade word list go into the prompt; outcome `skills` decide which activity types are needed. |
| Grounding | None (curriculum is not a source). |
| Validators | `english.vocab_level` reads `vocabulary.words` ∪ `vocabulary.classroom_language` ∪ unit `vocabulary`; `english.grammar_scope` reads `grammar_scope.forbidden`; `english.timing` reads `period_minutes`; `curriculum.outcome_covered` reads `outcomes`. |
| Export | Lesson plan header lists outcomes by ID and text, as Vietnamese lesson plans conventionally do. |

## Sourcing the data

Outcome text is transcribed from the published curriculum documents of the Ministry of Education and Training (Thông tư 32/2018/TT-BGDĐT, English language programme, and amendments). Word lists follow the programme's vocabulary expectations per grade. Only outcome statements, word lists, pattern templates and textbook unit *numbers and titles* are stored — no textbook content.

The seed files shipped in this repository are marked `status: seed` in their header until each has been checked line by line against the official document.
