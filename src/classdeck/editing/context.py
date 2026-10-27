"""Build what the model sees for an edit, and only that (``docs/editing.md`` section 3).

The context holds the selected blocks, the spans they cite plus other spans of the same pages
ranked against the instruction, the read-only neighbours and section, the English rules for the
grade, the period's outcomes and the unit word list. The rest of the lesson is left out, which
keeps edits local by construction and token usage small.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping

from classdeck.curriculum.loader import Curriculum
from classdeck.editing.models import EditRequest
from classdeck.english.rules import prompt_rules
from classdeck.lesson.schema import Block, Citation, Lesson, Section, SourceDocument, Strict

Sources = Mapping[str, SourceDocument]

CitedSpan = tuple[Citation, str]
"""A citation and the text of the span it points at."""

MAX_EXTRA_SPANS = 20
"""Most spans, beyond the cited ones, taken from the cited pages for the instruction."""

_TOKEN = re.compile(r"\w+")


class EditContext(Strict):
    """Everything the model is shown for one edit request."""

    target_blocks: list[Block]
    cited_spans: list[CitedSpan]
    extra_spans: list[CitedSpan]
    neighbours: list[Block]
    section: Section | None
    rules: str
    outcomes: list[tuple[str, str]]
    word_list_excerpt: list[str]


def build_edit_context(
    lesson: Lesson,
    request: EditRequest,
    *,
    sources: Sources | None = None,
    curriculum: Curriculum | None = None,
) -> EditContext:
    """Assemble the context for ``request``; ``KeyError`` names a target id the lesson lacks.

    The section is that of the first target. Outcomes and the word list are filled only when a
    curriculum is given; outcome ids the curriculum does not know are left out.
    """
    target_ids = list(dict.fromkeys(request.target_block_ids))
    targets: list[Block] = []
    for block_id in target_ids:
        if block_id not in lesson.blocks:
            raise KeyError(f"unknown target block {block_id!r}")
        targets.append(lesson.blocks[block_id])
    documents = sources or {}
    cited = _cited_spans(targets, documents)
    outcomes: list[tuple[str, str]] = []
    words: list[str] = []
    if curriculum is not None:
        outcomes = _outcomes(lesson, curriculum)
        words = sorted(_word_list(lesson, curriculum))
    return EditContext(
        target_blocks=targets,
        cited_spans=cited,
        extra_spans=_extra_spans(cited, documents, request.instruction),
        neighbours=_neighbours(lesson, target_ids),
        section=lesson.section_of(target_ids[0]),
        rules=prompt_rules(lesson.grade, curriculum),
        outcomes=outcomes,
        word_list_excerpt=words,
    )


def _cited_spans(targets: Iterable[Block], sources: Sources) -> list[CitedSpan]:
    """Each distinct citation of the targets with its span text (the quote when unresolved)."""
    found: list[CitedSpan] = []
    seen: set[tuple[str, int, str]] = set()
    for block in targets:
        for citation in block.sources:
            key = (citation.doc_id, citation.page, citation.span_id)
            if key in seen:
                continue
            seen.add(key)
            document = sources.get(citation.doc_id)
            span = document.find_span(citation.page, citation.span_id) if document else None
            found.append((citation, span.text if span is not None else citation.quote or ""))
    return found


def _extra_spans(cited: list[CitedSpan], sources: Sources, instruction: str) -> list[CitedSpan]:
    """Other text spans of the cited pages, by distinct instruction tokens shared, then order."""
    cited_keys = {(c.doc_id, c.page, c.span_id) for c, _ in cited}
    pages = list(dict.fromkeys((c.doc_id, c.page) for c, _ in cited))
    query = set(_TOKEN.findall(instruction.lower()))
    scored: list[tuple[tuple[int, int, int], CitedSpan]] = []
    for page_index, (doc_id, page_number) in enumerate(pages):
        document = sources.get(doc_id)
        if document is None:
            continue
        for page in document.pages:
            if page.number != page_number:
                continue
            for span in page.spans:
                if not span.text.strip() or (doc_id, page_number, span.span_id) in cited_keys:
                    continue
                overlap = len(query & set(_TOKEN.findall(span.text.lower())))
                citation = Citation(doc_id=doc_id, page=page_number, span_id=span.span_id)
                scored.append(((-overlap, page_index, span.order), (citation, span.text)))
    scored.sort(key=lambda item: item[0])
    return [cited_span for _, cited_span in scored[:MAX_EXTRA_SPANS]]


def _neighbours(lesson: Lesson, target_ids: list[str]) -> list[Block]:
    """The block before and after each target in its section, targets excluded, lesson order."""
    excluded = set(target_ids)
    found: dict[str, Block] = {}
    for block_id in target_ids:
        section = lesson.section_of(block_id)
        if section is None:
            continue
        index = section.block_ids.index(block_id)
        for neighbour_index in (index - 1, index + 1):
            if 0 <= neighbour_index < len(section.block_ids):
                neighbour_id = section.block_ids[neighbour_index]
                if neighbour_id not in excluded:
                    found.setdefault(neighbour_id, lesson.blocks[neighbour_id])
    rank = {block.id: index for index, block in enumerate(lesson.ordered_blocks())}
    return sorted(found.values(), key=lambda block: rank[block.id])


def _outcomes(lesson: Lesson, curriculum: Curriculum) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for outcome_id in lesson.curriculum.outcome_ids:
        try:
            found.append((outcome_id, curriculum.outcome(outcome_id).text))
        except KeyError:
            continue
    return found


def _word_list(lesson: Lesson, curriculum: Curriculum) -> frozenset[str]:
    try:
        return curriculum.word_list(lesson.curriculum.unit_id)
    except KeyError:
        return curriculum.word_list()


def render_context(context: EditContext, request: EditRequest) -> str:
    """The user message: instruction, target JSON, spans, section, neighbours, rules, words."""
    target_ids = ", ".join(block.id for block in context.target_blocks)
    if context.section is None:
        section = "The selected block is not placed in any section."
    else:
        section = (
            f"{context.section.title} (phase: {context.section.phase.value}, "
            f"{context.section.minutes} min)"
        )
    outcomes = "\n".join(f"- {outcome_id}: {text}" for outcome_id, text in context.outcomes)
    neighbours = _json(context.neighbours) if context.neighbours else "(none)"
    parts = [
        ("Instruction", request.instruction),
        (f"Target blocks (edit only these: {target_ids})", _json(context.target_blocks)),
        ("Spans cited by the target blocks", _span_lines(context.cited_spans) or "(none)"),
        (
            "Other spans on the same pages, most relevant first",
            _span_lines(context.extra_spans) or "(none)",
        ),
        ("Section", section),
        ("Neighbouring blocks (read-only)", neighbours),
        ("Rules", context.rules),
        ("Curriculum outcomes for this period", outcomes or "(not available)"),
        ("Unit word list", ", ".join(context.word_list_excerpt) or "(not available)"),
    ]
    return "\n\n".join(f"## {heading}\n{body}" for heading, body in parts)


def _json(blocks: Iterable[Block]) -> str:
    data = [block.model_dump(mode="json") for block in blocks]
    return "```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```"


def _span_lines(spans: Iterable[CitedSpan]) -> str:
    return "\n".join(
        f"- {citation.span_id} (page {citation.page}, {citation.doc_id}): {' '.join(text.split())}"
        for citation, text in spans
    )
