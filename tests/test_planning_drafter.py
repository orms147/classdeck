"""Template and model drafting of a lesson from an outline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from classdeck.curriculum import load_curriculum
from classdeck.ingest import ingest
from classdeck.lesson import (
    ActivityBlock,
    ActivityType,
    Block,
    BlockKind,
    CurriculumRef,
    DialogueBlock,
    ImageRefBlock,
    Lesson,
    ObjectiveBlock,
    Page,
    PhonicsBlock,
    Provenance,
    SentencePatternBlock,
    SourceDocument,
    Span,
    SummaryBlock,
    TeacherNoteBlock,
    VocabularyBlock,
    find_dangling_citations,
)
from classdeck.llm import LLMError, MockProvider
from classdeck.planning import (
    DraftBlock,
    DraftReport,
    DraftSection,
    ModelDrafter,
    Outline,
    TemplateDrafter,
    build_context,
    build_skeleton,
    draft_lesson,
    draft_lesson_report,
    reply_problems,
    review_proposal,
    with_review,
)
from classdeck.planning.drafter import RECAP_TITLE

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "eval" / "fixtures" / "public"
CURRICULUM = load_curriculum(ROOT / "curriculum", "gdpt-2018", 3)
UNIT_WORDS = "hello, hi, goodbye, bye, nice, meet"


@pytest.fixture(scope="module")
def document() -> SourceDocument:
    return ingest(FIXTURES / "mini-unit-hello.pdf")


@pytest.fixture(scope="module")
def outline(document: SourceDocument) -> Outline:
    return build_skeleton([document], CURRICULUM, unit_id="ta3.u1", lesson_no=1, period=1, grade=3)


@pytest.fixture(scope="module")
def template_lesson(document: SourceDocument, outline: Outline) -> Lesson:
    return draft_lesson(outline, [document], CURRICULUM, provider=None)


def blocks_of(lesson: Lesson, index: int) -> list[Block]:
    return [lesson.blocks[block_id] for block_id in lesson.sections[index].block_ids]


def only[T](blocks: list[Block], cls: type[T]) -> T:
    found = [block for block in blocks if isinstance(block, cls)]
    assert len(found) == 1, [block.kind for block in blocks]
    return found[0]


def make_document(spans: list[tuple[str, str]], doc_id: str = "d_notes") -> SourceDocument:
    return SourceDocument(
        doc_id=doc_id,
        filename="notes.docx",
        sha256="0" * 64,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        pages=[
            Page(
                number=1,
                spans=[
                    Span(span_id=f"p1.s{order}", kind=kind, text=text, order=order)
                    for order, (kind, text) in enumerate(spans, start=1)
                ],
            )
        ],
    )


def activity(activity_type: str, title: str, **overrides: Any) -> dict[str, Any]:
    content: dict[str, Any] = {
        "type": activity_type,
        "title": title,
        "skills": ["speaking"],
        "interaction": "pairs",
        "minutes": 5,
        "instructions": "Say hello to your partner.",
        "teacher_steps": ["Model it with one pupil."],
        "student_steps": ["Say hello."],
    }
    content.update(overrides)
    return content


def block(
    kind: str,
    content: dict[str, Any],
    *,
    span_ids: list[str] | None = None,
    depends_on: list[int] | None = None,
    teacher_note: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "content": content,
        "span_ids": span_ids or [],
        "depends_on": depends_on or [],
        "teacher_note": teacher_note,
    }


def reply(*blocks: dict[str, Any]) -> str:
    return json.dumps({"blocks": list(blocks)})


DIALOGUE = {
    "context": "Ha meets Tien.",
    "lines": [
        {"speaker": "Ha", "text": "Hello, I'm Ha."},
        {"speaker": "Tien", "text": "Hi, Ha. I'm Tien."},
    ],
}
SCRIPT = [
    reply(block("activity", activity("game", "Hello ball"))),
    reply(
        block("dialogue", DIALOGUE, span_ids=["p1.s4", "p1.s5"]),
        block(
            "activity",
            activity("look_listen_repeat", "Look, listen and repeat"),
            span_ids=["p1.s2"],
            depends_on=[0],
            teacher_note="Use puppets.",
        ),
    ),
    reply(
        block(
            "activity",
            activity("listen_point_say", "Listen, point and say"),
            span_ids=["p1.s6", "p1.s7"],
        )
    ),
    reply(block("activity", activity("lets_talk", "Let's talk"))),
]
BAD_SPAN = reply(block("activity", activity("game", "Ghost"), span_ids=["p9.s9"]))


# --- template drafting -------------------------------------------------------------------------


def test_template_lesson_validates_and_round_trips(
    template_lesson: Lesson, document: SourceDocument, outline: Outline
) -> None:
    assert Lesson.model_validate(template_lesson.model_dump(mode="json")) == template_lesson
    assert template_lesson.title == "Unit 1 Hello — Lesson 1, Period 1"
    assert template_lesson.grade == 3
    assert template_lesson.curriculum == outline.curriculum
    assert [source.doc_id for source in template_lesson.sources] == [document.doc_id]
    assert template_lesson.sources[0].sha256 == document.sha256
    assert find_dangling_citations(template_lesson, {document.doc_id: document}) == []
    assert template_lesson.checks == []


def test_sections_carry_the_outline_minutes(template_lesson: Lesson, outline: Outline) -> None:
    assert [section.id for section in template_lesson.sections] == [
        "s_warm_up",
        "s_presentation",
        "s_practice",
        "s_production",
        "s_wrap_up",
    ]
    assert [(s.phase, s.title, s.minutes) for s in template_lesson.sections] == [
        (s.phase, s.title, s.minutes) for s in outline.sections
    ]


def test_objectives_come_first_one_per_outcome(template_lesson: Lesson, outline: Outline) -> None:
    first = blocks_of(template_lesson, 0)
    objectives = [b for b in first if isinstance(b, ObjectiveBlock)]
    assert len(objectives) == len(outline.curriculum.outcome_ids) == 2
    assert first[: len(objectives)] == objectives
    assert [o.content.outcome_ids for o in objectives] == [
        [outcome_id] for outcome_id in outline.curriculum.outcome_ids
    ]
    assert objectives[0].content.text == "Greet and respond to greetings using Hello / Hi."
    assert objectives[0].content.skills == ["listening", "speaking"]
    for objective in objectives:
        assert objective.provenance is Provenance.DERIVED
        assert objective.sources == []
    for index in range(1, 5):
        assert not any(isinstance(b, ObjectiveBlock) for b in blocks_of(template_lesson, index))


def test_warm_up_has_one_generated_game(template_lesson: Lesson) -> None:
    game = only(blocks_of(template_lesson, 0), ActivityBlock)
    assert game.content.type is ActivityType.GAME
    assert game.provenance is Provenance.GENERATED
    assert game.sources == []
    assert game.content.skills and game.content.teacher_steps and game.content.student_steps


def test_presentation_dialogue(template_lesson: Lesson) -> None:
    dialogue = only(blocks_of(template_lesson, 1), DialogueBlock)
    assert [(line.speaker, line.text) for line in dialogue.content.lines] == [
        ("Ha", "Hello, I'm Ha."),
        ("Tien", "Hi, Ha. I'm Tien."),
    ]
    assert dialogue.provenance is Provenance.EXTRACTED
    assert [c.span_id for c in dialogue.sources] == ["p1.s4", "p1.s5"]
    assert dialogue.sources[0].quote == "Hello, I'm Ha."


def test_presentation_vocabulary(template_lesson: Lesson) -> None:
    vocabulary = only(blocks_of(template_lesson, 1), VocabularyBlock)
    assert [item.word for item in vocabulary.content.items] == ["hello", "hi"]
    for item in vocabulary.content.items:
        assert item.ipa is None
        assert item.meaning_vi == ""
    assert vocabulary.content.items[0].example == "Hello, I'm Ha."
    assert {c.span_id for c in vocabulary.sources} == {"p1.s4", "p1.s5"}


def test_presentation_pattern(template_lesson: Lesson) -> None:
    pattern = only(blocks_of(template_lesson, 1), SentencePatternBlock)
    assert pattern.content.pattern == "Hello, I'm ___."
    assert pattern.content.function == "introducing oneself"
    assert pattern.content.examples == ["Hello, I'm Ha."]
    assert [c.span_id for c in pattern.sources] == ["p1.s4"]


def test_presentation_image_ref(template_lesson: Lesson) -> None:
    image = only(blocks_of(template_lesson, 1), ImageRefBlock)
    assert [c.span_id for c in image.sources] == ["p1.s3"]
    assert image.sources[0].quote is None
    assert image.content.alt


def test_one_activity_per_content_section(template_lesson: Lesson, outline: Outline) -> None:
    for index, instruction in zip(range(1, 4), ["p1.s2", "p1.s6", "p1.s9"], strict=True):
        blocks = blocks_of(template_lesson, index)
        activities = [b for b in blocks if isinstance(b, ActivityBlock)]
        assert len(activities) == 1
        assert blocks[-1] is activities[0]
        assert activities[0].content.type is outline.sections[index].activity_type
        assert activities[0].provenance is Provenance.DERIVED
        assert activities[0].depends_on
        assert [c.span_id for c in activities[0].sources] == [instruction]
    presentation = only(blocks_of(template_lesson, 1), ActivityBlock)
    assert "hello, hi" in presentation.content.instructions
    practice = only(blocks_of(template_lesson, 2), ActivityBlock)
    assert "Hello, I'm ___." in practice.content.instructions
    assert "{{" not in json.dumps(template_lesson.model_dump(mode="json"))


def test_every_citation_resolves_with_an_eighty_char_quote(
    template_lesson: Lesson, document: SourceDocument
) -> None:
    cited = 0
    for block in template_lesson.blocks.values():
        for citation in block.sources:
            span = document.find_span(citation.page, citation.span_id)
            assert span is not None
            assert citation.doc_id == document.doc_id
            assert citation.quote == (" ".join(span.text.split())[:80] or None)
            cited += 1
    assert cited > 0


def test_provenance_follows_citations(template_lesson: Lesson) -> None:
    for block in template_lesson.blocks.values():
        textbook_activity = isinstance(block, ActivityBlock) and bool(block.sources)
        recap = isinstance(block, ActivityBlock) and block.content.title == RECAP_TITLE
        if block.kind in (BlockKind.OBJECTIVE, BlockKind.SUMMARY) or textbook_activity or recap:
            assert block.provenance is Provenance.DERIVED
        elif block.sources:
            assert block.provenance is Provenance.EXTRACTED
        else:
            assert block.provenance is Provenance.GENERATED


def test_wrap_up_has_a_derived_summary_and_a_teacher_note(template_lesson: Lesson) -> None:
    blocks = blocks_of(template_lesson, 4)
    assert [b.kind for b in blocks] == [
        BlockKind.SUMMARY,
        BlockKind.ACTIVITY,
        BlockKind.TEACHER_NOTE,
    ]
    summary = only(blocks, SummaryBlock)
    note = only(blocks, TeacherNoteBlock)
    vocabulary = only(blocks_of(template_lesson, 1), VocabularyBlock)
    pattern = only(blocks_of(template_lesson, 1), SentencePatternBlock)
    assert summary.provenance is Provenance.DERIVED
    assert summary.content.bullets == ["hello, hi", "Hello, I'm ___."]
    assert summary.depends_on == [vocabulary.id, pattern.id]
    assert note.content.text
    assert note.provenance is Provenance.GENERATED


def test_wrap_up_has_a_recap_activity(template_lesson: Lesson) -> None:
    """The wrap-up is more than a summary: pupils say the words and the pattern together."""
    blocks = blocks_of(template_lesson, 4)
    recap = only(blocks, ActivityBlock)
    summary = only(blocks, SummaryBlock)
    vocabulary = only(blocks_of(template_lesson, 1), VocabularyBlock)
    pattern = only(blocks_of(template_lesson, 1), SentencePatternBlock)
    content = recap.content
    assert content.type is ActivityType.OTHER
    assert content.title == "Say it together"
    assert content.minutes == 2
    assert content.interaction.value == "whole_class"
    assert content.instructions == "Say the words and the pattern together."
    assert content.student_steps == [
        "Listen to the teacher.",
        "Say the words: hello, hi",
        "Say: Hello, I'm ___.",
    ]
    assert any("summary" in step for step in content.teacher_steps)
    assert recap.provenance is Provenance.DERIVED
    assert recap.sources == []
    assert {summary.id, vocabulary.id, pattern.id} <= set(recap.depends_on)
    assert content.skills


def test_the_recap_names_the_sounds_of_a_phonics_lesson(document: SourceDocument) -> None:
    third = build_skeleton([document], CURRICULUM, unit_id="ta3.u1", lesson_no=3, period=1, grade=3)
    lesson = draft_lesson(third, [document], CURRICULUM)
    recap = only(blocks_of(lesson, len(lesson.sections) - 1), ActivityBlock)
    assert recap.content.title == "Say it together"
    assert any(step.startswith("Say the sound: /") for step in recap.content.student_steps)


def test_inferred_edges(template_lesson: Lesson) -> None:
    presentation = blocks_of(template_lesson, 1)
    dialogue = only(presentation, DialogueBlock)
    vocabulary = only(presentation, VocabularyBlock)
    pattern = only(presentation, SentencePatternBlock)
    assert set(only(presentation, ActivityBlock).depends_on) == {
        dialogue.id,
        vocabulary.id,
        pattern.id,
    }
    practice = only(blocks_of(template_lesson, 2), ActivityBlock)
    assert pattern.id in practice.depends_on
    assert vocabulary.id in practice.depends_on
    production = only(blocks_of(template_lesson, 3), ActivityBlock)
    assert pattern.id in production.depends_on


PERIOD_TWO_TYPES = [ActivityType.LISTEN_AND_DO, ActivityType.LOOK_COMPLETE_READ, ActivityType.SONG]


def period_two(document: SourceDocument) -> Outline:
    return build_skeleton([document], CURRICULUM, unit_id="ta3.u1", lesson_no=1, period=2, grade=3)


def test_period_two_opens_with_a_review_of_the_previous_period(document: SourceDocument) -> None:
    """docs/planning.md, "Period 2 opens with a review"."""
    lesson = draft_lesson(period_two(document), [document], CURRICULUM)
    assert [section.id for section in lesson.sections] == [
        "s_warm_up",
        "s_practice",
        "s_practice_2",
        "s_production",
        "s_wrap_up",
    ]
    warm_up = blocks_of(lesson, 0)
    assert [b.kind for b in warm_up] == [
        BlockKind.OBJECTIVE,
        BlockKind.OBJECTIVE,
        BlockKind.VOCABULARY,
        BlockKind.SENTENCE_PATTERN,
        BlockKind.ACTIVITY,
    ]
    vocabulary = only(warm_up, VocabularyBlock)
    pattern = only(warm_up, SentencePatternBlock)
    game = only(warm_up, ActivityBlock)
    assert [item.word for item in vocabulary.content.items] == ["hello", "hi"]
    assert {c.span_id for c in vocabulary.sources} == {"p1.s4", "p1.s5", "p1.s7", "p1.s8"}
    assert all(c.page == 1 for c in vocabulary.sources)
    assert vocabulary.provenance is Provenance.EXTRACTED
    assert pattern.content.pattern == "Hello, I'm ___."
    assert pattern.content.examples == ["Hello, I'm Ha."]
    assert [c.span_id for c in pattern.sources] == ["p1.s4", "p1.s7"]
    assert game.content.type is ActivityType.GAME
    assert game.sources == []  # the game is ours even though it reviews cited spans
    assert game.provenance is Provenance.GENERATED
    assert set(game.depends_on) >= {vocabulary.id, pattern.id}
    assert "Hello, I'm ___." in game.content.instructions
    assert not any(isinstance(b, DialogueBlock) for b in lesson.blocks.values())
    for index, activity_type in zip(range(1, 4), PERIOD_TWO_TYPES, strict=True):
        drilled = only(blocks_of(lesson, index), ActivityBlock)
        assert drilled.content.type is activity_type
        assert pattern.id in drilled.depends_on
        assert drilled.provenance is Provenance.DERIVED
    summary = only(blocks_of(lesson, 4), SummaryBlock)
    assert summary.content.bullets == ["hello, hi", "Hello, I'm ___."]
    assert summary.depends_on == [vocabulary.id, pattern.id]
    assert Lesson.model_validate(lesson.model_dump(mode="json")) == lesson


def test_period_two_without_earlier_activities_has_no_review_and_a_unit_summary() -> None:
    later = make_document(
        [
            ("list_item", "4. Listen and tick."),
            ("paragraph", "a. Hello, I'm Duc. b. Hi, I'm Lan."),
            ("list_item", "5. Look, complete and read."),
            ("paragraph", "Hello, I'm _____."),
            ("list_item", "6. Let's sing."),
            ("paragraph", "Hello, hello, hello! I'm Ha."),
        ]
    )
    second = period_two(later)
    assert second.sections[0].review is False
    lesson = draft_lesson(second, [later], CURRICULUM)
    taught = (VocabularyBlock, SentencePatternBlock, DialogueBlock)
    assert not any(isinstance(b, taught) for b in lesson.blocks.values())
    types = [only(blocks_of(lesson, i), ActivityBlock).content.type for i in range(1, 4)]
    assert types == PERIOD_TWO_TYPES
    summary = only(blocks_of(lesson, 4), SummaryBlock)
    assert summary.content.bullets == [UNIT_WORDS, "Hello, I'm ___.", "Nice to meet you."]
    assert summary.depends_on == []


def test_review_blocks_and_with_review(document: SourceDocument) -> None:
    second = period_two(document)
    context = build_context(second, 0, [document], CURRICULUM)
    review = TemplateDrafter().review_blocks(context)
    assert [b.kind for b in review] == [BlockKind.VOCABULARY, BlockKind.SENTENCE_PATTERN]
    assert reply_problems(DraftSection(blocks=review), context) == []
    # the template drafter alone writes the game; draft_lesson prepends the review
    alone = TemplateDrafter().draft_section(context)
    assert [b.kind for b in alone.blocks] == [BlockKind.ACTIVITY]
    assert alone.blocks[0].span_ids == []
    proposal = DraftSection(
        blocks=[
            DraftBlock(kind=BlockKind.DIALOGUE, content=DIALOGUE),
            DraftBlock(kind=BlockKind.ACTIVITY, content=activity("game", "Chain"), depends_on=[0]),
        ]
    )
    combined = with_review(review, proposal)
    assert [b.kind for b in combined.blocks] == [
        BlockKind.VOCABULARY,
        BlockKind.SENTENCE_PATTERN,
        BlockKind.DIALOGUE,
        BlockKind.ACTIVITY,
    ]
    assert combined.blocks[2].depends_on == []
    assert combined.blocks[3].depends_on == [0, 1, 2]
    assert with_review([], proposal) is proposal
    # a section with no spans has nothing to review
    first = build_skeleton([document], CURRICULUM, unit_id="ta3.u1", lesson_no=1, period=1, grade=3)
    assert TemplateDrafter().review_blocks(build_context(first, 0, [document], CURRICULUM)) == []


def test_model_path_prepends_the_review_and_reports_only_model_blocks(
    document: SourceDocument,
) -> None:
    second = period_two(document)
    script = [
        reply(block("activity", activity("game", "Hello chain"))),
        reply(block("activity", activity("listen_and_do", "Listen and tick"), span_ids=["p2.s2"])),
        reply(block("activity", activity("look_complete_read", "Complete"), span_ids=["p2.s4"])),
        reply(block("activity", activity("song", "Sing"), span_ids=["p2.s6"])),
    ]
    provider = MockProvider(responses=script)
    lesson, report = draft_lesson_report(second, [document], CURRICULUM, provider)
    warm_up = blocks_of(lesson, 0)
    vocabulary = only(warm_up, VocabularyBlock)
    pattern = only(warm_up, SentencePatternBlock)
    game = only(warm_up, ActivityBlock)
    assert game.content.title == "Hello chain"
    assert game.depends_on[:2] == [vocabulary.id, pattern.id]
    activities = [only(blocks_of(lesson, i), ActivityBlock) for i in range(4)]
    assert report.model_block_ids == {a.id for a in activities}
    assert vocabulary.id not in report.model_block_ids
    assert pattern.id not in report.model_block_ids
    prompt = provider.calls[0]["messages"][0]["content"]
    assert "engine has already built the vocabulary and sentence_pattern" in prompt
    assert "[p1.s4] Hello, I'm Ha." in prompt
    assert "ALLOWED WORDS" in prompt
    assert "may use only these words, pupils' names, and numbers" in prompt
    allowed = prompt.split("ALLOWED WORDS")[1]
    words = allowed.splitlines()[2].split(", ")  # the sorted list: grade, classroom, unit words
    assert words == sorted(words) and {"hello", "hi", "partner", "swap"} <= set(words)
    assert "teacher_steps" in allowed and "any English" in allowed


def test_model_review_warm_up_keeps_only_its_uncited_game(document: SourceDocument) -> None:
    """A reply that writes the review blocks itself must not duplicate the engine's."""
    second = period_two(document)
    chatty = reply(
        block(
            "vocabulary",
            {"items": [{"word": "hello", "meaning_vi": "xin chao"}]},
            span_ids=["p1.s4"],
        ),
        block("dialogue", DIALOGUE, span_ids=["p1.s4", "p1.s5"]),
        block("activity", activity("game", "Hello chain"), span_ids=["p1.s4"], depends_on=[0, 1]),
    )
    script = [
        chatty,
        reply(block("activity", activity("listen_and_do", "Listen and tick"), span_ids=["p2.s2"])),
        reply(block("activity", activity("look_complete_read", "Complete"), span_ids=["p2.s4"])),
        reply(block("activity", activity("song", "Sing"), span_ids=["p2.s6"])),
    ]
    lesson, report = draft_lesson_report(second, [document], CURRICULUM, MockProvider(script))
    warm_up = blocks_of(lesson, 0)
    assert [b.kind for b in warm_up[2:]] == [
        BlockKind.VOCABULARY,
        BlockKind.SENTENCE_PATTERN,
        BlockKind.ACTIVITY,
    ]
    vocabulary = only(warm_up, VocabularyBlock)
    assert [item.meaning_vi for item in vocabulary.content.items] == [""] * 2  # the engine's
    game = only(warm_up, ActivityBlock)
    assert game.content.title == "Hello chain"
    assert game.sources == [] and game.provenance is Provenance.GENERATED
    assert set(game.depends_on) >= {vocabulary.id, only(warm_up, SentencePatternBlock).id}
    assert game.id in report.model_block_ids
    assert vocabulary.id not in report.model_block_ids
    assert not any(isinstance(b, DialogueBlock) for b in lesson.blocks.values())


