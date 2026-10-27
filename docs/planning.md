# Planning: outliner and drafter

Planning turns an ingested document plus a curriculum unit into a `Lesson` draft. It is two stages with one principle: **a deterministic skeleton first, the model refines second**. Everything the skeleton can decide from the textbook's structure and the curriculum config (phases, timing, which activity drills what, which spans belong to which activity) is decided without a model call; the model fills in language that needs judgement. With no model at all (mock mode), planning still produces a complete, valid, citable lesson — that is what offline eval and CI run on.

## Inputs

| input | from |
|---|---|
| `SourceDocument` (one or more) | `ingest/` |
| `Curriculum`, `unit_id`, `lesson_no`, `period`, `grade` | `curriculum/` + CLI args |
| `LESSON_SHAPES`, `PHASES`, `phase_for`, activity templates, `prompt_rules` | `english/rules.py` |
| `LLMProvider` or `None` | `llm/factory.py` |

## Stage 1 — Outliner → `Outline`

```json
{
  "title": "Unit 1 Hello — Lesson 1, Period 1",
  "grade": 3,
  "curriculum": { "version": "gdpt-2018", "unit_id": "ta3.u1", "lesson_no": 1, "period": 1, "outcome_ids": ["ta3.u1.o1", "ta3.u1.o2"] },
  "doc_ids": ["d_8f2a1c"],
  "sections": [
    { "phase": "warm_up",      "title": "Warm-up: listen and do", "minutes": 5,  "activity_type": "tpr", "outcome_ids": [], "span_refs": [], "notes": "Classroom-language TPR only; the pattern is first taught in the presentation." },
    { "phase": "presentation", "title": "1. Look, listen and repeat", "minutes": 10, "activity_type": "look_listen_repeat", "outcome_ids": ["ta3.u1.o1", "ta3.u1.o2"], "span_refs": [{ "doc_id": "d_8f2a1c", "page": 1, "span_id": "p1.s2" }, { "doc_id": "d_8f2a1c", "page": 1, "span_id": "p1.s3" }, { "doc_id": "d_8f2a1c", "page": 1, "span_id": "p1.s4" }], "notes": "Dialogue introduces hello / hi and the pattern Hello, I'm ___." },
    { "phase": "practice",     "title": "2. Listen, point and say", "minutes": 12, "activity_type": "listen_point_say", "outcome_ids": ["ta3.u1.o2"], "span_refs": [ … ], "notes": "" },
    { "phase": "production",   "title": "3. Let's talk", "minutes": 6, "activity_type": "lets_talk", "outcome_ids": ["ta3.u1.o1", "ta3.u1.o2"], "span_refs": [ … ], "notes": "" },
    { "phase": "wrap_up",      "title": "Wrap-up", "minutes": 2, "activity_type": null, "outcome_ids": [], "span_refs": [], "notes": "Recap words and pattern." }
  ]
}
```

### 1a. Skeleton (deterministic)

1. `LESSON_SHAPES[(lesson_no, period)]` gives the ordered textbook activity types for this period (e.g. `look_listen_repeat, listen_point_say, lets_talk`). Each becomes a section with `phase = phase_for(type)` and the phase's default minutes; `warm_up` and `wrap_up` sections are added around them. Minutes are then scaled so the total equals `curriculum.period_minutes`. Period 2 has its own shape, see [Period 2 shape](#period-2-shape).

   **The warm-up never drills language the pupils have not been taught.** Lesson 1, period 1 is the first time the unit's pattern appears, so its warm-up is marked `activity_type = tpr` (title *Warm-up: listen and do*): a total-physical-response routine in classroom language only (*Stand up. Sit down. Clap. Point to the board.*), with no greeting, no unit word and no pattern. The model's warm-up guidance says the same: the new pattern is taught from the presentation onward; a warm-up recycles earlier units or is a TPR. Every later period keeps the pass-the-ball pattern game (`game`), because the pattern has been taught by then. (The curriculum carries no song data, so a unit-specific `song` warm-up is not chosen.)
