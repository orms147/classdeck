# Deck themes

A theme is one CSS file in this folder. It changes how the deck **looks and is laid out**; it never
changes the HTML. Every theme gets the same markup, the same behaviour and the same teacher layer.

`classdeck gallery <lesson>` renders a lesson in every theme here and lays them out side by side.

## Files and manifest

`themes/<id>.css`, where `<id>` is lowercase letters, digits, `-` or `_`. The very first thing in
the file is a manifest comment on one line:

```css
/* theme: sunny | Sunny | Bold, flat colours and chunky shadows - a modern learning-app look | suits: grades 3-4 */
```

`theme: <id> | <label> | <description> | suits: <grades>` - no `|` inside a field. `<id>` must equal
the file name. The loader (`available_themes()`) rejects a file without a valid manifest.

## How the stylesheet stack works

The page loads three layers, in this order:

1. **Structure** (in `deck_template.html`, not themeable): the 1920x1080 stage and its scaling,
   which slide is visible, the teacher layer (`body.teacher-mode`, `.notes-open`), reveal mechanics
   (what stays hidden until a tap), the speech buttons, the progress dots' hit targets, print and
   reduced motion.
2. **Base look** (also in the template): a plain default for every element, written with low
   specificity (mostly one class), using your tokens. A theme can lean on it and only change what it
   wants.
3. **Your theme.** It comes last, so a rule of the same specificity wins.

Do not hide, move or resize `.slide`, `.slide-body`, `.notes`, `.footer`, `.badge`, `.dots` or
`.speak` in a way that breaks the above: pupils must be able to read and tap, and teachers must get
their panel.

## Tokens (CSS custom properties) every theme defines in `:root`

Colours that carry text must be 6-digit hex (`#RRGGBB`) so they can be checked.

| token | meaning |
|---|---|
| `--bg`, `--ink`, `--muted` | page background, main text, secondary text |
| `--card`, `--line` | card surface, borders and rules |
| `--accent`, `--accent-2` | bright accents (borders, focus rings - never for text) |
| `--accent-ink`, `--accent-2-ink` | darker accents that may carry text, and white text sits on them |
| `--badge` | AI-added badge, "time's up" colour (white text sits on it) |
| `--mark` | highlight behind the target letters in phonics |
| `--bubble-left`, `--bubble-right` | the two speakers' bubble backgrounds (`--ink` text on them) |
| `--slot-border` | the dashed slot in a sentence pattern |
| `--teacher-bg` | the teacher notes panel |
| `--pulse` | the running countdown's glow |
| `--phase-warm_up`, `--phase-presentation`, `--phase-practice`, `--phase-production`, `--phase-wrap_up` | one colour per lesson phase |
| `--phase-lesson`, `--phase-teacher` | the cover and the teacher-only slides |
| `--radius`, `--shadow` | corner radius and card shadow |

Optional sizes (each must be 32px or more): `--size-body` (40), `--size-min` (32), `--size-title`
(64), `--size-word` (96), `--size-word-bare` (120, a flashcard with no picture and no meaning),
`--size-pattern` (56), `--size-dialogue` (44), `--size-sound` (180), `--size-exercise` (44, the body size of
an exercise; a slide with a lot of text uses 6px less).

Set by the template, for you to read: `--phase-color` is the current phase's colour on `.slide`,
`.dot` and `.phase-strip__item` (it follows `data-phase`). Use `background: var(--phase-color)`.

## Slides

`.stage-wrap` > `.stage` is the fixed 1920x1080 canvas (scaled to the window; give it a
background if you like, but do not resize it). Every slide is `<section class="slide slide--<type>" data-phase data-kind data-slide-type>`.

- `data-phase`: `lesson` (cover), `warm_up`, `presentation`, `practice`, `production`, `wrap_up`,
  `teacher` (the teacher-only slides).
- `data-slide-type` / the `slide--<type>` class: `.slide--cover`, `.slide--divider`,
  `.slide--flashcard`, `.slide--pattern`, `.slide--dialogue`, `.slide--phonics`, `.slide--activity`,
  `.slide--summary`, `.slide--image`, `.slide--exercise` (reading, matching, gap fill, choice, true/false and
  question exercises), `.slide--rule` (the *Remember!* box), `.slide--notes`, `.slide--source-map`.
- `data-kind`: the lesson block kind (`vocabulary`, `sentence_pattern`, `dialogue`, `phonics`,
  `activity`, `summary`, `image_ref`, `reading`, `match`, `gap_fill`, `choice`, `true_false`,
  `rule_box`, `question_set`) or, for the framing slides, the slide type.
