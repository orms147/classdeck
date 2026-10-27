"""Stage 2 of planning: draft a lesson from an outline, by template or by model.

Both drafters return :class:`DraftSection` proposals; this module turns a proposal into blocks
(ids, citations, a provenance proposal, dependency edges) and assembles the :class:`Lesson`.
Grounding and the English validators run afterwards, not here. See ``docs/planning.md``,
stage 2.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import ValidationError

from classdeck.curriculum.loader import Curriculum
from classdeck.curriculum.models import Outcome, Pattern, PhonicsItem, Unit
from classdeck.english.dependencies import infer
from classdeck.english.rules import (
    DRAFTABLE_BLOCK_KINDS,
    ActivityTemplate,
    load_activity_templates,
    prompt_rules,
)
from classdeck.english.validators import pattern_regex
from classdeck.lesson.ids import new_id
from classdeck.lesson.schema import (
    ActivityBlock,
    ActivityContent,
    ActivityType,
    Block,
    BlockAdapter,
    BlockKind,
    Citation,
    DialogueContent,
    DialogueLine,
    ImageRefContent,
    Interaction,
    Lesson,
    ObjectiveBlock,
    ObjectiveContent,
    Phase,
    PhonicsBlock,
    PhonicsContent,
    Provenance,
    Section,
    SentencePatternBlock,
    SentencePatternContent,
    SourceDocument,
    SourceRef,
    Span,
    SpanKind,
    SummaryBlock,
    SummaryContent,
    TeacherNoteBlock,
    TeacherNoteContent,
    VocabItem,
    VocabularyBlock,
    VocabularyContent,
)
from classdeck.llm.base import LLMProvider, Message
from classdeck.llm.prompts import load_prompt
from classdeck.llm.structured import StructuredOutputError, complete_json
from classdeck.planning.models import DraftBlock, DraftSection, Outline, OutlineSection, SpanRef
from classdeck.planning.segment import (
    by_doc_id,
    instruction_number,
    is_qualified,
    label_span,
    match_ref,
    resolve_ref,
    span_token,
)

logger = logging.getLogger(__name__)

QUOTE_LENGTH = 80
"""Characters of span text kept in a proposed ``Citation.quote``."""

MAX_DIALOGUE_LINE = 80
"""Longest paragraph the template drafter treats as one spoken line."""

WRAP_UP_NOTE = (
    "Recap the words and the pattern with the class, praise the pupils' effort, and set a "
    "short homework task: practise the pattern with a family member."
)

RECAP_TITLE = "Say it together"
RECAP_MINUTES = 2
RECAP_INSTRUCTIONS = "Say the words and the pattern together."
RECAP_TEACHER_STEPS = (
    "Point to the summary and say each word and the pattern; the class repeats after you.",
    "Ask the class to say them again, first together, then row by row.",
    "Praise clear speaking.",
)

LYRIC_INSTRUCTIONS: dict[ActivityType, str] = {
    ActivityType.SONG: "Sing together:",
    ActivityType.CHANT: "Chant together:",
}
"""Song and chant activities carry the chunk's lyric lines in ``student_steps``, verbatim."""

OPTION_PREFIXES: dict[ActivityType, str] = {
    ActivityType.LISTEN_AND_DO: "Listen. Tick: ",
    ActivityType.LOOK_COMPLETE_READ: "Complete: ",
}
"""Tick-the-picture and gap-fill activities carry the chunk's option or gap lines the same way."""