2. **Segmentation.** Spans are scanned in reading order for *activity instruction lines* — `list_item` spans matching `^\s*(\d{1,2})[.)]\s` — and the document is cut into chunks: an instruction span and everything after it up to the next instruction. Chunk *n* is textbook activity *n*. Period 1 of a lesson covers activities 1–3, period 2 covers 4–6, so section *i* of the shape gets chunk `(period - 1) * 3 + i`. Headings, and spans before the first instruction, attach to the first chunk of the page.
3. When segmentation finds no instruction lines (notes, slides), fall back to **BM25 retrieval** (`rank_bm25`) over span texts with the activity template's title as the query, top 6 spans per section.
4. `outcome_ids` per section: an outcome goes to a content section only when its **terms** occur in the section's chunk. The terms of an outcome are its `keywords` plus the words of the unit's vocabulary and patterns that the outcome text itself mentions (`Say goodbye using Goodbye / Bye` names `goodbye` and `bye`; `Introduce oneself using "I'm …"` names `i'm`), minus function words; words are compared by lemma and plain stem, so `greetings` matches `greet`. The chunk is the section's text spans, instruction line included. An outcome no chunk of the period mentions is assigned to no section and so is **not** in the lesson: `curriculum.outcome_ids` is the union of the sections' `outcome_ids` in curriculum order, and the objectives are derived from that list, so a lesson never lists an outcome (say, saying goodbye) that none of its activities teaches. Only when no outcome overlaps any chunk of the period (notes, slides, a unit the sources do not match) does the outliner fall back to the old skill rule — the unit's outcomes whose `skills` intersect the template's default skills — for every section, and it logs that. `warm_up`/`wrap_up` get none. `title` is the instruction span's text when found, else the template title.

### 1b. Refinement (model, optional)

With a provider, one structured call (`complete_json` → `OutlineRefinement`) receives the skeleton, each section's chunk text (span IDs in front of every line), the unit's outcomes, vocabulary and patterns, and `prompt_rules(grade)`. It may: rewrite titles, reassign `outcome_ids` from the given list (only outcomes whose words the section's spans contain), write `notes` for the drafter, and move a span ref between adjacent sections. The prompt also says a section title must describe what the section's blocks will contain, and that the wrap-up title must not promise language the unit's vocabulary and patterns lack ("Say goodbye" when goodbye is not taught). The lesson's outcomes stay the union of the refined sections' `outcome_ids`. It may not move spans into or out of a `review` warm-up (the review spans are the engine's), add or remove sections, change phases, or invent span IDs — the engine validates the reply against the skeleton and rejects any of those (one re-prompt, then keep the skeleton).

## Stage 2 — Drafter → `Lesson`

Per section, in order:

| phase / type | drafted by | how |
|---|---|---|
| `objective` blocks (first section) | engine | one per `outcome_id`, text from the curriculum, `provenance = derived` |
| `look_listen_repeat` | model (or template) | `dialogue` from the chunk's paragraph spans, `vocabulary` from words in the dialogue that are in the unit's vocabulary (IPA filled later), `sentence_pattern` from the unit's patterns that occur in the dialogue, then the `activity` |
| `listen_point_say`, `listen_and_do`, `look_complete_read`, `read_and_match` | model (or template) | one `activity` drilling the section's pattern and words; `image_ref` for pictures in the chunk; `listen_and_do` and `look_complete_read` end their `teacher_steps` with an [answer key](#answer-keys) |
| `lets_talk`, `song`, `chant`, `game`, `role_play`, `project`, `lets_write` | model (or template) | one `activity` |
| phonics (`look_listen_repeat` in lesson 3) | model (or template) | `phonics` block + `activity` |
| `warm_up` | model (or template) | one `tpr` activity in lesson 1, period 1 (classroom language only), else one `game` or `song`; no new language, no citation |
| `presentation` with `review: true` (period 2) | engine, then model (or template) | `vocabulary` + `sentence_pattern` (+ `phonics`) built from the review spans exactly as the presentation drafter builds them (template-built, cited), then the drafter's one *Say it again* `look_listen_repeat` activity — see [Period 2 shape](#period-2-shape) |
| `wrap_up` | engine | `summary` from the lesson's vocabulary, patterns and sounds (`derived`), the two-minute recap activity *Say it together* (see below), one `teacher_note` |