def test_model_review_warm_up_without_a_game_uses_the_template_game(
    document: SourceDocument,
) -> None:
    second = period_two(document)
    only_vocabulary = reply(block("vocabulary", {"items": [{"word": "hello"}]}, span_ids=["p1.s4"]))
    script = [
        only_vocabulary,
        reply(block("activity", activity("listen_and_do", "Listen and tick"), span_ids=["p2.s2"])),
        reply(block("activity", activity("look_complete_read", "Complete"), span_ids=["p2.s4"])),
        reply(block("activity", activity("song", "Sing"), span_ids=["p2.s6"])),
    ]
    lesson, report = draft_lesson_report(second, [document], CURRICULUM, MockProvider(script))
    game = only(blocks_of(lesson, 0), ActivityBlock)
    template_game = TemplateDrafter().draft_section(
        build_context(second, 0, [document], CURRICULUM)
    )
    assert game.content.title == template_game.blocks[0].content["title"]
    assert game.id not in report.model_block_ids  # the template wrote it
    assert game.sources == []


def test_review_proposal_keeps_one_uncited_activity() -> None:
    chatty = DraftSection(
        blocks=[
            DraftBlock(kind=BlockKind.VOCABULARY, content={"items": [{"word": "hello"}]}),
            DraftBlock(
                kind=BlockKind.ACTIVITY,
                content=activity("game", "A"),
                span_ids=["p1.s4"],
                depends_on=[0],
            ),
            DraftBlock(kind=BlockKind.ACTIVITY, content=activity("game", "B")),
        ]
    )
    template = DraftSection(blocks=[DraftBlock(kind=BlockKind.ACTIVITY, content={})])
    kept, from_reply = review_proposal(chatty, template)
    assert from_reply is True
    assert [(b.content["title"], b.span_ids, b.depends_on) for b in kept.blocks] == [("A", [], [])]
    assert review_proposal(DraftSection(blocks=[chatty.blocks[0]]), template) == (template, False)


