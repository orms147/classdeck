"""Stage 1 of planning: a deterministic outline skeleton, optionally refined by a model.

Everything the textbook structure and the curriculum can decide is decided here without a
model call. :func:`refine_outline` lets a model rewrite titles, reassign outcomes, write notes
and move spans between neighbouring sections; any other change is rejected and, after one
re-prompt, the skeleton is kept. See ``docs/planning.md``, stage 1.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence

from classdeck.curriculum.loader import Curriculum
from classdeck.curriculum.models import Outcome, Unit
from classdeck.english.rules import (
    LESSON_SHAPES,
    PHASES,
    ActivityTemplate,
    load_activity_templates,
    phase_for,
    prompt_rules,
)
from classdeck.english.validators import word_forms
from classdeck.lesson.labels import lesson_label_for
from classdeck.lesson.schema import ActivityType, CurriculumRef, Phase, SourceDocument, SpanKind
from classdeck.llm.base import LLMProvider, Message
from classdeck.llm.prompts import load_prompt
from classdeck.llm.structured import StructuredOutputError, complete_json
from classdeck.planning.models import Outline, OutlineRefinement, OutlineSection, SpanRef
from classdeck.planning.segment import (
    Chunk,
    bm25_rank_all,
    by_doc_id,
    instruction_number,
    is_qualified,
    lesson_runs,
    match_ref,
    resolve_ref,
    segment_document,
    span_lines,
    span_text,
    span_token,
)

logger = logging.getLogger(__name__)

ACTIVITIES_PER_PERIOD = 3
"""Textbook activities covered by one period: period 1 takes 1-3, period 2 takes 4-6."""

REVIEW_ACTIVITIES = 2
"""Textbook activities a period-2 warm-up reviews: the dialogue (1) and the pattern drill (2)."""

BM25_SPANS_PER_SECTION = 6
WARM_UP_ACTIVITY = ActivityType.GAME
FIRST_PERIOD_WARM_UP_ACTIVITY = ActivityType.TPR
"""Lesson 1, period 1 introduces the unit's pattern, so its warm-up drills none of it: TPR."""
WARM_UP_TITLE = "Warm-up"
TPR_WARM_UP_TITLE = "Warm-up: listen and do"
REVIEW_TITLE = "Review: say it again"
WRAP_UP_TITLE = "Wrap-up"

PERIOD_TWO_PHASES: tuple[Phase, ...] = (Phase.PRACTICE, Phase.PRACTICE, Phase.PRODUCTION)
"""Phases of the three textbook activities of a period 2 that opens with a review."""
PERIOD_TWO_MINUTES: tuple[int, ...] = (4, 8, 8, 7, 6, 2)
"""Warm-up, review, activities 4-6, wrap-up: 35 minutes (scaled for another period length)."""

_DEFAULT_MINUTES: dict[Phase, int] = {spec.phase: spec.default_minutes for spec in PHASES}


# --- skeleton ----------------------------------------------------------------------------------


def scale_minutes(defaults: Sequence[int], total: int) -> list[int]:
    """Scale ``defaults`` so they sum to ``total``, as integers, largest remainders rounded up."""
    base = sum(defaults)
    if base == total or base <= 0:
        return list(defaults)
    exact = [value * total / base for value in defaults]
    scaled = [math.floor(value) for value in exact]
    by_remainder = sorted(range(len(exact)), key=lambda i: (-(exact[i] - scaled[i]), i))
    for index in by_remainder[: total - sum(scaled)]:
        scaled[index] += 1
    return scaled


_WORD = re.compile(r"[a-z]+(?:'[a-z]+)*")
_APOSTROPHES = str.maketrans({"\N{RIGHT SINGLE QUOTATION MARK}": "'"})
_FUNCTION_WORDS = frozenset(
    {"a", "an", "the", "to", "of", "and", "or", "in", "on", "at", "is", "are", "it", "this"}
    | {"that", "with", "for", "from", "by", "about", "using", "use", "i", "you", "he", "she"}
    | {"we", "they", "me", "my", "your", "do", "does", "did", "am", "be"}
)
"""Words too common to tie an outcome to a chunk; ``i'm`` stays, it is a pattern word."""


def _words(text: str) -> list[str]:
    return _WORD.findall(text.translate(_APOSTROPHES).lower())


def _forms(words: Sequence[str]) -> set[str]:
    forms: set[str] = set()
    for word in words:
        forms.update(word_forms(word))
    return forms