### The wrap-up recap activity

The wrap-up is more than a summary and a note: a section titled "Say goodbye" with no steps cannot be taught. Both drafting paths therefore end with an engine-built `activity` of type `other`: *Say it together*, 2 minutes, whole class, skills listening and speaking, instructions "Say the words and the pattern together." Its `student_steps` are "Listen to the teacher.", "Say the words: {{words}}", one "Say: {{pattern}}" per pattern and one "Say the sound: …" per phonics block; its `teacher_steps` point at the summary on the board. It is `derived` (no span to cite), depends on the summary and the vocabulary, pattern and phonics blocks the summary lists, and is left out of the grounding ratios because it cites nothing.

### Model drafting

One structured call per section (`complete_json` → `DraftSection`). The prompt contains: the section from the outline, the chunk's spans as `[p1.s3] text` lines, the activity template for the type (default steps as a starting point), the unit's vocabulary/patterns/phonics for the lesson, the curriculum outcomes for the section, `prompt_rules(grade)`, and the **allowed words** — `curriculum.word_list(unit_id)`, i.e. the grade list ∪ classroom language ∪ the unit's words (about 300 words), with the rule that pupil-facing text (instructions, student steps, dialogue lines, examples) may use only these words, pupils' names and numbers, while `teacher_steps` may use any English. This is the same list `english.vocab_level` checks against, so the model is told the rule it will be measured by. The reply:

```json
{
  "blocks": [
    { "kind": "dialogue", "content": { "context": "Ha meets Tien at school.", "lines": [ { "speaker": "Ha", "text": "Hello, I'm Ha." }, { "speaker": "Tien", "text": "Hi, Ha. I'm Tien." } ] }, "span_ids": ["p1.s3", "p1.s4"], "depends_on": [] },
    { "kind": "activity", "content": { … }, "span_ids": ["p1.s2"], "depends_on": [0], "teacher_note": "…" }
  ]
}
```

