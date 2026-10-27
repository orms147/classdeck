"""One repair round for model-written blocks that fail the level checks.

The level checks are deterministic and the drafting model never sees them, so a model-drafted
period usually ends with a few ``english.vocab_level`` warnings on pupil-facing text and the odd
``english.grammar_scope`` hit or ``english.wording_kept`` warning (a contraction written out).
:func:`repair_lesson` sends each such block -- only blocks the model wrote, at most
:data:`MAX_REPAIR_BLOCKS` of them, once -- back with the offending words and the allowed words,
and accepts the reply only when the engine can measure an improvement: same id and kind, valid
content, strictly fewer offending words, hits and written-out contractions (and no more hits),
no check turned ``fail``. Only the pupil-facing text of the reply is taken: teacher steps,
timing, skills and every other field of the block (citations, provenance, dependencies, checks,
teacher note) stay as they were. Cited text is never rewritten: blocks whose provenance is
``extracted``, the ``word`` of a vocabulary item, the ``pattern`` of a sentence pattern and
dialogue lines that carry citations stay as the source has them, and an activity step that the
reply cuts to less than half its words (a concrete step turned vague) is rejected. See
``docs/planning.md``, "Repair round".
"""

from __future__ import annotations

import json
import logging
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from classdeck.curriculum.loader import Curriculum
from classdeck.curriculum.models import GrammarRule
from classdeck.english.rules import prompt_rules
from classdeck.english.validators import (
    GRAMMAR_KINDS,
    GRAMMAR_SCOPE,
    VOCAB_LEVEL,
    VOCAB_LEVEL_KINDS,
    WORDING_KEPT,
    allowed_forms,
    contraction_expansions,
    grammar_hits,
    steps_present,
    tokenize,
    vocab_offenders,
    wording_offenders,
)
from classdeck.lesson.schema import (
    Block,
    BlockAdapter,
    BlockKind,
    CheckStatus,
    Lesson,
    Provenance,
    SourceDocument,
)
from classdeck.llm.base import LLMProvider, Message
from classdeck.llm.prompts import load_prompt
from classdeck.llm.structured import StructuredOutputError, complete_json

logger = logging.getLogger(__name__)

MAX_REPAIR_BLOCKS = 8
"""Most blocks one lesson sends to the repair prompt, in lesson order."""

MIN_LENGTH_RATIO = 0.5
"""A repaired instruction or step keeps at least this share of the original's words."""

MAX_ROUNDS = 1
"""Each offending block is sent once; a reply that does not improve it is dropped."""

REPAIR_PROMPT = "repair_block"
REPAIRED_CHECKS: frozenset[str] = frozenset({VOCAB_LEVEL, GRAMMAR_SCOPE, WORDING_KEPT})
"""The checks whose ``warn`` makes a model-written block a repair candidate."""

Sources = Mapping[str, SourceDocument]


class RepairReply(BaseModel):
    """What the model returns: the same block, read for its content only."""

    model_config = ConfigDict(extra="ignore")

    id: str
    kind: str
    content: dict[str, Any]


@dataclass
class RepairReport:
    """What one repair round did, block by block."""

    candidates: list[str] = field(default_factory=list)
    """Model-written blocks with a level warning, in lesson order."""
    sent: list[str] = field(default_factory=list)
    """The candidates that were prompted (the first :data:`MAX_REPAIR_BLOCKS`)."""
    replaced: list[str] = field(default_factory=list)
    kept: dict[str, str] = field(default_factory=dict)
    """Sent blocks that stayed as they were, with the reason."""

    def summary(self) -> str:
        return (
            f"repair: {len(self.candidates)} offending model block(s), {len(self.sent)} sent, "
            f"{len(self.replaced)} replaced, {len(self.kept)} kept"
        )


def offending_blocks(lesson: Lesson, model_block_ids: Collection[str]) -> list[Block]:
    """Model-written blocks carrying a ``warn`` on a repaired check, in lesson order.

    Blocks whose provenance is ``extracted`` quote the textbook and are never candidates.
    """
    return [
        block
        for block in lesson.ordered_blocks()
        if block.id in model_block_ids
        and block.provenance is not Provenance.EXTRACTED
        and any(
            check.name in REPAIRED_CHECKS and check.status is CheckStatus.WARN
            for check in block.checks
        )
    ]


def offences(
    block: Block,
    allowed: frozenset[str],
    rules: Iterable[GrammarRule],
    sources: Sources | None,
    expansions: Mapping[str, str] | None = None,
) -> list[str]:
    """The offending words, grammar hits and written-out contractions of ``block``, as the
    three validators would list them."""
    found: list[str] = []
    if block.kind in VOCAB_LEVEL_KINDS:
        found.extend(vocab_offenders(block, allowed, sources))
    if block.kind in GRAMMAR_KINDS:
        found.extend(grammar_hits(block, rules))
    if expansions:
        found.extend(wording_offenders(block, expansions))
    return found


