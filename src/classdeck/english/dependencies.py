"""Infer ``depends_on`` edges the drafter may have missed.

An edge ``activity -> vocabulary`` means "if the vocabulary changes, re-check the activity".
The rules are in ``docs/english-rules.md``; objectives never receive an edge because they come
from the curriculum, not from other blocks.
"""

from __future__ import annotations

import re

from classdeck.english.validators import english_text_of, patterns_of
from classdeck.lesson.schema import (
    ActivityBlock,
    ActivityType,
    BlockKind,
    DialogueBlock,
    Lesson,
    SummaryBlock,
    VocabularyBlock,
)

_DIALOGUE_ACTIVITIES = frozenset({ActivityType.LOOK_LISTEN_REPEAT, ActivityType.ROLE_PLAY})
_SUMMARISED_KINDS = frozenset({BlockKind.VOCABULARY, BlockKind.SENTENCE_PATTERN, BlockKind.PHONICS})


def _word_regex(word: str) -> re.Pattern[str]:
    body = r"\s+".join(re.escape(part) for part in word.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


def _uses_vocabulary(block: VocabularyBlock, texts: list[str]) -> bool:
    words = [item.word for item in block.content.items if item.word.strip()]
    return any(_word_regex(word).search(text) for word in words for text in texts)


def _activity_targets(lesson: Lesson, activity: ActivityBlock) -> set[str]:
    texts = english_text_of(activity)
    targets = {
        block.id
        for block in lesson.blocks.values()
        if isinstance(block, VocabularyBlock) and _uses_vocabulary(block, texts)
    }
    targets.update(
        pattern_block.id
        for pattern_block, regex in patterns_of(lesson)
        if any(regex.search(text) for text in texts)
    )
    if activity.content.type in _DIALOGUE_ACTIVITIES:
        section = lesson.section_of(activity.id)
        if section is not None:
            targets.update(
                block_id
                for block_id in section.block_ids
                if isinstance(lesson.blocks[block_id], DialogueBlock)
            )
    return targets


def _summary_targets(lesson: Lesson) -> set[str]:
    return {block.id for block in lesson.blocks.values() if block.kind in _SUMMARISED_KINDS}


def infer(lesson: Lesson) -> Lesson:
    """A copy of the lesson with the missing ``depends_on`` edges added.

    Existing edges are kept in order; new ones follow, sorted by block id. Running it again
    changes nothing.
    """
    result = lesson.model_copy(deep=True)
    for block in result.blocks.values():
        targets: set[str] = set()
        if isinstance(block, ActivityBlock):
            targets = _activity_targets(result, block)
        elif isinstance(block, SummaryBlock):
            targets = _summary_targets(result)
        new = sorted(
            target
            for target in targets
            if target != block.id
            and target not in block.depends_on
            and result.blocks[target].kind != BlockKind.OBJECTIVE
        )
        block.depends_on = [*block.depends_on, *new]
    return result
