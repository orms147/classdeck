# ClassDeck

**Eye-catching, source-grounded lessons for primary-school English teachers.** From a textbook unit to a colourful slide deck pupils enjoy — where every point still traces back to the page it came from, every new word is checked against the grade's scope by code, and the teacher stays in control of every edit.

> Started during the Meta Global AI Developer Hackathon (Oct–Nov 2026), where it runs on the [Meta Model API](https://dev.meta.ai/docs/): Muse Spark 1.3 drafts and edits lessons, Muse Image 1.0 draws flashcard pictures (`classdeck illustrate`). It is model-agnostic: any OpenAI-compatible endpoint works, and inexpensive models are enough because the pipeline makes few, small calls. It also works with **no API at all** — a prompt pack hands the generative step to the free chat model a teacher already uses in the browser.

## The problem

Teachers spend hours turning a textbook unit into a lesson plan and a slide deck. Existing "document to slides" tools produce fluent output that is hard to verify: the teacher cannot tell which parts came from the book and which the model invented, and the vocabulary or sentence patterns are often pitched at the wrong level for the class. For an 8-year-old learning English, one out-of-scope tense or a handful of unknown words is the difference between a lesson that works and one that does not.

## What it does

1. **Upload your own material** — the unit from the textbook, your teaching notes (PDF, DOCX, PPTX).
2. **Extract and outline** — the content is split into addressable spans (page + paragraph), then outlined against the national curriculum (*Chương trình GDPT 2018*, English grades 3–5) using versioned curriculum config.
3. **Draft a lesson** — a lesson plan and slide deck made of typed **blocks** (vocabulary, sentence pattern, dialogue, phonics, activity, …). Each block carries its own ID and its own list of citations.
4. **Verify grounding** — every citation is checked against the source span it points to. Blocks the model added on its own are labelled **AI-added** so the teacher knows what to double-check.
5. **Check the level with code** — every new word is checked against the grade word list, every structure against the grade grammar scope, and the lesson against the four skills. These are deterministic checks, not the model's opinion.
6. **Edit by instruction** — select any block and say what to change (*"make it a pair-work activity"*, *"reduce new words to four"*, *"add a listening task"*). Only the selected block changes. You see before/after, accept or undo. If the change invalidates something else (drop two words → the drill that uses them is now wrong), the dependent block is **flagged for review, never silently rewritten**.
7. **Export** — an **HTML slide deck** made for the classroom (one offline file: picture-first flashcards, chat-bubble dialogues, tap-to-reveal patterns, phase colours, countdowns; teacher notes and sources one key-press away), plus PPTX slides and a DOCX lesson plan in the two-column *Teacher's activities / Students' activities* format with a source map appended.
8. **Or hand it off** — with no API key, `classdeck handoff` writes a prompt pack to paste into a free chat model; the reply is checked against the lesson the same way the pipeline checks its own model output.

## Why it is different

| | Typical doc-to-slides tool | ClassDeck |
|---|---|---|
| Where did this sentence come from? | Unknown | Citation to document, page, paragraph |
| Model added something extra | Blends in | Labelled *AI-added* |
| "Fix this one slide" | Regenerates the deck | Replaces exactly the selected block |
| Edit breaks a dependent activity | Silent | Flagged, teacher decides |
| Is this word right for grade 3? | Model's judgement | Checked against the curriculum word list |
| Is this structure in scope? | Model's judgement | Checked against the grade grammar scope |
| Looks like | A business template | Big pictures, chat bubbles, tap-to-reveal, colour per phase — designed for 8–10-year-olds |
| Needs | A paid API | Any cheap OpenAI-compatible model, or none (prompt pack) |

## Scope

**English (Tiếng Anh), grades 3–5**, textbook series *Global Success*, 35-minute periods. One subject, done properly: vocabulary, sentence patterns, phonics and skill-based activities (listening, speaking, reading, writing), with teacher guidance for each step.

## Architecture at a glance

```
 PDF / DOCX / PPTX
        │
        ▼
 ┌─────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
 │   Ingest    │───▶│   Planning   │───▶│   Drafting   │───▶│  Grounding   │
 │ pages+spans │    │ outline vs.  │    │ typed blocks │    │ verify every │
 │ (addressable│    │ curriculum   │    │ with         │    │ citation,    │
 │  text)      │    │ outcomes     │    │ citations    │    │ label extras │
 └─────────────┘    └──────────────┘    └──────────────┘    └──────┬───────┘
                                                                   │
                     ┌──────────────┐    ┌──────────────┐          ▼
                     │    Export    │◀───│   Editing    │◀── ┌──────────────┐
                     │ HTML deck,   │    │ select block │    │ Level checks │
                     │ PPTX, DOCX   │    │ instruct,    │    │ word list,   │
                     │ + source map │    │ diff, undo   │    │ grammar      │
                     └──────────────┘    └──────────────┘    │ scope, four  │
                            ▲                                │ skills       │
                     ┌──────┴───────┐                        └──────────────┘
                     │   Hand-off   │  prompt pack out → free chat model → reply checked in
                     └──────────────┘
      Shared core: lesson model (blocks + citations + dependency graph),
      versioned curriculum config, one provider interface for any OpenAI-compatible model
```

Full write-up: [docs/architecture.md](docs/architecture.md).

## Repository layout

```
docs/                 architecture and design documents
src/classdeck/     Python package
  ingest/             PDF / DOCX / PPTX → pages + spans
  lesson/             lesson model (blocks, citations, revisions) + storage
  planning/           outliner and drafter
  grounding/          citation linking and verification
  editing/            instruction-based edits, dependency checks, undo
  export/             HTML deck (primary), PPTX and DOCX writers, source map
  handoff/            prompt packs for a free chat model, and checking what comes back
  llm/                provider abstraction (any OpenAI-compatible API, mock, cache)
  english/            lesson structure, prompt rules, level validators, presets
  curriculum/         loader for versioned curriculum config
  api/                HTTP API (phase 2)
curriculum/           curriculum data, versioned (gdpt-2018/tieng-anh/…)
eval/                 evaluation cases, self-written textbook-style units (grades 3–5), runner
tests/                unit tests
web/                  block-level web editor (phase 2)
```

## Status

Phase 1 (core library + CLI) is functional end to end: the public fixture goes from PDF to an HTML deck, PPTX and DOCX offline, and the same pipeline runs on any OpenAI-compatible model — measured at about $0.002 per period on `openai/gpt-6-luna` through OpenRouter ([docs/llm-provider.md](docs/llm-provider.md)). See [docs/roadmap.md](docs/roadmap.md) for what is next (web editor, multimodal ingest).

## Quick start

```bash
# Python 3.12+
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env                              # optional: set MODEL_API_KEY for a real model

classdeck config                               # effective settings (keys masked)
classdeck ping                                 # one tiny request to confirm the key works
classdeck ping --endpoint meta                    # same, on the Meta Model API (META_API_KEY)
```

A whole period, offline (no key needed — the deterministic template pipeline):

```bash
export CLASSDECK_PROVIDER=mock                 # Windows: set CLASSDECK_PROVIDER=mock
classdeck ingest eval/fixtures/public/mini-unit-hello.pdf      # -> d_xxxxxxxx
classdeck plan d_xxxxxxxx --grade 3 --unit ta3.u1 --lesson 1 --period 1
classdeck draft out/outlines/ta3.u1-l1-p1.outline.json         # -> l_xxxxxx (blocks, checks)
classdeck export l_xxxxxx                                       # out/exports/l_xxxxxx/lesson.{html,pptx,docx}
```

Open `lesson.html` in a browser: arrow keys to move, tap to reveal, `T` for teacher mode (sources, notes, AI-added badges).

With a model (`CLASSDECK_PROVIDER=api`), `plan` refines the outline, `draft` writes richer blocks, and `edit` works:

```bash
classdeck edit l_xxxxxx "Reduce new words to four" -b b_xxxxxx        # preview: diff, affected blocks, checks
classdeck edit l_xxxxxx "Reduce new words to four" -b b_xxxxxx --apply
classdeck undo l_xxxxxx
```

Flashcards without a textbook picture can get one drawn by an image model (about $0.01 per picture on the Meta Model API; `--provider mock` draws placeholders offline):

```bash
classdeck illustrate l_xxxxxx --endpoint meta --dry-run     # list the words and the price
classdeck illustrate l_xxxxxx --endpoint meta --max 4       # then export again: pictures are marked "AI picture" in teacher mode
```

No API key at all? Hand the generative step to a free chat model in the browser:

```bash
classdeck handoff deck l_xxxxxx            # -> prompt pack; paste it, save the reply as deck.html
classdeck handoff check-deck l_xxxxxx deck.html   # verifies block ids and word list, embeds the pictures
classdeck handoff draft out/outlines/ta3.u1-l1-p1.outline.json     # -> draft pack (JSON reply)
classdeck handoff import-draft out/outlines/ta3.u1-l1-p1.outline.json reply.json
```

Measure: `classdeck eval eval/cases --provider mock --report out/eval/run.json` and `classdeck eval-compare before.json after.json`. The 20 cases cover nine self-written units in the textbook's shape (`eval/fixtures/public/units/*.json`, rendered to PDF/DOCX/PPTX by `pagekit.py`) across grades 3–5; real textbook pages stay private and never enter the repository.

## Documentation

- [Architecture](docs/architecture.md)
- [Lesson model](docs/lesson-model.md)
- [Source grounding](docs/source-grounding.md)
- [Planning: outliner and drafter](docs/planning.md)
- [Editing workflow](docs/editing.md)
- [English rules and level checks](docs/english-rules.md)
- [Output formats: HTML deck, PPTX, DOCX, prompt packs](docs/output-formats.md)
- [Curriculum config](docs/curriculum-config.md)
- [LLM provider](docs/llm-provider.md)
- [Evaluation](docs/evaluation.md)
- [Roadmap](docs/roadmap.md)

## License

MIT — see [LICENSE](LICENSE).
