"""Pydantic models for the lesson, its blocks and the source documents they cite.

This is the contract between every pipeline stage; see ``docs/lesson-model.md``. The schema
enforces structure and reference integrity inside one lesson (invariant 1). Rules that depend
on curriculum data or on source documents (invariants 2-7) live in the store, the grounding
stage and the English validators, and surface as checks rather than exceptions.
"""

from __future__ import annotations

import re
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
    field_validator,
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
    INSTRUCTION = "instruction"  # a numbered exercise line of a scanned page (vision reading)
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
    # Interactive exercise kinds (see docs/secondary-units.md, "Interactive exercise blocks").
    READING = "reading"
    MATCH = "match"
    GAP_FILL = "gap_fill"
    CHOICE = "choice"
    TRUE_FALSE = "true_false"
    RULE_BOX = "rule_box"
    QUESTION_SET = "question_set"
    SOUND_SORT = "sound_sort"
    GALLERY = "gallery"
    GRID = "grid"
    SELF_CHECK = "self_check"


# --- source side -------------------------------------------------------------------------------


class Span(Strict):
    """The smallest unit a citation can point at."""

    span_id: str = Field(min_length=1)
    kind: SpanKind
    text: str
    order: int
    bbox: tuple[float, float, float, float] | None = None
    image_path: str | None = None
    # Phrases of the span that the page prints highlighted (bold, coloured, boxed), as written.
    highlights: list[str] = []
    # The audio track the page marks on an instruction line ("Track 3"), when it shows one.
    track: int | None = None


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
    # A generated flashcard picture (path relative to the image root); see `classdeck illustrate`.
    generated_picture: str | None = None


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
    # Phrases the page highlights in the conversation ("Pay attention to the highlighted parts");
    # the deck shows them as chips. Each occurs in at least one line.
    highlights: list[str] = []

    @model_validator(mode="after")
    def _highlights_in_lines(self) -> Self:
        text = " ".join(line.text for line in self.lines).lower()
        for phrase in self.highlights:
            if not phrase.strip() or phrase.lower() not in text:
                raise ValueError(f"highlighted phrase {phrase!r} does not occur in the lines")
        return self


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


# --- exercise content --------------------------------------------------------------------------

_BLANK = re.compile(r"_{2,}")


def _wrap_text(value: Any, field: str) -> Any:
    """Let a bare string stand for ``{field: string}``, as a model often writes lists of text."""
    return {field: value} if isinstance(value, str) else value


class ExerciseBase(Strict):
    """What every textbook exercise carries: its instruction line and number."""

    instruction: str = ""
    exercise_no: str | None = None


class ReadingParagraph(Strict):
    text: str = Field(min_length=1)
    # Words of ``text`` shown highlighted; with a glossary entry they reveal a meaning on tap.
    highlight: list[str] = []
    # The textbook picture printed with this paragraph (an image span).
    image: Citation | None = None

    @model_validator(mode="after")
    def _highlight_in_text(self) -> Self:
        lowered = self.text.lower()
        for word in self.highlight:
            if not word.strip() or word.lower() not in lowered:
                raise ValueError(f"highlighted word {word!r} does not occur in the paragraph")
        return self


class ReadingContent(ExerciseBase):
    title: str = ""
    paragraphs: list[ReadingParagraph] = Field(min_length=1)
    # Highlighted word -> meaning (English or Vietnamese), shown on tap.
    glossary: dict[str, str] = {}
    # The textbook picture next to the text (an image span).
    image: Citation | None = None

    @field_validator("paragraphs", mode="before")
    @classmethod
    def _bare_paragraphs(cls, value: Any) -> Any:
        return [_wrap_text(item, "text") for item in value] if isinstance(value, list) else value


class MatchContent(ExerciseBase):
    # Item texts. A picture item keeps its description here (its alt text and answer-key label).
    left: list[str] = Field(min_length=2)
    right: list[str] = Field(min_length=2)
    # Optional pictures (image spans), parallel to ``left`` / ``right``; ``None`` for a text item.
    # A word-to-picture exercise has pictures on the left and the word box on the right.
    left_images: list[Citation | None] = []
    right_images: list[Citation | None] = []
    # left index -> right index; every left item is matched, no right item twice.
    answer: dict[int, int]

    @model_validator(mode="after")
    def _answer_valid(self) -> Self:
        if self.left_images and len(self.left_images) != len(self.left):
            raise ValueError("left_images must have one entry per left item")
        if self.right_images and len(self.right_images) != len(self.right):
            raise ValueError("right_images must have one entry per right item")
        if sorted(self.answer) != list(range(len(self.left))):
            raise ValueError("answer must give a right item for every left item")
        for left_index, right_index in self.answer.items():
            if not 0 <= right_index < len(self.right):
                raise ValueError(f"answer for left item {left_index}: no right item {right_index}")
        if len(set(self.answer.values())) != len(self.answer):
            raise ValueError("answer matches two left items to the same right item")
        return self


