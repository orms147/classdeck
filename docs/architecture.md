# Architecture

ClassDeck turns a teacher's own material into a lesson plan and a slide deck for one 35-minute primary-English period, where every block of content can be traced to its source, checked against the grade's language scope by code, and edited one block at a time under the teacher's control.

This document is the map. The other documents in this folder are the territory: follow the links for each part.

## 1. Pipeline

```
                 ┌───────────────────────────── core library (src/classdeck) ─────────────────────────────┐
                 │                                                                                            │
 teacher's ──▶ ingest ──▶ SourceDocument ──▶ planning ──▶ Outline ──▶ planning ──▶ Lesson (draft) ──▶ grounding
 PDF/DOCX/PPTX   │        (pages, spans)    (outliner)              (drafter)     typed blocks with   verify citations,
                 │                               ▲                      ▲         proposed citations  assign provenance
                 │                               │                      │                                   │
                 │                        curriculum config      English rules                              ▼
                 │                        (outcomes, words,      (phases, kinds,                     english validators
                 │                         grammar scope)         prompt rules)                      word list, grammar,
                 │                                                                                   skills, IPA, timing
                 │                                                                                          │
                 │        ◀──────────────────────── editing ◀──────────────────────────── Lesson (checked) ◀┘
                 │        select blocks → instruction → EditProposal → preview → accept/undo
                 │                                                        │
                 │                                                        ▼
                 │                                                     export ──▶ PPTX + DOCX (+ source map)
                 └────────────────────────────────────────────────────────────────────────────────────────────┘
                                                        ▲
                                     CLI (phase 1)  ────┤
                                     HTTP API + web editor (phase 2)
```