def outcome_terms(unit: Unit, outcome: Outcome) -> frozenset[str]:
    """The words that show a chunk teaches ``outcome``, in all their forms.

    The outcome's ``keywords``, plus the words of the unit's vocabulary and patterns that the
    outcome text itself mentions (``Say goodbye using Goodbye / Bye`` names ``goodbye`` and
    ``bye``, ``Introduce oneself using "I'm ..."`` names ``i'm``), minus function words.
    """
    mentioned = set(_words(outcome.text))
    unit_words: set[str] = set()
    for text in [*unit.vocabulary, *(pattern.pattern for pattern in unit.patterns)]:
        unit_words.update(_words(text))
    terms = [*_words(" ".join(outcome.keywords)), *sorted(mentioned & unit_words)]
    return frozenset(_forms([term for term in terms if term not in _FUNCTION_WORDS]))


def _chunk_forms(section: OutlineSection, docs: Mapping[str, SourceDocument]) -> set[str]:
    """The word forms of the section's text spans (images and empty spans give none)."""
    texts: list[str] = []
    for ref in section.span_refs:
        span = resolve_ref(docs, ref)
        if span is not None and span.kind is not SpanKind.IMAGE:
            texts.append(span.text)
    return _forms(_words(" ".join(texts)))


def _matching_outcomes(unit: Unit, template: ActivityTemplate) -> list[str]:
    """The skill rule: the unit's outcomes whose skills intersect the template's."""
    skills = {skill.value for skill in template.default_skills}
    return [outcome.id for outcome in unit.outcomes if skills & set(outcome.skills)]


def _covered_outcomes(
    unit: Unit, forms: set[str], terms: Mapping[str, frozenset[str]]
) -> list[str]:
    return [outcome.id for outcome in unit.outcomes if terms[outcome.id] & forms]


def _assign_outcomes(
    unit: Unit,
    content: Sequence[tuple[ActivityTemplate, OutlineSection]],
    docs: Mapping[str, SourceDocument],
) -> list[list[str]]:
    """Outcome ids per content section: those whose terms occur in the section's chunk.

    A period whose chunks mention no outcome term at all (notes, slides, a unit the sources do
    not match) falls back to the skill rule for every section, and says so in the log.
    """
    terms = {outcome.id: outcome_terms(unit, outcome) for outcome in unit.outcomes}
    covered = [
        _covered_outcomes(unit, _chunk_forms(section, docs), terms) for _, section in content
    ]
    if any(covered):
        return covered
    logger.info(
        "unit %s: no chunk of the period mentions an outcome's keywords; assigning outcomes by "
        "skill",
        unit.id,
    )
    return [_matching_outcomes(unit, template) for template, _ in content]


def _unit_outcome_ids(unit: Unit, sections: Sequence[OutlineSection]) -> list[str]:
    """The unit's outcomes that some section serves, in curriculum order."""
    used = {outcome_id for section in sections for outcome_id in section.outcome_ids}
    return [outcome.id for outcome in unit.outcomes if outcome.id in used]


def _chunk_title(chunk: Chunk, docs: Mapping[str, SourceDocument]) -> str | None:
    if chunk.instruction is None or chunk.doc_id not in docs:
        return None
    return span_text(docs[chunk.doc_id], chunk.instruction).strip() or None


def _unit_label(unit: Unit) -> str:
    """``Unit 1 Hello`` when the curriculum numbers the textbook unit, else the bare title."""
    if unit.textbook_units:
        return f"Unit {unit.textbook_units[0].unit} {unit.title}"
    return unit.title


def _lesson_chunks(sources: Sequence[SourceDocument], lesson_no: int) -> dict[int, Chunk]:
    """The chunks of one lesson by activity number.

    A document holding several lessons restarts the numbering at 1 for each, so the chunks are
    grouped into runs and run ``lesson_no`` is used. When fewer runs exist the first run is
    used. Across documents the runs at the same position are merged, the first chunk of a
    number winning.
    """
    runs_per_doc = [lesson_runs(segment_document(document)) for document in sources]
    position = lesson_no - 1
    if any(len(runs) > position for runs in runs_per_doc):
        index = position
    else:
        index = 0
        if lesson_no > 1 and any(runs_per_doc):
            logger.warning(
                "the sources hold fewer than %d lessons; using their first lesson's activities",
                lesson_no,
            )
    chunks: dict[int, Chunk] = {}
    for runs in runs_per_doc:
        if len(runs) > index:
            for chunk in runs[index]:
                chunks.setdefault(chunk.number, chunk)
    return chunks