def test_draft_lesson_report_is_empty_without_a_provider(
    document: SourceDocument, outline: Outline
) -> None:
    lesson, report = draft_lesson_report(outline, [document], CURRICULUM)
    assert report.model_block_ids == set()
    assert isinstance(report, DraftReport)
    assert len(lesson.blocks) == len(draft_lesson(outline, [document], CURRICULUM).blocks)


def test_lesson_three_presentation_has_phonics(document: SourceDocument) -> None:
    third = build_skeleton([document], CURRICULUM, unit_id="ta3.u1", lesson_no=3, period=1, grade=3)
    lesson = draft_lesson(third, [document], CURRICULUM)
    phonics = [b for b in blocks_of(lesson, 1) if isinstance(b, PhonicsBlock)]
    assert [p.content.sound for p in phonics] == ["/b/", "/h/"]
    assert phonics[1].content.words == ["hello", "hi"]
    assert phonics[1].content.chant_lines == ["Hello, I'm Ha.", "Hi, Ha. I'm Tien."]
    assert [c.span_id for c in phonics[1].sources] == ["p1.s4", "p1.s5"]
    assert phonics[0].provenance is Provenance.GENERATED
    assert not any(isinstance(b, DialogueBlock) for b in lesson.blocks.values())
    presentation = only(blocks_of(lesson, 1), ActivityBlock)
    assert set(presentation.depends_on) >= {p.id for p in phonics}
    summary = only(blocks_of(lesson, 4), SummaryBlock)
    assert "/h/: hello, hi" in summary.content.bullets


