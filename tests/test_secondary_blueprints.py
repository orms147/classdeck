"""Lesson blueprints of a secondary unit: one plan per textbook lesson type.

Every page is self-written (a made-up unit about a town park), laid out the way a scanned page
reads after the vision model.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from classdeck.curriculum import load_curriculum
from classdeck.curriculum.loader import Curriculum
from classdeck.lesson import Page, Phase, SourceDocument, Span, SpanKind
from classdeck.lesson.labels import lesson_label_for
from classdeck.llm import MockProvider
from classdeck.planning.blueprints import BLUEPRINTS, blueprint_for, blueprint_prompt
from classdeck.planning.secondary import (
    SecondaryDraft,
    SecondaryPlan,
    build_prompt,
    chunk_lesson,
    draft_secondary_lesson,
    plan_lesson,
    reply_problems,
    split_lessons,
)

CURRICULUM_ROOT = Path(__file__).resolve().parent.parent / "curriculum"
UNIT = "ta9.u1"
BLANK = re.compile(r"_{2,}")


def doc(*pages: list[tuple[Any, ...]]) -> SourceDocument:
    """A document from ``(kind, text[, extras])`` rows; extras: ``highlights`` / ``track``."""
    built: list[Page] = []
    for number, rows in enumerate(pages, start=1):
        spans = []
        for order, row in enumerate(rows, start=1):
            kind, text, *rest = row
            extra = rest[0] if rest else {}
            spans.append(
                Span(
                    span_id=f"p{number}.s{order}",
                    kind=SpanKind(kind),
                    text=text,
                    order=order,
                    image_path=f"d/p{number}_img{order}.png" if kind == "image" else None,
                    **extra,
                )
            )
        built.append(Page(number=number, spans=spans))
    return SourceDocument(
        doc_id="d_test0002",
        filename="unit.pdf",
        sha256="1" * 64,
        media_type="application/pdf",
        pages=built,
    )


@pytest.fixture(scope="module")
def curriculum() -> Curriculum:
    return load_curriculum(CURRICULUM_ROOT, "gdpt-2018", 9)


def is_timer_frame(block: Any) -> bool:
    return block.kind == "activity" and block.content.materials == ["timer"]


def plan_of(document: SourceDocument, curriculum: Curriculum, lesson: int) -> SecondaryPlan:
    return plan_lesson([document], curriculum, unit_id=UNIT, lesson_no=lesson)


def phase_of(plan: SecondaryPlan) -> dict[str, Phase]:
    return {e.id: e.phase for e in plan.exercises}


def fake_reply(plan: SecondaryPlan, **extra: Any) -> dict[str, Any]:
    """A reply that passes validation for the plan's exercises."""
    entries: list[dict[str, Any]] = []
    for e in plan.exercises:
        entry: dict[str, Any] = {"exercise": e.id, "steps": ["Do it.", "Check it."], "minutes": 3}
        c = e.content
        if e.kind == "gap_fill":
            box = c.get("word_box") or ["word"]
            entry["gaps"] = [[box[0]] * len(BLANK.findall(i["text"])) for i in c["items"]]
        elif e.kind == "match":
            entry["letters"] = [chr(97 + n) for n in range(len(c["left"]))]
        elif e.kind == "choice":
            entry["letters"] = ["a"] * len(c["items"])
        elif e.kind == "true_false":
            entry["truths"] = [True] * len(c["items"])
        elif e.kind == "grid":
            blanks = sum(len(BLANK.findall(cell)) for row in c["rows"] for cell in row)
            entry["answers"] = ["x"] * blanks
        entries.append(entry)
    phase = {
        "title": "A short task",
        "instructions": "Talk to a partner.",
        "teacher_steps": ["Show.", "Ask."],
        "student_steps": ["Look.", "Talk."],
        "interaction": "pairs",
        "skills": ["speaking"],
    }
    reply: dict[str, Any] = {
        "exercises": entries,
        "vocabulary": [],
        "warm_up": phase,
        "wrap_up": phase,
        "homework": "Read again.",
        "summary": ["Key idea one.", "Key idea two."],
    }
    if plan.needs_production:
        reply["production"] = phase
    reply.update(extra)
    return reply