_TAUGHT_KINDS = frozenset(
    {BlockKind.DIALOGUE, BlockKind.VOCABULARY, BlockKind.SENTENCE_PATTERN, BlockKind.PHONICS}
)
_SUMMARISED_KINDS = frozenset({BlockKind.VOCABULARY, BlockKind.SENTENCE_PATTERN, BlockKind.PHONICS})
_PROMPT_KINDS = DRAFTABLE_BLOCK_KINDS - {BlockKind.OBJECTIVE, BlockKind.SUMMARY}
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_COPIED_LINES_RULE = (
    "A song or chant activity has the instructions 'Sing together:' or 'Chant together:' and "
    "the lyric lines of the spans below as its student steps, one line per step, copied word for "
    "word, citing those spans. A listen_and_do or look_complete_read activity copies the "
    "spans' option or gap-fill lines into its student steps in the same way ('Listen. Tick: a. "
    "...', 'Complete: ...') and cites them."
)
_WARM_UP_RULES = (
    "The activity must give concrete rules in 3 to 5 student steps: who starts, what each pupil "
    "says, how the turn passes to the next pupil and how the game ends; name any material (a "
    "ball, cards). Never write a vague step such as 'Play the game.' The template below is the "
    "shape to fall back on: a circle, a ball, one short sentence each."
)
_PHASE_GUIDANCE: dict[Phase, str] = {
    Phase.WARM_UP: (
        "Exactly one activity block, of type game or song, that reviews language the pupils "
        "already know. Introduce no new words or patterns and cite no span. " + _WARM_UP_RULES
    ),
    Phase.PRESENTATION: (
        "Introduce the new language: the dialogue (or the phonics block in a phonics lesson), "
        "then the vocabulary and sentence pattern it carries, then one activity that drills them."
    ),
    Phase.PRACTICE: (
        "One activity that drills the section's pattern and words in controlled practice. "
        + _COPIED_LINES_RULE
    ),
    Phase.PRODUCTION: (
        "One freer activity in which pupils use the pattern and words to communicate. "
        + _COPIED_LINES_RULE
    ),
    Phase.WRAP_UP: "Recap only; the engine writes the summary.",
}
_REVIEW_GUIDANCE = (
    "This warm-up reviews the words and the pattern of the previous period, which the spans "
    "below carry; the engine has already built the vocabulary and sentence_pattern blocks from "
    "them, so do not write those. Write exactly one activity block, of type game or song, in "
    "which pupils use these words and this pattern again. Introduce no new language and cite no "
    "span. " + _WARM_UP_RULES
)
_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_GREETING = re.compile(
    r"\b(?:hello|hi|good\s+(?:morning|afternoon|evening|night)|goodbye|bye|"
    r"nice\s+to\s+meet\s+you)\b",
    re.IGNORECASE,
)
_APOSTROPHES = "'\N{RIGHT SINGLE QUOTATION MARK}"
_FIRST_PERSON = re.compile(
    rf"\b(?:I|I[{_APOSTROPHES}]m|my|me|we|we[{_APOSTROPHES}]re)\b", re.IGNORECASE
)
_QUOTE_MARKS = frozenset('"\N{LEFT DOUBLE QUOTATION MARK}\N{RIGHT DOUBLE QUOTATION MARK}')
_SPEAKER = re.compile(rf"\bI[{_APOSTROPHES}]m\s+([A-Z][a-z]+)\s*[.!]?\s*$")

Sources = Mapping[str, SourceDocument]


def _word_regex(word: str) -> re.Pattern[str]:
    body = r"\s+".join(re.escape(part) for part in word.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


# --- section context ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SectionContext:
    """What a drafter knows about one section: the outline entry, its spans, the unit."""

    section: OutlineSection
    index: int
    unit: Unit
    grade: int
    lesson_no: int | None
    spans: list[tuple[SpanRef, Span]]
    outcomes: list[Outcome]
    template: ActivityTemplate | None
    rules: str
    allowed_words: list[str] = field(default_factory=list)
    """``curriculum.word_list(unit_id)`` sorted: what pupil-facing text may use."""

    @property
    def qualified(self) -> bool:
        """Whether the section mixes documents, so span ids are written ``doc_id:span_id``."""
        return is_qualified(ref for ref, _ in self.spans)

    def token(self, ref: SpanRef) -> str:
        """How a drafter names ``ref``: the bare span id, or ``doc_id:span_id`` when mixed."""
        return span_token(ref, qualified=self.qualified)

    def lookup(self, token: str) -> tuple[SpanRef, Span] | None:
        """The span a token (``p1.s3`` or ``d_8f2a1c:p1.s3``) names in this section."""
        ref = match_ref((ref for ref, _ in self.spans), token)
        if ref is None:
            return None
        return next((ref, span) for found, span in self.spans if found == ref)

    def span_ids(self) -> set[str]:
        return {self.token(ref) for ref, _ in self.spans}

    def instruction(self) -> tuple[SpanRef, Span] | None:
        for ref, span in self.spans:
            if instruction_number(span) is not None:
                return ref, span
        return None

    def text_spans(self) -> list[tuple[SpanRef, Span]]:
        """Content spans: no images, headings or instruction lines."""
        return [
            (ref, span)
            for ref, span in self.spans
            if span.kind not in (SpanKind.IMAGE, SpanKind.HEADING)
            and instruction_number(span) is None
            and span.text.strip()
        ]

    def image_spans(self) -> list[tuple[SpanRef, Span]]:
        return [(ref, span) for ref, span in self.spans if span.kind is SpanKind.IMAGE]


def build_context(
    outline: Outline, index: int, sources: Sequence[SourceDocument], curriculum: Curriculum
) -> SectionContext:
    """The :class:`SectionContext` of ``outline.sections[index]``; unresolvable refs are dropped."""
    section = outline.sections[index]
    docs = by_doc_id(sources)
    unit = curriculum.unit(outline.curriculum.unit_id)
    outcomes_by_id = {outcome.id: outcome for outcome in unit.outcomes}
    templates = load_activity_templates()
    if section.phase is Phase.WRAP_UP:
        template = None
    else:
        template = templates[section.activity_type or ActivityType.GAME]
    spans: list[tuple[SpanRef, Span]] = []
    for ref in section.span_refs:
        span = resolve_ref(docs, ref)
        if span is not None:
            spans.append((ref, span))
    return SectionContext(
        section=section,
        index=index,
        unit=unit,
        grade=outline.grade,
        lesson_no=outline.curriculum.lesson_no,
        spans=spans,
        outcomes=[outcomes_by_id[oid] for oid in section.outcome_ids if oid in outcomes_by_id],
        template=template,
        rules=prompt_rules(outline.grade, curriculum),
        allowed_words=sorted(curriculum.word_list(unit.id)),
    )


# --- section language --------------------------------------------------------------------------


@dataclass(frozen=True)
class SectionLanguage:
    """The unit language a section's spans contain, with unit-level fallbacks for templates."""

    unit: Unit
    patterns: list[tuple[Pattern, re.Pattern[str]]]
    words: list[str]
    sound: PhonicsItem | None

    @property
    def pattern_text(self) -> str:
        if self.patterns:
            return self.patterns[0][0].pattern
        return self.unit.patterns[0].pattern if self.unit.patterns else ""

    @property
    def words_text(self) -> str:
        return ", ".join(self.words or [word.lower() for word in self.unit.vocabulary])

    @property
    def sound_text(self) -> str:
        return self.sound.sound if self.sound is not None else ""


def section_language(context: SectionContext) -> SectionLanguage:
    texts = [span.text for _, span in context.text_spans()]
    joined = "\n".join(texts)
    patterns: list[tuple[Pattern, re.Pattern[str]]] = []
    for pattern in context.unit.patterns:
        regex = pattern_regex(pattern.pattern)
        if regex is not None and any(regex.search(text) for text in texts):
            patterns.append((pattern, regex))
    words = [
        word.lower()
        for word in dict.fromkeys(context.unit.vocabulary)
        if _word_regex(word).search(joined)
    ]
    phonics = context.unit.phonics
    sound = next(
        (item for item in phonics if any(_word_regex(w).search(joined) for w in item.words)),
        phonics[0] if phonics else None,
    )
    return SectionLanguage(unit=context.unit, patterns=patterns, words=words, sound=sound)


# --- activity templates ------------------------------------------------------------------------


def fill_template(template: ActivityTemplate, **values: str) -> ActivityTemplate:
    """A copy of the template with ``{{pattern}}``, ``{{words}}`` and ``{{sound}}`` filled."""

    def fill(text: str) -> str:
        return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), text)

    return template.model_copy(
        update={
            "title_template": fill(template.title_template),
            "instructions_template": fill(template.instructions_template),
            "teacher_steps": [fill(step) for step in template.teacher_steps],
            "student_steps": [fill(step) for step in template.student_steps],
        }
    )