def test_template_drafter_section_directly(document: SourceDocument, outline: Outline) -> None:
    drafter = TemplateDrafter()
    draft = drafter.draft_section(build_context(outline, 1, [document], CURRICULUM))
    assert [b.kind for b in draft.blocks] == [
        BlockKind.DIALOGUE,
        BlockKind.VOCABULARY,
        BlockKind.SENTENCE_PATTERN,
        BlockKind.IMAGE_REF,
        BlockKind.ACTIVITY,
    ]
    assert draft.blocks[-1].depends_on == [0, 1, 2]
    assert reply_problems(draft, build_context(outline, 1, [document], CURRICULUM)) == []
    assert drafter.draft_section(build_context(outline, 4, [document], CURRICULUM)).blocks == []


def test_two_sources_are_listed(document: SourceDocument, outline: Outline) -> None:
    other = ingest(FIXTURES / "mini-unit-hello.docx")
    lesson = draft_lesson(outline, [document, other], CURRICULUM)
    assert [source.doc_id for source in lesson.sources] == [document.doc_id, other.doc_id]
    assert {c.doc_id for b in lesson.blocks.values() for c in b.sources} == {document.doc_id}


def test_empty_outline_is_rejected(outline: Outline) -> None:
    empty = Outline(
        title="Empty",
        grade=3,
        curriculum=CurriculumRef(version="gdpt-2018", unit_id="ta3.u1"),
        sections=[],
    )
    with pytest.raises(ValueError, match="no sections"):
        draft_lesson(empty, [], CURRICULUM)
    assert outline.sections