def draft(
    document: SourceDocument, curriculum: Curriculum, lesson: int, **extra: Any
) -> tuple[Any, Any]:
    plan = plan_of(document, curriculum, lesson)
    reply = json.dumps(fake_reply(plan, **extra))
    provider = MockProvider(handler=lambda _messages: reply)
    return draft_secondary_lesson(
        [document], curriculum, unit_id=UNIT, lesson_no=lesson, provider=provider
    )


# --- the blueprints ----------------------------------------------------------------------------


def test_there_is_one_blueprint_per_lesson_type() -> None:
    assert sorted(BLUEPRINTS) == [1, 2, 3, 4, 5, 6, 7]
    for number, blueprint in BLUEPRINTS.items():
        assert blueprint.number == number and blueprint.stages and blueprint.budget[0] < 20
        assert sum(blueprint.weights.values()) >= 40
    assert blueprint_for(7).name == "Looking Back & Project"
    text = blueprint_prompt(blueprint_for(6))
    assert "writing" in text and "model paragraph" in text and "You must not" in text


def test_lesson_labels_name_the_type() -> None:
    assert lesson_label_for(9, 7, None) == "Grade 9 · Lesson 7 · Looking Back & Project"
    assert lesson_label_for(3, 2, 1) == "Grade 3 · Lesson 2 · Period 1"


def test_only_the_new_word_lessons_ask_for_vocabulary(curriculum: Curriculum) -> None:
    page = [("heading", "SKILLS 2"), ("instruction", "1. Talk."), ("list_item", "1. Why?")]
    prompt = build_prompt(plan_of(doc(page), curriculum, 6), curriculum)
    assert "not needed for this kind of lesson" in prompt
    assert "writing_frame" in prompt and "model_paragraph" in prompt
    getting_started = [("heading", "GETTING STARTED"), ("instruction", "1. Talk.")]
    prompt = build_prompt(plan_of(doc(getting_started), curriculum, 1), curriculum)
    assert '"vocabulary": the new or key words' in prompt


# --- looking back and project ------------------------------------------------------------------

LOOKING_BACK = [
    ("heading", "LOOKING BACK"),
    ("heading", "Vocabulary"),
    ("instruction", "1. Write a word for each description."),
    ("list_item", "1. This person bakes bread."),
    ("list_item", "2. This person drives a bus."),
    ("instruction", "2. Circle the correct word to complete each sentence."),
    ("list_item", "1. The park is (big / small) and green."),
    ("list_item", "2. We (play / plays) football there."),
    ("heading", "PROJECT"),
    ("heading", "A better park"),
    ("image", "A park with a fountain"),
    ("image", "A park with a playground"),
    ("paragraph", "Work in groups."),
    ("instruction", "1. Interview some pupils about the park. Use the questions below."),
    ("list_item", "1. Do you go to the park?"),
    ("list_item", "2. What do you like there?"),
    ("instruction", "2. Collect the answers. Use the table below."),
    ("table", "Pupil | Question 1 | Question 2"),
    ("instruction", "3. Report to your class."),
    ("list_item", "1. How many pupils did you ask?"),
    ("table", "Now I can ... | ✓ | ✓✓ | ✓✓✓"),
    ("list_item", "use the words about the park."),
    ("list_item", "ask and answer about places."),
]