def _review_refs(chunks: Mapping[int, Chunk], docs: Mapping[str, SourceDocument]) -> list[SpanRef]:
    """The text spans of activities 1-``REVIEW_ACTIVITIES``: no headings, instructions, images."""
    refs: list[SpanRef] = []
    for number in range(1, REVIEW_ACTIVITIES + 1):
        chunk = chunks.get(number)
        if chunk is None:
            continue
        for ref in chunk.span_refs:
            span = resolve_ref(docs, ref)
            if (
                span is None
                or span.kind in (SpanKind.HEADING, SpanKind.IMAGE)
                or instruction_number(span) is not None
                or not span.text.strip()
            ):
                continue
            refs.append(ref)
    return refs


def _warm_up(*, lesson_no: int, period: int) -> OutlineSection:
    """The warm-up section: a TPR in lesson 1, period 1, else a pattern game; no new language.

    Lesson 1, period 1 is the first time the unit's pattern appears, so its warm-up uses
    classroom language only (``tpr``); the pattern game belongs to later periods, when the
    pattern has been taught.
    """
    if lesson_no == 1 and period == 1:
        return OutlineSection(
            phase=Phase.WARM_UP,
            title=TPR_WARM_UP_TITLE,
            minutes=0,
            activity_type=FIRST_PERIOD_WARM_UP_ACTIVITY,
            notes="Classroom-language TPR only; the pattern is first taught in the presentation.",
        )
    return OutlineSection(
        phase=Phase.WARM_UP, title=WARM_UP_TITLE, minutes=0, activity_type=WARM_UP_ACTIVITY
    )


def _review_section(refs: list[SpanRef]) -> OutlineSection:
    """The presentation of a period 2: the words and pattern of activities 1-2, said again."""
    return OutlineSection(
        phase=Phase.PRESENTATION,
        title=REVIEW_TITLE,
        minutes=0,
        activity_type=ActivityType.LOOK_LISTEN_REPEAT,
        span_refs=refs,
        review=True,
        notes="Review of the previous period's words and pattern; no new language.",
    )


def build_skeleton(
    sources: Sequence[SourceDocument],
    curriculum: Curriculum,
    *,
    unit_id: str,
    lesson_no: int,
    period: int,
    grade: int,
) -> Outline:
    """The deterministic outline: lesson shape, phases, timing, chunks, outcomes.

    Raises ``ValueError`` when ``(lesson_no, period)`` is not a known lesson shape and
    ``KeyError`` when the unit is not in the curriculum.
    """
    try:
        shape = LESSON_SHAPES[(lesson_no, period)]
    except KeyError:
        known = ", ".join(f"({lesson}, {per})" for lesson, per in LESSON_SHAPES)
        raise ValueError(
            f"no lesson shape for lesson {lesson_no}, period {period}; "
            f"known (lesson, period) pairs: {known}"
        ) from None
    unit = curriculum.unit(unit_id)
    templates = load_activity_templates()
    docs = by_doc_id(sources)

    chunks_by_number = _lesson_chunks(sources, lesson_no)

    review_refs: list[SpanRef] = []
    if period > 1:
        review_refs = _review_refs(chunks_by_number, docs)
        if not review_refs:
            logger.info(
                "lesson %d, period %d: the sources hold no text for activities 1-%d, so the "
                "period opens without a review",
                lesson_no,
                period,
                REVIEW_ACTIVITIES,
            )
    reviewing = bool(review_refs)

    content: list[OutlineSection] = []
    content_templates: list[ActivityTemplate] = []
    for index, activity_type in enumerate(shape):
        template = templates[activity_type]
        title = template.title_template
        span_refs: list[SpanRef] = []
        if chunks_by_number:
            chunk = chunks_by_number.get((period - 1) * ACTIVITIES_PER_PERIOD + index + 1)
            if chunk is not None:
                span_refs = list(chunk.span_refs)
                title = _chunk_title(chunk, docs) or title
        else:
            span_refs = bm25_rank_all(sources, template.title_template, k=BM25_SPANS_PER_SECTION)
        content.append(
            OutlineSection(
                phase=PERIOD_TWO_PHASES[index] if reviewing else phase_for(activity_type),
                title=title,
                minutes=0,
                activity_type=activity_type,
                span_refs=span_refs,
            )
        )
        content_templates.append(template)
    assigned = _assign_outcomes(unit, list(zip(content_templates, content, strict=True)), docs)
    content = [
        section.model_copy(update={"outcome_ids": outcome_ids})
        for section, outcome_ids in zip(content, assigned, strict=True)
    ]

    sections = [
        _warm_up(lesson_no=lesson_no, period=period),
        *([_review_section(review_refs)] if reviewing else []),
        *content,
        OutlineSection(phase=Phase.WRAP_UP, title=WRAP_UP_TITLE, minutes=0),
    ]
    defaults = (
        list(PERIOD_TWO_MINUTES)
        if reviewing
        else [_DEFAULT_MINUTES[section.phase] for section in sections]
    )
    minutes = scale_minutes(defaults, curriculum.period_minutes)
    sections = [
        section.model_copy(update={"minutes": value})
        for section, value in zip(sections, minutes, strict=True)
    ]
    return Outline(
        title=lesson_label_for(grade, lesson_no, period),
        grade=grade,
        curriculum=CurriculumRef(
            version=curriculum.file.version,
            unit_id=unit.id,
            unit_no=unit.textbook_units[0].unit if unit.textbook_units else None,
            unit_title=unit.title,
            lesson_no=lesson_no,
            period=period,
            outcome_ids=_unit_outcome_ids(unit, sections),
        ),
        doc_ids=[document.doc_id for document in sources],
        sections=sections,
    )


