# Public fixtures

Self-written imitations of a primary English textbook unit, in the three formats the pipeline ingests. The wording is invented and no textbook text, character, song or picture is used, so the files are safe to commit and to run in CI. Real textbook material belongs in `eval/fixtures/private/`, which is git-ignored.

```
units/<id>.json      the unit as data (one file per unit) — this is what content writers edit
pagekit.py           renders a spec to <id>.pdf, <id>.docx and <id>.pptx
make_fixtures.py     renders every spec in units/ into this directory
mini-unit-hello.*    the two-page unit used by tests/test_ingest.py and the public eval cases
```

The three formats of one unit carry the same text and ingest to the same sequence of span kinds and texts (one exception below), so a test written against the PDF holds for DOCX and PPTX too.

## Writing a unit

A spec is a JSON object: unit header, one to three lessons, six numbered activities per lesson. Activities 1-3 of a lesson go on its first page, 4-6 on its second. The full format, with every field, is documented at the top of `pagekit.py`; in short:

```json
{
  "id": "gs3-unit1", "grade": 3, "unit": 1, "title": "Hello",
  "lessons": [
    { "no": 1, "activities": [
      { "no": 1, "instruction": "Look, listen and repeat.", "type": "look_listen_repeat",
        "picture": { "scene": "two children greeting at the school gate" },
        "lines": ["Ha: Hello, I'm Ha.", "Tien: Hi, Ha. I'm Tien."] },
      { "no": 2, "instruction": "Listen, point and say.", "type": "listen_point_say",
        "pattern": "Hello, I'm ___.", "items": [ { "word": "Ha" }, { "word": "Tien" } ],
        "lines": ["Hello, I'm ___.", "Hi, ___. I'm ___."] },
      { "no": 3, "instruction": "Let's talk.", "type": "lets_talk", "lines": ["Hello. / Hi."] },
      { "no": 4, "instruction": "Listen and tick.", "type": "listen_and_do",
        "options": ["a. Hello, I'm Lan.", "b. Hi, I'm Duc."] },
      { "no": 5, "instruction": "Look, complete and read.", "type": "look_complete_read",
        "lines": ["Hello, I'm _____."] },
      { "no": 6, "instruction": "Let's sing.", "type": "song", "lines": ["Hello, hello! I'm Ha."] }
    ] }
  ]
}
```

Rules a writer needs to know:

- **Original material only.** Invent the children's names, dialogues, songs and chants. Generic vocabulary and sentence patterns (greetings, names, body parts, hobbies) are fine; copied wording is not.
- **Shapes, not content, follow the textbook.** Lessons 1-2: look_listen_repeat, listen_point_say, lets_talk, listen_and_do, look_complete_read, song or game. Lesson 3: phonics, listen_and_do, chant, read_and_match, lets_write, project. Any activity may use any content field (`lines`, `pattern`, `picture`, `items`, `options`, `pairs`, `sound`/`letters`/`words`).
- **Instructions are the anchors.** They render as `N. Instruction.` and are what `segment_document` cuts on, so no other line may start with a number and a dot or bracket. The validator rejects such lines.
- **Plain Latin-1 text**, one line per entry (the built-in PDF fonts cover nothing else); Vietnamese belongs in `meaning_vi`-style fields of the lesson, not in fixtures.
- **Pictures are drawn, not uploaded.** `picture.scene` is a short description; the drawing (a pastel panel with faces, a sun, a house, a ball or a tree, and the description as a caption) is inferred from it, or forced with `"shapes": ["face", "sun"]`. `items` become a row of cards with the words under them; an item without a picture gets a card drawn from its word.
- **A page must fit.** Roughly one scene picture, one row of cards and a dozen text lines per page; `render_unit` raises `ValueError` naming the page otherwise.
- **The id is the file stem** (`units/gs3-unit1.json` has `"id": "gs3-unit1"`), lower-case letters, digits and hyphens.

Layout details: every page carries the heading `Unit N  Title` (20 pt bold); the first page of each lesson adds a `Lesson L` line (16 pt bold). Instructions are 12 pt bold list items, other text 12 pt. Short options and words share one line separated by three spaces, which ingest collapses to one; longer ones wrap to one per line. `pairs` render as two columns with the right column shifted by one row. `layout.lesson_line: false` drops the lesson line and a lesson's `headings` can override its two page headings verbatim; `mini-unit-hello.json` uses both to keep its frozen text.

The one cross-format difference: the `Lesson L` line is a heading in PDF and DOCX but a paragraph in PPTX, where only the title placeholder is read as a heading. Everything else, including page numbers, is identical.

## Regenerate

```
python eval/fixtures/public/make_fixtures.py
```

The script needs the project dependencies (`pymupdf`, `python-docx`, `python-pptx`, `pydantic`) and overwrites the output files next to it. Output is deterministic: the PDF is saved without a fresh document id, the Office files get fixed core-property dates and zip timestamps, so rerunning without changing a spec changes no byte and every `doc_id` stays stable. Changing a spec changes its files' hashes and therefore their `doc_id`; tests and eval cases refer to the files by path, not by id.

From Python:

```python
import pagekit  # eval/fixtures/public/pagekit.py

specs = pagekit.load_specs(Path("eval/fixtures/public/units"))  # validated dicts
paths = pagekit.render_unit(specs[0], Path("out"), formats=("pdf",))  # {"pdf": Path(...)}
expected = pagekit.text_sequence(specs[0])  # (kind, text) per page
```

`text_sequence` is the oracle `tests/test_fixture_pagekit.py` compares ingest against.