def test_looking_back_reviews_then_the_project_produces(curriculum: Curriculum) -> None:
    plan = plan_of(doc(LOOKING_BACK), curriculum, 7)
    assert [(e.kind, e.phase.value) for e in plan.exercises] == [
        ("question_set", "practice"),
        ("choice", "practice"),
        ("gallery", "production"),
        ("project", "production"),
        ("question_set", "production"),
        ("grid", "production"),
        ("question_set", "production"),
        ("self_check", "production"),
    ]
    choice = plan.exercises[1].content["items"]
    assert choice[0]["stem"] == "The park is ___ and green." and choice[0]["options"] == [
        "big",
        "small",
    ]
    project = plan.exercises[3].content
    assert project["title"] == "A better park" and project["instructions"] == "Work in groups."
    assert project["student_steps"][1] == "Collect the answers. Use the table below."
    grid = plan.exercises[5].content
    assert grid["header"] == ["Pupil", "Question 1", "Question 2"] and grid["blank_rows"] == 4
    assert plan.exercises[7].content["items"][0] == "use the words about the park."


def test_project_lesson_builds_gallery_grid_and_self_check(curriculum: Curriculum) -> None:
    lesson, report = draft(doc(LOOKING_BACK), curriculum, 7)
    assert not report.problems
    kinds = [lesson.blocks[b].kind for s in lesson.sections for b in s.block_ids]
    assert kinds.count("gallery") == 1 and "grid" in kinds and kinds[-6:-3] != ["gallery"] * 3
    gallery = next(b for b in lesson.blocks.values() if b.kind == "gallery")
    assert len(gallery.content.images) == 2 and gallery.content.title == "A better park"
    check = next(b for b in lesson.blocks.values() if b.kind == "self_check")
    assert len(check.content.items) == 2 and check.content.scale == ["✓", "✓✓", "✓✓✓"]
    assert not any(b.kind == "vocabulary" for b in lesson.blocks.values())
    production = next(s for s in lesson.sections if s.phase is Phase.PRODUCTION)
    ends = [lesson.blocks[b].kind for b in production.block_ids]
    assert ends[0] == "gallery" and ends[-1] == "self_check"
    summary = next(b for b in lesson.blocks.values() if b.kind == "summary")
    assert summary.content.bullets == ["Key idea one.", "Key idea two."]


# --- a reading and its matching exercise -------------------------------------------------------

SKILLS_1 = [
    ("heading", "SKILLS 1"),
    ("heading", "Reading"),
    ("instruction", "1. Work in pairs. Answer the questions."),
    ("list_item", "1. What can you see in each picture?"),
    ("caption", "a"),
    ("image", "A bowl of rice"),
    ("caption", "b"),
    ("image", "A clay pot"),
    (
        "instruction",
        "2. Read the leaflet about two villages. Match each highlighted word with its meaning.",
    ),
    ("image", "Green rice flakes"),
    (
        "paragraph",
        "Hill village is famous for rice flakes. People preserve the old way of making them "
        "and the fragrance is lovely.",
    ),
    ("image", "Clay pots"),
    (
        "paragraph",
        "River village makes clay pots. The original function of the pots was to keep food, "
        "and people still use them.",
    ),
    (
        "table",
        "1. preserve | a. to keep something as it is\n2. fragrance | b. a pleasant smell\n"
        "3. function | c. a special purpose",
    ),
    ("instruction", "3. Read the leaflet again. Decide which village each detail belongs to."),
    (
        "table",
        "In which village ...? | Hill | River\n1. people make food there\n"
        "2. people make pots there",
    ),
    ("heading", "Speaking"),
    ("instruction", "4. Work in pairs. Ask and answer about a speciality."),
    ("list_item", "1. What is it?"),
    ("list_item", "2. Where is it made?"),
    ("instruction", "5. Work in groups. Give a short presentation about it."),
    ("heading", "Example:"),
    ("paragraph", "I live in Tay Ho village. My village is famous for conical hats."),
]


