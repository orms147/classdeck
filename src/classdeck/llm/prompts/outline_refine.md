You are refining the outline of one {{period_minutes}}-minute primary-school English period for grade {{grade}}. The skeleton below was built by code from the textbook page and the curriculum unit. Keep its structure; improve its wording and its mapping.

SKELETON (JSON)
{{skeleton}}

SOURCE SPANS BY SECTION (every line starts with its span id)
{{sections}}

UNIT OUTCOMES (id: text [skills])
{{outcomes}}

UNIT VOCABULARY
{{vocabulary}}

UNIT PATTERNS
{{patterns}}

RULES
{{rules}}

You may:
- rewrite a section title so it says what pupils do;
- choose each section's outcome_ids from the unit outcomes listed above;
- write short notes that tell the drafter what the section teaches and which spans matter;
- move a span between a section and the section directly before or after it.

Every section's span_ids must list ALL of the spans that section keeps, not only the ones you move: copy the spans shown under it, and add or remove only the ones you move. Each span must end up in exactly one section; a span may not be dropped or listed twice.

A section title must describe what the section's blocks will contain. The wrap-up title must not promise language that is not in the unit vocabulary or patterns above: for example, do not title the wrap-up "Say goodbye" when goodbye is not taught; keep "Wrap-up" or name the words and the pattern the lesson does teach.

A section's outcome_ids may list only outcomes whose words appear in that section's spans (and the lesson's outcomes are the union of its sections'): an outcome that no span teaches, such as saying goodbye when no span says goodbye, is listed nowhere. A section with no matching outcome gets an empty list.

A section whose skeleton entry says "review": true keeps exactly the spans it has: move none into it and none out of it.

You may not add or remove sections, reorder them, change their phases, or use a span id that is not listed above (write each span id exactly as shown in the brackets). A section's span_ids must come from the spans listed under it or under its neighbours.

Reply with one JSON object of the form {"sections": [{"title": "...", "outcome_ids": ["..."], "notes": "...", "span_ids": ["..."]}, ...]} with exactly {{section_count}} sections, in the same order as the skeleton.
