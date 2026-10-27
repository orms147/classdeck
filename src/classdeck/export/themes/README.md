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
`--size-pattern` (56), `--size-dialogue` (44), `--size-sound` (180).

Set by the template, for you to read: `--phase-color` is the current phase's colour on `.slide`,
`.dot` and `.phase-strip__item` (it follows `data-phase`). Use `background: var(--phase-color)`.

## Slides

`.stage-wrap` > `.stage` is the fixed 1920x1080 canvas (scaled to the window; give it a
background if you like, but do not resize it). Every slide is `<section class="slide slide--<type>" data-phase data-kind data-slide-type>`.

- `data-phase`: `lesson` (cover), `warm_up`, `presentation`, `practice`, `production`, `wrap_up`,
  `teacher` (the teacher-only slides).
- `data-slide-type` / the `slide--<type>` class: `.slide--cover`, `.slide--divider`,
  `.slide--flashcard`, `.slide--pattern`, `.slide--dialogue`, `.slide--phonics`, `.slide--activity`,
  `.slide--summary`, `.slide--image`, `.slide--notes`, `.slide--source-map`.
- `data-kind`: the lesson block kind (`vocabulary`, `sentence_pattern`, `dialogue`, `phonics`,
  `activity`, `summary`, `image_ref`) or, for the framing slides, the slide type.
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

`.dialogue.tap.stepper` > `.dialogue__context`, `.chat` > `.bubble.step-item` with `.bubble--left` or `.bubble--right`
(`data-speaker`; the second speaker is on the right) > `.bubble__avatar`, `.bubble__text`, `.speak`; `.dialogue__hint`.
`.bubble.pending` is a line not yet shown (the structure layer hides its text and button);
`.stepper.complete` once all lines are shown.

### Sentence pattern - `.slide--pattern`

`.pattern.card.tap.cycle` > `.pattern__head` > `.pattern__text` (with `.pattern__slot`, the box that
examples fill; `.filled` when filled; `data-empty`) + `.speak`; `.pattern__line` (the example as one
line when it does not align with the slot), `.pattern__function`, `ul.pattern__examples` >
`li.pattern__example` (`.current` is the one in the slot) > `.pattern__example-text` + `.speak`;
`.pattern__hint`.

### Phonics - `.slide--phonics`

`.phonics` > `.phonics__sound`, `.phonics__letters`, `ul.phonics__words` > `li.phonics__word` >
`.phonics__word-text` (target letters in `<mark>`) + `.speak`; `ul.phonics__chant` >
`li.phonics__chant-line`.

### Activity - `.slide--activity`

`.activity` > `.activity__heading` > `.icon.activity__icon` + `.activity__title`;
`.activity__instructions`; `ol.activity__steps` > `li.step.card` > `.step__number`, `.step__text`;
`.countdown`.

### Summary - `.slide--summary`

`.summary` > `.summary__title`, `.summary-grid` > `figure.summary-card.card` >
`.summary-card__picture`, `.summary-card__word`, `.speak`; `.summary__pattern` >
`.summary__pattern-text` (with `.pattern__slot`) + `.speak`; `ul.summary__bullets` (only when there
are no words or patterns).

### Picture - `.slide--image`

`figure.image-ref` > `.image-ref__picture`, `.image-ref__caption`, or `.image-ref__placeholder`.

### Teacher-only slides

`.slide--notes` (`ul.warnings`) and `.slide--source-map` (`table.source-map`). Plain functional
styling is fine here.

### Chrome

`nav.dots.ui` > `button.dot.ui[data-phase]` (`.current`): the progress dots. Keep them at least 32px
to hit. `button.speak.ui[data-say]` is the loudspeaker button: the template draws its glyph with
`.speak__icon::before`; override that `content` (and the button's look) to taste.

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
