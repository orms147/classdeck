# Lesson model

The lesson model is the contract between every stage of the pipeline. Ingest produces spans; planning and drafting produce blocks that cite spans; grounding and the level checks annotate blocks with results; editing replaces blocks and records revisions; export reads blocks. Nothing passes between stages as free text.

Everything here is implemented as Pydantic models in `src/classdeck/lesson/schema.py` and stored as one JSON file per lesson (`<lesson_id>.lesson.json`).

## Entities

```
SourceDocument ──< Page ──< Span
                                ▲
                                │ Citation (doc_id, page, span_id)
Lesson ──< Section ──< Block ───┘
   │                    │
   │                    ├── provenance: extracted | derived | generated
   │                    ├── depends_on: [block_id]
   │                    └── checks: [CheckResult]
   └──< Revision (append-only history for undo)
```

### SourceDocument, Page, Span

Produced by `ingest/`. A span is the smallest unit a citation can point at.

```json
{
  "doc_id": "d_8f2a1c",
  "filename": "english3-unit1.pdf",
  "sha256": "…",
  "media_type": "application/pdf",
  "extraction_version": 1,
  "pages": [
    {
      "number": 6,
      "spans": [
        { "span_id": "p6.s1", "kind": "heading",   "text": "Unit 1  Hello", "order": 1, "bbox": [60.0, 40.0, 300.0, 70.0] },
        { "span_id": "p6.s2", "kind": "list_item", "text": "1. Look, listen and repeat.", "order": 2, "bbox": [60.0, 90.0, 320.0, 108.0] },
        { "span_id": "p6.s3", "kind": "paragraph", "text": "Hello, I'm Ha.  Hi, Ha. I'm Tien.", "order": 3, "bbox": [60.0, 120.0, 520.0, 260.0] },
        { "span_id": "p6.s4", "kind": "image",     "text": "", "order": 4, "bbox": [60.0, 120.0, 520.0, 260.0], "image_path": "d_8f2a1c/p6_img1.png" },
        { "span_id": "p6.s5", "kind": "list_item", "text": "2. Listen, point and say.", "order": 5 },
        { "span_id": "p6.s6", "kind": "paragraph", "text": "Hello, I'm ___.  Hi, ___. I'm ___.", "order": 6 }
      ]
    }
  ]
}
```

- `span_id` is stable for a given `(sha256, extraction_version)`: re-ingesting the same file yields the same IDs, so citations survive.
- `kind` ∈ `paragraph | heading | list_item | table | caption | image`. Primary English pages are picture-driven, so `image` spans are first-class: they carry a cropped image file (`image_path`, relative to the ingest image directory — `<workdir>/images/` by default) and can be cited by vocabulary items and `image_ref` blocks.
- `bbox` is optional (DOCX/PPTX have no reliable coordinates); `page` for DOCX is a synthetic page based on section breaks, for PPTX it is the slide number.

### Lesson

```json
{
  "id": "l_3b9e",
  "schema_version": 1,
  "title": "Unit 1 Hello — Lesson 1, Period 1",
  "subject": "english",
  "grade": 3,
  "language": "en",
  "curriculum": {
    "version": "gdpt-2018",
    "unit_id": "ta3.u1",
    "lesson_no": 1,
    "period": 1,
    "outcome_ids": ["ta3.u1.o1", "ta3.u1.o2"]
  },
  "sources": [{ "doc_id": "d_8f2a1c", "filename": "english3-unit1.pdf", "sha256": "…" }],
  "sections": [ … ],
  "blocks": { "b_01": { … }, "b_02": { … } },
  "revisions": [ … ],
  "checks": [ { "name": "english.timing", "status": "pass", "detail": "35 min" } ],
  "created_at": "2026-10-27T10:12:00Z",
  "updated_at": "2026-10-27T10:40:00Z"
}
```

`blocks` is a map keyed by `block_id`; `sections` hold ordered `block_ids`. This keeps a block's identity independent of its position, so moving a block is a section edit, not a block rewrite.

A *Global Success* unit has three lessons of six activities each, taught over two 35-minute periods per lesson. A `Lesson` here is **one period**: that is what a teacher plans and what a slide deck covers.

### Section

A section is a phase of the period. Phases follow the presentation–practice–production shape used in Vietnamese primary English lesson plans (see [english-rules.md](english-rules.md)):

`warm_up` → `presentation` → `practice` → `production` → `wrap_up`

```json
{
  "id": "s_presentation",
  "phase": "presentation",
  "title": "Presentation: Look, listen and repeat",
  "minutes": 10,
  "block_ids": ["b_02", "b_03", "b_04", "b_05"]
}
```

### Block

A vocabulary block extracted from the page:

```json
{
  "id": "b_02",
  "kind": "vocabulary",
  "content": {
    "items": [
      { "word": "hello", "ipa": "/həˈləʊ/", "pos": "interjection", "meaning_vi": "xin chào", "example": "Hello, I'm Ha.", "image": { "doc_id": "d_8f2a1c", "page": 6, "span_id": "p6.s4" } },
      { "word": "hi",    "ipa": "/haɪ/",    "pos": "interjection", "meaning_vi": "chào",     "example": "Hi, Ha." }
    ]
  },
  "sources": [
    { "doc_id": "d_8f2a1c", "page": 6, "span_id": "p6.s3", "quote": "Hello, I'm Ha. Hi, Ha.", "confidence": 0.97 }
  ],
  "provenance": "extracted",
  "depends_on": [],
  "checks": [
    { "name": "grounding.citation_supported", "status": "pass", "detail": "lexical overlap 0.88" },
    { "name": "english.vocab_level",          "status": "pass", "detail": "2/2 lemmas in ta3 word list" },
    { "name": "english.ipa_present",          "status": "pass" }
  ]
}
```