def test_a_reading_is_split_from_its_matching_exercise(curriculum: Curriculum) -> None:
    plan = plan_of(doc(SKILLS_1), curriculum, 5)
    kinds = {e.id: (e.kind, e.phase.value) for e in plan.exercises}
    assert kinds["e1"] == ("gallery", "presentation")
    assert kinds["e2"] == ("reading", "presentation") and kinds["e2m"] == ("match", "practice")
    assert kinds["e3"] == ("choice", "practice")
    assert kinds["e4"] == ("question_set", "production")
    assert kinds["e5"] == ("activity", "production") and kinds["e5x"][1] == "production"
    reading = next(e for e in plan.exercises if e.id == "e2")
    assert reading.instruction == "Read the leaflet about two villages."
    assert next(e for e in plan.exercises if e.id == "e2m").instruction == (
        "Match each highlighted word with its meaning."
    )
    assert [p.get("image", {}).get("span_id") for p in reading.content["paragraphs"]] == [
        "p1.s10",
        "p1.s12",
    ]
    gallery = next(e for e in plan.exercises if e.id == "e1").content
    assert [i["label"] for i in gallery["images"]] == ["a", "b"]
    assert gallery["questions"] == ["What can you see in each picture?"]
    choice = next(e for e in plan.exercises if e.id == "e3").content["items"]
    assert choice[0]["options"] == ["Hill", "River"]


def test_the_reading_highlights_the_matched_words_with_the_books_definitions(
    curriculum: Curriculum,
) -> None:
    lesson, report = draft(
        doc(SKILLS_1),
        curriculum,
        5,
        speaking_frame={
            "title": "Say it",
            "instructions": "Use the starters.",
            "starters": ["My speciality is ...", "People make it ...", "It is famous for ..."],
            "teacher_steps": ["Model it.", "Time one minute."],
        },
    )
    assert not report.problems and not report.fallback_extras
    reading = next(b for b in lesson.blocks.values() if b.kind == "reading")
    assert [p.highlight for p in reading.content.paragraphs] == [
        ["preserve", "fragrance"],
        ["function"],
    ]
    assert reading.content.glossary["fragrance"] == "a pleasant smell"
    frame = next(b for b in lesson.blocks.values() if is_timer_frame(b))
    assert frame.content.student_steps[0] == "My speciality is ..."
    assert frame.provenance == "generated"
    production = next(s for s in lesson.sections if s.phase is Phase.PRODUCTION)
    assert lesson.blocks[production.block_ids[-1]].content.title == "Say it"


def test_a_missing_frame_is_reported_and_left_out(curriculum: Curriculum) -> None:
    lesson, report = draft(doc(SKILLS_1), curriculum, 5)
    assert report.fallback_extras == ["speaking_frame"]
    assert "speaking_frame" in report.problems
    assert not any(is_timer_frame(b) for b in lesson.blocks.values())


# --- skills 2: listening and writing -----------------------------------------------------------

SKILLS_2 = [
    ("heading", "SKILLS 2"),
    ("heading", "Listening"),
    ("instruction", "1. Work in pairs. Discuss the questions.", {"track": None}),
    ("list_item", "1. Who can you see?"),
    ("image", "A cleaner in orange"),
    ("instruction", "2. Listen and fill in each blank with ONE word.", {"track": 5}),
    ("list_item", "1. The job is ___."),
    ("list_item", "2. He is tall and ___."),
    ("heading", "Writing"),
    ("instruction", "3. Work in pairs. Answer the questions."),
    ("list_item", "- What is his job?"),
    ("instruction", "4. Write a paragraph about your favourite helper."),
    ("paragraph", "Start your paragraph with:"),
    ("paragraph", "My favourite helper is ___"),
    ("image", "A lined writing area"),
]


def test_listening_then_writing(curriculum: Curriculum) -> None:
    plan = plan_of(doc(SKILLS_2), curriculum, 6)
    assert [(e.kind, e.phase.value) for e in plan.exercises] == [
        ("gallery", "presentation"),
        ("gap_fill", "practice"),
        ("question_set", "production"),
        ("activity", "production"),
    ]
    assert plan.exercises[1].track == 5 and plan.exercises[1].listening
    writing = plan.exercises[3].content
    assert "Start your paragraph with:" in writing["student_steps"]
    assert "My favourite helper is ___" in writing["student_steps"]