class GapItem(Strict):
    text: str = Field(min_length=1)
    # One answer per blank (``___``); a single string for a single blank.
    answer: str | list[str]

    @property
    def answers(self) -> list[str]:
        return [self.answer] if isinstance(self.answer, str) else list(self.answer)

    @model_validator(mode="after")
    def _one_answer_per_blank(self) -> Self:
        blanks = len(_BLANK.findall(self.text))
        if blanks == 0:
            raise ValueError(f"gap item {self.text!r} has no blank (___)")
        if blanks != len(self.answers) or not all(a.strip() for a in self.answers):
            raise ValueError(f"gap item {self.text!r} has {blanks} blank(s) but different answers")
        return self


class GapFillContent(ExerciseBase):
    items: list[GapItem] = Field(min_length=1)
    word_box: list[str] = []

    @model_validator(mode="after")
    def _answers_in_box(self) -> Self:
        if self.word_box:
            box = {word.lower() for word in self.word_box}
            for item in self.items:
                for answer in item.answers:
                    if answer.lower() not in box:
                        raise ValueError(f"answer {answer!r} is not in the word box")
        return self


class ChoiceItem(Strict):
    stem: str = Field(min_length=1)
    options: list[str] = Field(min_length=2)
    answer: str

    @model_validator(mode="after")
    def _answer_among_options(self) -> Self:
        if len(set(self.options)) != len(self.options):
            raise ValueError(f"options of {self.stem!r} contain a duplicate")
        if self.answer not in self.options:
            raise ValueError(f"answer {self.answer!r} is not among the options of {self.stem!r}")
        return self


class ChoiceContent(ExerciseBase):
    items: list[ChoiceItem] = Field(min_length=1)


class TrueFalseItem(Strict):
    statement: str = Field(min_length=1)
    answer: bool


class TrueFalseContent(ExerciseBase):
    items: list[TrueFalseItem] = Field(min_length=1)


class RuleBoxContent(Strict):
    """The textbook's *Remember!* box: a rule, its points and examples."""

    title: str = Field(min_length=1)
    points: list[str] = []
    examples: list[str] = []


class Question(Strict):
    question: str = Field(min_length=1)
    # A sample answer, for the teacher only.
    answer: str = ""


