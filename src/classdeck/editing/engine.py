"""A thin front for the editing stage, holding the provider, sources and curriculum once."""

from __future__ import annotations

from collections.abc import Mapping

from classdeck.curriculum.loader import Curriculum
from classdeck.editing.history import accept, redo, undo
from classdeck.editing.models import EditProposal, EditRequest
from classdeck.editing.proposal import direct_edit, propose_edit
from classdeck.english.presets import EditPreset, presets_for
from classdeck.lesson.schema import Block, BlockKind, Lesson, Revision, SourceDocument
from classdeck.llm.base import LLMProvider

Sources = Mapping[str, SourceDocument]


class EditEngine:
    """Propose, hand-edit, accept, undo and redo against one provider, sources and curriculum."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        sources: Sources | None = None,
        curriculum: Curriculum | None = None,
        grounding_provider: LLMProvider | None = None,
    ) -> None:
        self.provider = provider
        self.sources = sources
        self.curriculum = curriculum
        self.grounding_provider = grounding_provider

    def propose(self, lesson: Lesson, request: EditRequest) -> EditProposal:
        return propose_edit(
            lesson,
            request,
            provider=self.provider,
            sources=self.sources,
            curriculum=self.curriculum,
            grounding_provider=self.grounding_provider,
        )

    def direct_edit(self, lesson: Lesson, block_id: str, block: Block) -> EditProposal:
        return direct_edit(
            lesson,
            block_id,
            block,
            sources=self.sources,
            curriculum=self.curriculum,
            grounding_provider=self.grounding_provider,
        )

    def accept(self, lesson: Lesson, proposal: EditProposal) -> Lesson:
        return accept(lesson, proposal, curriculum=self.curriculum, sources=self.sources)

    def undo(self, lesson: Lesson) -> tuple[Lesson, Revision]:
        return undo(lesson)

    def redo(self, lesson: Lesson, revision: Revision) -> Lesson:
        return redo(lesson, revision)

    @staticmethod
    def presets_for(kind: BlockKind) -> list[EditPreset]:
        return presets_for(kind)