def test_writing_has_a_frame_and_a_model_paragraph(curriculum: Curriculum) -> None:
    frame = {
        "title": "Plan it",
        "instructions": "Use the frame.",
        "starters": ["He is ...", "I like him because ..."],
        "linking_words": ["and", "but", "because", "so"],
        "word_count": 100,
        "teacher_steps": ["Show the frame.", "Collect ideas."],
    }
    text = (
        "My favourite helper is Mr Vinh. He is a cleaner and he is tall and kind. "
        "He works hard every morning because he wants our street to be clean. "
        "He is friendly but he is also very busy. I like him so much, and I thank him."
    )
    lesson, report = draft(
        doc(SKILLS_2),
        curriculum,
        6,
        writing_frame=frame,
        model_paragraph={"title": "Model paragraph", "text": text},
    )
    assert not report.problems
    production = next(s for s in lesson.sections if s.phase is Phase.PRODUCTION)
    kinds = [lesson.blocks[b].kind for b in production.block_ids]
    assert kinds[-2:] == ["activity", "reading"]
    model = lesson.blocks[production.block_ids[-1]]
    assert model.provenance == "generated" and model.content.title == "Model paragraph"
    assert "and" in model.content.paragraphs[0].highlight
    writing_frame = lesson.blocks[production.block_ids[-2]]
    assert writing_frame.content.student_steps[-2:] == [
        "Use linking words: and, but, because, so.",
        "Write about 100 words.",
    ]
    summary = next(b for b in lesson.blocks.values() if b.kind == "summary")
    assert summary.content.bullets == ["Key idea one.", "Key idea two."]


def test_the_audio_track_goes_into_the_teacher_notes(curriculum: Curriculum) -> None:
    lesson, _ = draft(doc(SKILLS_2), curriculum, 6)
    gap = next(b for b in lesson.blocks.values() if b.kind == "gap_fill")
    assert "1. Play track 5." in gap.teacher_note
    assert "check them against the recording" in gap.teacher_note


def test_unusable_extras_are_named_in_the_problems(curriculum: Curriculum) -> None:
    plan = plan_of(doc(SKILLS_2), curriculum, 6)
    reply = fake_reply(
        plan, writing_frame={"instructions": "x"}, model_paragraph={"text": "Short."}
    )
    problems = reply_problems(SecondaryDraft.model_validate(reply), plan)
    assert set(problems) == {"writing_frame", "model_paragraph"}


# --- grammar and communication -----------------------------------------------------------------

CLOSER_LOOK_2 = [
    ("heading", "A CLOSER LOOK 2"),
    ("heading", "Grammar"),
    ("heading", "Question words before to-infinitives"),
    ("heading", "Remember!"),
    ("list_item", "\N{EN DASH} We use a question word before a to-infinitive."),
    ("heading", "Example:"),
    ("paragraph", "We don't know what to do."),
    ("instruction", "1. Fill in each blank with a question word."),
    ("list_item", "1. I don't know ___ to go."),
    ("instruction", "2. Rewrite the sentences."),
    ("list_item", "1. I don't know how I can get there."),
    ("heading", "Phrasal verbs (1)"),
    ("heading", "Remember!"),
    ("list_item", "\N{EN DASH} A phrasal verb has a verb and a particle."),
    ("heading", "Example:"),
    ("paragraph", "go out = leave your house"),
    ("instruction", "3. Match each phrasal verb with its meaning."),
    ("table", "1. go out | a. leave the house\n2. run out of | b. have no more of"),
    ("instruction", "4. GAME Find someone who ..."),
    ("instruction", "Ask your friends the questions. Write their names."),
    ("table", "| Name\n1. Do you go out at weekends? |\n2. Do you run out of time? |"),
]


