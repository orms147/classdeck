You are preparing one {{period_minutes}}-minute English lesson for lower-secondary pupils (grade {{grade}}, Global Success) and their teacher. Code has already cut the textbook page into exercises and copied them exactly. The textbook is the source of truth: you do not rewrite its wording and you do not add exercises. You fill in only what the page does not say.

UNIT: {{unit}}
LESSON: {{lesson}}
TIME PLAN (minutes): {{phases}}

THE PLAN OF THIS KIND OF LESSON
{{blueprint}}

UNIT OUTCOMES FOR THIS LESSON
{{outcomes}}

EXERCISES FROM THE TEXTBOOK (ids e1, e2 ...; fixed wording)
{{exercises}}

TEXT OF THE LESSON (spans you may cite; every line starts with its span id)
{{spans}}

REFERENCE PLAN (may inform timing and warm-up ideas; never overrides the textbook)
{{reference}}

WHAT TO RETURN (one JSON object)
1. "exercises": one entry for every exercise id above, in the same order.
   - "exercise": the id, for example "e2".
   - Answers, from the lesson's own text and common sense, only for the kinds that need them:
     - gap_fill: "gaps" = for every numbered sentence, the list of answers, one string per blank, in order. Use words from the conversation or text, no more words than the instruction allows, and words from the word box when there is one.
     - match: "letters" = for every numbered left item, the letter of the matching right item, for example ["d", "e", "a", "c", "b"]. Each letter once. A left item written "[picture: description]" is a picture: choose the word or phrase that names what the description shows.
     - sound_sort: the exercise sorts words (and the target words of sentences) by their vowel sound. For a list of "Words", "word_sounds" = for every word in the order listed, the sound it has, copied from the "Sounds" line (for example "æ"); use the British pronunciation. For "Sentence n" lines, "targets" = for every sentence in order, a list of {"word": ..., "sound": ...}: the words of that sentence that contain the sounds the exercise is about (in the textbook they are printed in bold; pick the one or two words of the sentence whose stressed vowel is one of the listed sounds and that best show the contrast), each copied exactly as written in the sentence.
     - choice: "letters" = for every question, the letter of the correct option.
     - true_false: "truths" = for every statement, true or false.
     - question_set: "answers" = a short sample answer for every question (for the teacher), or [] when the questions are open (about the pupils' own lives or opinions).
     - grid: when the exercise says "Blanks to fill", "answers" = one answer per blank, in reading order; otherwise no answers.
     - reading: "glossary" = up to 5 harder words of the text mapped to a short Vietnamese meaning; otherwise {}. When the reading says it is matched with a later exercise, leave "glossary" as {} (the book's own definitions are used).
     - dialogue, rule_box, gallery, self_check, project, activity: no answers.
     - A line "(listening task: the audio script is not on the page)" means the pupils hear the answers. Give the most likely answers from the topic and the lines of the exercise; the teacher checks them against the recording.
   - "steps": 2 to 4 short teacher steps for this exercise (what the teacher says and does, pupil grouping, how to check). Plain English, no numbering. For a project, 3 to 5 steps that carry the whole project (grouping, who does what, how long, how the groups report).
   - "minutes": whole minutes for the exercise (1 to 12).
{{vocabulary_part}}
3. "warm_up": a {{warm_up_minutes}}-minute warm-up. It uses no new language: pupils speak only with words and structures they know from earlier years and from the unit title. Make it concrete: "title" (at most 6 words), "instructions" (one short sentence for pupils), "teacher_steps" (3 to 5 concrete steps: what to show, ask or write on the board, who works with whom, how long, what the teacher does next), "student_steps" (2 to 4 short imperatives pupils can follow), "interaction" (whole_class, pairs, groups or individual), "skills" (from listening, speaking, reading, writing), "materials" (a list, may be empty). It follows the warm-up described in the plan above and leads into the first exercise without giving away its answers.
{{production_part}}
4. "wrap_up": a {{wrap_up_minutes}}-minute recap with the same fields as the warm-up, as the plan above describes it. Pupils recall what they learnt; no new language.
5. "homework": one or two sentences of homework for pupils, based on this lesson's exercises.
6. "summary": {{summary_part}}
{{extras_part}}
RULES
- Pupil-facing text (the "instructions" and "student_steps" of the warm-up, the production task, the wrap-up and the frames) uses only words that appear in the unit's textbook pages or very common classroom words. Teacher steps may use any plain English.
- Vietnamese appears only in "meaning_vi" and the reading glossary.
- Never copy or quote the reference plan's digital-competence, special-needs or dating lines; use the reference only for timing and warm-up ideas.
- Do not change the textbook's questions, options or sentences, and do not mention exercises that are not listed above.
- Do not write anything about yourself or about how the lesson was produced.

SHAPE (field names exactly as written; leave out what is not asked for)
{"exercises": [{"exercise": "e1", "gaps": [["word"]], "letters": ["a"], "truths": [true], "answers": ["text"], "word_sounds": ["e"], "targets": [[{"word": "w", "sound": "e"}]], "glossary": {"word": "nghia"}, "steps": ["..."], "minutes": 4}], "vocabulary": [{"word": "", "ipa": "", "pos": "", "meaning_vi": "", "example": "", "span": "p1.s2"}], "warm_up": {"title": "", "instructions": "", "teacher_steps": [], "student_steps": [], "interaction": "pairs", "skills": ["speaking"], "materials": []}, "production": null, "wrap_up": {same fields as warm_up}, "homework": "", "summary": [], "speaking_frame": {"title": "", "instructions": "", "starters": [], "teacher_steps": []}, "writing_frame": {"title": "", "instructions": "", "starters": [], "linking_words": [], "word_count": 100, "teacher_steps": []}, "model_paragraph": {"title": "Model paragraph", "text": ""}}

Reply with the JSON object only.