- `span_ids` are the model's **proposed** citations; `depends_on` are indices into this reply's `blocks` (the engine turns them into block IDs).
- The engine assigns IDs (`new_id("b")`), resolves `span_ids` to `Citation`s (doc_id and page from the outline's span refs; `quote` = first 80 characters of the span), sets `provenance = extracted` when there is ≥ 1 citation and `generated` otherwise — **a proposal only; grounding decides** — and appends the blocks to the section.
- Phase guidance for a `warm_up` requires concrete rules in 3–5 student steps — who starts, what each pupil says, how the turn passes, how the game ends — and names any material; the template game is the fallback shape. The warm-up recycles earlier language and never drills the new pattern; the TPR warm-up of lesson 1, period 1 is told to use classroom language only. The review presentation of a period 2 asks for one `look_listen_repeat` activity titled *Say it again*.
- **Practice and production steps are concrete.** The guidance for `practice` and `production` (and `prompt_rules`) requires `teacher_steps` that say what to point at or show, who speaks first, how many rounds to run and how to check, and `student_steps` of 3–5 concrete imperatives naming the action and who does it. The `listen_point_say` and `lets_talk` templates follow the same shape: they name the picture card, make *Pupil A* (the pupil on the left) speak first, run two rounds then swap, and end with a check, with every pupil-facing word inside the grade-3 list plus classroom language.
- **The textbook's wording is kept.** `draft_section.md` and `repair_block.md` say that patterns and dialogue lines are copied exactly, contractions included (*I'm*, never *I am*), and that no pattern or phrase the unit does not teach is introduced (no *Nice to meet you* unless it is a unit pattern or in the spans). The deterministic check is `english.wording_kept` ([english-rules.md](english-rules.md#validators)).
- Practice and production guidance (and `draft_section.md` itself) tell the model to copy lyric and option lines verbatim and cite them, as the template drafter does.
- Replies are validated: unknown kinds, kinds outside `DRAFTABLE_BLOCK_KINDS`, span IDs not in the section's chunk, or `depends_on` out of range → one re-prompt naming the problem, then fall back to the template drafter for that section.

### Template drafting (no model)

`TemplateDrafter` builds the same block kinds from the chunk alone: `dialogue` from consecutive short paragraphs that contain a quotation-like exchange (two or more sentences with first-person pronouns or greetings), `vocabulary` from the intersection of chunk words with the unit vocabulary, `sentence_pattern` from unit patterns whose slot-filled regex matches a chunk line, `activity` from the activity template with `{{pattern}}`/`{{words}}` substituted, citations to every span used. It is used when the provider is `None`, when a model reply fails validation twice, and in CI. **Lyrics and options are copied, not paraphrased.** For a `song` or `chant` section the activity's instructions are "Sing together:" / "Chant together:" and its `student_steps` are the chunk's lyric lines (every line of every content span, no headings, pictures or instruction line), one per step, verbatim; for `listen_and_do` the steps are "Listen. Tick: <option line>" per line followed by the template's closing step, for `look_complete_read` "Complete: <gap-fill line>" followed by the closing step. The activity cites the instruction span and every span it copied, depends on the section's vocabulary and pattern blocks when present (inference adds the rest) and is therefore `derived`. Grounding compares an activity's title and instructions with each cited span and, when that fails, its pupil steps too (`grounding/linker.steps_text`), so a song or chant that repeats the textbook's lines is supported by them. Its output is deliberately plain; it exists so that grounding, validators, editing, export and eval have real input without a model.

### Answer keys

`listen_and_do` and `look_complete_read` have right answers, so a teacher must be able to check them. Both drafting paths end the activity's `teacher_steps` with one answer-key step:

- **Template path.** `SectionContext.reference_lines` holds the text lines of the outline's other sections (dialogue, frames, lyrics; not headings, instructions, pictures or another exercise's options; in period 2 that includes the review spans of activities 1-2). For *listen and tick / number / circle* the section's `a. ... b. ...` options are split; the options that equal a sentence of the lesson run, or match one of its frames or the unit's patterns (`pattern_regex`), are the key, provided they are some but not all of the options: `Answer key: a. Hello, I'm Duc. (it matches the lesson's dialogue and pattern; confirm it with the audio)`. For *look, complete and read* each gap-fill line (`_____` slots) is matched, a slot being one or two words, against the lesson run's dialogue; failing that the slots are filled with the names introduced in the dialogue (*I'm Ha*) and the line is kept when it matches a unit pattern: `Answer key: Hello, I'm Ha. Hi, Ha. I'm Tien.` When a key cannot be derived the step is `Play the audio twice; check the answers together on the board.`
- **Model path.** `draft_section.md` requires an `Answer key: ...` step for these two types. A reply that has none gets the template's step appended by the engine (`add_answer_keys`), so the teacher always has one.

### After drafting

`draft_lesson_report()` returns the lesson together with a `DraftReport` whose `model_block_ids` are the ids of the blocks a model wrote (accepted replies only; engine-built objectives, review blocks, wrap-up and template fallbacks are not in it). `draft_lesson()` is the same call without the report. Everything that follows — dependency inference, grounding, level checks, the repair round — is composed once, in `pipeline.py`.

## Shared pipeline (`pipeline.py`)

The steps every draft goes through live in one module so that the CLI, eval and the hand-off import cannot drift apart:

```python
build_lesson(outline, sources, curriculum, provider, *, repair=True, judge=None, on_stage=None) -> Lesson
finish_lesson(lesson, sources, curriculum, provider, *, on_stage=None) -> Lesson
```

