# Roadmap

## Phase 0 — Structure and design ✅

- Repository layout, design documents (this folder), curriculum config format.

## Phase 1 — Core library + CLI ✅ (first pass)

Done means: a grade-3 *Global Success* unit goes from PDF to an HTML deck that looks good on a projector (plus PPTX + DOCX) from the command line, with grounding verification, level checks, and at least one scripted edit with dependency flagging, all covered by eval cases — and the same result is reachable with no API key through the prompt packs.

Status: every item below is implemented and covered by tests. The offline (template) pipeline passes all 20 eval cases over nine self-written units (grades 3–5, curriculum seeds for all three grades). With a real model (`openai/gpt-6-luna` via OpenRouter and an independent judge model from another family) the two public cases pass with zero out-of-list words and an independent judge mean of 3.6/5 (up from 2.6 before the repair round, the period-2 review, glosses, answer keys and the wording check). Still open: runs on real textbook pages (private fixtures), the teacher study, Vietnamese glosses in template drafts, and answer keys for listening tasks.

1. **Lesson model** — Pydantic schema, JSON store, invariants, revisions/undo. (`lesson/`)
2. **LLM provider** — Meta Model API adapter via the OpenAI-compatible SDK, mock provider, disk cache, structured-output helper, `ping`. (`llm/`)
3. **Ingest** — PDF (PyMuPDF) with stable span IDs and cropped image spans; DOCX; PPTX. (`ingest/`)
4. **Curriculum** — loader + `tieng-anh/lop-3.yaml` seed (grades 4–5 follow). (`curriculum/`)
5. **Planning** — outliner (sections ↔ outcomes ↔ spans, using the Global Success activity shapes), drafter (typed blocks with proposed citations). (`planning/`)
6. **Grounding** — citation resolution, lexical + judge verification, provenance assignment. (`grounding/`)
7. **English rules** — phases and timing, prompt rules, validators (word list, grammar scope, skills, IPA, pattern use, timing), dependency inference, presets. (`english/`)
8. **Editing** — edit context builder, proposal validation, dependency flagging, JSON Patch revisions. (`editing/`)
9. **Export** — PPTX with flashcard/dialogue/activity layouts, citation footers and AI-added badges; DOCX two-column lesson plan with source map. (`export/`)
10. **HTML deck** — the primary, pupil-facing output: one offline file, `kids` theme, tap-to-reveal flashcards and patterns, chat-bubble dialogues, countdowns, teacher mode. (`export/html.py`, spec in [output-formats.md](output-formats.md))
11. **Hand-off** — deck pack and draft pack for a free chat model; `check-deck` and `import-draft` validate what comes back. (`handoff/`)
12. **CLI** — `ingest · plan · draft · check · edit · export · handoff · eval`. (`cli.py`)
13. **Eval** — runner, metrics, 3+ public-fixture cases, compare script. (`eval/`)

## Phase 2 — Web editor

Done means: a teacher can do everything in Phase 1 from a browser, clicking blocks instead of typing IDs.

- FastAPI service over the library (`api/`): upload, plan, draft, edit, export, lesson CRUD.
- Web client (`web/`, React + Vite): lesson view with block selection, instruction box with presets, before/after diff, affected-blocks panel, undo/redo, source pane with page image and span highlight.
- SQLite-backed lesson store behind the same interface as the JSON file store.

## Phase 3 — Multimodal

Primary English material is pictures and audio as much as text; this phase uses the rest of the Meta Model API for it.

- **Scanned pages** — Muse Spark vision: page image → spans with positions, picture descriptions for flashcards.
- **Audio tracks** — Muse Voice Transcribe: the unit's listening tracks → transcript spans, so *Listen and tick* activities cite the actual script.
- **Flashcards** ✅ — `classdeck illustrate` draws a picture for each vocabulary item without a textbook picture (Muse Image 1.0 through the `meta` endpoint; mock provider offline), cached by prompt hash and labelled *AI picture* in teacher mode.
- Word-level selection in the editor.
- Teacher study: 3–5 teachers, review sheet, `time_to_ready_min` and `manual_fix_ratio`.

## Next (design notes, not built)

A review of the seven Unit 1 decks found the same problems in most lessons: a tap colours an answer right or wrong at once (the first pupil gives the answer away), short exercises are cut over two slides, speaking prompts are shown one at a time, answer keys the page cannot confirm are shown as fact, and homework is only in the teacher's notes. The rules below are the planned fix; none of it is implemented.

### Classroom interaction rules

The deck is projected and driven by the teacher; one pupil's tap must never give the answer away to the class. Every interactive slide follows the same rules (export, all themes).