def activity_content(template: ActivityTemplate, activity_type: ActivityType) -> ActivityContent:
    """An activity built from a filled template, with the template's defaults."""
    interaction = template.default_interaction
    tags = ["communication"]
    if interaction in (Interaction.PAIRS, Interaction.GROUPS):
        tags.append("cooperation")
    return ActivityContent(
        type=activity_type,
        title=template.title_template,
        skills=list(template.default_skills),
        interaction=interaction,
        minutes=template.default_minutes,
        instructions=template.instructions_template,
        teacher_steps=list(template.teacher_steps),
        student_steps=list(template.student_steps),
        competency_tags=tags,
    )


def copied_lines(context: SectionContext, activity_type: ActivityType) -> list[tuple[SpanRef, str]]:
    """The chunk's lines a song, chant, tick or gap-fill activity repeats verbatim.

    One entry per line of every content span (no headings, pictures or instruction lines), with
    the span it comes from; empty for any other activity type.
    """
    if activity_type not in LYRIC_INSTRUCTIONS and activity_type not in OPTION_PREFIXES:
        return []
    return [
        (ref, line.strip())
        for ref, span in context.text_spans()
        for line in span.text.splitlines()
        if line.strip()
    ]


def with_copied_lines(content: ActivityContent, lines: Sequence[str]) -> ActivityContent:
    """``content`` with the source's lines in its pupil steps, copied word for word.

    A song or chant is introduced by ``Sing together:`` / ``Chant together:`` and its lines are
    the steps; a tick or gap-fill activity keeps its closing step after one step per line
    (``Listen. Tick: a. ...``, ``Complete: ...``).
    """
    if content.type in LYRIC_INSTRUCTIONS:
        return content.model_copy(
            update={"instructions": LYRIC_INSTRUCTIONS[content.type], "student_steps": list(lines)}
        )
    prefix = OPTION_PREFIXES[content.type]
    closing = content.student_steps[-1:]
    return content.model_copy(
        update={"student_steps": [f"{prefix}{line}" for line in lines] + closing}
    )


# --- drafters ----------------------------------------------------------------------------------


class SectionDrafter(Protocol):
    def draft_section(self, context: SectionContext) -> DraftSection | None: ...


def _is_dialogue_line(span: Span, limit: int = MAX_DIALOGUE_LINE) -> bool:
    text = span.text.strip()
    if span.kind is not SpanKind.PARAGRAPH or len(text) > limit:
        return False
    return bool(_GREETING.search(text) or _FIRST_PERSON.search(text) or _QUOTE_MARKS & set(text))