# --- model drafting ----------------------------------------------------------------------------


def test_model_drafter_maps_indices_and_citations(
    document: SourceDocument, outline: Outline
) -> None:
    provider = MockProvider(responses=list(SCRIPT))

    lesson = draft_lesson(outline, [document], CURRICULUM, provider)

    assert len(provider.calls) == 4
    assert Lesson.model_validate(lesson.model_dump(mode="json")) == lesson
    presentation = blocks_of(lesson, 1)
    assert [b.kind for b in presentation] == [BlockKind.DIALOGUE, BlockKind.ACTIVITY]
    dialogue, drilled = presentation
    assert isinstance(dialogue, DialogueBlock)
    assert isinstance(drilled, ActivityBlock)
    assert drilled.depends_on == [dialogue.id]
    assert drilled.teacher_note == "Use puppets."
    assert [c.span_id for c in drilled.sources] == ["p1.s2"]
    assert drilled.provenance is Provenance.DERIVED
    assert dialogue.content.context == "Ha meets Tien."
    assert [(c.doc_id, c.page, c.span_id, c.quote) for c in dialogue.sources] == [
        (document.doc_id, 1, "p1.s4", "Hello, I'm Ha."),
        (document.doc_id, 1, "p1.s5", "Hi, Ha. I'm Tien."),
    ]
    practice = only(blocks_of(lesson, 2), ActivityBlock)
    assert [c.span_id for c in practice.sources] == ["p1.s6", "p1.s7"]
    assert practice.provenance is Provenance.EXTRACTED
    assert practice.depends_on == []
    production = only(blocks_of(lesson, 3), ActivityBlock)
    assert production.provenance is Provenance.GENERATED
    assert production.sources == []
    assert only(blocks_of(lesson, 0), ActivityBlock).content.title == "Hello ball"
    assert len([b for b in blocks_of(lesson, 0) if isinstance(b, ObjectiveBlock)]) == 2
    summary = only(blocks_of(lesson, 4), SummaryBlock)
    assert summary.content.bullets[0] == UNIT_WORDS

    prompt = provider.calls[1]["messages"][0]["content"]
    assert "[p1.s4] Hello, I'm Ha." in prompt
    assert "[p1.s3] (image)" in prompt
    assert "WHAT THIS PHASE NEEDS" in prompt
    assert "Introduce the new language" in prompt
    assert "look_listen_repeat" in prompt
    assert "Hello, I'm ___." in prompt
    assert "ta3.u1.o1" in prompt
    assert "Language scope for grade 3" in prompt
    assert provider.calls[1]["json_schema"] is not None


