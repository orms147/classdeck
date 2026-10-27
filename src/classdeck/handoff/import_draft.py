"""Import a chat model's reply to the draft pack as a lesson.

``docs/output-formats.md``, "Draft pack": the reply is validated with the rules of the model
drafter (:func:`~classdeck.planning.reply_problems`); a section that fails is drafted by the
template drafter instead. Accepted sections are materialised exactly as the model drafter's
are, with the engine-built blocks ``draft_lesson`` adds (objectives, the period-2 review
blocks, the wrap-up); then :func:`classdeck.pipeline.finish_lesson` infers dependencies,
verifies citations without a provider (midpoint rule) and runs the level checks.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import ValidationError

from classdeck.curriculum import Curriculum
from classdeck.lesson import (
    Block,
    Lesson,
    Phase,
    Section,
    SourceDocument,
    SourceRef,
    Strict,
)
from classdeck.llm import extract_json
from classdeck.pipeline import finish_lesson
from classdeck.planning import (
    DraftBlock,
    DraftSection,
    Outline,
    SectionContext,
    TemplateDrafter,
    build_context,
    by_doc_id,
    materialise,
    reply_problems,
    review_proposal,
    with_review,
)

# Private helpers of the planning module, reused so that the imported lesson is assembled
# exactly as ``draft_lesson`` assembles it (ids, objectives, wrap-up, section ids).
from classdeck.planning.drafter import _Ids, _objectives, _section_id, _wrap_up_blocks


class SectionReport(Strict):
    """How one outline section entered the lesson."""

    index: int
    title: str
    status: Literal["model", "template"]
    problems: list[str] = []


class ImportResult(Strict):
    """The imported lesson and, per outline section, whether the reply or the template made it.

    A wrap-up section is always built by the engine and reported as ``template`` with no
    problems.
    """

    lesson: Lesson
    section_reports: list[SectionReport]


def import_draft(
    outline: Outline,
    sources: Sequence[SourceDocument],
    curriculum: Curriculum,
    reply_text: str,
) -> ImportResult:
    """The lesson drafted by ``reply_text``, a JSON array with one ``DraftSection`` per section.

    A section whose entry is missing or fails validation is drafted by the template drafter and
    its problems are recorded in its :class:`SectionReport`.
    """
    if not outline.sections:
        raise ValueError("the outline has no sections")
    entries, reply_problem, surplus = _entries(reply_text, len(outline.sections))
    unit = curriculum.unit(outline.curriculum.unit_id)
    fallback = TemplateDrafter()
    ids = _Ids()
    blocks: dict[str, Block] = {}
    sections: list[Section] = []
    reports: list[SectionReport] = []
    section_ids: set[str] = set()
    for index, outline_section in enumerate(outline.sections):
        context = build_context(outline, index, sources, curriculum)
        section_blocks: list[Block] = []
        if index == 0:
            section_blocks.extend(_objectives(outline, curriculum, ids))
        status: Literal["model", "template"] = "template"
        problems: list[str] = []
        if outline_section.phase is Phase.WRAP_UP:
            section_blocks.extend(_wrap_up_blocks(blocks, unit, ids))
        else:
            reply, problems = _accepted(entries[index], reply_problem, context)
            if reply is None:
                reply = fallback.draft_section(context)
            else:
                status = "model"
            review: list[DraftBlock] = []
            if outline_section.review:
                review = fallback.review_blocks(context)
                reply, from_reply = review_proposal(reply, fallback.draft_section(context))
                if not from_reply:
                    status = "template"
            section_blocks.extend(materialise(with_review(review, reply), context, ids))
        reports.append(
            SectionReport(
                index=index, title=outline_section.title, status=status, problems=problems
            )
        )
        for block in section_blocks:
            blocks[block.id] = block
        sections.append(
            Section(
                id=_section_id(outline_section.phase, section_ids),
                phase=outline_section.phase,
                title=outline_section.title,
                minutes=outline_section.minutes,
                block_ids=[block.id for block in section_blocks],
            )
        )
    if surplus:
        reports[-1].problems.append(surplus)
    lesson = Lesson(
        id=ids.next("l"),
        title=outline.title,
        grade=outline.grade,
        curriculum=outline.curriculum.model_copy(),
        sources=[
            SourceRef(doc_id=doc.doc_id, filename=doc.filename, sha256=doc.sha256)
            for doc in sources
        ],
        sections=sections,
        blocks=blocks,
    )
    lesson = finish_lesson(lesson, by_doc_id(sources), curriculum, None)
    return ImportResult(lesson=lesson, section_reports=reports)


def _entries(reply_text: str, count: int) -> tuple[list[Any], str, str]:
    """One raw entry per section (``None`` when absent), a reply-wide problem, a surplus note.

    A single object with ``blocks`` counts as a one-entry array; an object with a ``sections``
    array is read as that array.
    """
    try:
        data = json.loads(extract_json(reply_text))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        return [None] * count, f"the reply is not valid JSON: {exc}", ""
    if isinstance(data, dict):
        data = data["sections"] if isinstance(data.get("sections"), list) else [data]
    if not isinstance(data, list):
        return [None] * count, "the reply is not a JSON array of section objects", ""
    surplus = f"the reply has {len(data) - count} extra entries; they were ignored"
    entries = [data[i] if i < len(data) else None for i in range(count)]
    return entries, "", surplus if len(data) > count else ""


def _accepted(
    entry: Any, reply_problem: str, context: SectionContext
) -> tuple[DraftSection | None, list[str]]:
    """The section's reply when it passes the model drafter's validation, else its problems."""
    if reply_problem:
        return None, [reply_problem]
    if entry is None:
        return None, ["the reply has no entry for this section"]
    try:
        reply = DraftSection.model_validate(entry)
    except ValidationError as exc:
        error = exc.errors(include_url=False)[0]
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        return None, [f"not a valid DraftSection: {location}: {error['msg']}"]
    problems = reply_problems(reply, context)
    if not reply.blocks:
        problems.append("the entry has no blocks")
    return (None, problems) if problems else (reply, [])
