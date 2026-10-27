# Output formats

What a teacher gets out of ClassDeck, in order of importance:

| format | for | module | status |
|---|---|---|---|
| **HTML deck** | the classroom — projector, pupils, offline | `export/html.py` | primary; spec below |
| PPTX | teachers who must hand in or edit PowerPoint | `export/pptx.py` | done, see [english-rules.md](english-rules.md#export-conventions) |
| DOCX lesson plan | the written plan (two-column Teacher's / Students' activities) | `export/docx.py` | done, see [english-rules.md](english-rules.md#export-conventions) |
| Prompt packs | teachers with no API key: hand the generative step to a free chat model in the browser | `handoff/` | spec below |
| Flashcard pictures | vocabulary items without a textbook picture | `classdeck illustrate` (`llm/images.py`) | spec below |

The goal that orders this list: **lessons that look good and feel right for 8–10-year-olds**. A grounded, well-checked lesson that looks like a budget report is not a lesson a teacher will show. The HTML deck is where the visual effort goes; PPTX and DOCX are faithful but plain.

## HTML deck

### Shape

One self-contained file, `<lesson_id>.html`: all CSS and JavaScript inline, every picture embedded as a data URI, a system font stack — it opens from a USB stick on an offline school computer and looks the same everywhere. No external requests of any kind.

- 16:9 stage, scaled to fit the window; works at 1024×768 projectors and on a phone.
- Navigation: arrow keys, space, click/tap on the right or left third, swipe; `F` fullscreen; progress dots at the bottom.
- **Pupil mode (default):** clean slides, no citations, no badges, no notes.
- **Teacher mode** (`T` key or `?teacher=1`): teacher notes panel (teacher steps, materials, interaction, minutes), source footer `Source: p. 1, ¶3` on each slide, the **AI-added** badge on `generated` blocks, the warnings list and the source map as final slides.
- `N` toggles only the notes panel; `P` / print gives one slide per page with notes under each.
- Respects `prefers-reduced-motion`; contrast ≥ 4.5:1; large hit targets.

### Design language (theme `kids`, default)

- One accent colour per phase, used for the divider slide and a top band on every slide of that phase: warm-up ☀️ yellow `#F9C74F`, presentation 📖 blue `#4D96FF`, practice ✏️ green `#6BCB77`, production 🎤 coral `#FF6B6B`, wrap-up ⭐ violet `#9D4EDD`. Backgrounds are off-white `#FFFDF7`; text is near-black `#1F2937`.
- Rounded cards (24 px radius), soft shadows, generous padding; nothing smaller than 32 px on a slide; words on flashcards at 96 px, patterns at 56 px, dialogue lines at 44 px.
- An emoji icon per activity type: 👂 listen_and_do · 👀🗣️ look_listen_repeat · 👉🗣️ listen_point_say · 💬 lets_talk · ✍️ lets_write · 🎵 song · 🥁 chant · 🎮 game · 🎭 role_play · 🧩 project · 🧠 read_and_match · 📝 look_complete_read · 🤸 tpr · ✨ other.
- Simple CSS transitions (fade/slide-in); no libraries.

### Slide types

| block / position | slide |
|---|---|
| cover | unit and lesson title, grade, phase strip with minutes |
| section start | divider: phase icon, title, minutes, a **tap-to-start countdown** for the section |
| `vocabulary` | one flashcard per item: picture dominant (the cited image span), word, IPA; **tap to reveal** `meaning_vi` |
| `dialogue` | chat bubbles alternating left/right by speaker with initial-letter avatars; **tap to reveal line by line** |
| `sentence_pattern` | the pattern with the slot as a dashed box; **tap cycles** the examples into the slot; `function` as a small caption |
| `phonics` | the sound very large, the words with the target letters highlighted, chant lines |
| `activity` | icon, title, `instructions` (≤ 12 words, large), `student_steps` as numbered cards, countdown for `minutes` |
| `summary` | grid of the lesson's words (with pictures where available) and the pattern |
| `image_ref` | the picture with its caption |
| `objective`, `teacher_note` | teacher mode only (notes panel on the cover / previous slide) |

### Implementation notes

- Interface text that is not lesson content — navigation hints ("Tap to reveal"), countdown labels, the "Start" button, the cover's "Grade 3" line — is marked `class="ui"`. It is visible to pupils but it is not checked against the word list, by the validators or by `check-deck`; a hand-off deck must use the same class for its own chrome.
- Rendered from `export/deck_template.html` plus `export/themes/<theme>.css` by a small internal renderer (string substitution and escaped HTML fragments; no template-engine dependency). Every rendered block carries `data-block-id`, `data-kind`, `data-provenance`, so the deck can be checked against the lesson later. Pictures are `<img data-block-id="b_02" data-item="0" alt="hello" src="data:…">` — the same attributes a hand-off deck must use as placeholders, so `check-deck` can embed the real pictures.
- `export_html(lesson, out_path, *, sources=None, image_root=None, theme="kids") -> Path`; the same `ensure_exportable` gate as the other writers (`fail` blocks the export, `warn` is listed in teacher mode).
- Tests parse the file: one element per placed block with the right `data-block-id`; images are `data:` URIs and there is no `http://`/`https://` reference; teacher-only elements carry class `teacher`; the fixture deck is under 5 MB.

## Flashcard pictures (`classdeck illustrate`)

Primary-English flashcards live or die by their pictures. The textbook page usually has one scene picture per activity, not one picture per word, so most vocabulary items have no `image` citation and the flashcard slide falls back to word + IPA. `classdeck illustrate` fills that gap with an image model.

- **What it draws.** One picture per vocabulary item that has no cited picture and whose `pos` is a content word (nouns, verbs, adjectives, interjections; pronouns, determiners, prepositions and conjunctions are skipped unless `--all`). The prompt is fixed by a style guide, not by the model: *flat, friendly children's-textbook illustration, a single clear subject for the word, plain white background, bright pastel colours, simple shapes, no text, no letters, no logo*, plus the word, its gloss and its example sentence for disambiguation.
- **Where it goes.** `<workdir>/images/generated/<sha256(prompt)[:16]>.png`, downscaled to 512 px on the long side and re-encoded as PNG (the API returns up to 1024 px). The item records it in `VocabItem.generated_picture` (path relative to the image root) — a new optional field next to `image`. The hash makes re-runs free and lets two lessons share a picture.
- **Provenance.** A generated picture is content the model added, so the deck, PPTX and DOCX show it with the same **AI-added** marker as generated text (teacher mode on the deck; a small caption in PPTX/DOCX). The `handoff` deck pack lists it as a generated picture and `check-deck` embeds it like a cited one.
- **Provider.** The images endpoint of any OpenAI-compatible API (`client.images.generate`), configured by the same endpoint profile as the text model: with `--endpoint meta` it is the Meta Model API's `muse-image-1.0` ($0.01 per image, PNG via `output_format="png"`); `CLASSDECK_IMAGE_MODEL` overrides the model id. Offline (`--provider mock`) a flat placeholder picture with the word on a coloured card is produced, so tests and CI never call the API.
- **Cost control.** `--max N` (default 8 per lesson), a dry run that prints the prompts and the price before generating (`--dry-run`), and the prompt-hash cache. A typical period (2–6 new words) costs $0.02–0.06.
- **Not done here.** Pictures for `image_ref` blocks and dialogue scenes (the textbook already has those), and style variations; both are later options.

## Prompt packs (`handoff/`)

Many teachers have a free chat model in the browser and no API key. A prompt pack moves the **generative** step there while ClassDeck keeps everything that must be deterministic: ingest, segmentation, the outline, citations, the level checks, export. The principle does not change — the model proposes, the engine validates — only the transport does (copy and paste instead of HTTP).

### Deck pack

`classdeck handoff deck lesson.json` → `lesson.deck-pack.md`, one prompt the teacher pastes into the chat model:

1. Role and audience (primary English, grade, 35-minute period).
2. The design brief above, condensed, with the colour and size rules.
3. The lesson: blocks with ids, kinds, content and formatted citations; pictures described by caption and position (the chat model cannot see files — the deck it produces must put `<img data-block-id="…" data-item="…" alt="…">` placeholders where pictures go, and `classdeck handoff check-deck` embeds the real pictures there). The allowed word list (grade list ∪ unit words) is included verbatim.
4. Hard rules: keep every `data-block-id`; one slide per block as in the table above; English only on slides; no words outside the provided list; teacher content in `class="teacher"` elements; output **one complete HTML file and nothing else**.

The teacher saves the reply as `.html`. `classdeck handoff check-deck lesson.json deck.html` then: verifies that every block id is present and no extra blocks appeared; runs the vocabulary tokenizer over pupil-visible text nodes and lists out-of-list words; embeds the lesson's pictures where the deck references `data-block-id`; writes `deck.checked.html` and a short report. This is the same gate the pipeline applies to its own model output.

### Draft pack

`classdeck handoff draft outline.json` → `draft-pack.md`: the outline, each section's spans as `[p1.s3] text` lines, the drafting rules (`english.rules.prompt_rules`), the activity templates, and the `DraftSection` JSON schema; the model is asked for one JSON object per section. `classdeck handoff import-draft outline.json reply.json` → `lesson.json` runs the **same validation as the model drafter** (kinds, span ids, dependency indices, content shapes; a section that fails falls back to the template drafter), adds the engine-built blocks exactly as `draft_lesson` does (objectives, the period-2 review blocks, the wrap-up), then runs `pipeline.finish_lesson` with no provider — dependency inference, grounding by the midpoint rule, the level checks — and saves the lesson. The pack lists the allowed words next to the rules, the same list the level check uses. From there `export` and `edit` work as usual.

With both packs the whole pipeline runs with no API at all.

## Token economy on API runs

For teachers who do use an API, the design keeps calls few and small so a cheap model is enough:

- The outline skeleton is deterministic; refinement is one optional call (`--no-refine` skips it).
- One drafting call per section, ~1–2k input tokens (one chunk's spans, the template, the rules), ≤ 800 output tokens; five sections ≈ six calls ≈ under 10k tokens per period.
- The grounding judge is only consulted in the borderline band; vocabulary, grammar, timing and coverage checks never call a model.
- Edits send only the selected blocks and their neighbours.
- Responses are cached by prompt hash; re-running a period costs nothing.