- State classes on the slide: `.active` (the visible one), `.badged` (has an AI-added badge),
  `.teacher` (teacher-only slide).

Inside every slide, in this order:

- `.band` - the phase colour strip along the top (a bar by default; restyle freely).
- `.deco.deco--1`, `.deco.deco--2`, `.deco.deco--3` - three empty, inert, `aria-hidden` boxes for
  decoration. They are `position: absolute` inside the slide, below the content. Size and place
  them yourself and draw with gradients, borders, `clip-path`, `::before` / `::after`. They are not
  printed.
- `.badge.teacher` (only on AI-added blocks), `.slide-body` (the content, centred by default),
  `.footer.teacher` (source line), `.notes.teacher` (teacher panel; `.notes__title`,
  `.notes__block`, `.notes__origin`, `.notes__ai`).
- `.slide-title` is on every title (`h1`, `h2`).

Common helpers: `.card` (a surface), `.block` (the wrapper of a lesson block, with `data-block-id`,
`data-kind`, `data-provenance`), `.icon[data-icon]` (an empty span - the theme fills it, e.g.
`.icon[data-icon="warm_up"]::before { content: "..." }`, or hides it; `data-icon` is a phase or an
activity type), `.hint` (small "Tap to ..." text), `.tap` (clickable), `.ui` (interface text, not
lesson content), `.sr-only`.

### Cover - `.slide--cover`

`.cover` > `.cover__title`, `.cover__meta`, `ol.phase-strip` > `li.phase-strip__item[data-phase]` >
`.phase-strip__chip`, `.icon.phase-strip__icon`, `.phase-strip__label`, `.phase-strip__minutes`;
`.cover__total`.

### Divider - `.slide--divider`

`.divider.card` > `.icon.divider__icon`, `.divider__phase`, `.divider__title`, `.divider__minutes`,
`.countdown`. The slide's own background is the phase colour by default.

### Countdown (dividers and activities)

`.countdown.tap` > `.countdown__time`, `.countdown__hint`. States: `.running`, `.done`.

### Flashcard - `.slide--flashcard`

`article.flashcard.card` (`data-block-id`, `data-item`) > `.flashcard__picture` (img),
`.flashcard__ai` (teacher-only caption), `.flashcard__wordline` > `.flashcard__word` + `.speak`,
`.flashcard__ipa`, `.flashcard__meaning` (Vietnamese), `.flashcard__example` >
`.flashcard__example-text` + `.speak`, `.flashcard__hint`.
Modifiers: `.has-picture`, `.bare` (no picture, no meaning), `.has-example`, and when a meaning exists
`.tap.reveal` which becomes `.revealed` after a tap (the meaning and example stay hidden until then;
that is enforced by the structure layer).

### Dialogue - `.slide--dialogue`