class TemplateDrafter:
    """Plain blocks from the chunk alone: the offline baseline and the model's fallback."""

    def draft_section(self, context: SectionContext) -> DraftSection:
        section = context.section
        if section.phase is Phase.WRAP_UP:
            return DraftSection(blocks=[])
        language = section_language(context)
        blocks: list[DraftBlock] = []
        if section.phase is not Phase.WARM_UP:
            if section.activity_type is ActivityType.LOOK_LISTEN_REPEAT:
                if context.lesson_no == 3:
                    blocks.extend(self._phonics(context))
                else:
                    dialogue = self._dialogue(context)
                    if dialogue is not None:
                        blocks.append(dialogue)
                    vocabulary = self._vocabulary(context, language)
                    if vocabulary is not None:
                        blocks.append(vocabulary)
                    blocks.extend(self._patterns(context, language))
            blocks.extend(self._images(context))
        taught = [i for i, block in enumerate(blocks) if block.kind in _TAUGHT_KINDS]
        blocks.append(self._activity(context, language, depends_on=taught))
        return DraftSection(blocks=blocks)

    def review_blocks(self, context: SectionContext) -> list[DraftBlock]:
        """The blocks a review warm-up opens with: the unit words and patterns in its spans.

        Built exactly as the presentation drafter builds them, citing the spans they come from;
        empty when the spans carry no unit word and match no unit pattern.
        """
        language = section_language(context)
        blocks: list[DraftBlock] = []
        vocabulary = self._vocabulary(context, language)
        if vocabulary is not None:
            blocks.append(vocabulary)
        blocks.extend(self._patterns(context, language))
        if context.lesson_no == 3:
            blocks.extend(block for block in self._phonics(context) if block.span_ids)
        return blocks

    def _dialogue(self, context: SectionContext) -> DraftBlock | None:
        run: list[tuple[SpanRef, Span]] = []
        lines: list[tuple[SpanRef, Span]] = []
        for ref, span in [*context.text_spans(), (None, None)]:
            if span is not None and _is_dialogue_line(span):
                run.append((ref, span))
                continue
            if len(run) >= 2:
                lines = run
                break
            run = []
        if lines:
            texts = [span.text.strip() for _, span in lines]
            refs = [ref for ref, _ in lines]
        else:
            single = self._single_span_dialogue(context)
            if single is None:
                return None
            refs, texts = [single[0]], single[1]
        names = [_SPEAKER.search(text) for text in texts]
        if all(names):
            speakers = [match.group(1) for match in names if match is not None]
        else:
            speakers = ["A" if i % 2 == 0 else "B" for i in range(len(texts))]
        content = DialogueContent(
            lines=[
                DialogueLine(speaker=speaker, text=text)
                for speaker, text in zip(speakers, texts, strict=True)
            ]
        )
        return DraftBlock(
            kind=BlockKind.DIALOGUE,
            content=content.model_dump(mode="json"),
            span_ids=[context.token(ref) for ref in refs],
        )

    @staticmethod
    def _single_span_dialogue(context: SectionContext) -> tuple[SpanRef, list[str]] | None:
        """A paragraph holding the whole exchange: its sentences, one per dialogue line."""
        limit = MAX_DIALOGUE_LINE * 3
        for ref, span in context.text_spans():
            sentences = [part for part in _SENTENCE_END.split(span.text.strip()) if part.strip()]
            if len(sentences) >= 2 and _is_dialogue_line(span, limit=limit):
                return ref, sentences
        return None

    def _vocabulary(self, context: SectionContext, language: SectionLanguage) -> DraftBlock | None:
        if not language.words:
            return None
        items: list[VocabItem] = []
        cited: dict[str, None] = {}
        for word in language.words:
            regex = _word_regex(word)
            example: str | None = None
            for ref, span in context.text_spans():
                if regex.search(span.text):
                    example = example or span.text.strip()
                    cited[context.token(ref)] = None
            items.append(VocabItem(word=word, ipa=None, meaning_vi="", example=example))
        content = VocabularyContent(items=items)
        return DraftBlock(
            kind=BlockKind.VOCABULARY, content=content.model_dump(mode="json"), span_ids=list(cited)
        )

    def _patterns(self, context: SectionContext, language: SectionLanguage) -> list[DraftBlock]:
        blocks: list[DraftBlock] = []
        for pattern, regex in language.patterns:
            matching = [
                (ref, span) for ref, span in context.text_spans() if regex.search(span.text)
            ]
            content = SentencePatternContent(
                pattern=pattern.pattern,
                function=pattern.function,
                examples=[span.text.strip() for _, span in matching if "__" not in span.text],
            )
            blocks.append(
                DraftBlock(
                    kind=BlockKind.SENTENCE_PATTERN,
                    content=content.model_dump(mode="json"),
                    span_ids=[context.token(ref) for ref, _ in matching],
                )
            )
        return blocks

    def _phonics(self, context: SectionContext) -> list[DraftBlock]:
        blocks: list[DraftBlock] = []
        for item in context.unit.phonics:
            regexes = [_word_regex(word) for word in item.words]
            matching = [
                (ref, span)
                for ref, span in context.text_spans()
                if any(regex.search(span.text) for regex in regexes)
            ]
            content = PhonicsContent(
                sound=item.sound,
                letters=item.letters,
                words=list(item.words),
                chant_lines=[span.text.strip() for _, span in matching],
            )
            blocks.append(
                DraftBlock(
                    kind=BlockKind.PHONICS,
                    content=content.model_dump(mode="json"),
                    span_ids=[context.token(ref) for ref, _ in matching],
                )
            )
        return blocks

    def _images(self, context: SectionContext) -> list[DraftBlock]:
        title = context.template.title_template if context.template else context.section.title
        content = ImageRefContent(caption="", alt=f"Textbook picture for {title}")
        return [
            DraftBlock(
                kind=BlockKind.IMAGE_REF,
                content=content.model_dump(mode="json"),
                span_ids=[context.token(ref)],
            )
            for ref, _ in context.image_spans()
        ]

    def _activity(
        self, context: SectionContext, language: SectionLanguage, *, depends_on: list[int]
    ) -> DraftBlock:
        template = context.template or load_activity_templates()[ActivityType.GAME]
        activity_type = context.section.activity_type or ActivityType.GAME
        filled = fill_template(
            template,
            pattern=language.pattern_text,
            words=language.words_text,
            sound=language.sound_text,
        )
        instruction = context.instruction()
        content = activity_content(filled, activity_type)
        lines: list[tuple[SpanRef, str]] = []
        if context.section.phase is not Phase.WARM_UP:
            lines = copied_lines(context, activity_type)
        if lines:
            content = with_copied_lines(content, [text for _, text in lines])
        if context.section.phase is Phase.WARM_UP:
            span_ids: list[str] = []  # a warm-up game is ours, even when it reviews cited spans
        elif lines:
            cited = [instruction[0]] if instruction is not None else []
            cited.extend(ref for ref, _ in lines)
            span_ids = list(dict.fromkeys(context.token(ref) for ref in cited))
        elif instruction is not None:
            span_ids = [context.token(instruction[0])]
        else:
            span_ids = [context.token(ref) for ref, _ in context.text_spans()]
        return DraftBlock(
            kind=BlockKind.ACTIVITY,
            content=content.model_dump(mode="json"),
            span_ids=span_ids,
            depends_on=depends_on,
        )


