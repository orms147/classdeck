# Editing workflow

The teacher owns the lesson. The model proposes; the teacher accepts. Nothing changes outside the selection without an explicit decision.

## The loop

```
 select block(s) ──▶ instruction ──▶ EditProposal ──▶ preview (before / after / affected / checks)
                                                              │
                                              ┌───────────────┼────────────────┐
                                              ▼               ▼                ▼
                                           accept           reject        edit instruction
                                              │                                 and retry
                                   apply patch, append Revision,
                                   re-run checks on changed + dependent blocks
                                              │
                                              ▼
                                       undo / redo available
```

### 1. Selection

Phase 1 (CLI): `--block b_02 --block b_03`. Phase 2 (web): click blocks; multi-select with shift. Selection is at **block granularity** — sub-block (word-level) editing is out of scope for the MVP; the teacher can always edit the text directly.

### 2. Instruction

Free text in English or Vietnamese. Presets per [english-rules.md](english-rules.md#edit-presets): *make it a pair-work activity*, *reduce new words to N*, *add a listening task*, *simpler instructions for pupils*, …

### 3. Building the edit context

`editing/commands.py` assembles what the model sees — and only that:

| included | why |
|---|---|
| the selected blocks (full JSON) | the thing to change |
| the spans those blocks cite, plus spans retrieved for the instruction | so the edit stays grounded |
| read-only neighbours: previous and next block in the section, the section title and phase | so the edit fits the flow |
| the English rules fragment for the grade | scope, structure, step format |
| curriculum outcomes for the period and the grade word list excerpt | so "simplify" does not drop a required outcome or pull in out-of-scope words |

Not included: the rest of the lesson. This keeps edits local by construction and keeps token usage small.

### 4. EditProposal

```json
{
  "id": "ep_12",
  "instruction": "Reduce new words to four",
  "target_block_ids": ["b_02"],
  "replacements": {
    "b_02": { "...vocabulary block with 4 items instead of 6, same id and kind..." }
  },
  "added_blocks": [],
  "removed_block_ids": [],
  "rationale": "Kept hello, hi, goodbye, bye — the four used in the dialogue and the pattern. Dropped nice, meet (only in the extension line).",
  "affected": [
    { "block_id": "b_06", "reason": "depends_on b_02 — Listen, point and say drills 'nice' and 'meet'", "suggested_action": "regenerate" },
    { "block_id": "b_09", "reason": "depends_on b_02 — summary lists all six words", "suggested_action": "regenerate" }
  ],
  "checks_preview": [
    { "block_id": "b_02", "name": "english.vocab_level", "status": "pass" },
    { "block_id": "b_02", "name": "english.ipa_present", "status": "pass" },
    { "block_id": "b_02", "name": "grounding.citation_supported", "status": "pass" }
  ]
}
```

The model returns `replacements`, `added_blocks`, `removed_block_ids`, `rationale`. The engine fills in `affected` and `checks_preview`:

- **affected** — every block whose `depends_on` includes a changed or removed block, transitively. The engine does **not** modify them. Each gets a `suggested_action` (`regenerate` for activities and summaries that drill or list the changed content, `review` otherwise) that the teacher can trigger as a follow-up edit.
- **checks_preview** — grounding verification and the English validators run on the proposed blocks *before* accept, so the teacher sees e.g. `english.grammar_scope: warn — "has been" out of scope for grade 3` on a proposal and can reject it without touching the lesson.

### 5. Accept / reject / undo

Accept:
1. Apply the JSON Patch to the lesson.
2. Append a `Revision` with `patch`, `inverse`, `flagged_block_ids = affected`.
3. Mark checks on affected blocks `stale`.
4. Save.

Reject: discard the proposal; nothing is written.

Undo: apply the latest revision's `inverse`, pop it onto a redo stack. Redo: re-apply `patch`. History is linear in the MVP.

### 6. Guardrails

- A proposal that changes a block's `kind` or touches blocks outside `target_block_ids` is rejected by the engine before the teacher sees it (the model is re-prompted once with the violation named).
- A proposal whose replacement fails `english.steps_present` is rejected the same way; `warn`-level checks are shown, not blocked.
- An edit can never remove the last block that covers a curriculum outcome without a `curriculum.outcome_covered: warn` in the preview.
- `extracted` blocks keep their citations through an edit; if the replacement text no longer matches the cited spans, grounding downgrades it to `generated` in the preview and says so.

### 7. Direct edits

The teacher can also edit a block's text by hand (web, phase 2; or by editing the JSON in phase 1). A direct edit is recorded as a revision with `actor = teacher`, `instruction = null`, and it triggers the same dependency flagging and check re-run. Hand-edited `extracted` blocks are re-verified; if the text drifts from the citation, provenance drops to `generated` and the teacher is told why.

**Editing on the slide.** The deck itself is the usual place for a hand edit (`docs/output-formats.md`, "Editing on the slide"): every text on a slide is a field with a path into its block, and Save sends all the changed fields at once. `editing.fields.set_fields(block, [(path, text)])` applies them to a block and validates it once: unknown paths, targets that are not text (ids, citations, checks, enums, numbers, answer flags) and edits that would break an answer key are rejected with a message naming the field (a gap sentence keeps its blanks, renaming the right option of a multiple-choice item renames the answer, an edited paragraph keeps only the highlights that still occur in it). `editing.deck_edits.apply_deck_edits(lesson, edits, sources=, curriculum=)` builds **one** direct-edit proposal for all the edited blocks (`direct_edit_many`, `actor = teacher`, instruction label "Edited on the slide"), accepts it and then re-runs `pipeline.finish_lesson`, taking the result only for the edited blocks, so a single *Undo* restores everything. The local app (`POST /api/lessons/{id}/edits`) and `classdeck import-edits <lesson> <edited.html>` (the edit list embedded by a standalone deck's Save) both call it; the returned notices carry provenance changes and new warnings.