1. **Select, then Check.** In a choice, true/false, matching, picture matching or sound-sort exercise a tap only *selects* (a neutral outline in the theme's accent; a second tap on the same item clears it; in matching the pair gets a pair colour, the same on both sides, no number badges). Nothing is coloured right or wrong until **Check**, the slide's one primary button. Check marks each answered item right (✓ and green) or wrong (✗, red outline and the word *Try again*): colour is never the only cue. **Try again** clears the marks and the selections. **Show answers** is a teacher-mode control.
2. **Gap fill.** Blanks are neutral (no dashed red), the same width whatever the answer, with a large touch area. The word box is shown whenever the exercise has one; a used word is struck out. A blank shows its answer only through Show answers (all) or, in teacher mode, a tap on that blank.
3. **One exercise, one slide, when it fits.** An exercise of five items or fewer stays on one slide unless it cannot fit at 85% size; a cut never leaves a slide with a single item. When a matching exercise is cut, each slide's right column holds only the right items of that slide's pairs. The right column keeps the book's labels (a–e).
4. **Question sets.** `mode: prompts` (questions pupils talk or write about): all the questions on one slide as large numbered rows, no buttons, the prompt line under them, the activity timer when it has minutes. `mode: reveal` (a quiz, a rewrite, comprehension with a key): each row has its own **Reveal** that shows the answer under the question. There is no "Next question" / "Show all".
5. **Answer keys the page cannot confirm** (`key_source: ai`, e.g. a listening task without its script) are teacher-only: in pupil view the slide has no Check and no Show answers; teacher mode shows the key with a chip *AI key – check with the recording*. Keys from the page show a chip *Key: page* in teacher mode.
6. **Speech buttons** only where hearing the model matters: vocabulary words, dialogue lines, model phrases and reading paragraphs; never on exercise prompts or questions.
7. **Wrap-up is visible.** The recap slide shows pairs where the data has them (word – meaning, rule – first example, phrase – function; a summary bullet written `left – right`, with a spaced en dash, is a pair), and `summary.homework` is a pupil-facing **Homework** slide after it.
8. **Dialogue.** Each speaker's full name next to the avatar, one colour per speaker; lines not yet revealed are not drawn (no empty placeholders).
9. **Pictures that a task is about are big.** A warm-up or pre-reading slide with a picture gives it at least 60% of the stage; its pupil steps are questions for the pupils, never instructions copied from the teacher's steps.

### Frames: what code fixes and what the model fills

The cheap daily model should only fill short slots; everything that must be the same every time is code. For each lesson type (`planning/blueprints.py`) code builds the whole slide sequence: the phases and minutes, the exercises from the page in book order with their wording, the interaction of each exercise, the teacher steps of each exercise (templates per exercise kind and phase, with *Play track N.* first when the page has a track), the warm-up shape, the production shape, the recap shape and the homework slide. The model's reply holds only:

| slot | used by | shape |
|---|---|---|
| answer keys | every book exercise whose key is not deterministic from the page | as today; `key_source` is `ai` for listening tasks and general-knowledge quizzes, `page` otherwise |
| warm-up questions | types 1, 4, 5, 6 (about the page's picture: the code attaches it) | 2–3 pupil questions |
| quiz clues | type 2 (last lesson's words) and type 7 (the whole unit's words) warm-up | 6–8 clue → word items, rendered as a `reveal` question set; each word must be in the unit's pages |
| sentence game | type 3 warm-up | 3–4 sentences on the unit topic with a gap for the rule of the lesson |
| gist / prediction question | types 5, 6 | one question |
| sentence starters | production of types 1, 4, 5, 6 (speaking/writing frame) | 4–6 starters |
| model paragraph | type 6 | 60–90 words, using only facts from the lesson's pages, AI-added |
| recap pairs | wrap-up, every type | 3–6 pairs (left, right) |
| homework | wrap-up, every type | 1–2 lines |
| glosses / IPA | vocabulary of types 1, 2 | as today |

The model no longer writes teacher steps, pupil steps, activity titles or timings. A slot that fails validation after one re-prompt falls back to a template (a warm-up falls back to the page picture with *What can you see? What do you think the lesson is about?*); a lesson is always complete. Pupil-facing lines are validated: they must not repeat the instruction or the teacher steps, and lines such as *Listen to me* or *Remember …* are rejected.

## Out of scope

- Other subjects. The pipeline is written against the lesson model and the English rules are isolated in `english/`, so a second subject is possible, but no plugin interface is built until one exists.
- Grading pupils' work, pupil-facing chat, medical or psychological content of any kind.
- Automatic fact-checking of the source material itself.
- Multi-user collaboration and permissions.
