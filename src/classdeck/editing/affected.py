"""The affected set: blocks that depend on what an edit changes (``docs/editing.md`` section 4).

The engine flags them with a suggested follow-up; it never rewrites them.
"""

from __future__ import annotations

from collections.abc import Iterable

from classdeck.editing.models import Affected
from classdeck.lesson.schema import BlockKind, Lesson

_REGENERATE_KINDS = frozenset({BlockKind.ACTIVITY, BlockKind.SUMMARY})


def compute_affected(lesson: Lesson, changed_ids: Iterable[str]) -> list[Affected]:
    """Transitive dependents of the changed blocks, in lesson order, without the changed ones.

    A block reached from several changed ids is listed once, for the first changed id it
    depends on directly, else for the first one it depends on through other blocks. Activities
    and summaries drill or list the changed content and get ``regenerate``; every other kind
    gets ``review``.
    """
    changed = list(dict.fromkeys(changed_ids))
    excluded = set(changed)
    found: dict[str, Affected] = {}
    for direct in (True, False):
        for changed_id in changed:
            changed_block = lesson.blocks.get(changed_id)
            kind = changed_block.kind if changed_block is not None else "unknown"
            how = "" if direct else " indirectly"
            for dependent_id in lesson.dependents_of(changed_id, transitive=not direct):
                if dependent_id in excluded or dependent_id in found:
                    continue
                dependent = lesson.blocks[dependent_id]
                action = "regenerate" if dependent.kind in _REGENERATE_KINDS else "review"
                found[dependent_id] = Affected(
                    block_id=dependent_id,
                    reason=f"depends_on {changed_id}{how} — {kind}",
                    suggested_action=action,
                )
    rank = {block_id: index for index, block_id in enumerate(_lesson_order(lesson))}
    return sorted(found.values(), key=lambda affected: rank[affected.block_id])


def _lesson_order(lesson: Lesson) -> list[str]:
    order = [block.id for block in lesson.ordered_blocks()]
    placed = set(order)
    order.extend(block_id for block_id in lesson.blocks if block_id not in placed)
    return order
