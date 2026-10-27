# Source grounding

Goal: a teacher looking at any block can answer "where did this come from?" in one click, and can tell at a glance what the model added on its own.

## 1. Addressable spans

`ingest/` turns each document into pages of spans (see [lesson-model.md](lesson-model.md#sourcedocument-page-span)).

| format | library | page | span | notes |
|---|---|---|---|---|
| PDF | PyMuPDF | physical page | text block from `page.get_text("dict")`, merged into paragraphs by vertical gap and font size; each embedded picture becomes an `image` span with a cropped PNG | bbox kept; headings detected by font size ratio; numbered activity lines ("1. Look, listen and repeat.") detected as `list_item` |
| DOCX | python-docx | synthetic page per section break (or every N paragraphs) | paragraph / table | heading from style name |
| PPTX | python-pptx | slide number | text frame / table | title placeholder → `heading` |
| scanned PDF | Muse Spark vision (phase 3) | physical page | model-produced paragraphs with page-relative positions | lower confidence; flagged |

Span IDs are `p<page>.s<order>`. Stability: the same file (same `sha256`) with the same `extraction_version` always produces the same IDs. Changing the extraction algorithm bumps `extraction_version` and invalidates stored citations for that document — the lesson then shows `stale` grounding checks until re-linked.

## 2. Retrieval for drafting

The drafter never sees the whole document. For each outline section it receives the top-k spans selected by:

1. the outline's `source_span_ids` (the outliner already mapped sections to spans), expanded by ±1 neighbouring span for context;
2. BM25 over span text with the section title and the curriculum outcome text as the query (`rank_bm25`); embeddings are a later option, not needed for single-chapter documents.

Each span is presented to the model with its ID in front: `[p6.s2] Viết tập hợp A các số tự nhiên…`. The model is instructed to cite by ID and to output `sources: []` when it adds content that no span supports.

## 3. Citation verification

The model's citations are proposals. `grounding/verifier.py` checks each one:

```
for citation in block.sources:
    span = resolve(citation)                     # must exist, else check=fail "dangling"
    overlap = token_f1(block_text, span.text)    # cheap lexical signal
    if overlap >= HIGH:   supported = True
    elif overlap <  LOW:  supported = False
    else:                 supported = llm_judge(block_text, span.text)   # borderline only
    citation.confidence = combine(overlap, judge)
```

For an activity the comparison text is its title and instructions; when that does not support a citation, its pupil steps are tried as well (`steps_text`). A song or chant repeats the textbook's lyric lines in its steps, and a tick or gap-fill activity repeats its option lines, so the cited span supports the steps even though the title and instructions only introduce them.

- `HIGH`/`LOW` are set in `english/rules.py` — `(0.20, 0.60)`. Dialogues, patterns and vocabulary are near-verbatim and usually clear `HIGH`; activities paraphrase and extend, so the judge is consulted for them more often.
- `llm_judge` is a short yes/no prompt with the two texts and nothing else, run with the mock-friendly provider so eval is reproducible.
- A block with no supported citation is **downgraded to `provenance = generated`**, regardless of what the drafter claimed.

## 4. Labelling

Provenance is rendered everywhere the content is:

- **Slides (PPTX):** footer `Source: p. 6, ¶3` per block; AI-added blocks get a small badge in the corner and a speaker note *"AI-added — check before teaching"*.
- **Lesson plan (DOCX):** superscript markers in the body; an appendix table *Source map* lists every block → citation(s) → quote.
- **Editor (web, phase 2):** click a block → the source pane scrolls to the span and highlights its bbox on the page image.

## 5. Metric

```
grounded_ratio = |content blocks with ≥1 supported citation| / |content blocks|
```

"Content blocks" excludes `objective`, `teacher_note`, `summary` (these are expected to be derived/generated). It also leaves out any `derived` block that cites nothing, such as the wrap-up's recap activity (built from the summary; there is no span to cite). Reported per lesson and aggregated in [evaluation.md](evaluation.md). A second number, `ai_added_ratio`, is reported alongside — low grounding is not a failure if the teacher *asked* for extra material, so both are shown rather than a single score.

## 6. Non-goals

- We do not try to detect errors inside a correctly cited span (if the textbook is wrong, the lesson is wrong). The level checks catch out-of-scope language, not source errors.
- We do not fingerprint images for provenance; `image_ref` blocks and vocabulary pictures cite the image span and reuse the cropped image file directly.