def _hit_count(block: Block, rules: Iterable[GrammarRule]) -> int:
    return len(grammar_hits(block, rules)) if block.kind in GRAMMAR_KINDS else 0


def _word_list(lesson: Lesson, curriculum: Curriculum) -> list[str]:
    try:
        return sorted(curriculum.word_list(lesson.curriculum.unit_id))
    except KeyError:
        return sorted(curriculum.word_list())


def _names_in_cited_spans(block: Block, sources: Sources | None) -> list[str]:
    """Capitalised words inside sentences of the spans the block cites: pupils' names."""
    if not sources:
        return []
    names: dict[str, None] = {}
    for citation in block.sources:
        document = sources.get(citation.doc_id)
        span = document.find_span(citation.page, citation.span_id) if document else None
        if span is None:
            continue
        for token in tokenize(span.text):
            if token.proper:
                names[token.surface.capitalize()] = None
    return list(names)


def repair_prompt(
    block: Block, lesson: Lesson, curriculum: Curriculum, sources: Sources | None
) -> str:
    """The one compact prompt for ``block``: its JSON, its problems, the allowed words, rules."""
    problems = [
        check.detail
        for check in block.checks
        if check.name in REPAIRED_CHECKS and check.status is CheckStatus.WARN and check.detail
    ]
    data = block.model_dump(mode="json")
    compact = {"id": block.id, "kind": block.kind, "content": data["content"]}
    return load_prompt(
        REPAIR_PROMPT,
        grade=str(lesson.grade),
        block=json.dumps(compact, ensure_ascii=False, indent=2),
        problems="\n".join(f"- {problem}" for problem in problems) or "- (see the allowed words)",
        allowed_words=", ".join(_word_list(lesson, curriculum)) or "(no word list)",
        names=", ".join(_names_in_cited_spans(block, sources)) or "(none)",
        rules=prompt_rules(lesson.grade, curriculum),
    )


def _same_length(original: Any, reply: Any) -> bool:
    return (
        isinstance(original, list)
        and isinstance(reply, list)
        and len(original) == len(reply)
        and all(isinstance(item, dict) for item in [*original, *reply])
    )


def _word_count(text: object) -> int:
    return len(text.split()) if isinstance(text, str) else 0


def _too_vague(original: dict[str, Any], merged: dict[str, Any]) -> str:
    """Why the merged activity text is not a rewording of the original; empty when it is.

    The repair swaps words, not steps: the number of student steps stays, and no step or
    instruction may keep less than :data:`MIN_LENGTH_RATIO` of its words ("Pass the ball.
    Greet the player ..." must not become "Play the game.").
    """
    steps, new_steps = original.get("student_steps"), merged.get("student_steps")
    if isinstance(steps, list) and isinstance(new_steps, list) and new_steps:
        if len(steps) != len(new_steps):
            return (
                f"the reply changed the number of student steps ({len(steps)} to {len(new_steps)})"
            )
        for number, (old, new) in enumerate(zip(steps, new_steps, strict=True), start=1):
            if _word_count(new) < MIN_LENGTH_RATIO * _word_count(old):
                return f"the reply cut student step {number} to less than half its words"
    old_text, new_text = original.get("instructions"), merged.get("instructions")
    if isinstance(new_text, str) and _word_count(new_text) < MIN_LENGTH_RATIO * _word_count(
        old_text
    ):
        return "the reply cut the instructions to less than half their words"
    return ""


