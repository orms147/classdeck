# Evaluation

We measure instead of eyeballing. A small, fixed set of cases is run end-to-end after every meaningful change; numbers go in a results file and are compared with the previous run.

## Cases

`eval/cases/<case_id>.yaml`

```yaml
id: en3-u1-l1-p1
grade: 3
curriculum: { version: gdpt-2018, unit_id: ta3.u1, lesson_no: 1, period: 1 }
source: fixtures/private/english3-gs-unit1.pdf          # private: real textbook pages
pages: [6, 7]                                           # restrict ingest for speed
expect:
  min_grounded_ratio: 0.80
  max_vocab_violations: 0
  max_grammar_violations: 0
  must_cover_outcomes: [ta3.u1.o1, ta3.u1.o2]
  must_cover_skills: [listening, speaking]
edits:                                                  # scripted edit instructions
  - target_kind: vocabulary
    instruction: "Reduce new words to four"
    expect: { affected_includes_kind: activity, checks_pass_after_regenerate: true }
  - target_kind: activity
    instruction: "Make it a pair-work activity"
    expect: { changed_blocks: 1, interaction: pairs }
```

Current set: **20 cases** — the two public mini-unit cases plus, for each self-written unit (grade 3: five units; grades 4 and 5: two each), a Lesson 1 Period 1 case and a Lesson 3 Period 1 (phonics) case. All run offline in CI; the same files run against a real model on demand.

## Metrics

| metric | definition | where it comes from |
|---|---|---|
| `grounded_ratio` | content blocks with ≥ 1 supported citation / content blocks | [source-grounding.md](source-grounding.md) |
| `ai_added_ratio` | blocks with `provenance = generated` / content blocks | reported, not scored |
| `vocab_violations` | out-of-list lemmas from `english.vocab_level`, summed over blocks | [english-rules.md](english-rules.md) |
| `grammar_violations` | `english.grammar_scope` hits | [english-rules.md](english-rules.md) |
| `skill_coverage` | skills with ≥ 1 activity / skills required by the period's outcomes | validators |
| `outcome_coverage` | outcomes with ≥ 1 objective and ≥ 1 activity / outcomes in the period | curriculum |
| `timing_ok` | `english.timing` pass | validators |
| `edit_locality` | edits where only `target_block_ids` changed / edits | editing engine |
| `edit_success` | scripted edits whose post-accept checks all pass / edits | editing engine |
| `manual_fix_ratio` | blocks a reviewer had to hand-edit / blocks (teacher study only) | human review sheet |
| `time_to_ready_min` | wall-clock from upload to "I would teach this" (teacher study only) | human review sheet |
| `judge_*`, `judge_mean` | rubric scores from an independent model (see below) | [LLM judge](#llm-judge) |
| `tokens_in / tokens_out / cost_usd` | per case, generator and judge separately | LLM provider |
| `latency_s` | per stage | pipeline |

Eval drafts every case through the shared `pipeline.build_lesson` with the repair round on ([planning.md](planning.md#repair-round)), so `vocab_violations` and `grammar_violations` are measured after the repair; `latency_s` carries a `repair` entry on real-provider runs, and the generator's tokens for it are counted in `tokens_in / tokens_out`. The automatic metrics run in CI with the mock provider on public fixtures (deterministic) and on demand with the real provider on private fixtures. The two human metrics are collected in a short review sheet when a teacher tries the output; they are the ones that answer "does this actually save time?".

## LLM judge

The deterministic checks say whether a lesson is *in scope*; they cannot say whether it is *good*. For that, eval asks an independent model to grade each lesson on a fixed rubric (1–5 each, with a one-sentence justification):

| criterion | question |
|---|---|
| `faithfulness` | Do `extracted` blocks say what their cited spans say — no additions, no distortions? (Spans are shown to the judge next to each block.) |
| `age_fit` | Would an 8–10-year-old in a Vietnamese classroom follow these instructions and this language? |
| `step_clarity` | Could a substitute teacher run each activity from `teacher_steps` / `student_steps` alone? |
| `outcome_alignment` | Does the period actually teach the outcomes it lists, at the right cognitive level? |
| `flow` | Do warm-up → presentation → practice → production → wrap-up build on each other? |

The prompt (`llm/prompts/eval_judge.md`) first tells the judge what the format intends — IPA, `meaning_vi`, examples, derived objectives and summaries are enrichments, image spans have no text, `derived` activities go beyond their citation by design — so `faithfulness` is judged on the wording of `extracted` text only (a changed contraction or an invented dialogue line counts; an added gloss does not).

Reported as `judge_<criterion>` per lesson plus `judge_mean`. The judge is configured separately from the generator (`JUDGE_*` in [llm-provider.md](llm-provider.md#two-roles-generator-and-judge)) and **should be a different model family**: a model grading its own family's output is biased towards it. When no judge is configured, eval uses the generator and marks the scores `judge=generator` in the report so they are not read as independent.

Judge scores are *relative* instruments — good for comparing two runs after a prompt change, not for absolute claims. A pairwise mode (`eval compare --judge`) that shows the judge both versions of the same case and asks which is better is the planned follow-up.

## Running

```bash
classdeck eval eval/cases/en3-u1-l1-p1.yaml                 # one case, real provider
classdeck eval eval/cases --provider mock                    # all cases, offline
classdeck eval eval/cases --report eval/results/2026-10-30.json
```

Results are written to `eval/results/` (git-ignored). A results file is a list of per-case metric dicts plus a summary row; `eval/compare.py` prints the diff between two results files.

## Fixture policy

- `eval/fixtures/public/` — material we have the right to publish: self-written mini-units that imitate the textbook's structure (instruction line, dialogue, pattern, picture placeholders), public-domain texts. Committed. Used in CI.
- `eval/fixtures/private/` — real textbook pages and real lesson plans. **Never committed** (git-ignored). Used for local, real-provider runs.
- Case files may reference private fixtures; the runner skips a case with a `missing fixture` note instead of failing.
