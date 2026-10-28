# English rules and level checks

Everything that is specific to primary-school English — how a period is structured, which block kinds exist, what the model is told, and which checks run deterministically — lives in `src/classdeck/english/`. The rest of the pipeline is written against the lesson model and reads these rules; it does not hard-code them.

```
english/
  rules.py        phases, timing, block kinds, prompt fragments, grounding thresholds
  validators.py   deterministic level checks (word list, grammar scope, skills, IPA, glosses,
                  wording, timing, pattern use)
  data/           ipa_core.json (pronunciations), gloss_vi.json (Vietnamese glosses)
  dependencies.py how depends_on edges are inferred between block kinds
  presets.py      quick edit instructions shown in the editor
  templates/      activity templates by Global Success activity type
```

## Target

Grades 3–5, textbook series *Global Success*, curriculum *Chương trình GDPT 2018* (English). Periods are 35 minutes. Lesson content is in English; teacher notes may be bilingual.

A *Global Success* unit has three lessons of six activities each, two periods per lesson. The shapes recur:

| Lessons 1–2 | Lesson 3 |
|---|---|
| 1. Look, listen and repeat | 1. Listen and repeat (phonics) |
| 2. Listen, point and say | 2. Listen and circle / tick |
| 3. Let's talk | 3. Let's chant |
| 4. Listen and tick / number / circle | 4. Read and match / tick |
| 5. Look, complete and read | 5. Let's write |
| 6. Let's sing / Let's play | 6. Project |

Period 1 of a lesson usually covers activities 1–3, period 2 activities 4–6. The outliner uses these shapes to map page spans to activities and activities to phases.

## Period structure (phases)

| phase | default minutes | what goes in it |
|---|---|---|
| `warm_up` | 5 | review game, song or chant, or a TPR in classroom language; **no new pattern or word**: lesson 1, period 1 uses a `tpr` warm-up, later periods a pattern game on language already taught |
| `presentation` | 10 | *Look, listen and repeat* — dialogue, new vocabulary, target pattern |
| `practice` | 12 | *Listen, point and say*, *Listen and tick*, *Look, complete and read* — controlled practice |
| `production` | 6 | *Let's talk*, role play, game, project — freer use of the pattern |
| `wrap_up` | 2 | recap, homework, praise |

Minutes are defaults the outliner starts from; the `english.timing` check keeps the sum at 35 ± 2.