def test_invalid_span_id_is_reprompted_then_template_fallback(
    document: SourceDocument, outline: Outline
) -> None:
    provider = MockProvider(handler=lambda messages: BAD_SPAN)

    lesson = draft_lesson(outline, [document], CURRICULUM, provider)

    assert len(provider.calls) == 8
    second = provider.calls[1]["messages"]
    assert [message["role"] for message in second] == ["user", "assistant", "user"]
    assert "p9.s9" in second[-1]["content"]
    assert Lesson.model_validate(lesson.model_dump(mode="json")) == lesson
    assert only(blocks_of(lesson, 1), DialogueBlock)
    for index in range(1, 4):
        assert len([b for b in blocks_of(lesson, index) if isinstance(b, ActivityBlock)]) == 1
    assert not any(
        isinstance(b, ActivityBlock) and b.content.title == "Ghost" for b in lesson.blocks.values()
    )


def test_unparsable_reply_is_repaired_once(document: SourceDocument, outline: Outline) -> None:
    provider = MockProvider(responses=["nonsense", *SCRIPT])
    lesson = draft_lesson(outline, [document], CURRICULUM, provider)
    assert len(provider.calls) == 5
    assert "not valid JSON" in provider.calls[1]["messages"][-1]["content"]
    assert only(blocks_of(lesson, 0), ActivityBlock).content.title == "Hello ball"


def test_model_failures_propagate(document: SourceDocument, outline: Outline) -> None:
    with pytest.raises(LLMError):
        draft_lesson(outline, [document], CURRICULUM, MockProvider(responses=[]))


def test_model_drafter_gives_up_after_two_bad_replies(
    document: SourceDocument, outline: Outline
) -> None:
    provider = MockProvider(responses=[BAD_SPAN, BAD_SPAN])
    context = build_context(outline, 2, [document], CURRICULUM)
    assert ModelDrafter(provider).draft_section(context) is None
    assert len(provider.calls) == 2


def test_reply_problems(document: SourceDocument, outline: Outline) -> None:
    context = build_context(outline, 1, [document], CURRICULUM)
    valid = DraftSection.model_validate(json.loads(SCRIPT[1]))
    assert reply_problems(valid, context) == []
    assert reply_problems(DraftSection(blocks=[]), context) == []

    def problems(*blocks: dict[str, Any]) -> str:
        return " ".join(reply_problems(DraftSection.model_validate({"blocks": blocks}), context))

    game = activity("game", "Chain")
    assert "p2.s2" in problems(block("activity", game, span_ids=["p2.s2"]))
    assert "out of range" in problems(block("activity", game, depends_on=[5]))
    assert "out of range" in problems(block("activity", game, depends_on=[0]))
    assert "cycle" in problems(
        block("activity", game, depends_on=[1]), block("dialogue", DIALOGUE, depends_on=[0])
    )
    assert "not valid vocabulary" in problems(block("vocabulary", {"bullets": []}))
    assert "not valid activity" in problems(block("activity", {**game, "minutes": 0}))


# --- provenance, dialogue shapes, citations ------------------------------------------------------


def test_model_warm_up_prompt_asks_for_no_new_language(
    document: SourceDocument, outline: Outline
) -> None:
    context = build_context(outline, 0, [document], CURRICULUM)
    prompt = ModelDrafter(MockProvider(responses=[])).prompt(context)
    assert "game or song" in prompt
    assert "no new words or patterns" in prompt


def test_template_activities_are_derived_and_warm_up_is_generated(template_lesson: Lesson) -> None:
    taught = {BlockKind.DIALOGUE, BlockKind.VOCABULARY, BlockKind.SENTENCE_PATTERN}
    for index in range(1, 4):
        drafted = only(blocks_of(template_lesson, index), ActivityBlock)
        assert drafted.provenance is Provenance.DERIVED
        assert any(template_lesson.blocks[t].kind in taught for t in drafted.depends_on)
        assert drafted.sources
    warm_up = only(blocks_of(template_lesson, 0), ActivityBlock)
    assert warm_up.provenance is Provenance.GENERATED


def test_model_activity_citing_the_instruction_without_dependencies_stays_extracted(
    document: SourceDocument, outline: Outline
) -> None:
    script = [
        SCRIPT[0],
        reply(block("activity", activity("look_listen_repeat", "Repeat"), span_ids=["p1.s2"])),
        SCRIPT[2],
        SCRIPT[3],
    ]
    lesson = draft_lesson(outline, [document], CURRICULUM, MockProvider(responses=script))
    drilled = only(blocks_of(lesson, 1), ActivityBlock)
    assert drilled.depends_on == []
    assert drilled.provenance is Provenance.EXTRACTED