# --- refinement --------------------------------------------------------------------------------

_REJECTION = (
    "Your reply was rejected:\n{problems}\n"
    "Respond again with a single JSON object that fixes these problems and changes nothing else."
)


def _sections_text(outline: Outline, docs: Mapping[str, SourceDocument]) -> str:
    qualified = is_qualified(ref for section in outline.sections for ref in section.span_refs)
    parts: list[str] = []
    for index, section in enumerate(outline.sections):
        lines = span_lines(docs, section.span_refs, qualified=qualified) or ["(no spans)"]
        parts.append(f"Section {index + 1} ({section.phase.value}): {section.title}")
        parts.extend(lines)
    return "\n".join(parts)


def _outcomes_text(unit: Unit) -> str:
    lines = [f"- {o.id}: {o.text} [{', '.join(o.skills)}]" for o in unit.outcomes]
    return "\n".join(lines) or "(none)"


def _patterns_text(unit: Unit) -> str:
    lines = [f"- {p.pattern}" + (f" ({p.function})" if p.function else "") for p in unit.patterns]
    return "\n".join(lines) or "(none)"


def _key(ref: SpanRef) -> tuple[str, str]:
    return ref.doc_id, ref.span_id


def _allowed_refs(outline: Outline, index: int) -> list[SpanRef]:
    """Spans a refined section may use: its own refs first, then its neighbours'."""
    allowed: list[SpanRef] = []
    for position in (index, index - 1, index + 1):
        if 0 <= position < len(outline.sections):
            for ref in outline.sections[position].span_refs:
                if ref not in allowed:
                    allowed.append(ref)
    return allowed


def _resolve_span_ids(
    outline: Outline, index: int, span_ids: Sequence[str]
) -> tuple[list[SpanRef], list[str]]:
    """The distinct refs a refined section names, and the span ids that name no allowed ref."""
    allowed = _allowed_refs(outline, index)
    resolved: list[SpanRef] = []
    invented: list[str] = []
    for token in span_ids:
        ref = match_ref(allowed, token)
        if ref is None:
            invented.append(token)
        elif ref not in resolved:
            resolved.append(ref)
    return resolved, invented