**Period 2** of a lesson has its own skeleton (see [planning.md](planning.md#period-2-shape)): warm-up 4 min, a `presentation` of 8 min that reviews the previous period (vocabulary, pattern and phonics blocks plus a short *Say it again* repeat activity), `practice` for activities 4 and 5 (8 + 7 min), `production` for activity 6 (6 min) and a 2-minute wrap-up.

## Block kinds

See [lesson-model.md](lesson-model.md#block-kinds). The drafter may emit: `objective, vocabulary, sentence_pattern, dialogue, phonics, activity, teacher_note, summary, image_ref` (the interactive exercise kinds are not drafted; they come from a textbook's own exercises).

## Prompt rules

`rules.prompt_rules(grade)` returns the fragment injected into the outliner, drafter and editor prompts. Abridged:

- Vocabulary and sentence patterns come from the cited unit. New words beyond the source are allowed only with `sources: []` and only if they are in the grade word list.
- Every `activity` names its `type`, at least one skill, the interaction pattern, and separate `teacher_steps` and `student_steps` written as imperatives.
- Language scope by grade (from curriculum config): grade 3 — present simple with *be* and *have*, imperatives, *this/that*, *What's …?*, numbers 1–20; grade 4 — adds *can*, present continuous, *like + -ing*, days, months, *Where/When*; grade 5 — adds past simple (regular and common irregular), *going to*, *will* for offers, *because*, comparatives. No perfect tenses, no passive, no reported speech at any grade.
- Instructions for pupils are short (≤ 12 words), one action each.
- Picture-driven: when a vocabulary item has a picture on the page, reference the image span; do not describe the picture in words on the slide.
- Vietnamese is allowed only in `meaning_vi` and in teacher notes.
- Keep the textbook's wording: patterns and dialogue lines are copied exactly, contractions included (*I'm*, never *I am*); no pattern or phrase the unit does not teach is introduced.
- The new pattern is taught from the presentation onward; a warm-up recycles earlier language or is a TPR in classroom language.
- Practice and production steps are concrete: `teacher_steps` say what to point at or show, who speaks first, how many rounds and how to check; `student_steps` are 3 to 5 concrete imperatives. A `listen_and_do` or `look_complete_read` activity ends its `teacher_steps` with an `Answer key: ...` step.

The drafter and repair prompts add the **allowed words** to these rules: `curriculum.word_list(unit_id)` (grade list ∪ classroom language ∪ unit words), with the statement that pupil-facing text may use only these words, pupils' names and numbers, and that `teacher_steps` may use any English. The list is the one `english.vocab_level` checks against (see [planning.md](planning.md#repair-round)).

## Validators

All validators are deterministic (no model call). They run after drafting and after every proposed edit, before the teacher accepts.

**Pupil-facing vs. teacher-facing text.** The level checks (`vocab_level`, `grammar_scope`) apply only to language pupils see or hear: vocabulary items and examples, sentence patterns, dialogue lines, phonics words and chants, activity `instructions` and `student_steps`, summary bullets. Teacher-facing text — activity `title`, `teacher_steps`, `materials`, `competency_tags`, `teacher_note`, `meaning_vi` — is written for an adult and is not checked against the grade word list.

| name | applies to | what it does | status on failure |
|---|---|---|---|
| `english.vocab_level` | `vocabulary, sentence_pattern, dialogue, activity, summary` | Tokenise and lemmatise the block's English text (`simplemma`); every lemma must be in the grade word list (curriculum config `vocabulary.words` ∪ `vocabulary.classroom_language` ∪ the unit's `vocabulary`), in the cited spans, a proper noun, or a number. `classroom_language` is the instruction language pupils hear every lesson — *listen, point, partner, picture, swap* — which is receptive, not taught as new words, and therefore allowed in instructions and student steps. Offenders listed. | `warn` |
| `english.words_from_unit` | `vocabulary, sentence_pattern, activity, summary` (a *secondary* curriculum, in place of `english.vocab_level`) | There is no closed word list for grades 6-9. Pupil-facing words of the blocks must occur in the unit's ingested pages (any span of the lesson's sources), in the block's cited spans, among the function words and a short list of plain classroom words (`COMMON_WORDS`), or be a proper noun or number. Offenders listed. Exercise blocks and dialogues are the book's own wording and are not judged. | `warn` |
| `english.grammar_scope` | `sentence_pattern, dialogue, activity` | Regex patterns per grade from curriculum config `grammar_scope.forbidden` (e.g. `\b(has|have|had) \w+ed\b`, `\b(was|were|is|are) \w+ed by\b`). | `warn` |
| `english.skill_coverage` | lesson | Every skill named in the period's outcomes has ≥ 1 activity. | `warn` |
| `english.ipa_present` | `vocabulary` | Every item has `ipa`; the validator fills it from a bundled dictionary when the model omitted it, and warns only if still missing. | `warn` |
| `english.gloss_present` | `vocabulary` | Every item has a Vietnamese `meaning_vi`; the validator step first fills empty glosses from the bundled `data/gloss_vi.json` (`fill_glosses`, lower-cased lookup, a phrase falls back to its words' glosses joined; a gloss someone wrote is kept) and warns, listing the items, only when one is still missing. The list covers every word of the grade 3-5 word lists and unit vocabularies. | `warn` |
| `english.wording_kept` | `activity, summary` | For every `sentence_pattern` of the lesson that contains a contraction (*I'm, it's, what's, let's, don't ...*), the pupil-facing text (activity instructions and student steps, summary bullets) must not write it out (*I am, it is, what is, let us, do not ...*). A form the lesson's own patterns or dialogue write out is left alone; a possessive *'s* is not a contraction. The offending phrase is named in the detail (`contraction written out in pupil text: "I am" (the unit writes "I'm")`), which is how the [repair round](planning.md#repair-round) sees it. | `warn` |
| `english.pattern_used` | `activity` in `practice`/`production` | The activity text or steps contain the target pattern from a `sentence_pattern` block in the lesson (slot-filled match). Sets the `depends_on` edge when found. | `warn` |
| `english.steps_present` | `activity` | `teacher_steps` and `student_steps` non-empty, ≥ 1 skill. | `fail` |
| `english.timing` | lesson | Sum of section minutes within 35 ± 2. | `warn` |
| `curriculum.outcome_covered` | lesson | Every `curriculum.outcome_ids` entry is referenced by ≥ 1 `objective` and covered by ≥ 1 activity. | `warn` |

`fail` blocks export until fixed; `warn` is shown and exported with a notice.

## Dependency rules

`dependencies.infer(lesson)` adds `depends_on` edges the drafter may have missed:

- `activity → vocabulary` when the activity text contains ≥ 1 item of that vocabulary block.
- `activity → sentence_pattern` when `english.pattern_used` matches.
- `activity → dialogue` for `look_listen_repeat` and `role_play` activities in the same section as the dialogue.
- `summary → vocabulary, sentence_pattern, phonics`.
- `objective → *` is never added (objectives come from the curriculum, not from blocks).

## Grounding thresholds

`(LOW, HIGH) = (0.20, 0.60)` for lexical overlap (see [source-grounding.md](source-grounding.md#3-citation-verification)). Dialogues, patterns and vocabulary are near-verbatim and usually clear `HIGH`; activities paraphrase and extend, so the LLM judge is consulted for them more often.

## Edit presets

Shown in the editor as one-click instructions; free text is always allowed.

- Make it a pair-work activity
- Make it a whole-class game
- Add a listening task
- Reduce new words to N
- Simpler instructions for pupils
- Add a TPR (total physical response) step
- Add bilingual teacher notes
- Shorten to N minutes

## Export conventions

The pupil-facing **HTML deck** is specified in [output-formats.md](output-formats.md); the conventions below are for the PPTX and DOCX writers.

- **Slides (PPTX):** one slide per activity; vocabulary as flashcard slides (word, IPA, picture from the cited image span); dialogue slide with speaker labels; pattern slide with the slot highlighted. Citation footer `Source: p. 6, ¶3`. AI-added badge and speaker note on `generated` blocks.
- **Lesson plan (DOCX):** header (unit, lesson, period, date), objectives by outcome ID, teaching aids, then the procedure as a two-column table — *Teacher's activities* / *Students' activities* — grouped by phase with minutes. Competences developed by each activity (`competency_tags`, including digital competence where tagged) are listed and highlighted. Source map appended.