| stage | input → output | module | document |
|---|---|---|---|
| Ingest | file → `SourceDocument` (pages, spans with stable IDs, cropped images) | `ingest/` | [source-grounding.md §1](source-grounding.md#1-addressable-spans) |
| Planning: outline | `SourceDocument` + curriculum unit + period → `Outline` (sections ↔ outcomes ↔ span IDs) | `planning/outliner.py` | [planning.md](planning.md) |
| Planning: draft | `Outline` + section spans → `Lesson` with typed blocks and proposed citations | `planning/drafter.py` | [planning.md](planning.md) |
| Grounding | `Lesson` → `Lesson` with verified citations, provenance, `grounding.*` checks | `grounding/` | [source-grounding.md §3](source-grounding.md#3-citation-verification) |
| English validators | `Lesson` → `Lesson` with `english.*` and `curriculum.*` checks, inferred `depends_on` | `english/` | [english-rules.md](english-rules.md#validators) |
| Repair (optional, model) | `Lesson` with level warnings on model-written blocks → `Lesson` with those blocks reworded, one round | `planning/repair.py` | [planning.md §Repair round](planning.md#repair-round) |
| Editing | `Lesson` + selection + instruction → `EditProposal`; accept → `Lesson` + `Revision` | `editing/` | [editing.md](editing.md) |
| Export | `Lesson` → `.html` deck (primary), `.pptx`, `.docx` | `export/` | [output-formats.md](output-formats.md) |
| Hand-off (optional) | `Lesson`/`Outline` → prompt pack for a free chat model; reply → checked deck / validated draft | `handoff/` | [output-formats.md](output-formats.md#prompt-packs-handoff) |

Grounding and the validators run twice: after drafting, and on every edit proposal before the teacher sees it. `pipeline.py` composes draft → grounding → validators → repair once, for the CLI, eval and the hand-off import ([planning.md §Shared pipeline](planning.md#shared-pipeline-pipelinepy)).

## 2. The one data structure

Everything is a transformation of the **lesson model** ([lesson-model.md](lesson-model.md)): a `Lesson` holds ordered `Section`s of `Block`s; each `Block` has an ID, a typed `content`, `sources` (citations to spans), a `provenance` label, `depends_on` edges, and `checks`. A `Lesson` is one JSON file. Stages read it and return a new one; the store writes it atomically.

Three consequences shape the rest of the design:

- **Blocks are the unit of everything** — citation, editing, dependency, export layout. Nothing is addressed by text position.
- **The model proposes, the engine decides.** The drafter and the editor return *proposals* validated against the pipeline's own small schemas; grounding assigns provenance, validators assign checks, the editing engine computes affected blocks. The model never writes provenance or checks.
- **Deterministic where it can be.** Word lists, grammar scope, skill coverage, timing, IPA presence and pattern use are plain code over curriculum data. The model is used for what needs language understanding: outlining, drafting, borderline citation judgement, and rewriting on instruction.

## 3. Modules

```
src/classdeck/
  config.py        Settings from env / .env (pydantic-settings)
  cli.py           typer app: config · ping · ingest · plan · draft · check · edit · export · eval
  pipeline.py      build_lesson() / finish_lesson(): the composed stages every draft goes through
  lesson/          schema.py (Pydantic models), store.py (JSON files, atomic writes, invariants), ids.py
  ingest/          base.py (Extractor protocol), pdf.py (PyMuPDF), docx.py, pptx.py
  curriculum/      models.py (YAML schema), loader.py (lookup, word list, grammar scope, search)
  llm/             base.py (Message, LLMProvider, UsageMeter), openai_compat.py, mock.py, cache.py,
                   structured.py (complete_json), factory.py (generator / judge roles), pricing.py,
                   prompts.py + prompts/*.md
  planning/        models.py, segment.py (instruction-line chunking, BM25 fallback), outliner.py, drafter.py,
                   repair.py (one-round repair of model-written blocks that fail the level checks)
  grounding/       linker.py (resolve citations), verifier.py (overlap + judge), provenance.py
  english/         rules.py, validators.py, dependencies.py, presets.py, templates/
  editing/         context.py, proposal.py, dependencies.py (affected set), history.py (JSON Patch)
  export/          html.py (+ deck_template.html, themes/), pptx.py, docx.py, source_map.py
  handoff/         deck_pack.py, draft_pack.py, check_deck.py, import_draft.py
  api/             FastAPI app and routes (phase 2)
```

Dependency direction: `cli`/`api` → `pipeline` → stages → `lesson`, `llm`, `curriculum`, `english`. `lesson` depends on nothing else in the package. `english` depends on `lesson` and `curriculum` only. No stage imports another stage; `pipeline.py` composes them for the CLI, eval and the hand-off import.

## 4. Boundaries and trust

| boundary | rule |
|---|---|
| Model output | Always parsed into a Pydantic model (`llm/structured.py`) and then validated by the receiving stage. Free text from the model never reaches the lesson file. |
| Source documents | Treated as untrusted input: parsers run on the file only, span text is data, never executed or templated. Copyrighted material stays in `eval/fixtures/private/` (git-ignored). |
| Curriculum data | Versioned YAML, validated on load. A lesson records the version it was built against. |
| Teacher edits | Recorded as revisions with inverses; nothing is lost on accept, undo is one step. |
| Secrets | Only `config.py` reads the environment. The API key is a `SecretStr` and is masked in every printout. |

## 5. Run modes

- **CLI, mock provider** — offline, deterministic, free: skeleton outline + template drafter. Used for development and CI.
- **CLI, any OpenAI-compatible API** — real drafting and edits on whatever `MODEL_API_BASE_URL` points at; inexpensive models are the intended daily driver. Responses cached on disk by prompt hash so re-runs cost nothing.
- **CLI, no API** — the mock pipeline plus prompt packs: the teacher's own free chat model does the generative step, ClassDeck checks the result.
- **API + web** (phase 2) — the same library behind FastAPI; the web client only ever calls the API.

## 6. Error handling

- A failed check is **data**, not an exception: it lands in `block.checks` with `fail`/`warn` and is shown to the teacher. Export refuses on `fail`, proceeds with a notice on `warn`.
- Model and network failures raise `LLMError` and abort the stage; the lesson file is never left half-written (atomic save).
- A proposal the engine rejects (kind changed, out-of-selection edit) is re-prompted once with the violation named; a second failure is reported to the teacher as "could not produce a valid edit", with the model's rationale.

## 7. Where the design decisions are recorded

| decision | where |
|---|---|
| Block-based lesson model with citations and provenance | [lesson-model.md](lesson-model.md) |
| Citation verification = lexical overlap + model judge for the borderline band | [source-grounding.md](source-grounding.md) |
| Dependent blocks are flagged, never auto-rewritten | [editing.md](editing.md) |
| English scope checks are deterministic over curriculum data | [english-rules.md](english-rules.md), [curriculum-config.md](curriculum-config.md) |
| One provider interface; Chat Completions on the Meta Model API; mock + cache | [llm-provider.md](llm-provider.md) |
| Measure with fixed eval cases, public fixtures in CI, private fixtures locally | [evaluation.md](evaluation.md) |
| Phases: library + CLI first, web second, multimodal third | [roadmap.md](roadmap.md) |
