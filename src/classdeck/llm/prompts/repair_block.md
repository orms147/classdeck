You fix one block of a grade {{grade}} primary-school English lesson. Its pupil-facing text uses words outside the grade's word list or grammar outside the grade's scope. The block is otherwise right and must stay as it is.

BLOCK (JSON)
{{block}}

PROBLEMS (from the level checks)
{{problems}}

ALLOWED WORDS
Pupil-facing text — instructions, student steps, dialogue lines, vocabulary words and examples, sentence patterns and their examples — may use only these words, pupils' names, and numbers:
{{allowed_words}}
Names in the spans this block cites, also allowed: {{names}}

RULES
{{rules}}

Rewrite only the pupil-facing text so that every word is in the allowed words, a name or a number, and the grammar stays inside the scope. Rephrase each step with allowed words; never replace a concrete step by a vague one ("Play the game." is not a step); keep the number of steps and roughly the length of each; name the object, the action and who speaks. Prefer the plainest allowed word. A reply that has fewer steps, or steps and instructions under half their original words, is thrown away.

Do not rewrite quoted text: vocabulary words, the sentence pattern itself and dialogue lines copied from the textbook stay exactly as given (they are not part of your reply's effect). Teacher-facing text (title, teacher_steps, materials) may use any English and stays as given. Change nothing else: only the pupil-facing text of your reply is used.

Reply with one JSON object: the same block with the same "id" and the same "kind", of the form {"id": "...", "kind": "...", "content": {...}}, where "content" has every field of the block above.