- `build_lesson` = `draft_lesson_report` → `english.dependencies.infer` → `grounding.fill_quotes` → `grounding.verify_lesson(lesson, sources, judge if judge is not None else provider)` → `english.validators.run_all` → [repair round](#repair-round) → `run_all` again (only when the repair replaced a block). A model failure (`LLMError`) during the repair is logged and the lesson stays as drafted; the repair is an improvement, not a stage the lesson depends on. `sources` is a mapping `doc_id → SourceDocument`, as the stores hand it out. `judge` lets a caller use a different model for the borderline citation judge; the product pipeline passes none and the generator judges.
- `finish_lesson` is the re-check: `infer` → `fill_quotes` → `verify_lesson` → `run_all`. It is what `classdeck check` runs on a saved lesson and what the hand-off import runs on an assembled one (with no provider: midpoint rule).
- `on_stage(name)` is an optional callback called when a stage starts (`draft`, `grounding`, `validators`, `repair`); eval uses it to time stages and to attribute token usage to them.

Callers: `cli.draft` (`--no-repair` turns the repair off), `cli.check` (`finish_lesson`), `evaluation.runner` (`build_lesson`, repair on) and `handoff.import_draft` (`finish_lesson`, no provider). `planning` itself stays a pair of stages; the module list in [architecture.md](architecture.md#3-modules) names `pipeline.py` as the one place that composes them.

## Repair round

The level checks are deterministic and the model does not see them, so a model-drafted period typically ends with a handful of `english.vocab_level` warnings on pupil-facing text ("greet the player", "complete the frames", "introductions") and the odd `english.grammar_scope` hit. The repair round (`planning/repair.py`, prompt `llm/prompts/repair_block.md`) gives the model one chance to fix exactly those blocks:

1. **Candidates**: blocks that carry a `warn` on `english.vocab_level`, `english.grammar_scope` or `english.wording_kept` (a contraction written out) **and** whose id is in `DraftReport.model_block_ids` **and** whose provenance is not `extracted`. Template-built blocks are never sent: their wording is ours to fix in `templates/activities.yaml`. At most 8 blocks per lesson, in lesson order; one round only (`MAX_ROUNDS = 1`); skipped entirely when there is no provider.
2. **One compact prompt per block**, temperature 0: the block as JSON (`id`, `kind`, `content`), the offending words and grammar hits from the check details, the allowed words (`curriculum.word_list(unit_id)` sorted, plus the names found in the block's cited spans), `prompt_rules(grade)`, and the instruction to return the same block — same `id`, same `kind` — with the pupil-facing text rewritten using only allowed words and in-scope grammar, changing nothing else. The problems come from the check details, so a `wording_kept` warning names the offending phrase (`contraction written out in pupil text: "I am" (the unit writes "I'm")`) and the prompt tells the model to put the contraction back and to add no pattern the unit does not teach. The prompt demands concreteness: rephrase each step with allowed words, never replace a concrete step by a vague one ("Play the game."), keep the number of steps and roughly the length, name the object, the action and who speaks.
3. **The engine decides**: the reply is parsed as `{id, kind, content}`; `id` or `kind` changed → kept. Only the **repairable pupil-facing text** of `content` is taken from the reply, and **cited text is never rewritten**: an activity's `instructions` and `student_steps` are always repairable; for other kinds only `generated` blocks are, and then only a pattern's `examples`, a vocabulary item's `example` and a dialogue's line texts when the dialogue carries no citation — never a vocabulary `word`, never a `pattern`, never anything of an `extracted` block (which is not even a candidate: the grounding stage has already tied its words to the textbook, as in "I'm" kept as "I'm"). Title, teacher steps, materials, skills, timing, tags, speakers, glosses and pictures stay as they were, and a reply that changes the number of dialogue lines, vocabulary items or student steps, or that cuts an instruction or a student step to less than half of its words (a concrete step turned vague), is kept out. The original block with that content is validated with `BlockAdapter`; invalid → kept. The validators are re-run on the candidate and it is accepted only if its number of offending words, hits and written-out contractions **strictly decreased**, its grammar hits did not increase (no trading words for hits) and no check on it turned `fail` (an activity that lost its steps is not a fix). Otherwise the original stays.
4. Sources, provenance, `depends_on`, checks and teacher notes of a replaced block are untouched; the repair changes wording, not what the block cites or depends on. `run_all` then refreshes the checks. A one-line summary is logged: candidates, sent, replaced, kept.

The round costs one small call per offending block (≈ 1.5k input tokens, mostly the word list) and nothing when the draft is already clean.

## Period 2 shape

A period-2 lesson (textbook activities 4-6) has no *Look, listen and repeat*, so a straight mapping gives it no `vocabulary` or `sentence_pattern` block, no presentation stage and cramped practice: its activities drill nothing the lesson names, `english.pattern_used` can only warn, and model-drafted activities end up `generated` with no supported citation. Real period-2 lessons restate the words and the pattern of the previous period before practising, and so does the skeleton. A period 2 has this shape (minutes scale when the period is not 35):

| # | phase | min | content |
|---|---|---|---|
| 1 | `warm_up` | 4 | quick pattern game (`game`); the pattern was taught in period 1, so no new language |
| 2 | `presentation` (`review: true`) | 8 | the review: `vocabulary` + `sentence_pattern` (+ `phonics` in lesson 3) built from the review spans, and a short *Say it again* `look_listen_repeat` activity |
| 3 | `practice` | 8 | textbook activity 4 |
| 4 | `practice` | 7 | textbook activity 5 (activities 4 and 5 together about 15 min) |
| 5 | `production` | 6 | textbook activity 6 |
| 6 | `wrap_up` | 2 | recap |

Period 1 keeps its five-section shape. The review is the presentation of the period, not a warm-up, so `review = true` sits on the `presentation` section:

- **Outliner**: for `period > 1` the review section gets `phase = presentation`, `activity_type = look_listen_repeat`, the title *Review: say it again*, `review: true` and `span_refs` holding the text spans (no headings, instruction lines or images) of the same lesson run's activities 1-2 chunks (`_lesson_chunks` already resolves the run; chunks 1 and 2 of it are the dialogue and the *Listen, point and say* frames). The three textbook activities take the phases practice, practice, production whatever `phase_for` says (`lets_write` of lesson 3 practises). When the run has no chunk 1 or 2 (a source holding only activities 4-6, or notes with no instruction lines) the review is skipped and logged, and the period keeps the five-section shape with the plain warm-up and `phase_for` phases.
- **Drafter**: for a review section `draft_lesson` first builds the review blocks with the template drafter's own `_vocabulary` and `_patterns` on the section's spans (a `vocabulary` block of the unit words that occur in them and a `sentence_pattern` block per unit pattern that matches a span, each citing the spans it was built from), then lets the drafter (model or template) write the one *Say it again* activity (the template's own: *Look at the board / Listen to the teacher / Repeat the words: ... / Say the pattern: ...*); its `depends_on` points at the review blocks, which makes it `derived`. The model's prompt says the engine already built the blocks and asks for the activity alone. Whatever else a model reply holds is dropped (`review_proposal`): of its blocks only the first `activity` is kept, with its proposed citations and dependencies cleared; a reply with no activity gives way to the template's. The hand-off import does the same.
- **Downstream**: grounding verifies the review citations (near-verbatim, so `extracted`); `infer` adds `activity -> vocabulary / sentence_pattern` edges for activities 4-6 that use the words or the pattern, which makes the textbook-built ones `derived`; `pattern_used` has a pattern to find. For a lesson-3 period 2 (`lesson_no == 3`) the review also carries the `phonics` blocks of the sounds whose words occur in the review spans (activity 1's *Listen and repeat* in a lesson-3 run), cited to those spans, so the review activity and the wrap-up summary name the sound; lessons 1-2 have no phonics to review. The templates for *Listen and tick*, *Look, complete and read*, *Read and match* and *Let's sing* carry the pattern in a teacher step for the same reason. The wrap-up summary lists the reviewed words and pattern instead of falling back to the unit's whole list.

## Module layout

```
pipeline.py      build_lesson(), finish_lesson(): the composed stages (top level, next to cli.py)
planning/
  models.py      Outline, OutlineSection (review flag on the period-2 presentation), SpanRef, OutlineRefinement, DraftSection, DraftBlock
  segment.py     instruction-line detection and chunking; BM25 fallback (retrieval.py if it grows)
  outliner.py    build_skeleton(), refine_outline(), outline()
  drafter.py     draft_lesson(), draft_lesson_report(), DraftReport, ModelDrafter, TemplateDrafter,
                 review blocks, reply validation
  repair.py      repair_lesson(), RepairReport: the one-round repair of model-written blocks
prompts (llm/prompts/):  outline_refine.md, draft_section.md, repair_block.md
```

## Mock mode and eval

`--provider mock` means: skeleton outline (no refinement) + `TemplateDrafter`, no repair round. Every eval case therefore has a deterministic baseline lesson; the real-provider run — `build_lesson` with the repair on — is compared against it on the same metrics ([evaluation.md](evaluation.md)).
