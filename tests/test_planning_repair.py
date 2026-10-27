"""The one-round repair of model-written blocks that fail the level checks.

``docs/planning.md``, "Repair round": one compact prompt per offending block, the engine accepts
a reply only when the offending words strictly decrease, at most eight blocks, one round.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from classdeck.curriculum import load_curriculum
from classdeck.english.validators import GRAMMAR_SCOPE, VOCAB_LEVEL, allowed_forms, run_all
from classdeck.ingest import ingest
from classdeck.lesson import (
    ActivityBlock,
    Block,
    BlockAdapter,
    CheckStatus,
    DialogueBlock,
    Lesson,
    Phase,
    Provenance,
    SentencePatternBlock,
    SourceDocument,
    VocabularyBlock,
)
from classdeck.llm import LLMError, MockProvider
from classdeck.planning import (
    MAX_REPAIR_BLOCKS,
    RepairReport,
    build_skeleton,
    draft_lesson,
    repair_lesson,
)
from classdeck.planning.repair import offences, offending_blocks, pupil_content, repair_prompt

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "eval" / "fixtures" / "public"
CURRICULUM = load_curriculum(ROOT / "curriculum", "gdpt-2018", 3)

OFFENDING_INSTRUCTIONS = "Salute the contestant with Hello or Hi. Respond with Hello or Hi."
OFFENDING_STEPS = ["Listen to the rules.", "Salute the contestant.", "Respond with Hello or Hi."]
CLEAN_INSTRUCTIONS = "Say Hello or Hi to your partner. Listen to your partner."
CLEAN_STEPS = [
    "Listen to the rules.",
    "Say Hello or Hi to your partner.",
    "Listen to your partner.",
]


@pytest.fixture(scope="module")
def document() -> SourceDocument:
    return ingest(FIXTURES / "mini-unit-hello.pdf")


@pytest.fixture(scope="module")
def sources(document: SourceDocument) -> dict[str, SourceDocument]:
    return {document.doc_id: document}


def checked(lesson: Lesson, sources: dict[str, SourceDocument]) -> Lesson:
    return run_all(lesson, CURRICULUM, sources=sources)


def warm_up_game(lesson: Lesson) -> ActivityBlock:
    blocks = [lesson.blocks[block_id] for block_id in lesson.sections[0].block_ids]
    return next(block for block in blocks if isinstance(block, ActivityBlock))


@pytest.fixture
def lesson(document: SourceDocument, sources: dict[str, SourceDocument]) -> Lesson:
    """A checked period-1 lesson whose warm-up game uses words outside the grade list.

    The game plays the part of a model-written block; the tests pass its id as
    ``model_block_ids``.
    """
    outline = build_skeleton(
        [document], CURRICULUM, unit_id="ta3.u1", lesson_no=1, period=1, grade=3
    )
    drafted = draft_lesson(outline, [document], CURRICULUM)
    game = warm_up_game(drafted)
    game.content.instructions = OFFENDING_INSTRUCTIONS
    game.content.student_steps = list(OFFENDING_STEPS)
    game.teacher_note = "Keep the ball moving."
    return checked(drafted, sources)


def warn_detail(block: Block, name: str) -> str | None:
    for check in block.checks:
        if check.name == name and check.status is CheckStatus.WARN:
            return check.detail
    return None


def reply_for(block: Block, **content: Any) -> str:
    """The model's reply: the same block with some content fields changed."""
    data = block.model_dump(mode="json")
    return json.dumps(
        {"id": block.id, "kind": block.kind, "content": {**data["content"], **content}}
    )


def fixed(block: Block, **extra: Any) -> str:
    return reply_for(
        block, instructions=CLEAN_INSTRUCTIONS, student_steps=list(CLEAN_STEPS), **extra
    )


def prompt_of(provider: MockProvider, index: int = 0) -> str:
    return provider.calls[index]["messages"][0]["content"]


# --- candidates and offences -------------------------------------------------------------------


def test_the_fixture_game_is_flagged_by_vocab_level(lesson: Lesson) -> None:
    game = warm_up_game(lesson)
    detail = warn_detail(game, VOCAB_LEVEL)
    assert detail is not None
    assert detail.endswith("contestant, respond, salute")


