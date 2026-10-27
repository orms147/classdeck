"""``pipeline.build_lesson`` / ``finish_lesson``: the composed stages every draft goes through.

``docs/planning.md``, "Shared pipeline". Offline on the public fixture, plus one scripted model
path that exercises the repair round and the separate grounding judge.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import pytest

from classdeck.curriculum import load_curriculum
from classdeck.english.validators import (
    GLOSS_PRESENT,
    GRAMMAR_SCOPE,
    PATTERN_USED,
    TIMING,
    VOCAB_LEVEL,
    WORDING_KEPT,
    has_failures,
    warnings,
)
from classdeck.grounding import CITATION_SUPPORTED, grounded_ratio
from classdeck.ingest import ingest
from classdeck.lesson import (
    ActivityBlock,
    Block,
    CheckStatus,
    Lesson,
    Provenance,
    SentencePatternBlock,
    SourceDocument,
    SummaryBlock,
    VocabularyBlock,
    find_dangling_citations,
)
from classdeck.llm import LLMError, Message, MockProvider
from classdeck.pipeline import build_lesson, finish_lesson
from classdeck.planning import Outline, build_skeleton, draft_lesson

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "eval" / "fixtures" / "public"
CURRICULUM = load_curriculum(ROOT / "curriculum", "gdpt-2018", 3)

OFFENDING = "Salute the contestant with Hello or Hi."
CLEAN = "Say Hello or Hi to your partner."


@pytest.fixture(scope="module")
def document() -> SourceDocument:
    return ingest(FIXTURES / "mini-unit-hello.pdf")


@pytest.fixture(scope="module")
def sources(document: SourceDocument) -> dict[str, SourceDocument]:
    return {document.doc_id: document}


def outline_for(document: SourceDocument, period: int) -> Outline:
    return build_skeleton(
        [document], CURRICULUM, unit_id="ta3.u1", lesson_no=1, period=period, grade=3
    )


@pytest.fixture(scope="module")
def period_one(document: SourceDocument, sources: dict[str, SourceDocument]) -> Lesson:
    return build_lesson(outline_for(document, 1), sources, CURRICULUM, None)


@pytest.fixture(scope="module")
def period_two(document: SourceDocument, sources: dict[str, SourceDocument]) -> Lesson:
    return build_lesson(outline_for(document, 2), sources, CURRICULUM, None)


def blocks_of(lesson: Lesson, index: int) -> list[Block]:
    return [lesson.blocks[block_id] for block_id in lesson.sections[index].block_ids]


def only[T](blocks: list[Block], cls: type[T]) -> T:
    found = [block for block in blocks if isinstance(block, cls)]
    assert len(found) == 1, [block.kind for block in blocks]
    return found[0]


def status(block: Block, name: str) -> CheckStatus | None:
    return next((check.status for check in block.checks if check.name == name), None)


def projection(lesson: Lesson) -> list[tuple[str, str, list[tuple[str, str]]]]:
    """What the pipeline decides about each block, without ids or timestamps."""
    return [
        (block.kind, block.provenance, sorted((c.name, c.status) for c in block.checks))
        for block in lesson.ordered_blocks()
    ]


# --- offline -----------------------------------------------------------------------------------


@pytest.mark.parametrize("period", [1, 2])
def test_offline_lessons_are_valid_checked_and_cite_only_real_spans(
    period: int,
    document: SourceDocument,
    sources: dict[str, SourceDocument],
    period_one: Lesson,
    period_two: Lesson,
) -> None:
    lesson = period_one if period == 1 else period_two
    assert Lesson.model_validate(lesson.model_dump(mode="json")) == lesson
    assert lesson.curriculum.period == period
    assert find_dangling_citations(lesson, sources) == []
    assert not has_failures(lesson)
    assert {check.name for check in lesson.checks} >= {TIMING, "english.skill_coverage"}
    for block in lesson.content_blocks():
        if block.sources:
            assert status(block, CITATION_SUPPORTED) is not None
        if block.kind in ("activity", "dialogue", "vocabulary", "sentence_pattern"):
            assert status(block, VOCAB_LEVEL) is CheckStatus.PASS
            if block.kind != "vocabulary":
                assert status(block, GRAMMAR_SCOPE) is CheckStatus.PASS
    for block in lesson.blocks.values():
        if isinstance(block, VocabularyBlock):
            assert all(item.ipa for item in block.content.items)  # run_all filled the IPA
            assert all(item.meaning_vi for item in block.content.items)  # ... and the glosses
            assert status(block, GLOSS_PRESENT) is CheckStatus.PASS
        if isinstance(block, ActivityBlock | SummaryBlock):
            assert status(block, WORDING_KEPT) is CheckStatus.PASS
    assert all(block_id is None or block_id in lesson.blocks for block_id, _ in warnings(lesson))


def test_period_one_is_grounded_as_the_template_promises(period_one: Lesson) -> None:
    assert grounded_ratio(period_one) >= 0.6
    presentation = blocks_of(period_one, 1)
    assert only(presentation, VocabularyBlock).provenance is Provenance.EXTRACTED
    assert only(presentation, SentencePatternBlock).provenance is Provenance.EXTRACTED
    assert only(presentation, ActivityBlock).provenance is Provenance.DERIVED


def test_period_two_presentation_reviews_page_one_and_the_rest_is_derived(
    period_two: Lesson,
) -> None:
    warm_up = blocks_of(period_two, 1)
    vocabulary = only(warm_up, VocabularyBlock)
    pattern = only(warm_up, SentencePatternBlock)
    for block in (vocabulary, pattern):
        assert block.provenance is Provenance.EXTRACTED
        assert block.sources and all(c.page == 1 for c in block.sources)
        assert status(block, CITATION_SUPPORTED) is CheckStatus.PASS
    assert [item.word for item in vocabulary.content.items] == ["hello", "hi"]
    assert pattern.content.pattern == "Hello, I'm ___."
    repeat = only(warm_up, ActivityBlock)
    assert repeat.provenance is Provenance.DERIVED
    assert repeat.sources == []
    assert only(blocks_of(period_two, 0), ActivityBlock).provenance is Provenance.GENERATED
    assert grounded_ratio(period_two) >= 0.5
    for index in range(2, 5):  # textbook activities 4-6
        drilled = only(blocks_of(period_two, index), ActivityBlock)
        assert drilled.provenance is Provenance.DERIVED
        assert pattern.id in drilled.depends_on
        assert status(drilled, PATTERN_USED) is CheckStatus.PASS
        assert all(c.page == 2 for c in drilled.sources)


def test_repair_is_skipped_without_a_provider(
    document: SourceDocument, sources: dict[str, SourceDocument], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="classdeck.pipeline"):
        build_lesson(outline_for(document, 1), sources, CURRICULUM, None)
    assert "repair skipped: no provider" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="classdeck.pipeline"):
        build_lesson(outline_for(document, 1), sources, CURRICULUM, None, repair=False)
    assert "repair" not in caplog.text


def test_on_stage_names_the_stages_in_order(
    document: SourceDocument, sources: dict[str, SourceDocument]
) -> None:
    seen: list[str] = []
    build_lesson(outline_for(document, 1), sources, CURRICULUM, None, on_stage=seen.append)
    assert seen == ["draft", "grounding", "validators"]
    seen.clear()
    lesson = draft_lesson(outline_for(document, 1), [document], CURRICULUM)
    finish_lesson(lesson, sources, CURRICULUM, None, on_stage=seen.append)
    assert seen == ["grounding", "validators"]


def test_finish_lesson_is_the_re_check_and_is_idempotent(
    document: SourceDocument, sources: dict[str, SourceDocument], period_one: Lesson
) -> None:
    drafted = draft_lesson(outline_for(document, 1), [document], CURRICULUM)
    finished = finish_lesson(drafted, sources, CURRICULUM, None)
    assert projection(finished) == projection(period_one)
    again = finish_lesson(finished, sources, CURRICULUM, None)
    assert projection(again) == projection(finished)
    assert drafted.blocks[drafted.sections[1].block_ids[0]].checks == []  # input untouched


# --- scripted model path -----------------------------------------------------------------------


def activity(activity_type: str, title: str, instructions: str, **extra: Any) -> dict[str, Any]:
    return {
        "kind": "activity",
        "content": {
            "type": activity_type,
            "title": title,
            "skills": ["speaking", "listening"],
            "interaction": "pairs",
            "minutes": 5,
            "instructions": instructions,
            "teacher_steps": ["Model it with one pupil."],
            "student_steps": ["Listen.", instructions],
        },
        "span_ids": extra.get("span_ids", []),
        "depends_on": extra.get("depends_on", []),
    }


DIALOGUE = {
    "kind": "dialogue",
    "content": {
        "context": "Ha meets Tien.",
        "lines": [
            {"speaker": "Ha", "text": "Hello, I'm Ha."},
            {"speaker": "Tien", "text": "Hi, Ha. I'm Tien."},
        ],
    },
    "span_ids": ["p1.s4", "p1.s5"],
}


class ScriptedGenerator(MockProvider):
    """Answers drafting prompts by phase and repair prompts with a clean rewrite."""

    def __init__(self) -> None:
        super().__init__(handler=self.answer)
        self.repair_prompts: list[str] = []

    def answer(self, messages: list[Message]) -> str:
        text = messages[0].content
        assert isinstance(text, str)
        assert "CLAIM:" not in text, "the generator must not judge citations when a judge is given"
        if text.startswith("You fix one block"):
            self.repair_prompts.append(text)
            block_id = re.search(r'"id": "(b_[0-9a-f]+)"', text).group(1)  # type: ignore[union-attr]
            content = json.loads(text.split("BLOCK (JSON)\n", 1)[1].split("\n\nPROBLEMS", 1)[0])
            content["content"]["instructions"] = CLEAN
            content["content"]["student_steps"] = ["Listen.", CLEAN]
            return json.dumps({"id": block_id, "kind": "activity", "content": content["content"]})
        if '"phase": "warm_up"' in text:
            return json.dumps({"blocks": [activity("game", "Hello ball", OFFENDING)]})
        if '"phase": "presentation"' in text:
            drill = activity(
                "look_listen_repeat",
                "Look, listen and repeat",
                "Listen and repeat: Hello, I'm ___.",
                span_ids=["p1.s2"],
                depends_on=[0],
            )
            return json.dumps({"blocks": [DIALOGUE, drill]})
        if '"phase": "practice"' in text:
            drill = activity(
                "listen_point_say",
                "Listen, point and say",
                "Say: Hello, I'm ___.",
                span_ids=["p1.s6"],
            )
            return json.dumps({"blocks": [drill]})
        return json.dumps({"blocks": [activity("lets_talk", "Let's talk", "Say: Hello, I'm ___.")]})


def test_model_path_repairs_the_offending_block_and_uses_the_judge(
    document: SourceDocument, sources: dict[str, SourceDocument]
) -> None:
    generator = ScriptedGenerator()
    judge = MockProvider(handler=lambda messages: '{"supported": true, "reason": "same words"}')
    seen: list[str] = []

    lesson = build_lesson(
        outline_for(document, 1), sources, CURRICULUM, generator, judge=judge, on_stage=seen.append
    )

    assert seen == ["draft", "grounding", "validators", "repair"]
    assert len(generator.repair_prompts) == 1
    assert "contestant, salute" in generator.repair_prompts[0]
    game = only(blocks_of(lesson, 0), ActivityBlock)
    assert game.content.title == "Hello ball"
    assert game.content.instructions == CLEAN
    assert status(game, VOCAB_LEVEL) is CheckStatus.PASS  # the checks were re-run
    assert not any(
        check.name == VOCAB_LEVEL and check.status is CheckStatus.WARN for check in game.checks
    )
    for call in judge.calls:
        assert "CLAIM:" in call["messages"][0]["content"]
    assert Lesson.model_validate(lesson.model_dump(mode="json")) == lesson
    assert find_dangling_citations(lesson, sources) == []


def test_model_path_without_repair_keeps_the_offending_block(
    document: SourceDocument, sources: dict[str, SourceDocument]
) -> None:
    generator = ScriptedGenerator()
    judge = MockProvider(handler=lambda messages: '{"supported": true, "reason": "same words"}')
    seen: list[str] = []
    lesson = build_lesson(
        outline_for(document, 1),
        sources,
        CURRICULUM,
        generator,
        repair=False,
        judge=judge,
        on_stage=seen.append,
    )
    assert seen == ["draft", "grounding", "validators"]
    assert generator.repair_prompts == []
    game = only(blocks_of(lesson, 0), ActivityBlock)
    assert game.content.instructions == OFFENDING
    assert status(game, VOCAB_LEVEL) is CheckStatus.WARN


def test_a_model_failure_during_the_repair_keeps_the_drafted_lesson(
    document: SourceDocument, sources: dict[str, SourceDocument], caplog: pytest.LogCaptureFixture
) -> None:
    class FailsToRepair(ScriptedGenerator):
        def answer(self, messages: list[Message]) -> str:
            if messages[0].content.startswith("You fix one block"):  # type: ignore[union-attr]
                raise LLMError("rate limited")
            return super().answer(messages)

    judge = MockProvider(handler=lambda messages: '{"supported": true, "reason": "same words"}')
    with caplog.at_level(logging.WARNING, logger="classdeck.pipeline"):
        lesson = build_lesson(
            outline_for(document, 1), sources, CURRICULUM, FailsToRepair(), judge=judge
        )
    assert "repair abandoned" in caplog.text
    game = only(blocks_of(lesson, 0), ActivityBlock)
    assert game.content.instructions == OFFENDING
    assert status(game, VOCAB_LEVEL) is CheckStatus.WARN
    assert Lesson.model_validate(lesson.model_dump(mode="json")) == lesson


# --- outcomes, the recap, copied lines, cited text ---------------------------------------------


@pytest.mark.parametrize("period", [1, 2])
def test_outcomes_are_only_those_the_chunks_teach(
    period: int, period_one: Lesson, period_two: Lesson
) -> None:
    """The mini unit never says goodbye: o3 is neither listed nor turned into an objective."""
    lesson = period_one if period == 1 else period_two
    assert lesson.curriculum.outcome_ids == ["ta3.u1.o1", "ta3.u1.o2"]
    objectives = [b for b in lesson.blocks.values() if b.kind == "objective"]
    assert [o.content.outcome_ids for o in objectives] == [["ta3.u1.o1"], ["ta3.u1.o2"]]
    assert next(c for c in lesson.checks if c.name == "curriculum.outcome_covered").status is (
        CheckStatus.PASS
    )


@pytest.mark.parametrize("period", [1, 2])
def test_the_wrap_up_has_a_recap_activity_that_does_not_dilute_grounding(
    period: int, period_one: Lesson, period_two: Lesson
) -> None:
    lesson = period_one if period == 1 else period_two
    wrap_up = blocks_of(lesson, len(lesson.sections) - 1)
    assert [b.kind for b in wrap_up] == ["summary", "activity", "teacher_note"]
    recap = only(wrap_up, ActivityBlock)
    assert recap.content.title == "Say it together"
    assert recap.provenance is Provenance.DERIVED
    assert recap.sources == []
    assert status(recap, VOCAB_LEVEL) is CheckStatus.PASS
    assert status(recap, GRAMMAR_SCOPE) is CheckStatus.PASS
    # derived blocks that cite nothing are not counted, so the recap leaves the ratio alone
    cited = [
        b for b in lesson.content_blocks() if b.sources or b.provenance is not Provenance.DERIVED
    ]
    grounded = [b for b in cited if status(b, CITATION_SUPPORTED) is CheckStatus.PASS]
    assert grounded_ratio(lesson) == pytest.approx(len(grounded) / len(cited))


def test_period_two_activities_are_grounded_by_their_copied_lines(period_two: Lesson) -> None:
    for index in range(2, 5):
        activity_block = only(blocks_of(period_two, index), ActivityBlock)
        assert status(activity_block, CITATION_SUPPORTED) is CheckStatus.PASS
    song = only(blocks_of(period_two, 4), ActivityBlock)
    assert song.content.instructions == "Sing together:"
    assert grounded_ratio(period_two) >= 0.8


def test_the_repair_leaves_a_cited_extracted_dialogue_alone(
    document: SourceDocument, sources: dict[str, SourceDocument]
) -> None:
    class QuotesTheBook(ScriptedGenerator):
        def answer(self, messages: list[Message]) -> str:
            reply = super().answer(messages)
            if '"phase": "presentation"' in messages[0].content:  # type: ignore[operator]
                data = json.loads(reply)
                data["blocks"][0]["content"]["lines"][0]["text"] = "Hello, I'm Ha. Salute."
                return json.dumps(data)
            return reply

    generator = QuotesTheBook()
    judge = MockProvider(handler=lambda messages: '{"supported": true, "reason": "same words"}')
    lesson = build_lesson(outline_for(document, 1), sources, CURRICULUM, generator, judge=judge)
    dialogue = next(b for b in lesson.blocks.values() if b.kind == "dialogue")
    assert dialogue.provenance is Provenance.EXTRACTED
    assert status(dialogue, VOCAB_LEVEL) is CheckStatus.WARN  # "salute" is off the word list
    assert dialogue.content.lines[0].text == "Hello, I'm Ha. Salute."
    assert len(generator.repair_prompts) == 1  # the game only; the quoted dialogue is not sent
    assert "Salute the contestant" in generator.repair_prompts[0]