def test_single_paragraph_dialogue_is_split_into_lines() -> None:
    notes = make_document(
        [
            ("list_item", "1. Look, listen and repeat."),
            ("paragraph", "Hello, I'm Ha.  Hi, Ha. I'm Tien."),
        ]
    )
    result = build_skeleton([notes], CURRICULUM, unit_id="ta3.u1", lesson_no=1, period=1, grade=3)
    context = build_context(result, 1, [notes], CURRICULUM)
    draft = TemplateDrafter().draft_section(context)
    assert draft.blocks[0].kind is BlockKind.DIALOGUE
    assert draft.blocks[0].span_ids == ["p1.s2"]
    assert [line["text"] for line in draft.blocks[0].content["lines"]] == [
        "Hello, I'm Ha.",
        "Hi, Ha.",
        "I'm Tien.",
    ]
    lesson = draft_lesson(result, [notes], CURRICULUM)
    dialogue = only(blocks_of(lesson, 1), DialogueBlock)
    assert [c.span_id for c in dialogue.sources] == ["p1.s2"]


def test_reply_problems_reject_engine_owned_kinds(
    document: SourceDocument, outline: Outline
) -> None:
    context = build_context(outline, 1, [document], CURRICULUM)
    objective = {"text": "Greet.", "outcome_ids": ["ta3.u9.o9"], "skills": ["speaking"]}
    found = reply_problems(
        DraftSection.model_validate({"blocks": [block("objective", objective)]}), context
    )
    assert any("'objective' may not be drafted" in problem for problem in found)
    found = reply_problems(
        DraftSection.model_validate({"blocks": [block("summary", {"bullets": ["x"]})]}), context
    )
    assert any("'summary' may not be drafted" in problem for problem in found)


VOCAB_WITH_IMAGE = {
    "items": [
        {
            "word": "hello",
            "ipa": None,
            "pos": "interjection",
            "meaning_vi": "xin chao",
            "example": "Hello, I'm Ha.",
            "image": {"span_id": "p1.s3"},
        }
    ]
}


def test_vocabulary_image_is_resolved_from_the_outline(
    document: SourceDocument, outline: Outline
) -> None:
    pattern = {
        "pattern": "Hello, I'm ___.",
        "function": "introducing oneself",
        "examples": ["Hello, I'm Ha."],
    }
    picture = {"caption": "Ha and Tien", "alt": "Two pupils"}
    script = [
        SCRIPT[0],
        reply(
            block("vocabulary", VOCAB_WITH_IMAGE, span_ids=["p1.s4"]),
            block("sentence_pattern", pattern, span_ids=["p1.s4"]),
            block("image_ref", picture, span_ids=["p1.s3"]),
            block(
                "activity",
                activity("look_listen_repeat", "Look, listen and repeat"),
                span_ids=["p1.s2"],
                depends_on=[0, 1],
            ),
        ),
        SCRIPT[2],
        SCRIPT[3],
    ]
    lesson = draft_lesson(outline, [document], CURRICULUM, MockProvider(responses=script))

    presentation = blocks_of(lesson, 1)
    assert [b.kind for b in presentation] == [
        BlockKind.VOCABULARY,
        BlockKind.SENTENCE_PATTERN,
        BlockKind.IMAGE_REF,
        BlockKind.ACTIVITY,
    ]
    image = only(presentation, VocabularyBlock).content.items[0].image
    assert image is not None
    assert (image.doc_id, image.page, image.span_id) == (document.doc_id, 1, "p1.s3")
    assert find_dangling_citations(lesson, {document.doc_id: document}) == []
    assert [c.span_id for c in only(presentation, ImageRefBlock).sources] == ["p1.s3"]
    assert [c.span_id for c in only(presentation, SentencePatternBlock).sources] == ["p1.s4"]
    assert only(presentation, ActivityBlock).provenance is Provenance.DERIVED


def test_vocabulary_image_outside_the_section_is_rejected(
    document: SourceDocument, outline: Outline
) -> None:
    context = build_context(outline, 1, [document], CURRICULUM)
    bogus = {
        "items": [{"word": "hello", "image": {"doc_id": "d_bogus", "page": 9, "span_id": "p9.s9"}}]
    }
    found = reply_problems(
        DraftSection.model_validate({"blocks": [block("vocabulary", bogus)]}), context
    )
    assert any("p9.s9" in problem for problem in found)
    ok = DraftSection.model_validate({"blocks": [block("vocabulary", VOCAB_WITH_IMAGE)]})
    assert reply_problems(ok, context) == []


def test_drafting_does_not_mutate_the_outline(document: SourceDocument, outline: Outline) -> None:
    before = outline.model_dump_json()
    draft_lesson(outline, [document], CURRICULUM)
    draft_lesson(outline, [document], CURRICULUM, MockProvider(responses=list(SCRIPT)))
    assert outline.model_dump_json() == before


def test_two_sources_with_clashing_span_ids_stay_distinct() -> None:
    first = make_document(
        [("paragraph", "Pupils look at the picture, listen and repeat the greetings.")], "d_aaaa"
    )
    second = make_document(
        [
            ("paragraph", "Listen and repeat the song words."),
            ("paragraph", "Hello, I'm Ha. Hi, Ha. I'm Tien."),
        ],
        "d_bbbb",
    )
    result = build_skeleton(
        [first, second], CURRICULUM, unit_id="ta3.u1", lesson_no=1, period=1, grade=3
    )
    context = build_context(result, 1, [first, second], CURRICULUM)
    assert context.qualified
    prompt = ModelDrafter(MockProvider(responses=[])).prompt(context)
    assert "[d_aaaa:p1.s1] Pupils look" in prompt
    assert "[d_bbbb:p1.s1] Listen and repeat" in prompt
    found = context.lookup("d_bbbb:p1.s1")
    assert found is not None
    assert found[0].doc_id == "d_bbbb"
    assert found[1].text == "Listen and repeat the song words."
    assert context.lookup("d_cccc:p1.s1") is None

    template = TemplateDrafter().draft_section(context)
    assert reply_problems(template, context) == []
    proposal = DraftSection(
        blocks=[
            DraftBlock(
                kind=BlockKind.ACTIVITY,
                content=activity("look_listen_repeat", "Song"),
                span_ids=["d_bbbb:p1.s1"],
            )
        ]
    )
    assert reply_problems(proposal, context) == []
    lesson = draft_lesson(result, [first, second], CURRICULUM)
    cited = {(c.doc_id, c.span_id) for b in lesson.blocks.values() for c in b.sources}
    assert {("d_aaaa", "p1.s1"), ("d_bbbb", "p1.s1")} <= cited
    sources = {first.doc_id: first, second.doc_id: second}
    assert find_dangling_citations(lesson, sources) == []


