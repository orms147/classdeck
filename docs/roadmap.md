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

## Out of scope

- Other subjects. The pipeline is written against the lesson model and the English rules are isolated in `english/`, so a second subject is possible, but no plugin interface is built until one exists.
- Grading pupils' work, pupil-facing chat, medical or psychological content of any kind.
- Automatic fact-checking of the source material itself.
- Multi-user collaboration and permissions.