An activity the model added on its own, depending on the pattern it drills:

```json
{
  "id": "b_07",
  "kind": "activity",
  "content": {
    "type": "game",
    "title": "Name chain",
    "skills": ["speaking", "listening"],
    "interaction": "whole_class",
    "minutes": 5,
    "instructions": "Pass the ball. Say 'Hello, I'm …' and your name, then pass it on.",
    "teacher_steps": [
      "Model the chain with two confident pupils.",
      "Start the ball; keep the pace quick.",
      "Praise clear pronunciation of 'Hello, I'm'."
    ],
    "student_steps": [
      "Catch the ball, say 'Hello, I'm (name).'",
      "Pass the ball to a friend."
    ],
    "materials": ["a soft ball"],
    "competency_tags": ["communication", "cooperation"]
  },
  "sources": [],
  "provenance": "generated",
  "depends_on": ["b_03"],
  "checks": [
    { "name": "english.vocab_level",   "status": "pass", "detail": "all lemmas in ta3 word list or cited spans" },
    { "name": "english.grammar_scope", "status": "pass" },
    { "name": "english.pattern_used",  "status": "pass", "detail": "uses pattern from b_03: Hello, I'm ___." }
  ],
  "teacher_note": "If the class is large, run two chains in parallel."
}
```

#### Block kinds

| kind | content fields | typical provenance |
|---|---|---|
| `objective` | `text`, `outcome_ids[]`, `skills[]` | derived (from curriculum) |
| `vocabulary` | `items[]: {word, ipa, pos, meaning_vi, example, image?}` | extracted / generated |
| `sentence_pattern` | `pattern`, `function`, `examples[]`, `notes` | extracted |
| `dialogue` | `context`, `lines[]: {speaker, text}` | extracted |
| `phonics` | `sound`, `letters`, `words[]`, `chant_lines[]` | extracted |
| `activity` | `type`, `title`, `skills[]`, `interaction`, `minutes`, `instructions`, `teacher_steps[]`, `student_steps[]`, `materials[]`, `competency_tags[]` | derived (from a textbook activity) / generated (warm-up, game) |
| `teacher_note` | `text` | generated |
| `summary` | `bullets[]` | derived |
| `image_ref` | `caption`, `alt` (the image itself is the cited span) | extracted |

`activity.type` ∈ `look_listen_repeat | listen_point_say | lets_talk | listen_and_do | look_complete_read | read_and_match | lets_write | song | chant | game | role_play | project | tpr | other` — the shapes a *Global Success* lesson is built from, plus the generic ones a teacher adds.

Content is a discriminated union on `kind`; unknown kinds fail validation. Adding a kind means adding a model, an export layout, and (if the kind is checkable) a validator.

#### Provenance

| value | meaning | shown to teacher as |
|---|---|---|
| `extracted` | Content restates cited spans; at least one citation verified. | Citation footnote |
| `derived` | Computed from other blocks or from curriculum config (an objective from outcomes; a *Listen, point and say* activity built from the page's instruction, the vocabulary and the pattern). | "Derived from …" |
| `generated` | The model added it without a supporting span (a warm-up game, an extra example). | **AI-added** badge |

Provenance is **assigned by the grounding stage, not by the model**. The drafter may *propose* citations; grounding verifies them and downgrades a block to `generated` when nothing supports it. See [source-grounding.md](source-grounding.md).

#### depends_on

A directed edge `activity → vocabulary` means "if the vocabulary changes, re-check the activity". Edges are set by the drafter (it knows which pattern an activity drills) and by the English rules (an activity whose text contains a vocabulary item or pattern depends on that block). The editing stage walks this graph to flag affected blocks. See [editing.md](editing.md).

#### checks

Results from grounding and the level validators. `status` ∈ `pass | warn | fail | stale`. Re-running a validator replaces its previous result of the same `name` on that block rather than appending a duplicate. A check becomes `stale` when the block or one of its dependencies changes; the editing stage re-runs checks on accept. Lesson-level results (`english.timing`, `english.skill_coverage`, `curriculum.outcome_covered`) live in `lesson.checks`.

### Revision

```json
{
  "id": "r_0007",
  "parent": "r_0006",
  "at": "2026-10-27T10:40:00Z",
  "actor": "teacher",
  "instruction": "Reduce new words to four",
  "target_block_ids": ["b_02"],
  "patch":   [ { "op": "replace", "path": "/blocks/b_02", "value": { … } } ],
  "inverse": [ { "op": "replace", "path": "/blocks/b_02", "value": { … } } ],
  "flagged_block_ids": ["b_06", "b_09"]
}
```

Revisions are JSON Patch (RFC 6902) pairs. Undo applies `inverse`; redo applies `patch`. The history is linear for the MVP (no branching).

## Invariants

1. Every `block_id` referenced from a section, a citation of another block, or `depends_on` exists in `blocks`, and each block's `id` equals its key.
2. Every `Citation` resolves to an existing span in a listed source (`sources[].doc_id`).
3. A block with `provenance = extracted` has ≥ 1 citation whose `grounding.citation_supported` check is `pass`.
4. A block with `provenance = derived` has ≥ 1 `depends_on` or a curriculum reference.
5. Every `vocabulary` item has an `ipa` transcription (`english.ipa_present`).
6. Every `activity` names ≥ 1 skill and has non-empty `teacher_steps` and `student_steps`.
7. Section minutes sum to the period length in curriculum config (35) within ±2 (`english.timing`).

Invariants 1 and 2 are enforced at load/save (1 by the schema, 2 by the store, which has the source documents). 3–7 are enforced by the grounding stage and the English validators and surface as `fail`/`warn` checks rather than exceptions, so a teacher can still see and fix a flawed draft.