class QuestionSetContent(ExerciseBase):
    prompt: str = ""
    questions: list[Question] = Field(min_length=1)

    @field_validator("questions", mode="before")
    @classmethod
    def _bare_questions(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [_wrap_text(item, "question") for item in value]
        return value


class SoundWord(Strict):
    """A word and the sound (IPA, without slashes) that the exercise sorts it by."""

    word: str = Field(min_length=1)
    sound: str = Field(min_length=1)


class SoundSentence(Strict):
    """A sentence whose target words (bold in the textbook) carry the sounds."""

    text: str = Field(min_length=1)
    targets: list[SoundWord] = []

    @model_validator(mode="after")
    def _targets_in_text(self) -> Self:
        for target in self.targets:
            pattern = rf"(?<![\w'-]){re.escape(target.word)}(?![\w'-])"
            if not re.search(pattern, self.text, re.IGNORECASE):
                raise ValueError(f"target word {target.word!r} does not occur in {self.text!r}")
        return self


class SoundSortContent(ExerciseBase):
    """A pronunciation exercise: sort words (and the target words of sentences) by their sound."""

    # The sounds as the textbook writes them in its heading, without slashes.
    sounds: list[str] = Field(min_length=2)
    words: list[SoundWord] = []
    sentences: list[SoundSentence] = []

    @model_validator(mode="after")
    def _sounds_known(self) -> Self:
        if not self.words and not self.sentences:
            raise ValueError("a sound sort needs words or sentences")
        known = set(self.sounds)
        if len(known) != len(self.sounds):
            raise ValueError("sounds contain a duplicate")
        every = [*self.words, *(t for sentence in self.sentences for t in sentence.targets)]
        for item in every:
            if item.sound not in known:
                raise ValueError(f"sound {item.sound!r} of {item.word!r} is not one of the sounds")
        return self


MAX_GALLERY_IMAGES = 6


class GalleryImage(Strict):
    """One picture of a gallery: an image span, its letter or number on the page, a caption."""

    image: Citation
    label: str = ""
    caption: str = ""
    # A short description of the picture (its alt text).
    alt: str = ""


class GalleryContent(ExerciseBase):
    """The pictures of a page shown together on one slide, with the questions about them.

    ``title`` is the page's heading for the pictures (the project's title); ``questions`` are the
    textbook's prompts about them (*What can you see in each picture?*).
    """

    title: str = ""
    images: list[GalleryImage] = Field(min_length=1, max_length=MAX_GALLERY_IMAGES)
    questions: list[str] = []


class GridContent(ExerciseBase):
    """A table the pupils fill in: a header, rows (an empty cell is for the pupils) and
    ``blank_rows`` more empty rows (a survey table)."""

    header: list[str] = Field(min_length=1)
    rows: list[list[str]] = []
    blank_rows: int = Field(default=0, ge=0, le=12)
    # The teacher's key: one answer per blank (``___``) of the rows, in reading order.
    answers: list[str] = []

    @model_validator(mode="after")
    def _rows_fit_header(self) -> Self:
        if not self.rows and not self.blank_rows:
            raise ValueError("a grid needs rows or blank_rows")
        for row in self.rows:
            if len(row) != len(self.header):
                raise ValueError("every grid row needs one cell per header cell")
        blanks = sum(len(_BLANK.findall(cell)) for row in self.rows for cell in row)
        if self.answers and len(self.answers) != blanks:
            raise ValueError(f"the grid has {blanks} blank(s) but {len(self.answers)} answers")
        return self


class SelfCheckContent(ExerciseBase):
    """The *Now I can ...* list: pupils mark each line with one, two or three ticks."""

    title: str = "Now I can ..."
    items: list[str] = Field(min_length=1)
    scale: list[str] = ["✓", "✓✓", "✓✓✓"]


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


class ReadingBlock(BlockBase):
    kind: Literal["reading"]
    content: ReadingContent


class MatchBlock(BlockBase):
    kind: Literal["match"]
    content: MatchContent


class GapFillBlock(BlockBase):
    kind: Literal["gap_fill"]
    content: GapFillContent


class ChoiceBlock(BlockBase):
    kind: Literal["choice"]
    content: ChoiceContent


class TrueFalseBlock(BlockBase):
    kind: Literal["true_false"]
    content: TrueFalseContent


class RuleBoxBlock(BlockBase):
    kind: Literal["rule_box"]
    content: RuleBoxContent


class QuestionSetBlock(BlockBase):
    kind: Literal["question_set"]
    content: QuestionSetContent


class SoundSortBlock(BlockBase):
    kind: Literal["sound_sort"]
    content: SoundSortContent


class GalleryBlock(BlockBase):
    kind: Literal["gallery"]
    content: GalleryContent


class GridBlock(BlockBase):
    kind: Literal["grid"]
    content: GridContent


class SelfCheckBlock(BlockBase):
    kind: Literal["self_check"]
    content: SelfCheckContent


Block = Annotated[
    ObjectiveBlock
    | VocabularyBlock
    | SentencePatternBlock
    | DialogueBlock
    | PhonicsBlock
    | ActivityBlock
    | TeacherNoteBlock
    | SummaryBlock
    | ImageRefBlock
    | ReadingBlock
    | MatchBlock
    | GapFillBlock
    | ChoiceBlock
    | TrueFalseBlock
    | RuleBoxBlock
    | QuestionSetBlock
    | SoundSortBlock
    | GalleryBlock
    | GridBlock
    | SelfCheckBlock,
    Field(discriminator="kind"),
]
BlockAdapter: TypeAdapter[Block] = TypeAdapter(Block)

# Textbook exercises and texts that the deck runs in class (``docs/secondary-units.md``).
EXERCISE_KINDS = frozenset(
    {
        BlockKind.READING,
        BlockKind.MATCH,
        BlockKind.GAP_FILL,
        BlockKind.CHOICE,
        BlockKind.TRUE_FALSE,
        BlockKind.RULE_BOX,
        BlockKind.QUESTION_SET,
        BlockKind.SOUND_SORT,
        BlockKind.GALLERY,
        BlockKind.GRID,
        BlockKind.SELF_CHECK,
    }
)

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
    # The textbook unit as teachers know it ("Unit 1", "Local community"); ``unit_id`` stays
    # the internal key. Older lessons lack both: ``lesson.labels`` derives them.
    unit_no: int | None = Field(default=None, ge=1)
    unit_title: str | None = None
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
    """One teaching period (35 or 45 minutes): sections of typed blocks, plus its edit history."""

    id: str = Field(min_length=1)
    schema_version: int = SCHEMA_VERSION
    title: str = Field(min_length=1)
    subject: Literal["english"] = "english"
    grade: int = Field(ge=1, le=9)
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
