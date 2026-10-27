"""Pydantic models for the lesson, its blocks and the source documents they cite.

This is the contract between every pipeline stage; see ``docs/lesson-model.md``. The schema
enforces structure and reference integrity inside one lesson (invariant 1). Rules that depend
on curriculum data or on source documents (invariants 2-7) live in the store, the grounding
stage and the English validators, and surface as checks rather than exceptions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    model_serializer,
    model_validator,
)

SCHEMA_VERSION = 1


def utcnow() -> datetime:
    return datetime.now(UTC)


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]
"""A timezone-aware datetime, normalised to UTC. Naive datetimes are rejected."""


class Strict(BaseModel):
    """Base for every model here: unknown fields are errors, strings are stripped."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- enums -------------------------------------------------------------------------------------


class SpanKind(StrEnum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    LIST_ITEM = "list_item"
    TABLE = "table"
    CAPTION = "caption"
    IMAGE = "image"


class Provenance(StrEnum):
    EXTRACTED = "extracted"
    DERIVED = "derived"
    GENERATED = "generated"


class CheckStatus(StrEnum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    STALE = "stale"


class Phase(StrEnum):
    WARM_UP = "warm_up"
    PRESENTATION = "presentation"
    PRACTICE = "practice"
    PRODUCTION = "production"
    WRAP_UP = "wrap_up"


class Skill(StrEnum):
    LISTENING = "listening"
    SPEAKING = "speaking"
    READING = "reading"
    WRITING = "writing"


class Interaction(StrEnum):
    WHOLE_CLASS = "whole_class"
    PAIRS = "pairs"
    GROUPS = "groups"
    INDIVIDUAL = "individual"


class ActivityType(StrEnum):
    LOOK_LISTEN_REPEAT = "look_listen_repeat"
    LISTEN_POINT_SAY = "listen_point_say"
    LETS_TALK = "lets_talk"
    LISTEN_AND_DO = "listen_and_do"
    LOOK_COMPLETE_READ = "look_complete_read"
    READ_AND_MATCH = "read_and_match"
    LETS_WRITE = "lets_write"
    SONG = "song"
    CHANT = "chant"
    GAME = "game"
    ROLE_PLAY = "role_play"
    PROJECT = "project"
    TPR = "tpr"
    OTHER = "other"


class BlockKind(StrEnum):
    OBJECTIVE = "objective"
    VOCABULARY = "vocabulary"
    SENTENCE_PATTERN = "sentence_pattern"
    DIALOGUE = "dialogue"
    PHONICS = "phonics"
    ACTIVITY = "activity"
    TEACHER_NOTE = "teacher_note"
    SUMMARY = "summary"
    IMAGE_REF = "image_ref"


# --- source side -------------------------------------------------------------------------------


class Span(Strict):
    """The smallest unit a citation can point at."""

    span_id: str = Field(min_length=1)
    kind: SpanKind
    text: str
    order: int
    bbox: tuple[float, float, float, float] | None = None
    image_path: str | None = None


class Page(Strict):
    number: int
    spans: list[Span]


class SourceDocument(Strict):
    doc_id: str = Field(min_length=1)
    filename: str
    sha256: str
    media_type: str
    extraction_version: int = 1
    pages: list[Page]

    def find_span(self, page: int, span_id: str) -> Span | None:
        for candidate in self.pages:
            if candidate.number != page:
                continue
            for span in candidate.spans:
                if span.span_id == span_id:
                    return span
        return None


class SourceRef(Strict):
    """A source document as listed in a lesson."""

    doc_id: str = Field(min_length=1)
    filename: str
    sha256: str


class Citation(Strict):
    doc_id: str = Field(min_length=1)
    page: int
    span_id: str = Field(min_length=1)
    quote: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class CheckResult(Strict):
    name: str = Field(min_length=1)
    status: CheckStatus
    detail: str | None = None
    at: UtcDatetime = Field(default_factory=utcnow)


# --- block content -----------------------------------------------------------------------------


class ObjectiveContent(Strict):
    text: str = Field(min_length=1)
    outcome_ids: list[str] = []
    skills: list[Skill] = []


class VocabItem(Strict):
    word: str = Field(min_length=1)
    ipa: str | None = None
    pos: str | None = None
    meaning_vi: str | None = None
    example: str | None = None
    image: Citation | None = None


class VocabularyContent(Strict):
    items: list[VocabItem]


class SentencePatternContent(Strict):
    pattern: str = Field(min_length=1)
    function: str | None = None
    examples: list[str] = []
    notes: str | None = None


class DialogueLine(Strict):
    speaker: str
    text: str = Field(min_length=1)


class DialogueContent(Strict):
    context: str | None = None
    lines: list[DialogueLine]


class PhonicsContent(Strict):
    sound: str = Field(min_length=1)
    letters: str
    words: list[str] = []
    chant_lines: list[str] = []


class ActivityContent(Strict):
    type: ActivityType
    title: str = Field(min_length=1)
    skills: list[Skill] = []
    interaction: Interaction
    minutes: int = Field(ge=1)
    instructions: str = ""
    teacher_steps: list[str] = []
    student_steps: list[str] = []
    materials: list[str] = []
    competency_tags: list[str] = []


class TeacherNoteContent(Strict):
    text: str = Field(min_length=1)


class SummaryContent(Strict):
    bullets: list[str]


class ImageRefContent(Strict):
    caption: str = ""
    alt: str = Field(min_length=1)


# --- blocks ------------------------------------------------------------------------------------


class BlockBase(Strict):
    id: str = Field(min_length=1)
    sources: list[Citation] = []
    provenance: Provenance = Provenance.GENERATED
    depends_on: list[str] = []
    checks: list[CheckResult] = []
    teacher_note: str | None = None
    slide_hint: dict[str, str] | None = None


class ObjectiveBlock(BlockBase):
    kind: Literal["objective"]
    content: ObjectiveContent


class VocabularyBlock(BlockBase):
    kind: Literal["vocabulary"]
    content: VocabularyContent


class SentencePatternBlock(BlockBase):
    kind: Literal["sentence_pattern"]
    content: SentencePatternContent


class DialogueBlock(BlockBase):
    kind: Literal["dialogue"]
    content: DialogueContent


class PhonicsBlock(BlockBase):
    kind: Literal["phonics"]
    content: PhonicsContent


class ActivityBlock(BlockBase):
    kind: Literal["activity"]
    content: ActivityContent


class TeacherNoteBlock(BlockBase):
    kind: Literal["teacher_note"]
    content: TeacherNoteContent


class SummaryBlock(BlockBase):
    kind: Literal["summary"]
    content: SummaryContent


class ImageRefBlock(BlockBase):
    kind: Literal["image_ref"]
    content: ImageRefContent


Block = Annotated[
    ObjectiveBlock
    | VocabularyBlock
    | SentencePatternBlock
    | DialogueBlock
    | PhonicsBlock
    | ActivityBlock
    | TeacherNoteBlock
    | SummaryBlock
    | ImageRefBlock,
    Field(discriminator="kind"),
]
BlockAdapter: TypeAdapter[Block] = TypeAdapter(Block)

# Kinds that frame the period rather than teach content (see ``Lesson.content_blocks``).
_FRAMING_KINDS = frozenset({BlockKind.OBJECTIVE, BlockKind.TEACHER_NOTE, BlockKind.SUMMARY})


# --- lesson ------------------------------------------------------------------------------------


class Section(Strict):
    """One phase of the period, holding an ordered list of block ids."""

    id: str = Field(min_length=1)
    phase: Phase
    title: str
    minutes: int = Field(ge=0)
    block_ids: list[str]


class CurriculumRef(Strict):
    version: str
    unit_id: str
    lesson_no: int | None = None
    period: int | None = None
    outcome_ids: list[str] = []


class PatchOp(Strict):
    """One RFC 6902 JSON Patch operation. ``from_`` is written to JSON as ``from``."""

    model_config = ConfigDict(populate_by_name=True)

    op: Literal["add", "remove", "replace", "move", "copy", "test"]
    path: str
    value: Any = None
    from_: str | None = Field(default=None, alias="from")

    @model_serializer(mode="wrap")
    def _serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data = handler(self)
        if "from_" in data:
            data["from"] = data.pop("from_")
        if data.get("from") is None:
            data.pop("from", None)
        if self.op in ("remove", "move", "copy") and data.get("value") is None:
            data.pop("value", None)
        return data


class Revision(Strict):
    """An accepted edit as a JSON Patch pair; undo applies ``inverse``, redo applies ``patch``."""

    id: str = Field(min_length=1)
    parent: str | None = None
    at: UtcDatetime = Field(default_factory=utcnow)
    actor: Literal["teacher", "model"]
    instruction: str | None = None
    target_block_ids: list[str]
    patch: list[PatchOp]
    inverse: list[PatchOp]
    flagged_block_ids: list[str] = []


class Lesson(Strict):
    """One 35-minute period: ordered sections of typed blocks, plus its edit history."""

    id: str = Field(min_length=1)
    schema_version: int = SCHEMA_VERSION
    title: str = Field(min_length=1)
    subject: Literal["english"] = "english"
    grade: int = Field(ge=3, le=5)
    language: str = "en"
    curriculum: CurriculumRef
    sources: list[SourceRef]
    sections: list[Section]
    blocks: dict[str, Block]
    revisions: list[Revision] = []
    # Lesson-level check results (timing, skill coverage, outcome coverage); block-level
    # results live on each block.
    checks: list[CheckResult] = []
    created_at: UtcDatetime = Field(default_factory=utcnow)
    updated_at: UtcDatetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        """Invariant 1: every referenced block exists, ids match keys, placement is unique."""
        for key, block in self.blocks.items():
            if block.id != key:
                raise ValueError(f"block stored under key {key!r} has id {block.id!r}")

        section_ids: set[str] = set()
        placed_in: dict[str, str] = {}
        for section in self.sections:
            if section.id in section_ids:
                raise ValueError(f"duplicate section id {section.id!r}")
            section_ids.add(section.id)
            for block_id in section.block_ids:
                if block_id not in self.blocks:
                    raise ValueError(
                        f"section {section.id!r} references unknown block {block_id!r}"
                    )
                if block_id in placed_in:
                    raise ValueError(
                        f"block {block_id!r} is placed more than once "
                        f"(sections {placed_in[block_id]!r} and {section.id!r})"
                    )
                placed_in[block_id] = section.id

        for key, block in self.blocks.items():
            for dependency in block.depends_on:
                if dependency not in self.blocks:
                    raise ValueError(f"block {key!r} depends on unknown block {dependency!r}")
        return self

    def ordered_blocks(self) -> list[Block]:
        """Blocks in section order. Blocks not placed in any section are left out."""
        return [
            self.blocks[block_id] for section in self.sections for block_id in section.block_ids
        ]

    def content_blocks(self) -> list[Block]:
        """Ordered blocks that carry teaching content (no objectives, notes or summaries)."""
        return [block for block in self.ordered_blocks() if block.kind not in _FRAMING_KINDS]

    def section_of(self, block_id: str) -> Section | None:
        for section in self.sections:
            if block_id in section.block_ids:
                return section
        return None

    def dependents_of(self, block_id: str, *, transitive: bool = True) -> list[str]:
        """Ids of blocks that depend on ``block_id``, in lesson order and without duplicates.

        With ``transitive=False`` only direct dependents are returned. The input id itself is
        never included, even if the graph contains a cycle through it.
        """
        direct: dict[str, list[str]] = {}
        for block in self.blocks.values():
            for dependency in block.depends_on:
                direct.setdefault(dependency, []).append(block.id)

        found: set[str] = set()
        frontier = [block_id]
        while frontier:
            current = frontier.pop()
            for dependent in direct.get(current, []):
                if dependent not in found:
                    found.add(dependent)
                    if transitive:
                        frontier.append(dependent)
        found.discard(block_id)

        order = [block.id for block in self.ordered_blocks()]
        placed = set(order)
        order.extend(key for key in self.blocks if key not in placed)
        rank = {bid: index for index, bid in enumerate(order)}
        return sorted(found, key=lambda bid: rank[bid])

    def touch(self) -> None:
        self.updated_at = utcnow()
