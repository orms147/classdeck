You are drafting one section of a primary-school English period for grade {{grade}}. Write the blocks for this section only, in English, for pupils of this grade.

SECTION (from the outline)
{{section}}

WHAT THIS PHASE NEEDS
{{phase_guidance}}

SOURCE SPANS OF THIS SECTION (every line starts with its span id, written exactly as shown in the brackets; "(image)" marks a picture)
{{spans}}

ACTIVITY TEMPLATE (a starting point for the activity block; adapt the steps to the spans)
{{template}}

UNIT VOCABULARY
{{vocabulary}}

UNIT PATTERNS
{{patterns}}

UNIT PHONICS
{{phonics}}

OUTCOMES OF THIS SECTION
{{outcomes}}

RULES
{{rules}}

ALLOWED WORDS
Pupil-facing text — instructions, student steps, dialogue lines, examples — may use only these words, pupils' names, and numbers:
{{allowed_words}}
Teacher-facing text (title, teacher_steps, materials, teacher_note) may use any English. Before you reply, check every pupil-facing word against this list and replace any word that is not in it.

Block kinds you may use: {{kinds}}. Each block's "content" has the fields of its kind:
- dialogue: context, lines [{speaker, text}]
- vocabulary: items [{word, ipa, pos, meaning_vi, example, image}]; "image" is optional and, when a picture line "(image)" above shows the word, is {"span_id": "<that picture's span id>"}
- sentence_pattern: pattern (write ___ for the slot), function, examples, notes
- phonics: sound, letters, words, chant_lines
- activity: type, title, skills, interaction (whole_class | pairs | groups | individual), minutes, instructions, teacher_steps, student_steps, materials, competency_tags
- image_ref: caption, alt
- teacher_note: text

Citations: "span_ids" lists the span ids, from the list above, whose text the block restates or is built from. Leave span_ids empty for content that no span supports, such as a game you add. Never use a span id that is not listed above.
Lyrics and options are copied, not paraphrased: for a song or chant activity, set "instructions" to "Sing together:" or "Chant together:" and put each lyric line of the spans in "student_steps", one line per step, verbatim; for a listen_and_do or look_complete_read activity, put each option or gap-fill line of the spans in "student_steps" verbatim ("Listen. Tick: a. ...", "Complete: ..."). Cite every span you copy from in "span_ids", together with the instruction line of the section.
Warm-ups need rules: a warm-up game gives 3 to 5 student steps that say who starts, what each pupil says, how the turn passes and how the game ends, and names any material; the template above is the shape to fall back on.
Dependencies: "depends_on" lists the indices (0-based) of other blocks in this reply that the block builds on, for example the activity that drills a dialogue, vocabulary or pattern. A block may not depend on itself or form a cycle.
A block may carry an optional "teacher_note" string for the teacher; keep pupil-facing text in the content.

Reply with one JSON object of the form {"blocks": [{"kind": "...", "content": {...}, "span_ids": ["..."], "depends_on": [0], "teacher_note": null}, ...]}.