def review_proposal(reply: DraftSection, fallback: DraftSection) -> tuple[DraftSection, bool]:
    """What a review warm-up takes from a drafter's reply: its one activity, citing nothing.

    The engine builds the vocabulary and pattern blocks of a review itself
    (:meth:`TemplateDrafter.review_blocks`), so any other block in ``reply`` is dropped, and the
    game is ours: its proposed citations go, as for the template's. A reply with no activity is
    replaced by ``fallback``'s (the template drafter's). Returns the proposal and whether it
    still comes from ``reply``.
    """
    activity = next((block for block in reply.blocks if block.kind is BlockKind.ACTIVITY), None)
    if activity is None:
        return fallback, False
    kept = activity.model_copy(update={"span_ids": [], "depends_on": []})
    return DraftSection(blocks=[kept]), True


def with_review(review: Sequence[DraftBlock], reply: DraftSection) -> DraftSection:
    """``reply`` opened by the review blocks: indices shift, activities depend on the review."""
    if not review:
        return reply
    offset = len(review)
    taught = [i for i, block in enumerate(review) if block.kind in _TAUGHT_KINDS]
    blocks = list(review)
    for block in reply.blocks:
        depends_on = [index + offset for index in block.depends_on]
        if block.kind is BlockKind.ACTIVITY:
            depends_on = [*taught, *(i for i in depends_on if i not in taught)]
        blocks.append(block.model_copy(update={"depends_on": depends_on}))
    return DraftSection(blocks=blocks)


def _first_error(exc: ValidationError) -> str:
    error = exc.errors(include_url=False)[0]
    location = ".".join(str(part) for part in error["loc"]) or "(root)"
    return f"{location}: {error['msg']}"


def _has_cycle(reply: DraftSection) -> bool:
    state = [0] * len(reply.blocks)  # 0 unvisited, 1 on the stack, 2 done

    def visit(index: int) -> bool:
        if state[index] == 1:
            return True
        if state[index] == 2:
            return False
        state[index] = 1
        if any(visit(target) for target in reply.blocks[index].depends_on):
            return True
        state[index] = 2
        return False

    return any(visit(index) for index in range(len(reply.blocks)))