def test_candidates_are_model_written_blocks_with_a_level_warning(
    lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    game = warm_up_game(lesson)
    assert [b.id for b in offending_blocks(lesson, {game.id})] == [game.id]
    # a template block with the same warning is not a candidate; a clean model block neither
    assert offending_blocks(lesson, set()) == []
    clean = set(lesson.blocks) - {game.id}
    assert offending_blocks(lesson, clean) == []
    allowed = allowed_forms(lesson, CURRICULUM)
    assert offences(game, allowed, CURRICULUM.forbidden_rules(), sources) == [
        "contestant",
        "respond",
        "salute",
    ]


def test_nothing_offending_means_no_call(
    lesson: Lesson, sources: dict[str, SourceDocument], caplog: pytest.LogCaptureFixture
) -> None:
    provider = MockProvider(responses=[])
    with caplog.at_level(logging.INFO):
        result, report = repair_lesson(
            lesson, CURRICULUM, provider, sources=sources, model_block_ids=set()
        )
    assert provider.calls == []
    assert report == RepairReport()
    assert report.summary() == "repair: 0 offending model block(s), 0 sent, 0 replaced, 0 kept"
    assert report.summary() in caplog.text
    assert result.model_dump(mode="json") == lesson.model_dump(mode="json")


# --- the prompt --------------------------------------------------------------------------------


def test_prompt_carries_the_block_the_offenders_and_the_allowed_words(
    lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    game = warm_up_game(lesson)
    prompt = repair_prompt(game, lesson, CURRICULUM, sources)
    assert f'"id": "{game.id}"' in prompt
    assert '"kind": "activity"' in prompt
    assert OFFENDING_INSTRUCTIONS in prompt
    assert "not in the grade word list or cited spans: contestant, respond, salute" in prompt
    assert "may use only these words, pupils' names, and numbers" in prompt
    rest = prompt.partition("ALLOWED WORDS")[2]
    words = rest.splitlines()[2].split(", ")
    assert words == sorted(words)
    assert {"hello", "hi", "partner", "listen", "swap"} <= set(words)
    assert "Language scope for grade 3" in prompt
    assert 'same "id" and the same "kind"' in prompt
    assert "teacher_steps" in prompt  # teacher-facing text may use any English
    assert "Keep the ball moving." not in prompt  # the teacher note is not part of the task


def test_prompt_lists_the_names_of_the_cited_spans(
    lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    dialogue = next(b for b in lesson.blocks.values() if b.kind == "dialogue")
    prompt = repair_prompt(dialogue, lesson, CURRICULUM, sources)
    assert "also allowed: Ha, Tien" in prompt
    game = warm_up_game(lesson)  # cites nothing
    assert "also allowed: (none)" in repair_prompt(game, lesson, CURRICULUM, sources)


# --- accepting and dropping replies ------------------------------------------------------------


def test_offending_block_is_replaced_when_the_reply_removes_the_words(
    lesson: Lesson, sources: dict[str, SourceDocument], caplog: pytest.LogCaptureFixture
) -> None:
    game = warm_up_game(lesson)
    provider = MockProvider(responses=[fixed(game)])
    before = lesson.model_dump(mode="json")

    with caplog.at_level(logging.INFO):
        result, report = repair_lesson(
            lesson, CURRICULUM, provider, sources=sources, model_block_ids={game.id}
        )

    assert report.candidates == report.sent == report.replaced == [game.id]
    assert report.kept == {}
    assert "1 replaced, 0 kept" in caplog.text
    assert len(provider.calls) == 1
    assert provider.calls[0]["temperature"] == 0.0
    assert provider.calls[0]["json_schema"] is not None
    prompt = prompt_of(provider)
    assert "salute" in prompt and "contestant" in prompt and "respond" in prompt
    assert "ALLOWED WORDS" in prompt
    repaired = result.blocks[game.id]
    assert isinstance(repaired, ActivityBlock)
    assert repaired.content.instructions == CLEAN_INSTRUCTIONS
    assert repaired.content.student_steps == CLEAN_STEPS
    # nothing but the content changed
    assert repaired.content.teacher_steps == game.content.teacher_steps
    assert repaired.sources == game.sources
    assert repaired.provenance is game.provenance
    assert repaired.depends_on == game.depends_on
    assert repaired.teacher_note == "Keep the ball moving."
    assert repaired.checks == game.checks  # the caller re-runs the checks
    assert lesson.model_dump(mode="json") == before  # the input is untouched
    rechecked = checked(result, sources)
    assert warn_detail(rechecked.blocks[game.id], VOCAB_LEVEL) is None
    assert Lesson.model_validate(rechecked.model_dump(mode="json")) == rechecked


def test_only_the_pupil_facing_text_of_a_reply_is_taken(
    lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    game = warm_up_game(lesson)
    wild = fixed(
        game,
        title="A different game",
        type="song",
        skills=["writing"],
        interaction="individual",
        minutes=30,
        teacher_steps=["Ignore the plan."],
        materials=["a ball"],
        competency_tags=[],
    )
    result, report = repair_lesson(
        lesson,
        CURRICULUM,
        MockProvider(responses=[wild]),
        sources=sources,
        model_block_ids={game.id},
    )
    assert report.replaced == [game.id]
    repaired = result.blocks[game.id]
    assert isinstance(repaired, ActivityBlock)
    assert repaired.content.instructions == CLEAN_INSTRUCTIONS
    assert repaired.content.student_steps == CLEAN_STEPS
    # everything else of the content is the original's
    assert repaired.content.model_dump(exclude={"instructions", "student_steps"}) == (
        game.content.model_dump(exclude={"instructions", "student_steps"})
    )


def vocabulary_with_offence(lesson: Lesson, sources: dict[str, SourceDocument]) -> Lesson:
    """A copy of the lesson whose presentation vocabulary has an off-list example."""
    flagged = lesson.model_copy(deep=True)
    block = next(b for b in flagged.blocks.values() if isinstance(b, VocabularyBlock))
    block.provenance = Provenance.GENERATED  # stands for a block the model wrote from nothing
    block.sources = []
    block.content.items[0].example = "Salute the contestant."
    block.content.items[0].meaning_vi = "xin chao"
    return checked(flagged, sources)


def test_vocabulary_repair_keeps_the_items_glosses_and_ipa(
    lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    flagged = vocabulary_with_offence(lesson, sources)
    block = next(b for b in flagged.blocks.values() if isinstance(b, VocabularyBlock))
    first = block.content.items[0]
    items = [
        {**item.model_dump(mode="json"), "example": "Say Hello.", "meaning_vi": "changed"}
        for item in block.content.items
    ]
    items[0]["word"] = first.word.upper()
    result, report = repair_lesson(
        flagged,
        CURRICULUM,
        MockProvider(responses=[reply_for(block, items=items)]),
        sources=sources,
        model_block_ids={block.id},
    )
    assert report.replaced == [block.id]
    repaired = result.blocks[block.id]
    assert isinstance(repaired, VocabularyBlock)
    assert repaired.content.items[0].example == "Say Hello."
    assert [item.word for item in repaired.content.items] == [i.word for i in block.content.items]
    assert repaired.content.items[0].meaning_vi == "xin chao"  # the gloss is not the model's
    assert repaired.content.items[0].ipa == first.ipa


def test_reply_that_changes_the_number_of_items_is_dropped(
    lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    flagged = vocabulary_with_offence(lesson, sources)
    block = next(b for b in flagged.blocks.values() if isinstance(b, VocabularyBlock))
    fewer = reply_for(block, items=[block.content.items[0].model_dump(mode="json")])
    _, report = repair_lesson(
        flagged,
        CURRICULUM,
        MockProvider(responses=[fewer]),
        sources=sources,
        model_block_ids={block.id},
    )
    assert report.replaced == []
    assert report.kept[block.id] == "the reply changed the number of vocabulary items"


def test_a_reply_that_trades_words_for_grammar_hits_is_dropped(
    lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    game = warm_up_game(lesson)
    assert offences(game, allowed_forms(lesson, CURRICULUM), CURRICULUM.forbidden_rules(), sources)
    trade = reply_for(
        game,
        instructions="Say Hello to your partner. I have played.",
        student_steps=["Listen to the rules.", "Say Hello. I have played.", "Say Hello or Hi."],
    )
    result, report = repair_lesson(
        lesson,
        CURRICULUM,
        MockProvider(responses=[trade]),
        sources=sources,
        model_block_ids={game.id},
    )
    assert report.replaced == []
    assert report.kept == {game.id: "the reply trades words for new grammar hits"}
    assert result.blocks[game.id].model_dump() == game.model_dump()


def test_a_partial_improvement_is_accepted(
    lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    game = warm_up_game(lesson)
    partial = reply_for(
        game,
        instructions="Salute your partner with Hello or Hi. Respond with Hello or Hi.",
        student_steps=["Listen to the rules.", "Salute your partner.", "Respond with Hello."],
    )
    result, report = repair_lesson(
        lesson,
        CURRICULUM,
        MockProvider(responses=[partial]),
        sources=sources,
        model_block_ids={game.id},
    )
    assert report.replaced == [game.id]
    rechecked = checked(result, sources)
    assert warn_detail(rechecked.blocks[game.id], VOCAB_LEVEL) == (
        "not in the grade word list or cited spans: respond, salute"
    )


def test_reply_that_keeps_the_words_is_dropped(
    lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    game = warm_up_game(lesson)
    same = reply_for(game, instructions="Greet the player and reply with Hello or Hi.")
    result, report = repair_lesson(
        lesson,
        CURRICULUM,
        MockProvider(responses=[same]),
        sources=sources,
        model_block_ids={game.id},
    )
    assert report.replaced == []
    assert report.kept == {game.id: "still 3 offending word(s) or hit(s) (was 3)"}
    assert result.blocks[game.id].model_dump() == game.model_dump()


@pytest.mark.parametrize(("field", "value"), [("kind", "dialogue"), ("id", "b_other")])
def test_reply_that_changes_the_id_or_kind_is_dropped(
    lesson: Lesson, sources: dict[str, SourceDocument], field: str, value: str
) -> None:
    game = warm_up_game(lesson)
    data = json.loads(fixed(game))
    data[field] = value
    result, report = repair_lesson(
        lesson,
        CURRICULUM,
        MockProvider(responses=[json.dumps(data)]),
        sources=sources,
        model_block_ids={game.id},
    )
    assert report.replaced == []
    assert "changed the id or kind" in report.kept[game.id]
    assert result.blocks[game.id].model_dump() == game.model_dump()


def test_reply_with_invalid_content_is_dropped(
    lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    game = warm_up_game(lesson)
    broken = reply_for(
        game,
        instructions="Say Hello to your partner. Listen to your partner.",
        student_steps="Say hello to your partner.",
    )
    result, report = repair_lesson(
        lesson,
        CURRICULUM,
        MockProvider(responses=[broken]),
        sources=sources,
        model_block_ids={game.id},
    )
    assert report.replaced == []
    assert report.kept[game.id].startswith("content is not valid activity:")
    assert result.blocks[game.id].model_dump() == game.model_dump()


def test_reply_that_drops_the_steps_is_dropped(
    lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    game = warm_up_game(lesson)
    empty = reply_for(game, instructions=CLEAN_INSTRUCTIONS, student_steps=[])
    result, report = repair_lesson(
        lesson,
        CURRICULUM,
        MockProvider(responses=[empty]),
        sources=sources,
        model_block_ids={game.id},
    )
    assert report.replaced == []
    assert report.kept == {game.id: "the reply dropped the activity's skills or steps"}
    assert result.blocks[game.id].model_dump() == game.model_dump()


def test_unparsable_reply_is_dropped_without_a_retry(
    lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    game = warm_up_game(lesson)
    provider = MockProvider(responses=["I cannot help with that."])
    result, report = repair_lesson(
        lesson, CURRICULUM, provider, sources=sources, model_block_ids={game.id}
    )
    assert len(provider.calls) == 1  # one round, no re-prompt
    assert report.replaced == []
    assert report.kept[game.id].startswith("reply was not a block:")
    assert result.blocks[game.id].model_dump() == game.model_dump()


def test_model_failures_propagate(lesson: Lesson, sources: dict[str, SourceDocument]) -> None:
    game = warm_up_game(lesson)
    with pytest.raises(LLMError):
        repair_lesson(
            lesson,
            CURRICULUM,
            MockProvider(responses=[]),
            sources=sources,
            model_block_ids={game.id},
        )


def test_grammar_hits_count_as_offences(lesson: Lesson, sources: dict[str, SourceDocument]) -> None:
    game = warm_up_game(lesson)
    game.content.instructions = "Say Hello. I have played."
    game.content.student_steps = ["Say Hello."]
    flagged = checked(lesson, sources)
    game = warm_up_game(flagged)
    assert warn_detail(game, VOCAB_LEVEL) is None  # "played" lemmatises to the listed "play"
    assert "perfect" in (warn_detail(game, GRAMMAR_SCOPE) or "")
    provider = MockProvider(responses=[reply_for(game, instructions="Say Hello. I play.")])
    result, report = repair_lesson(
        flagged, CURRICULUM, provider, sources=sources, model_block_ids={game.id}
    )
    assert "perfect" in prompt_of(provider)
    assert report.replaced == [game.id]
    rechecked = checked(result, sources)
    assert warn_detail(rechecked.blocks[game.id], GRAMMAR_SCOPE) is None


# --- limits ------------------------------------------------------------------------------------


def with_many_offending_activities(lesson: Lesson, count: int) -> tuple[Lesson, list[str]]:
    """``count`` extra model-style activities with off-list words in the production section."""
    result = lesson.model_copy(deep=True)
    section = next(s for s in result.sections if s.phase is Phase.PRODUCTION)
    ids: list[str] = []
    for index in range(count):
        block_id = f"b_extra{index:02d}"
        result.blocks[block_id] = BlockAdapter.validate_python(
            {
                "id": block_id,
                "kind": "activity",
                "content": {
                    "type": "game",
                    "title": f"Extra game {index}",
                    "skills": ["speaking"],
                    "interaction": "pairs",
                    "minutes": 2,
                    "instructions": OFFENDING_INSTRUCTIONS,
                    "teacher_steps": ["Run the game."],
                    "student_steps": list(OFFENDING_STEPS),
                },
            }
        )
        section.block_ids.append(block_id)
        ids.append(block_id)
    return result, ids


def test_at_most_eight_blocks_are_sent_in_lesson_order(
    lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    many, ids = with_many_offending_activities(lesson, 10)
    many = checked(many, sources)

    def handler(messages: list[Any]) -> str:
        text = messages[0].content
        block_id = next(i for i in ids if f'"id": "{i}"' in text)
        return fixed(many.blocks[block_id])

    provider = MockProvider(handler=handler)
    result, report = repair_lesson(
        many, CURRICULUM, provider, sources=sources, model_block_ids=set(ids)
    )
    assert MAX_REPAIR_BLOCKS == 8
    assert len(provider.calls) == 8
    assert report.candidates == ids
    assert report.sent == report.replaced == ids[:8]
    assert report.kept == {}
    for block_id in ids[:8]:
        assert result.blocks[block_id].content.instructions == CLEAN_INSTRUCTIONS
    for block_id in ids[8:]:
        assert result.blocks[block_id].content.instructions == OFFENDING_INSTRUCTIONS
    assert report.summary() == "repair: 10 offending model block(s), 8 sent, 8 replaced, 0 kept"


def test_max_blocks_can_be_lowered(lesson: Lesson, sources: dict[str, SourceDocument]) -> None:
    many, ids = with_many_offending_activities(lesson, 3)
    many = checked(many, sources)
    provider = MockProvider(handler=lambda messages: "not json")
    _, report = repair_lesson(
        many, CURRICULUM, provider, sources=sources, model_block_ids=set(ids), max_blocks=1
    )
    assert len(provider.calls) == 1
    assert report.sent == ids[:1]
    assert list(report.kept) == ids[:1]


# --- cited text and concrete steps -------------------------------------------------------------


def test_a_reply_that_cuts_a_step_to_a_vague_fragment_is_dropped(
    lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    game = warm_up_game(lesson)
    vague = reply_for(
        game,
        instructions=CLEAN_INSTRUCTIONS,
        student_steps=["Listen to the rules.", "Say Hello to your partner.", "Play."],
    )
    result, report = repair_lesson(
        lesson,
        CURRICULUM,
        MockProvider(responses=[vague]),
        sources=sources,
        model_block_ids={game.id},
    )
    assert report.replaced == []
    assert report.kept == {game.id: "the reply cut student step 3 to less than half its words"}
    assert result.blocks[game.id].model_dump() == game.model_dump()


def test_a_reply_that_cuts_the_instructions_in_half_is_dropped(
    lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    game = warm_up_game(lesson)
    short = reply_for(game, instructions="Play the game.", student_steps=list(CLEAN_STEPS))
    _, report = repair_lesson(
        lesson,
        CURRICULUM,
        MockProvider(responses=[short]),
        sources=sources,
        model_block_ids={game.id},
    )
    assert report.replaced == []
    assert "instructions to less than half" in report.kept[game.id]


def test_a_reply_that_changes_the_number_of_steps_is_dropped(
    lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    game = warm_up_game(lesson)
    merged = reply_for(
        game,
        instructions=CLEAN_INSTRUCTIONS,
        student_steps=["Say Hello or Hi to your partner. Listen to your partner."],
    )
    _, report = repair_lesson(
        lesson,
        CURRICULUM,
        MockProvider(responses=[merged]),
        sources=sources,
        model_block_ids={game.id},
    )
    assert report.replaced == []
    assert report.kept[game.id] == "the reply changed the number of student steps (3 to 1)"


def test_extracted_blocks_are_never_candidates(
    lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    game = warm_up_game(lesson)
    assert [b.id for b in offending_blocks(lesson, {game.id})] == [game.id]
    quoted = lesson.model_copy(deep=True)
    quoted.blocks[game.id].provenance = Provenance.EXTRACTED
    assert offending_blocks(quoted, {game.id}) == []
    provider = MockProvider(responses=[fixed(game)])
    result, report = repair_lesson(
        quoted, CURRICULUM, provider, sources=sources, model_block_ids={game.id}
    )
    assert provider.calls == [] and report.candidates == []
    assert result.blocks[game.id].model_dump() == quoted.blocks[game.id].model_dump()


def test_cited_text_is_not_taken_from_a_reply(lesson: Lesson) -> None:
    """Vocabulary words, pattern text and cited dialogue lines stay as the source has them."""
    vocabulary = next(b for b in lesson.blocks.values() if isinstance(b, VocabularyBlock))
    dialogue = next(b for b in lesson.blocks.values() if isinstance(b, DialogueBlock))
    pattern = next(b for b in lesson.blocks.values() if isinstance(b, SentencePatternBlock))
    assert dialogue.sources and vocabulary.sources and pattern.sources

    # an extracted block is not rewritten at all
    for block in (vocabulary, dialogue, pattern):
        content, reason = pupil_content(block, block.model_dump(mode="json")["content"])
        assert content is None and "source" in reason

    # a block grounding found unsupported but that still carries citations: the lines stay
    cited = dialogue.model_copy(update={"provenance": Provenance.GENERATED})
    lines = [
        {**line, "text": "I am Ha."} for line in cited.model_dump(mode="json")["content"]["lines"]
    ]
    content, reason = pupil_content(cited, {"lines": lines})
    assert content is None and "citations" in reason

    # a generated vocabulary block takes the example, never the word
    made_up = vocabulary.model_copy(update={"provenance": Provenance.GENERATED, "sources": []})
    data = made_up.model_dump(mode="json")["content"]
    items = [{**item, "word": "HELLO!", "example": "Say Hello."} for item in data["items"]]
    content, _ = pupil_content(made_up, {"items": items})
    assert content is not None
    assert [i["word"] for i in content["items"]] == [i["word"] for i in data["items"]]
    assert all(i["example"] == "Say Hello." for i in content["items"])

    # a generated pattern block takes the examples, never the pattern
    made_up_pattern = pattern.model_copy(update={"provenance": Provenance.GENERATED})
    content, _ = pupil_content(
        made_up_pattern, {"pattern": "Hi, I am ___.", "examples": ["Hi, I am Duc."]}
    )
    assert content is not None
    assert content["pattern"] == pattern.content.pattern
    assert content["examples"] == ["Hi, I am Duc."]

    # a derived summary is not the model's to reword
    summary = next(b for b in lesson.blocks.values() if b.kind == "summary")
    assert pupil_content(summary, {"bullets": ["x"]})[0] is None


def test_the_repair_prompt_demands_concrete_steps(
    lesson: Lesson, sources: dict[str, SourceDocument]
) -> None:
    game = warm_up_game(lesson)
    prompt = repair_prompt(game, lesson, CURRICULUM, sources)
    assert "never replace a concrete step by a vague one" in prompt
    assert "name the object, the action and who speaks" in prompt
    assert "Play the game" in prompt
