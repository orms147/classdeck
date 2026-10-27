"""The draft pack: one prompt that asks a chat model to draft every section of an outline.

``docs/output-formats.md``, "Prompt packs": the model replies with one ``DraftSection`` JSON
object per section, as a JSON array; :mod:`classdeck.handoff.import_draft` validates the reply
with the rules the model drafter applies. The pack carries the outline, each section's source
spans as ``[p1.s3] text`` lines, the drafting rules, the activity template of the section's
type, the unit's language and the reply schema. For a given outline it is deterministic.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from classdeck.curriculum import Curriculum
from classdeck.english.rules import DRAFTABLE_BLOCK_KINDS, prompt_rules
from classdeck.handoff.common import one_line
from classdeck.lesson import BlockKind, Phase, SourceDocument
from classdeck.planning import (
    DraftSection,
    Outline,
    SectionContext,
    build_context,
    fill_template,
    label_span,
    section_language,
)

CONTENT_FIELDS = (
    "- dialogue: context, lines [{speaker, text}]\n"
    "- vocabulary: items [{word, ipa, pos, meaning_vi, example}]\n"
    "- sentence_pattern: pattern (write ___ for the slot), function, examples, notes\n"
    "- phonics: sound, letters, words, chant_lines\n"
    "- activity: type, title, skills, interaction (whole_class | pairs | groups | individual), "
    "minutes, instructions, teacher_steps, student_steps, materials, competency_tags\n"
    "- image_ref: caption, alt\n"
    "- teacher_note: text"
)
"""The ``content`` fields of each block kind, as the model drafter's prompt lists them."""

WRAP_UP_REPLY = '{"blocks": []}'
"""The entry for a wrap-up section: the engine builds the summary and the teacher note itself."""


def build_draft_pack(
    outline: Outline,
    sources: Sequence[SourceDocument],
    curriculum: Curriculum,
) -> str:
    """The complete Markdown prompt that drafts every section of ``outline``."""
    unit = curriculum.unit(outline.curriculum.unit_id)
    count = len(outline.sections)
    kinds = ", ".join(sorted(kind.value for kind in _prompt_kinds()))
    schema = json.dumps(DraftSection.model_json_schema(), indent=2, ensure_ascii=False)
    patterns = [
        f"- {one_line(p.pattern)}" + (f" ({one_line(p.function)})" if p.function else "")
        for p in unit.patterns
    ]
    phonics = [f"- {p.sound} ({p.letters}): {', '.join(p.words)}" for p in unit.phonics]
    parts = [
        f"# Draft pack — {one_line(outline.title)}",
        "",
        "## 1. Task",
        "",
        f"You draft a {curriculum.period_minutes}-minute period of primary English in Viet Duc "
        f"for grade {outline.grade}, section by section, in English, for pupils of that grade. "
        f"The outline below has {count} sections. Use only the source spans listed under each "
        "section as the basis of its blocks.",
        "",
        "## 2. Rules",
        "",
        prompt_rules(outline.grade, curriculum),
        "",
        "## 3. Unit language",
        "",
        f"Unit: {one_line(unit.title)}",
        "",
        "Vocabulary: " + (", ".join(unit.vocabulary) or "(none)"),
        "",
        "Patterns:",
        *(patterns or ["(none)"]),
        "",
        "Phonics:",
        *(phonics or ["(none)"]),
        "",
        "Allowed words. Pupil-facing text (instructions, student steps, dialogue lines, "
        "examples) may use only these words, pupils' names and numbers; teacher_steps may use "
        "any English:",
        "",
        ", ".join(sorted(curriculum.word_list(unit.id))) or "(no word list)",
        "",
        "## 4. Outline",
        "",
        f"{one_line(outline.title)} · grade {outline.grade} · {outline.total_minutes()} min "
        f"in {count} sections",
        "",
    ]
    for index in range(count):
        parts.extend(_section(build_context(outline, index, sources, curriculum)))
    parts.extend(
        [
            "## 5. Reply format",
            "",
            f'Block kinds you may use: {kinds}. Each block\'s "content" has the fields of its '
            "kind:",
            CONTENT_FIELDS,
            "",
            'Citations: "span_ids" lists the span ids, from the list of the same section, whose '
            "text the block restates or is built from. Leave span_ids empty for content that no "
            "span supports, such as a game you add. Never use a span id that is not listed "
            "under the section the block belongs to.",
            'Dependencies: "depends_on" lists the indices (0-based) of other blocks in the same '
            "section's blocks array that the block builds on, for example the activity that "
            "drills a dialogue, vocabulary or pattern. A block may not depend on itself or form "
            "a cycle.",
            'A block may carry an optional "teacher_note" string for the teacher; keep '
            "pupil-facing text in the content.",
            "",
            "Each section is one JSON object of this schema:",
            "",
            "```json",
            schema,
            "```",
            "",
            f"Answer with ONE JSON array of exactly {count} objects, one per section, in the "
            "order of the outline, and nothing else: no explanation and no text before or "
            "after the array. For a section marked as built by the engine, use "
            f"{WRAP_UP_REPLY}.",
        ]
    )
    return "\n".join(parts).rstrip() + "\n"


def _prompt_kinds() -> frozenset[BlockKind]:
    return DRAFTABLE_BLOCK_KINDS - {BlockKind.OBJECTIVE, BlockKind.SUMMARY}


def _section(context: SectionContext) -> list[str]:
    section = context.section
    lines = [
        f"### Section {context.index + 1} · {section.phase.value} · {one_line(section.title)} · "
        f"{section.minutes} min",
        "",
    ]
    if section.phase is Phase.WRAP_UP:
        lines.extend(
            [
                f"Built by the engine (summary and teacher note): reply with {WRAP_UP_REPLY} "
                "for this section.",
                "",
            ]
        )
        return lines
    if section.activity_type is not None:
        lines.append(f"- activity type: {section.activity_type.value}")
    if context.outcomes:
        lines.append("- outcomes:")
        lines.extend(
            f"  - {outcome.id}: {one_line(outcome.text)} [{', '.join(outcome.skills)}]"
            for outcome in context.outcomes
        )
    if section.notes:
        lines.append(f"- notes: {one_line(section.notes)}")
    if section.review:
        lines.append(
            "- review: the engine adds a vocabulary and a sentence_pattern block from the spans "
            "below; reply with exactly one look_listen_repeat activity titled 'Say it again' "
            "that repeats them and cites no span"
        )
    lines.extend(["", 'Source spans ("(image)" marks a picture):', "", "```text"])
    lines.extend(label_span(ref, span) for ref, span in context.spans)
    if not context.spans:
        lines.append("(no spans)")
    lines.append("```")
    if context.template is not None:
        language = section_language(context)
        filled = fill_template(
            context.template,
            pattern=language.pattern_text,
            words=language.words_text,
            sound=language.sound_text,
        )
        lines.extend(
            [
                "",
                "Activity template (a starting point for the activity block; adapt the steps "
                "to the spans):",
                "",
                "```json",
                filled.model_dump_json(indent=2),
                "```",
            ]
        )
    lines.append("")
    return lines