def _image_token(value: Any) -> str | None:
    """The span id a model wrote for a vocabulary item's picture: a string or ``{"span_id"}``."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("span_id"), str):
        return value["span_id"]
    return None


def _resolve_images(draft: DraftBlock, context: SectionContext) -> tuple[dict[str, Any], list[str]]:
    """The block content with each vocabulary item's ``image`` resolved to a full citation.

    doc_id, page and quote come from the outline's span refs, never from the model. An image
    that names no span of the section is dropped and its token returned.
    """
    items = draft.content.get("items")
    if draft.kind is not BlockKind.VOCABULARY or not isinstance(items, list):
        return draft.content, []
    unresolved: list[str] = []
    resolved: list[Any] = []
    for item in items:
        if isinstance(item, dict) and item.get("image") is not None:
            token = _image_token(item["image"])
            found = context.lookup(token) if token is not None else None
            if found is None:
                unresolved.append(token if token is not None else repr(item["image"]))
                item = {**item, "image": None}
            else:
                item = {**item, "image": _citation(*found).model_dump(mode="json")}
        resolved.append(item)
    return {**draft.content, "items": resolved}, unresolved


def reply_problems(reply: DraftSection, context: SectionContext) -> list[str]:
    """Why a model reply may not be used for the section; empty when it may."""
    problems: list[str] = []
    count = len(reply.blocks)
    for index, block in enumerate(reply.blocks):
        label = f"blocks[{index}]"
        if block.kind not in _PROMPT_KINDS:
            allowed = ", ".join(sorted(kind.value for kind in _PROMPT_KINDS))
            problems.append(f"{label}: kind {block.kind.value!r} may not be drafted; use {allowed}")
        unknown = [sid for sid in block.span_ids if context.lookup(sid) is None]
        if unknown:
            problems.append(f"{label}: span ids not in this section: {', '.join(unknown)}")
        bad = [i for i in block.depends_on if not 0 <= i < count or i == index]
        if bad:
            problems.append(f"{label}: depends_on indices out of range or self: {bad}")
        content, missing_images = _resolve_images(block, context)
        if missing_images:
            problems.append(
                f"{label}: vocabulary image span ids not in this section: "
                f"{', '.join(missing_images)}"
            )
        try:
            BlockAdapter.validate_python(
                {"id": "b_check", "kind": block.kind.value, "content": content}
            )
        except ValidationError as exc:
            problems.append(
                f"{label}: content is not valid {block.kind.value}: {_first_error(exc)}"
            )
    if not problems and _has_cycle(reply):
        problems.append("depends_on forms a cycle")
    return problems


_REJECTION = (
    "Your reply was rejected:\n{problems}\n"
    "Respond again with a single JSON object that fixes these problems."
)


class ModelDrafter:
    """One structured model call per section; ``None`` when the model fails validation twice."""

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def draft_section(self, context: SectionContext) -> DraftSection | None:
        messages = [Message(role="user", content=self.prompt(context))]
        problems: list[str] = []
        for attempt in range(2):
            try:
                reply = complete_json(self.provider, messages, DraftSection, retries=0)
            except StructuredOutputError as exc:
                reply_text, problems = exc.attempts[-1], [str(exc)]
            else:
                reply_text = reply.model_dump_json()
                problems = reply_problems(reply, context)
                if not problems:
                    return reply
            if attempt == 0:
                listed = "\n".join(f"- {problem}" for problem in problems)
                messages = [
                    *messages,
                    Message(role="assistant", content=reply_text),
                    Message(role="user", content=_REJECTION.format(problems=listed)),
                ]
        logger.warning(
            "section %r: model draft rejected twice, using the template drafter: %s",
            context.section.title,
            problems,
        )
        return None

    def prompt(self, context: SectionContext) -> str:
        unit = context.unit
        language = section_language(context)
        template = context.template or load_activity_templates()[ActivityType.GAME]
        filled = fill_template(
            template,
            pattern=language.pattern_text,
            words=language.words_text,
            sound=language.sound_text,
        )
        outcomes = [f"- {o.id}: {o.text} [{', '.join(o.skills)}]" for o in context.outcomes]
        patterns = [
            f"- {p.pattern}" + (f" ({p.function})" if p.function else "") for p in unit.patterns
        ]
        phonics = [f"- {p.sound} ({p.letters}): {', '.join(p.words)}" for p in unit.phonics]
        if context.section.review:
            guidance = _REVIEW_GUIDANCE
        else:
            guidance = _PHASE_GUIDANCE[context.section.phase]
        return load_prompt(
            "draft_section",
            grade=str(context.grade),
            section=context.section.model_dump_json(indent=2),
            spans="\n".join(
                label_span(ref, span, qualified=context.qualified) for ref, span in context.spans
            )
            or "(no spans)",
            phase_guidance=guidance,
            template=filled.model_dump_json(indent=2),
            vocabulary=", ".join(unit.vocabulary) or "(none)",
            patterns="\n".join(patterns) or "(none)",
            phonics="\n".join(phonics) or "(none)",
            outcomes="\n".join(outcomes) or "(none)",
            rules=context.rules,
            allowed_words=", ".join(context.allowed_words) or "(no word list)",
            kinds=", ".join(sorted(kind.value for kind in _PROMPT_KINDS)),
        )


# --- assembling the lesson ---------------------------------------------------------------------


class _Ids:
    """Fresh ids from :func:`new_id`, checked for uniqueness within one lesson."""

    def __init__(self) -> None:
        self.used: set[str] = set()

    def next(self, prefix: str) -> str:
        while (candidate := new_id(prefix)) in self.used:
            pass
        self.used.add(candidate)
        return candidate


def _citation(ref: SpanRef, span: Span) -> Citation:
    quote = " ".join(span.text.split())[:QUOTE_LENGTH]
    return Citation(doc_id=ref.doc_id, page=ref.page, span_id=ref.span_id, quote=quote or None)


def materialise(reply: DraftSection, context: SectionContext, ids: _Ids) -> list[Block]:
    """Blocks from a proposal: ids, citations, provenance proposal, dependency ids."""
    block_ids = [ids.next("b") for _ in reply.blocks]
    blocks: list[Block] = []
    for index, draft in enumerate(reply.blocks):
        citations: list[Citation] = []
        for span_id in draft.span_ids:
            found = context.lookup(span_id)
            if found is None:
                continue
            citation = _citation(*found)
            if (citation.doc_id, citation.span_id) not in {
                (c.doc_id, c.span_id) for c in citations
            }:
                citations.append(citation)
        content, _ = _resolve_images(draft, context)
        provenance = Provenance.EXTRACTED if citations else Provenance.GENERATED
        blocks.append(
            BlockAdapter.validate_python(
                {
                    "id": block_ids[index],
                    "kind": draft.kind.value,
                    "content": content,
                    "sources": citations,
                    "provenance": provenance,
                    "depends_on": [
                        block_ids[i]
                        for i in dict.fromkeys(draft.depends_on)
                        if 0 <= i < len(block_ids)
                    ],
                    "teacher_note": draft.teacher_note,
                }
            )
        )
    return blocks


def _instruction_activities(blocks: Sequence[Block], context: SectionContext) -> list[str]:
    """Ids of the activities that cite the section's instruction span (a textbook activity)."""
    instruction = context.instruction()
    if instruction is None:
        return []
    ref = instruction[0]
    return [
        block.id
        for block in blocks
        if isinstance(block, ActivityBlock)
        and any((c.doc_id, c.span_id) == (ref.doc_id, ref.span_id) for c in block.sources)
    ]