def test_grammar_rules_keep_their_examples_and_open_each_point(curriculum: Curriculum) -> None:
    plan = plan_of(doc(CLOSER_LOOK_2), curriculum, 3)
    assert [(e.kind, e.phase.value) for e in plan.exercises] == [
        ("rule_box", "presentation"),
        ("gap_fill", "practice"),
        ("question_set", "practice"),
        ("rule_box", "practice"),
        ("match", "practice"),
        ("grid", "production"),
    ]
    rule = plan.exercises[0].content
    assert rule["title"] == "Remember! Question words before to-infinitives"
    assert rule["points"] == ["We use a question word before a to-infinitive."]
    assert rule["examples"] == ["We don't know what to do."]
    grid = plan.exercises[5].content
    assert grid["header"] == ["", "Name"] and len(grid["rows"]) == 2


def test_the_summary_recaps_each_rule_with_one_example(curriculum: Curriculum) -> None:
    lesson, _ = draft(doc(CLOSER_LOOK_2), curriculum, 3)
    summary = next(b for b in lesson.blocks.values() if b.kind == "summary")
    assert summary.content.bullets == [
        "Question words before to-infinitives: We don't know what to do.",
        "Phrasal verbs (1): go out = leave your house",
    ]


COMMUNICATION = [
    ("heading", "COMMUNICATION"),
    ("heading", "Everyday English"),
    ("heading", "Seeking help"),
    ("instruction", "1. Listen and read. Pay attention to the highlighted parts.", {"track": 3}),
    ("paragraph", "Mai: Could you help me, please?", {"highlights": ["Could you help me"]}),
    ("paragraph", "Tom: Sure, no problem.", {"highlights": ["no problem"]}),
    ("instruction", "2. Work in pairs. Ask for help in these situations."),
    ("list_item", "1. You want to borrow a pen."),
    ("heading", "Places of interest"),
    ("instruction", "3. Do you know the place in each picture?"),
    ("caption", "a"),
    ("image", "A tall tower"),
    ("caption", "b"),
    ("image", "A round theatre"),
    ("instruction", "4. Work in pairs. Ask and answer about your favourite place."),
    ("list_item", "- What is it?"),
    ("list_item", "- Why do you like it?"),
]


def test_communication_keeps_the_highlighted_phrases(curriculum: Curriculum) -> None:
    plan = plan_of(doc(COMMUNICATION), curriculum, 4)
    assert [(e.kind, e.phase.value) for e in plan.exercises] == [
        ("dialogue", "presentation"),
        ("question_set", "practice"),
        ("gallery", "practice"),
        ("question_set", "production"),
    ]
    assert plan.exercises[0].content["highlights"] == ["Could you help me", "no problem"]
    assert plan.exercises[0].track == 3
    lesson, report = draft(doc(COMMUNICATION), curriculum, 4)
    assert not report.problems
    dialogue = next(b for b in lesson.blocks.values() if b.kind == "dialogue")
    assert dialogue.content.highlights == ["Could you help me", "no problem"]
    assert "1. Play track 3." in dialogue.teacher_note
    summary = next(b for b in lesson.blocks.values() if b.kind == "summary")
    assert summary.content.bullets == ["Could you help me", "no problem"]


def test_a_text_grid_listening_table_needs_one_answer_per_blank(curriculum: Curriculum) -> None:
    page = [
        ("heading", "COMMUNICATION"),
        ("instruction", "1. Listen and fill in each blank with a number."),
        ("table", "Place | Distance\nPark | (1) ___ km\nLake | (2) ___ km"),
    ]
    plan = plan_of(doc(page), curriculum, 4)
    (grid,) = plan.exercises
    assert grid.kind == "grid" and grid.needs_answers and grid.phase is Phase.PRACTICE
    lesson, _ = draft(doc(page), curriculum, 4)
    block = next(b for b in lesson.blocks.values() if b.kind == "grid")
    assert block.content.answers == ["x", "x"]


# --- getting started and a closer look 1 -------------------------------------------------------


