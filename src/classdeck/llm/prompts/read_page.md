You are reading page {{page_number}} of a scanned English textbook for Vietnamese lower-secondary pupils. The page image is attached. Transcribe it into blocks, in the order a person reads the page, so that a program can rebuild the text, the exercises and the pictures.

Reply with one JSON object {"blocks": [...]}. The keys of a block are exactly these, spelled like this and nothing else added to them:
- kind: one of section, heading, instruction, paragraph, list_item, table_row, caption, picture, speaker_line.
- text: the transcribed text, character for character as printed (see TEXT RULES). For a picture, a short neutral description instead.
- bbox: the block's rectangle as four numbers [x0, y0, x1, y1], fractions of the page: x from the left edge, y from the top edge, all between 0 and 1.
- speaker: only for speaker_line: the speaker's name exactly as printed, without the colon.
- column: 1 for the left column, 2 for the right column; leave it out on a page with one column.
- highlight: optional list of the phrases of this block's text that the page prints highlighted (a different colour, bold inside regular text, underlined or boxed), each copied exactly as it occurs in the text. Use it for dialogue turns, paragraphs and list items (for example the "highlighted parts" of a conversation, the bold words of a sentence); never for headings, instruction lines or speaker names. Leave it out when nothing is highlighted.
- track: optional, only for an instruction block that has a headphone or audio icon with a number (for example "Track 7"): the number as an integer. Leave it out otherwise.

Example of the shape (the content is made up):
{"blocks": [{"kind": "instruction", "text": "1. Listen and read.", "bbox": [0.05, 0.10, 0.45, 0.13]}, {"kind": "speaker_line", "speaker": "Tom", "text": "Hi, Lan!", "bbox": [0.05, 0.15, 0.45, 0.18], "column": 1}, {"kind": "picture", "text": "Two children reading a book", "bbox": [0.55, 0.15, 0.95, 0.40]}]}

KINDS
- section: the lesson banner or section name in capitals, such as GETTING STARTED, A CLOSER LOOK 1, COMMUNICATION, SKILLS 1. Also the unit banner and a "THIS UNIT INCLUDES" box title.
- heading: any other title or sub-heading, such as the title line under a section name or a box title such as "Remember!". The contents of a "THIS UNIT INCLUDES" or "Remember!" box are "list_item" or "paragraph" blocks.
- instruction: a numbered exercise line, such as "1 Listen and read." or "2 Read the conversation again and tick (✓) T (True) or F (False)."
- paragraph: running text that is not a dialogue line or an exercise line, including a sentence under an exercise.
- list_item: an item of a list: a lettered or numbered sub-item (a, b, c / 1, 2, 3), a bullet, one gap-fill sentence, one word in a word box.
- table_row: one row of a table or matching exercise, cells joined with " | ", for example "1. a. book | a thing you read".
- caption: a short label or caption attached to a picture.
- picture: every photo or illustration on the page that carries content, including a large photo beside or under a dialogue or exercise. One block per picture, with the tightest rectangle around it. Not decorative borders, backgrounds, icons or logos.
- speaker_line: one turn of a dialogue. One block per turn, never several turns in one block. Put the speaker's name in "speaker" and only the spoken words in "text".

READING ORDER
Read as a person does: top to bottom, and on a page with two columns finish the whole left column before the right column. An exercise line comes before its content; a picture comes where it is placed in the flow. Do not sort blocks by position mechanically.

TEXT RULES
- Copy the wording exactly, including capital letters, punctuation, contractions (How're, I'm), underscores or dots for blanks (write a blank as ___), and parenthesised hints. Do not correct, translate, summarise or add anything.
- Keep the exercise number at the start of an instruction line and write it with a full stop: the printed "2 Read the conversation again." becomes "2. Read the conversation again.". Do the same for numbered "list_item" and "table_row" blocks that print a number without a full stop. Keep the letter or number of sub-items ("a. ", "1. ").
- Join the lines of one paragraph into a single line of text; do not keep the printed line breaks.
- Vietnamese text (instructions, notes) is copied as printed, with diacritics.
- Skip page furniture and decoration: page numbers, running headers and footers (for example "Unit 1 - Local community 9"), watermarks, publisher or logo marks, decorative borders, icons next to exercise numbers (speaker or headphone icons) and audio track numbers (the number goes into "track" of the instruction, never into "text").
- Do not invent blocks for text you cannot read; leave it out.

Answer with the JSON object only.