def _mark_derived(lesson: Lesson, block_ids: Sequence[str]) -> None:
    """Relabel textbook-built activities ``derived`` once they depend on taught language.

    An activity built from a textbook instruction line plus the dialogue, pattern, vocabulary
    or phonics it drills is derived from those blocks (``docs/lesson-model.md``, "Provenance").
    One that depends on none keeps its proposal.
    """
    for block_id in block_ids:
        block = lesson.blocks[block_id]
        if any(
            lesson.blocks[target].kind in _TAUGHT_KINDS
            for target in block.depends_on
            if target in lesson.blocks
        ):
            block.provenance = Provenance.DERIVED


def _objectives(outline: Outline, curriculum: Curriculum, ids: _Ids) -> list[Block]:
    blocks: list[Block] = []
    for outcome_id in outline.curriculum.outcome_ids:
        outcome = curriculum.outcome(outcome_id)
        blocks.append(
            ObjectiveBlock(
                id=ids.next("b"),
                kind="objective",
                content=ObjectiveContent(
                    text=outcome.text, outcome_ids=[outcome_id], skills=list(outcome.skills)
                ),
                provenance=Provenance.DERIVED,
            )
        )
    return blocks


def _wrap_up_blocks(blocks: Mapping[str, Block], unit: Unit, ids: _Ids) -> list[Block]:
    """A derived summary of the lesson's words, patterns and sounds, plus one teacher note."""
    words: list[str] = []
    patterns: list[str] = []
    sounds: list[str] = []
    for block in blocks.values():
        if isinstance(block, VocabularyBlock):
            words.extend(item.word for item in block.content.items)
        elif isinstance(block, SentencePatternBlock):
            patterns.append(block.content.pattern)
        elif isinstance(block, PhonicsBlock):
            sounds.append(f"{block.content.sound}: {', '.join(block.content.words)}")
    if not (words or patterns or sounds):
        words = [word.lower() for word in unit.vocabulary]
        patterns = [pattern.pattern for pattern in unit.patterns]
    bullets: list[str] = []
    if words:
        bullets.append(", ".join(dict.fromkeys(words)))
    bullets.extend(dict.fromkeys(patterns))
    bullets.extend(dict.fromkeys(sounds))
    summary = SummaryBlock(
        id=ids.next("b"),
        kind="summary",
        content=SummaryContent(bullets=bullets),
        provenance=Provenance.DERIVED,
        depends_on=[block.id for block in blocks.values() if block.kind in _SUMMARISED_KINDS],
    )
    recap = _recap_activity(
        ids,
        words=list(dict.fromkeys(words)),
        patterns=list(dict.fromkeys(patterns)),
        sounds=list(dict.fromkeys(sounds)),
        depends_on=[summary.id, *summary.depends_on],
    )
    note = TeacherNoteBlock(
        id=ids.next("b"),
        kind="teacher_note",
        content=TeacherNoteContent(text=WRAP_UP_NOTE),
        provenance=Provenance.GENERATED,
    )
    return [summary, recap, note]


