"""Keep an edit's re-checks inside the blocks it changed (``docs/editing.md`` sections 4 and 5).

Grounding and the English validators look at the whole lesson. An edit must not move anything
outside its selection, so their results are taken over only for the blocks the edit replaced or
added, plus the lesson-level results; every other block stays as it was.
"""

from __future__ import annotations

from collections.abc import Iterable

from classdeck.lesson.schema import Lesson


def restrict_results(base: Lesson, checked: Lesson, block_ids: Iterable[str]) -> Lesson:
    """``checked`` with every block outside ``block_ids`` taken from ``base`` instead.

    Both lessons must hold the same block ids. Lesson-level results come from ``checked``;
    neither input is changed.
    """
    scope = set(block_ids)
    result = checked.model_copy(deep=True)
    for block_id in result.blocks:
        if block_id not in scope:
            result.blocks[block_id] = base.blocks[block_id].model_copy(deep=True)
    return result