def pupil_content(block: Block, reply: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """The block's content with only its repairable pupil-facing text taken from ``reply``.

    Everything the level checks do not read -- an activity's title, teacher steps, materials,
    skills, timing and tags, a vocabulary item's gloss and picture, a dialogue's speakers --
    stays as it was, so a repair cannot change what the lesson plan counts on. Cited text stays
    too: an ``extracted`` block is never rewritten, a vocabulary ``word`` and a sentence
    ``pattern`` are the unit's, and dialogue lines that carry citations are the source's. Outside
    activities only the text of ``generated`` blocks is taken. Returns the content, or ``None``
    and the reason when the reply changed how many items, lines or steps a block has, or cut an
    activity step to a vague fragment.
    """
    if block.provenance is Provenance.EXTRACTED:
        return None, "the block quotes the source; cited text is not rewritten"
    original: dict[str, Any] = block.model_dump(mode="json")["content"]
    merged = dict(original)
    editable = block.kind is BlockKind.ACTIVITY or block.provenance is Provenance.GENERATED

    def take(*fields: str) -> None:
        for name in fields:
            if name in reply:
                merged[name] = reply[name]

    match block.kind:
        case BlockKind.ACTIVITY:
            take("instructions", "student_steps")
            vague = _too_vague(original, merged)
            if vague:
                return None, vague
        case _ if not editable:
            return (
                None,
                f"the text of a {block.provenance.value} {block.kind} block is not rewritten",
            )
        case BlockKind.SENTENCE_PATTERN:
            take("examples")
        case BlockKind.SUMMARY:
            take("bullets")
        case BlockKind.DIALOGUE:
            if block.sources:
                return None, "the dialogue lines carry citations; cited text is not rewritten"
            if "lines" in reply:
                if not _same_length(original["lines"], reply["lines"]):
                    return None, "the reply changed the number of dialogue lines"
                merged["lines"] = [
                    {**line, "text": new.get("text", line["text"])}
                    for line, new in zip(original["lines"], reply["lines"], strict=True)
                ]
        case BlockKind.VOCABULARY:
            if "items" in reply:
                if not _same_length(original["items"], reply["items"]):
                    return None, "the reply changed the number of vocabulary items"
                merged["items"] = [
                    {**item, "example": new.get("example", item.get("example"))}
                    for item, new in zip(original["items"], reply["items"], strict=True)
                ]
        case _:
            return None, f"a {block.kind} block is not repaired"
    return merged, ""


def _first_error(exc: ValidationError) -> str:
    error = exc.errors(include_url=False)[0]
    location = ".".join(str(part) for part in error["loc"]) or "(root)"
    return f"{location}: {error['msg']}"


def _fails_steps(lesson: Lesson, block_id: str, curriculum: Curriculum) -> bool:
    return any(
        found == block_id and result.status is CheckStatus.FAIL
        for found, result in steps_present(lesson, curriculum)
    )


def repair_lesson(
    lesson: Lesson,
    curriculum: Curriculum,
    provider: LLMProvider,
    *,
    sources: Sources | None = None,
    model_block_ids: Collection[str],
    max_blocks: int = MAX_REPAIR_BLOCKS,
) -> tuple[Lesson, RepairReport]:
    """One repair round over the model-written blocks that fail the level checks.

    Returns a new lesson (the input is not changed) and the report. Only ``content`` of a
    replaced block changes. A reply the model cannot produce as JSON keeps the original; other
    model and network failures propagate as ``LLMError``. The caller re-runs the level checks
    when ``report.replaced`` is non-empty.
    """
    report = RepairReport()
    candidates = offending_blocks(lesson, model_block_ids)
    report.candidates = [block.id for block in candidates]
    result = lesson.model_copy(deep=True)
    if not candidates:
        logger.info(report.summary())
        return result, report
    allowed = allowed_forms(lesson, curriculum)
    rules = curriculum.forbidden_rules()
    expansions = contraction_expansions(lesson)
    for block in candidates[:max_blocks]:
        report.sent.append(block.id)
        before = offences(block, allowed, rules, sources, expansions)
        prompt = repair_prompt(block, lesson, curriculum, sources)
        messages = [Message(role="user", content=prompt)]
        try:
            reply = complete_json(provider, messages, RepairReply, retries=0, temperature=0.0)
        except StructuredOutputError as exc:
            report.kept[block.id] = f"reply was not a block: {exc}"
            continue
        if reply.id != block.id or reply.kind != block.kind:
            report.kept[block.id] = f"reply changed the id or kind to {reply.id!r} / {reply.kind!r}"
            continue
        content, problem = pupil_content(block, reply.content)
        if content is None:
            report.kept[block.id] = problem
            continue
        try:
            candidate = BlockAdapter.validate_python(
                {**block.model_dump(mode="json"), "content": content}
            )
        except ValidationError as exc:
            report.kept[block.id] = f"content is not valid {block.kind}: {_first_error(exc)}"
            continue
        after = offences(candidate, allowed, rules, sources, expansions)
        if len(after) >= len(before):
            report.kept[block.id] = (
                f"still {len(after)} offending word(s) or hit(s) (was {len(before)})"
            )
            continue
        if _hit_count(candidate, rules) > _hit_count(block, rules):
            report.kept[block.id] = "the reply trades words for new grammar hits"
            continue
        original = result.blocks[block.id]
        result.blocks[block.id] = candidate
        if _fails_steps(result, block.id, curriculum):
            result.blocks[block.id] = original
            report.kept[block.id] = "the reply dropped the activity's skills or steps"
            continue
        report.replaced.append(block.id)
    for block_id, reason in report.kept.items():
        logger.debug("repair kept %s: %s", block_id, reason)
    logger.info(report.summary())
    return result, report