`.dialogue.tap.stepper` > `.dialogue__context` (`.dialogue__context-text` is the context, `.dialogue__part.ui` the `(1/3)` of a long conversation), `.chat` > `.bubble.step-item` with `.bubble--left` or `.bubble--right`
(`data-speaker`; the second speaker is on the right) > `.bubble__avatar`, `.bubble__text` > `.bubble__who` (the speaker's name, read by screen readers only, shown while editing; its text sits in `.bubble__who-name`) + `.bubble__line` (what is said), `.speak`; `.dialogue__hint`.
`.bubble.pending` is a line not yet shown (the structure layer hides its text and button);
`.stepper.complete` once all lines are shown. A long conversation is cut into several slides (the
`.dialogue__context` then ends with `(1/3)`) and `.dialogue--dense` marks it: smaller bubbles and avatars.

### Sentence pattern - `.slide--pattern`

`.pattern.card.tap.cycle` > `.pattern__head` > `.pattern__text` (with `.pattern__slot`, the box that
examples fill; `.filled` when filled; `data-empty`) + `.speak`; `.pattern__line` (the example as one
line when it does not align with the slot), `.pattern__function`, `ul.pattern__examples` >
`li.pattern__example` (`.current` is the one in the slot) > `.pattern__example-text` + `.speak`;
`.pattern__hint`. `.pattern--long` (on `.pattern`) marks long examples: the template sets them smaller
in two columns.

### Phonics - `.slide--phonics`

`.phonics` > `.phonics__sound`, `.phonics__letters`, `ul.phonics__words` > `li.phonics__word` >
`.phonics__word-text` (target letters in `<mark>`) + `.speak`; `ul.phonics__chant` >
`li.phonics__chant-line`; `.phonics__part` (`(1/2)`: at most six words to a slide; the chant goes
on the last one; the template places it at the top right).

### Activity - `.slide--activity`

`.activity` > `.activity__heading` > `.icon.activity__icon` + `.activity__title`;
`.activity__part` (`(1/2)` when the steps continue on a second slide); `.activity__instructions`;
`ol.activity__steps` > `li.step.card` > `.step__number`, `.step__text` (`.activity__steps--single`
when there is one step); `.countdown`.

### Summary - `.slide--summary`

`.summary` > `.summary__title`, `.summary-grid` > `figure.summary-card.card` >
`.summary-card__picture`, `.summary-card__word`, `.speak` (`.summary-card--bare` is a card without
a picture, the speaker button then sits under the word; `.summary-card--long` / `.summary-card--xlong` a long word, set smaller); `.summary__pattern` >
`.summary__pattern-text` (with `.pattern__slot`) + `.speak`; `ul.summary__bullets` (only when there
are no words or patterns).

### Exercises - `.slide--exercise`

Every exercise is `.ex` > `.ex__head` (`.ex__no` the exercise number, `h2.ex__instruction.slide-title`
the textbook's instruction line, `.ex__part` the `(1/2)` of a long exercise) + the exercise body +
`.ex__bar.ui` (the buttons). `.ex--<kind>` names the kind (`.ex--match`, `.ex--gap_fill`,
`.ex--choice`, `.ex--true_false`, `.ex--question_set`, `.ex--reading`) and `.ex--dense` marks a slide
with a lot of text (use a smaller body size); `.ex--tight` is a matching exercise with long
definitions (smaller still, compact rows). A `.ex__sub` line sits under the heading of a reading
that has both a title and an instruction. The slide itself carries `data-exercise="<kind>"`.

Buttons: `button.ex__btn.ui` (`.ex__check` primary, `.ex__show`, `.ex__reset`, `.ex__next`) and
`.ex__score.ui` (the "3 of 5 correct" text, an `aria-live` region). Every control inside an
exercise is a real `<button data-act="...">`, 72px or more to tap; do not hide or disable them.

**State classes** (set by the script, reset when the slide is entered again; never rely on colour
alone, the structure layer adds a tick or a cross): `.is-correct`, `.is-wrong`, `.is-picked`
(selected, waiting for its partner), `.is-revealed` (an answer shown, a used word in the word box, a
question already asked) and `.is-done` (a choice or true/false item answered right). White text sits
on `--accent-2-ink` for `.is-correct` and on `--badge` for `.is-wrong`.

- **Match.** `.match` (`data-answer`, a JSON map of left index to right index) > `ol.match__left` +
  `ul.match__right` > `li` > `button.match__item` with `.match__item--left` or `.match__item--right` (`data-side`,
  `data-index`; `data-pair="1".."8"` and `data-partner` once paired) > `.match__label` (number or
  letter), `.match__text`, `.match__tag` (the pair number, empty until paired; give each pair its
  own colour with `--pair`). `.match__key` is the printed key, hidden on screen.
- **Gap fill.** `ul.wordbox` > `li.wordbox__chip` (`data-word`; `.is-revealed` once its word is
  shown); `ol.gap-list` > `li.gap-item` > `.gap-item__no`, `p.gap-item__text` with `button.gap`
  (`data-answer`; `--len` is the answer's length in characters) > `.gap__answer` (hidden until
  `.is-revealed`), and `.gap-item__answers.edit-only` > `.gap-item__answers-label`,
  `.gap-item__answer` (the answers as editable chips; shown only while editing).
- **Choice.** `ol.choice-list` (`.choice-list--cols` for more than two items) > `li.choice`
  (`data-answer` is the right option's index) > `p.choice__stem` (`.choice__no`,
  `.choice__stem-text`) + `.choice__options` > `button.choice__option` (`data-index`; `data-correct`
  on the right one, used in print) > `.choice__letter`, `.choice__text`.
- **True / false.** `ol.tf-list` > `li.tf` (`data-answer` is `true` or `false`) > `p.tf__statement`
  (`.tf__no`, `.tf__text`) + `.tf__btns` > `button.tf__btn` with `.tf__btn--true` or `.tf__btn--false`
  (`data-correct` on the right one).
- **Reading.** `.reading__body` (`.has-picture` with a picture) > `img.reading__picture`,
  `.reading__text` > `.reading__row` > `p.reading__para` (with `.reading__word`: a `button` that shows
  a meaning when the block has a glossary entry for it, else a plain `mark`) + `.speak`;
  `.reading__tip` (the meaning bubble, an `aria-live` region, `.is-revealed` when shown).
- **Questions.** `.qset__prompt`, `ol.qset__list` > `li.qset__q` (`.is-revealed` once asked; the first
  one is shown) > `.qset__no`, `p.qset__text`, `.speak`, and `p.qset__answer` (a sample answer, printed
  only; a question without one has an empty `p.qset__sample.edit-only` instead, shown only while
  editing, so one can be typed in).

Print shows every answer: gaps are filled, all questions are asked, the right choice is underlined
and `.match__key` lists the pairs; the buttons are hidden.

### Word cards - `.slide--flashcard` (lower secondary)

Grades 6-9 show about four words to a slide: `.wordcards-slide` > `.wordcards` (`.wordcards--single`
for one card) > `article.wordcard.card` (`.has-picture`, `.tap.reveal`, `.revealed`) >
`.wordcard__picture`, `.wordcard__ai` (teacher-only caption), `.wordcard__wordline` >
`.wordcard__word` + `.speak`, `.wordcard__ipa`, `.wordcard__meaning`, `.wordcard__example` >
`.wordcard__example-text` + `.speak`; `.wordcards__hint`. Primary grades keep one `.flashcard` per slide.

### Picture matching and sound sorting (exercises)

The exercise wrapper carries its kind: `.ex--match`, `.ex--gap_fill`, `.ex--sound_sort`,
`.ex--gallery`, `.ex--grid` ... (`.ex--dense` and `.ex--tight` are the smaller type steps).
A matching exercise with pictures is `.match.match--pictures` with `.match__item--picture` items
that hold a `.match__picture`.

Sound sorting: `.sort.sort--words` > `ul.sort__pool` > `li.sort__item` > `button.sort__chip` +
`.speak`, and `.sort__cols` > `.sort__col` > `button.sort__head` + `ul.sort__drop` (`data-s` is the
sound's number; each sound has its own colour). `.sort.sort--sentences` > `ul.sort__legend` >
`li.sort__key` > `.sort__target.is-sample` + `.sort__how` (the mark the instruction asks for: `data-m` is `underline`, `circle` or `tick`); `ol.sort__sentences` > `li.sort__row` > `.sort__no`,
`p.sort__sentence` (with `button.sort__target`, `data-s` and `data-m` once marked) + `.speak`.

### Gallery, grid and self-check (exercises)

- Gallery: `.ex` > `.gal__grid` with `.gal--cols1`, `.gal--cols2`, `.gal--cols3` and `.gal--rows1`, `.gal--rows2` > `figure.gal__item` > `.gal__label`,
  `.gal__picture` (or `.gal__placeholder`), `.gal__caption`; `ol.gal__questions` > `li.gal__q` >
  `.gal__no`, `.gal__question`, `.speak`.
- Grid: `.ex` > `table.grid__table` > `colgroup`, `th.grid__head`, `tr.grid__row` > `td.grid__cell` >
  `.grid__text` (an empty cell is for the pupils; a blank is a `.gap` button as in a gap fill).
- Self-check: `.block.sc` > `header.sc__head` > `h2.sc__title`, `.sc__part`; `.sc__sub`; `ol.sc__list` >
  `li.sc__row` > `p.sc__item` (`.sc__no`, `.sc__text`) + `.sc__btns` > `button.sc__btn[data-level]`
  (`.is-picked`; the choice is kept while the deck is open).

### Conversation chips and paragraph pictures

`ul.dialogue__highlights` > `li.dialogue__chip` under the last slide of a conversation;
`img.reading__para-picture` inside a `.reading__row`, beside its paragraph.

### Rule box - `.slide--rule`

`aside.rule-box` > `h2.rule-box__title.slide-title`, `span.rule-box__part.ui` (`(1/2)` when a long
box continues on a second slide; the template places it at the box's top right corner),
`ul.rule-box__points` > `li.rule-box__point`,
`ul.rule-box__examples` > `li.rule-box__example` > `.rule-box__example-text` + `.speak`.

### Picture - `.slide--image`

`figure.image-ref` > `.image-ref__picture`, `.image-ref__caption`, or `.image-ref__placeholder`.

Pictures that follow each other in a lesson share a slide (at most six): `div.gallery` >
`span.gallery__part.ui` (`(1/2)`) and `div.gallery__grid` with `.gallery--cols1`, `.gallery--cols2`, `.gallery--cols3` and `.gallery--rows1`, `.gallery--rows2` >
`figure.gallery__item` > `.gallery__picture` (or `.gallery__placeholder`) + `.gallery__caption`.

### Teacher notes, editable

`.note-steps` wraps a block's teacher note (a first line and numbered steps); it is one editable unit
(the page swaps it for its raw text while editing).

### Editing on the slide

The page carries a pencil, a Save button and toasts, all `.ui` and placed by the template; a theme only
restyles them. `.edit-ui` is on each of them: `button.edit-toggle` (the pencil, `[aria-pressed="true"]` in
edit mode), `.edit-bar` (the "Editing" chip, shown only while editing, with `.edit-bar__label` and
`.edit-bar__status`), `button.edit-save` > `.edit-save__label` + `.edit-save__count` (hidden until a field
has changed), `.edit-toasts` > `.edit-toast` (`.success`, `.error`).

In edit mode `<body class="editing">`; every editable text carries `data-field-block` and `data-field`
(a path into the block) and, while editing, `contenteditable`. Style them with
`body.editing [data-field]` (and `:hover`, `:focus`, `.is-edited` for a changed text, `.is-invalid` for
one the server refused). Anything hidden until a tap is shown while editing (the structure layer does
that); `.edit-only` elements appear only then. Print hides all of it.

### Teacher-only slides

`.slide--notes` (`ul.warnings`) and `.slide--source-map` (`table.source-map`). Plain functional
styling is fine here.

### Chrome

`nav.dots.ui` > `button.dot.ui[data-phase]` (`.current`): the progress dots. Keep them at least 32px
to hit. `button.speak.ui[data-say]` is the loudspeaker button: the template draws its glyph with
`.speak__icon::before`; override that `content` (and the button's look) to taste.

## Everything fits (`--fit`)

No slide may overflow the stage. The writer cuts long blocks into `(1/2)` slides (`export/fit.py`);
what is left over is shrunk by the deck script: when a slide's content leaves its `.slide-body`, the
script sets `--fit` on the slide (1, 0.95, ... down to 0.75) and the structure layer applies it as
`zoom` to every direct child of `.slide-body`, so type, gaps, pictures and buttons shrink together.
A theme does not have to do anything for this. Two things a theme must keep: `.slide-body` is the
safe area (keep its padding clear of the band, the footer and the dots; do not let content spill
out of it), and a tilted or offset decoration may leave the body by at most 12px.
`tools/deck_check.py <deck.html>` fails when a slide does not fit in any state (pupil, teacher,
teacher with notes, editing); run it on a new theme.

Also in the stack: `nav.dots.dots--many` (a long deck: the dots keep to two rows) and `.dots--crowded`.

## Rules

1. System fonts only: set `font-family` to a stack of installed fonts (`Georgia, "Times New Roman"`,
   `"Trebuchet MS"`, `"Segoe Print"`, `ui-rounded`, `system-ui`, ...) ending in a generic family.
   No `@font-face`.
2. No `url(...)`, no `@import`, no `http`: a deck is one offline file. Draw with gradients, borders,
   shadows, `clip-path`, and emoji or text in `content:`. The checker (`test_every_theme_follows_the_rules`)
   fails on any of them.
3. Contrast: text at least 4.5:1 against what it sits on. Define the tokens above so that `--ink`,
   `--muted` and `--accent-ink` pass on `--bg`, `--card` and the bubbles; white passes on
   `--accent-ink`, `--accent-2-ink` and `--badge`.
4. Text on a slide is at least 32px (the `--size-*` tokens are checked). Word 96px, pattern 56px,
   dialogue 44px are the design targets.
5. `.ui` chrome (hints, countdown label, the speaker buttons, the dots) must stay readable and at
   least 44px to tap. Do not set `display: none` on it.
6. Do not rely on motion: it is switched off for `prefers-reduced-motion`. Keep animations to
   `transform` and `opacity`.
7. Everything must still work when `.deco` boxes are empty and when pictures are missing.
8. Print is handled by the structure layer; do not use `!important` against it.

## Preview

```
classdeck export <lesson> --no-pptx --no-docx --theme <id>   # one deck; open the .html in a browser
classdeck gallery <lesson>                         # every theme side by side (out/gallery/<lesson>/index.html)
python tools/deck_shots.py out/deck.html --slides 1,3,5,8 --out out/shots
```

Open a deck with `#N` appended to jump to slide N, `?teacher=1` for teacher mode (also key `T`).
In a browser window: arrows move, `F` is full screen, `P` prints.