def test_chunks_of_the_project_are_marked(curriculum: Curriculum) -> None:
    chunks = chunk_lesson(split_lessons([doc(LOOKING_BACK)])[7])
    assert [c.project for c in chunks] == [False, False, True, True, True, True]
    assert chunks[-1].self_check and chunks[2].prelude


def test_pictures_no_exercise_uses_are_the_warm_up_prompt_of_getting_started(
    curriculum: Curriculum,
) -> None:
    page = [
        ("heading", "GETTING STARTED"),
        ("instruction", "1. Listen and read."),
        ("paragraph", "Lan: Hi, Nam!"),
        ("paragraph", "Nam: Hi, Lan!"),
        ("instruction", "2. Match each word with its meaning."),
        ("table", "1. park | a. green place\n2. lake | b. water place"),
        ("image", "A park in the morning"),
    ]
    lesson, _ = draft(doc(page), curriculum, 1)
    warm = lesson.sections[0]
    kinds = [lesson.blocks[b].kind for b in warm.block_ids]
    assert kinds[-2:] == ["gallery", "activity"]
    assert not any(b.kind == "image_ref" for b in lesson.blocks.values())


class _Flaky:
    """Fails ``failures`` times with an ``LLMError`` and then answers."""

    name, model = "flaky", "flaky"

    def __init__(self, failures: int, reply: str, status: int | None = None) -> None:
        self.failures, self.reply, self.status, self.calls = failures, reply, status, 0

    def complete(self, messages: list, **kwargs: Any):
        from classdeck.llm import LLMError, LLMResponse
        from classdeck.llm.base import Usage

        self.calls += 1
        if self.calls <= self.failures:
            raise LLMError("Connection error.", status=self.status)
        return LLMResponse(text=self.reply, model="flaky", usage=Usage())


def test_the_drafting_call_survives_a_network_blip(
    curriculum: Curriculum, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("classdeck.planning.secondary.time.sleep", lambda _s: None)
    page = [("heading", "GETTING STARTED"), ("instruction", "1. Talk.")]
    d = doc(page)
    reply = json.dumps(fake_reply(plan_of(d, curriculum, 1)))
    flaky = _Flaky(2, reply)
    _lesson, report = draft_secondary_lesson(
        [d], curriculum, unit_id=UNIT, lesson_no=1, provider=flaky
    )
    assert flaky.calls == 3 and report.model_called and not report.error
    rejected = _Flaky(5, reply, status=401)
    with pytest.raises(Exception, match="Connection error"):
        draft_secondary_lesson([d], curriculum, unit_id=UNIT, lesson_no=1, provider=rejected)
    assert rejected.calls == 1


def test_options_set_in_two_columns_are_the_options_of_the_item_above(
    curriculum: Curriculum,
) -> None:
    page = [
        ("heading", "LOOKING BACK"),
        ("instruction", "1. Choose the correct answer A, B, C, or D."),
        ("list_item", "1. You should decide ___ to move."),
        ("table", "A. while | B. when\nC. where | D. who"),
        ("list_item", "2. Do you know ___ to sort rubbish?"),
        ("table", "A. who | B. which\nC. how | D. what"),
    ]
    (choice,) = plan_of(doc(page), curriculum, 7).exercises
    assert choice.kind == "choice"
    assert [i["options"] for i in choice.content["items"]] == [
        ["while", "when", "where", "who"],
        ["who", "which", "how", "what"],
    ]


def test_the_example_label_may_be_read_as_a_paragraph(curriculum: Curriculum) -> None:
    page = [
        ("heading", "A CLOSER LOOK 2"),
        ("heading", "Remember!"),
        ("list_item", "We use a question word before a to-infinitive."),
        ("paragraph", "Example:"),
        ("paragraph", "We don't know what to do."),
        ("instruction", "1. Fill in each blank."),
        ("list_item", "1. I don't know ___ to go."),
    ]
    rule = plan_of(doc(page), curriculum, 3).exercises[0].content
    assert rule["points"] == ["We use a question word before a to-infinitive."]
    assert rule["examples"] == ["We don't know what to do."]