def refinement_problems(outline: Outline, refinement: OutlineRefinement, unit: Unit) -> list[str]:
    """Why a refinement may not be applied to the outline; empty when it may.

    Spans may only move: every span of the skeleton must be listed by as many sections as
    before (normally one), so a reply may neither drop nor duplicate a span.
    """
    expected, got = len(outline.sections), len(refinement.sections)
    if got != expected:
        return [
            f"the reply has {got} sections but the skeleton has {expected}; "
            "sections may not be added or removed"
        ]
    known_outcomes = {outcome.id for outcome in unit.outcomes}
    problems: list[str] = []
    listed: Counter[tuple[str, str]] = Counter()
    for index, (section, refined) in enumerate(
        zip(outline.sections, refinement.sections, strict=True)
    ):
        label = f"section {index + 1} ({section.title!r})"
        unknown = [oid for oid in refined.outcome_ids if oid not in known_outcomes]
        if unknown:
            problems.append(f"{label}: outcome ids not in the unit: {', '.join(unknown)}")
        resolved, invented = _resolve_span_ids(outline, index, refined.span_ids)
        if invented:
            problems.append(
                f"{label}: span ids not listed for this section or its neighbours: "
                f"{', '.join(invented)}"
            )
        if section.review and {_key(ref) for ref in resolved} != {
            _key(ref) for ref in section.span_refs
        }:
            problems.append(
                f"{label}: a review section keeps exactly its spans; do not move spans into "
                "or out of it"
            )
        listed.update(_key(ref) for ref in resolved)
    skeleton = [ref for section in outline.sections for ref in section.span_refs]
    kept = Counter(_key(ref) for ref in skeleton)
    qualified = is_qualified(skeleton)
    tokens = {_key(ref): span_token(ref, qualified=qualified) for ref in skeleton}
    dropped = [tokens[key] for key in kept if listed[key] < kept[key]]
    if dropped:
        problems.append(
            f"span ids dropped from the outline: {', '.join(dropped)}; every span must stay in "
            "a section (list all of a section's spans, not only the ones you move)"
        )
    repeated = [tokens[key] for key in kept if listed[key] > kept[key]]
    if repeated:
        problems.append(f"span ids listed in more than one section: {', '.join(repeated)}")
    return problems


def _apply_refinement(outline: Outline, refinement: OutlineRefinement, unit: Unit) -> Outline:
    sections: list[OutlineSection] = []
    for index, (section, refined) in enumerate(
        zip(outline.sections, refinement.sections, strict=True)
    ):
        span_refs, _ = _resolve_span_ids(outline, index, refined.span_ids)
        sections.append(
            section.model_copy(
                update={
                    "title": refined.title.strip() or section.title,
                    "outcome_ids": list(dict.fromkeys(refined.outcome_ids)),
                    "notes": refined.notes.strip(),
                    "span_refs": span_refs,
                }
            )
        )
    curriculum = outline.curriculum.model_copy(
        update={"outcome_ids": _unit_outcome_ids(unit, sections)}
    )
    return outline.model_copy(update={"sections": sections, "curriculum": curriculum})


def refine_outline(
    outline: Outline,
    sources: Sequence[SourceDocument],
    curriculum: Curriculum,
    provider: LLMProvider,
) -> Outline:
    """One structured call that may retitle, re-target and annotate the skeleton's sections.

    A reply that adds or removes sections, names outcomes outside the unit, cites span ids
    outside a section's own and neighbouring refs, or drops or duplicates a span is re-prompted
    once with the problems named;
    a second bad reply leaves the outline unchanged. Network and model failures propagate.
    """
    docs = by_doc_id(sources)
    unit = curriculum.unit(outline.curriculum.unit_id)
    prompt = load_prompt(
        "outline_refine",
        grade=str(outline.grade),
        period_minutes=str(curriculum.period_minutes),
        skeleton=outline.model_dump_json(indent=2),
        sections=_sections_text(outline, docs),
        outcomes=_outcomes_text(unit),
        vocabulary=", ".join(unit.vocabulary) or "(none)",
        patterns=_patterns_text(unit),
        rules=prompt_rules(outline.grade, curriculum),
        section_count=str(len(outline.sections)),
    )
    messages = [Message(role="user", content=prompt)]
    for attempt in range(2):
        try:
            refinement = complete_json(provider, messages, OutlineRefinement, retries=0)
        except StructuredOutputError as exc:
            reply_text, problems = exc.attempts[-1], [str(exc)]
        else:
            reply_text = refinement.model_dump_json()
            problems = refinement_problems(outline, refinement, unit)
            if not problems:
                return _apply_refinement(outline, refinement, unit)
        if attempt == 0:
            listed = "\n".join(f"- {problem}" for problem in problems)
            messages = [
                *messages,
                Message(role="assistant", content=reply_text),
                Message(role="user", content=_REJECTION.format(problems=listed)),
            ]
    logger.warning("outline refinement rejected twice; keeping the skeleton: %s", problems)
    return outline


def outline(
    sources: Sequence[SourceDocument],
    curriculum: Curriculum,
    *,
    unit_id: str,
    lesson_no: int,
    period: int,
    grade: int,
    provider: LLMProvider | None = None,
) -> Outline:
    """The skeleton, refined by the model when a provider is given."""
    skeleton = build_skeleton(
        sources, curriculum, unit_id=unit_id, lesson_no=lesson_no, period=period, grade=grade
    )
    if provider is None:
        return skeleton
    return refine_outline(skeleton, sources, curriculum, provider)