# --- copied lines, warm-up rules, the lesson-3 review ------------------------------------------


def test_the_song_carries_the_lyric_lines_verbatim(document: SourceDocument) -> None:
    """docs/planning.md, "Lyrics and options": song and chant lines are cited, not paraphrased."""
    lesson = draft_lesson(period_two(document), [document], CURRICULUM)
    song = only(blocks_of(lesson, 3), ActivityBlock)
    assert song.content.type is ActivityType.SONG
    assert song.content.instructions == "Sing together:"
    assert song.content.student_steps == [
        "Hello, hello, hello! I'm Ha. Hello, hello, hello! I'm Tien."
    ]
    assert [c.span_id for c in song.sources] == ["p2.s6", "p2.s7"]
    assert song.provenance is Provenance.DERIVED
    taught = {b.id for b in lesson.blocks.values() if b.kind in ("vocabulary", "sentence_pattern")}
    assert taught & set(song.depends_on)
    assert song.content.teacher_steps  # the teacher's side is still the template's


def test_options_and_gaps_are_copied_into_the_steps(document: SourceDocument) -> None:
    lesson = draft_lesson(period_two(document), [document], CURRICULUM)
    tick = only(blocks_of(lesson, 1), ActivityBlock)
    assert tick.content.student_steps == [
        "Listen. Tick: a. Hello, I'm Duc. b. Hi, I'm Lan.",
        "Check your answers with a friend.",
    ]
    assert [c.span_id for c in tick.sources] == ["p2.s2", "p2.s3"]
    complete = only(blocks_of(lesson, 2), ActivityBlock)
    assert complete.content.student_steps == [
        "Complete: Hello, I'm _____. Hi, _____. I'm _____.",
        "Read the sentences aloud.",
    ]
    assert [c.span_id for c in complete.sources] == ["p2.s4", "p2.s5"]


def test_the_chant_lines_are_one_step_each() -> None:
    notes = make_document(
        [
            ("list_item", "1. Listen and repeat."),
            ("paragraph", "Bill, bye, bye."),
            ("list_item", "2. Listen and tick."),
            ("paragraph", "a. Bill b. Ben"),
            ("list_item", "3. Let's chant."),
            ("paragraph", "Bye, Bill, bye!\nHello, Ben, hi!"),
            ("paragraph", "Bill, bye, bye."),
            ("image", ""),
        ]
    )
    third = build_skeleton([notes], CURRICULUM, unit_id="ta3.u1", lesson_no=3, period=1, grade=3)
    context = build_context(third, 3, [notes], CURRICULUM)
    chant = TemplateDrafter().draft_section(context).blocks[-1]
    assert chant.content["instructions"] == "Chant together:"
    assert chant.content["student_steps"] == [
        "Bye, Bill, bye!",
        "Hello, Ben, hi!",
        "Bill, bye, bye.",
    ]
    assert chant.span_ids == ["p1.s5", "p1.s6", "p1.s7"]
    assert reply_problems(DraftSection(blocks=[chant]), context) == []


def test_other_activities_keep_their_template_steps(
    document: SourceDocument, outline: Outline
) -> None:
    lesson = draft_lesson(outline, [document], CURRICULUM)
    practice = only(blocks_of(lesson, 2), ActivityBlock)
    assert practice.content.instructions.startswith("Listen and point.")
    assert practice.content.student_steps[0] == "Listen and point to the picture."


def test_the_lesson_three_review_includes_the_phonics_of_activity_one(
    document: SourceDocument,
) -> None:
    second = build_skeleton(
        [document], CURRICULUM, unit_id="ta3.u1", lesson_no=3, period=2, grade=3
    )
    lesson = draft_lesson(second, [document], CURRICULUM)
    warm_up = blocks_of(lesson, 0)
    phonics = only(warm_up, PhonicsBlock)
    assert (phonics.content.sound, phonics.content.words) == ("/h/", ["hello", "hi"])
    assert phonics.sources and phonics.provenance is Provenance.EXTRACTED
    game = only(warm_up, ActivityBlock)
    assert phonics.id in game.depends_on
    summary = only(blocks_of(lesson, len(lesson.sections) - 1), SummaryBlock)
    assert "/h/: hello, hi" in summary.content.bullets
    # lesson 1 has no phonics to review
    first = draft_lesson(period_two(document), [document], CURRICULUM)
    assert not any(isinstance(b, PhonicsBlock) for b in first.blocks.values())


def test_model_prompts_demand_warm_up_rules_and_copied_lines(
    document: SourceDocument, outline: Outline
) -> None:
    provider = MockProvider(responses=list(SCRIPT))
    draft_lesson(outline, [document], CURRICULUM, provider)
    warm_up = provider.calls[0]["messages"][0]["content"]
    assert "concrete rules in 3 to 5 student steps" in warm_up
    assert "who starts" in warm_up and "how the game ends" in warm_up
    assert "Never write a vague step" in warm_up
    for call in provider.calls:
        prompt = call["messages"][0]["content"]
        assert "Sing together:" in prompt and "Chant together:" in prompt
        assert "Listen. Tick: a." in prompt and "verbatim" in prompt
    production = provider.calls[3]["messages"][0]["content"]
    assert "copied word for word" in production
    review_provider = MockProvider(
        responses=[reply(block("activity", activity("game", "Chain")))] * 4
    )
    draft_lesson(period_two(document), [document], CURRICULUM, review_provider)
    assert (
        "concrete rules in 3 to 5 student steps"
        in review_provider.calls[0]["messages"][0]["content"]
    )