def _recap_activity(
    ids: _Ids,
    *,
    words: Sequence[str],
    patterns: Sequence[str],
    sounds: Sequence[str],
    depends_on: list[str],
) -> ActivityBlock:
    """The wrap-up's two-minute whole-class recap: say the lesson's words and pattern aloud.

    Derived from the summary (and so from the vocabulary, pattern and phonics blocks it
    summarises), built from the template alone: the model never writes it.
    """
    steps = ["Listen to the teacher."]
    if words:
        steps.append(f"Say the words: {', '.join(words)}")
    steps.extend(f"Say: {pattern}" for pattern in patterns)
    steps.extend(f"Say the sound: {sound.replace(': ', ', ', 1)}" for sound in sounds)
    return ActivityBlock(
        id=ids.next("b"),
        kind="activity",
        content=ActivityContent(
            type=ActivityType.OTHER,
            title=RECAP_TITLE,
            skills=["listening", "speaking"],
            interaction=Interaction.WHOLE_CLASS,
            minutes=RECAP_MINUTES,
            instructions=RECAP_INSTRUCTIONS,
            teacher_steps=list(RECAP_TEACHER_STEPS),
            student_steps=steps,
            competency_tags=["communication"],
        ),
        provenance=Provenance.DERIVED,
        depends_on=depends_on,
    )


def _section_id(phase: Phase, used: set[str]) -> str:
    base = f"s_{phase.value}"
    candidate, counter = base, 2
    while candidate in used:
        candidate = f"{base}_{counter}"
        counter += 1
    used.add(candidate)
    return candidate


@dataclass
class DraftReport:
    """What :func:`draft_lesson_report` knows about the lesson beyond the lesson itself."""

    model_block_ids: set[str] = field(default_factory=set)
    """Ids of the blocks a model wrote: accepted replies only, never engine-built blocks
    (objectives, review blocks, wrap-up) or template fallbacks. Empty without a provider."""


def draft_lesson_report(
    outline: Outline,
    sources: Sequence[SourceDocument],
    curriculum: Curriculum,
    provider: LLMProvider | None = None,
) -> tuple[Lesson, DraftReport]:
    """A lesson draft from the outline, with a :class:`DraftReport` of who wrote what.

    With a provider each section is drafted by the model and falls back to the template drafter
    when its reply fails validation twice; without one every section is template-drafted. The
    wrap-up is always built by the engine, and so are the vocabulary and pattern blocks that
    open a review warm-up (``OutlineSection.review``). Provenance is a proposal for grounding to
    confirm: ``extracted`` for cited blocks, ``generated`` for the rest, and ``derived`` for an
    activity built from a textbook instruction line that depends on the language it drills.
    """
    if not outline.sections:
        raise ValueError("the outline has no sections")
    unit = curriculum.unit(outline.curriculum.unit_id)
    drafter: SectionDrafter = ModelDrafter(provider) if provider is not None else TemplateDrafter()
    fallback = TemplateDrafter()
    report = DraftReport()
    ids = _Ids()
    blocks: dict[str, Block] = {}
    sections: list[Section] = []
    section_ids: set[str] = set()
    textbook_activities: list[str] = []
    for index, outline_section in enumerate(outline.sections):
        context = build_context(outline, index, sources, curriculum)
        section_blocks: list[Block] = []
        if index == 0:
            section_blocks.extend(_objectives(outline, curriculum, ids))
        if outline_section.phase is Phase.WRAP_UP:
            section_blocks.extend(_wrap_up_blocks(blocks, unit, ids))
        else:
            reply = drafter.draft_section(context)
            from_model = provider is not None and reply is not None
            if reply is None:
                reply = fallback.draft_section(context)
            review: list[DraftBlock] = []
            if outline_section.review:
                review = fallback.review_blocks(context)
                reply, from_reply = review_proposal(reply, fallback.draft_section(context))
                from_model = from_model and from_reply
            if outline_section.review and not review:
                logger.info(
                    "section %r: the review spans carry no unit word or pattern; no review blocks",
                    outline_section.title,
                )
            drafted = materialise(with_review(review, reply), context, ids)
            if from_model:
                report.model_block_ids.update(block.id for block in drafted[len(review) :])
            textbook_activities.extend(_instruction_activities(drafted, context))
            section_blocks.extend(drafted)
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
    lesson = infer(lesson)
    _mark_derived(lesson, textbook_activities)
    return lesson, report


def draft_lesson(
    outline: Outline,
    sources: Sequence[SourceDocument],
    curriculum: Curriculum,
    provider: LLMProvider | None = None,
) -> Lesson:
    """:func:`draft_lesson_report` without the report."""
    return draft_lesson_report(outline, sources, curriculum, provider)[0]
