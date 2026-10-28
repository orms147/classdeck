"""Secondary lessons (grades 6-9): segmentation, exercises, skeleton, model reply, fallback.

Every page here is self-written: a made-up unit about a town park, laid out the way a scanned
textbook page reads after the vision model (headings, ``instruction`` spans, numbered items).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from classdeck.curriculum import load_curriculum
from classdeck.curriculum.loader import Curriculum
from classdeck.english.validators import run_all, words_from_unit
from classdeck.lesson import (
    ActivityBlock,
    Lesson,
    Page,
    Phase,
    SourceDocument,
    Span,
    SpanKind,
)
from classdeck.llm import MockProvider
from classdeck.pipeline import build_secondary_lesson
from classdeck.planning.secondary import (
    SecondaryDraft,
    build_prompt,
    chunk_lesson,
    draft_secondary_lesson,
    occurs_in,
    plan_lesson,
    reply_problems,
    section_lesson,
    split_lessons,
)

CURRICULUM_ROOT = Path(__file__).resolve().parent.parent / "curriculum"
UNIT = "ta9.u1"

PAGE_1: list[tuple[str, str]] = [
    ("heading", "Unit 1 LOCAL COMMUNITY"),
    ("heading", "THIS UNIT INCLUDES:"),
    ("list_item", "Community helpers"),
    ("heading", "GETTING STARTED"),
    ("heading", "A day at the park."),
    ("instruction", "1. Listen and read."),
    ("image", "Two children walking in a park"),
    ("paragraph", "Lan: Hi, Nam. Where are you going?"),
    ("paragraph", "Nam: I'm going to the park near our school."),
    ("paragraph", "Lan: What do you do there?"),
    ("paragraph", "Nam: I usually play football with my friends. It's a lovely place."),
]
PAGE_2: list[tuple[str, str]] = [
    ("instruction", "2. Read the conversation again. Fill in each blank with ONE word."),
    ("list_item", "1. Nam is going to the ___ near his school."),
    ("list_item", "2. He plays ___ with his friends."),
    ("instruction", "3. Match each word with its meaning."),
    (
        "table",
        "1. park | a. a green place where people walk and play\n"
        "2. lovely | b. very nice\n"
        "3. usually | c. most of the time",
    ),
    ("instruction", "4. Choose the correct answer."),
    ("list_item", "1. Where is Nam going?"),
    ("list_item", "A. to school"),
    ("list_item", "B. to the park"),
    ("list_item", "C. home"),
    ("list_item", "2. What does Nam play? A. tennis B. football"),
    ("instruction", "5. Read the sentences and write T (true) or F (false)."),
    ("list_item", "1. Nam is going home."),
    ("list_item", "2. Lan asks a question."),
    ("instruction", "6. QUIZ What is the place?"),
    ("instruction", "Do the quiz."),
    ("list_item", "1. People swim there."),
    ("list_item", "2. People borrow books there."),
    ("heading", "What other places do you know?"),
    ("image", "A library reading room"),
]
PAGE_3: list[tuple[str, str]] = [
    ("heading", "A CLOSER LOOK 1"),
    ("heading", "Vocabulary"),
    ("instruction", "1. Match the helpers with their jobs."),
    ("table", "1. baker | a. makes bread\n2. driver | b. drives a bus"),
]


def make_document(*pages: list[tuple[str, str]], doc_id: str = "d_test0001") -> SourceDocument:
    built: list[Page] = []
    for number, items in enumerate(pages, start=1):
        spans = [
            Span(
                span_id=f"p{number}.s{order}",
                kind=SpanKind(kind),
                text=text,
                order=order,
                image_path=f"{doc_id}/p{number}_img{order}.png" if kind == "image" else None,
            )
            for order, (kind, text) in enumerate(items, start=1)
        ]
        built.append(Page(number=number, spans=spans))
    return SourceDocument(
        doc_id=doc_id,
        filename="unit.pdf",
        sha256="0" * 64,
        media_type="application/pdf",
        pages=built,
    )


@pytest.fixture(scope="module")
def curriculum() -> Curriculum:
    return load_curriculum(CURRICULUM_ROOT, "gdpt-2018", 9)


@pytest.fixture
def document() -> SourceDocument:
    return make_document(PAGE_1, PAGE_2, PAGE_3)


# --- sections ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "number"),
    [
        ("GETTING STARTED", 1),
        ("A CLOSER LOOK 1", 2),
        ("A Closer Look 2", 3),
        ("COMMUNICATION", 4),
        ("SKILLS 1", 5),
        ("Skills 2", 6),
        ("LOOKING BACK", 7),
        ("LOOKING BACK & PROJECT", 7),
        ("PROJECT", 7),
        ("Vocabulary", None),
        ("THIS UNIT INCLUDES:", None),
        ("Unit 1 LOCAL COMMUNITY", None),
    ],
)
def test_section_banners(text: str, number: int | None) -> None:
    assert section_lesson(text) == number


def test_lessons_are_cut_by_their_banners_across_pages(document: SourceDocument) -> None:
    lessons = split_lessons([document])
    assert sorted(lessons) == [1, 2]
    first = [ref.span_id for ref, _span in lessons[1].entries]
    assert first[0] == "p1.s5" and first[-1] == "p2.s20"  # the unit opener is not in lesson 1
    assert all(ref.span_id != "p3.s1" for ref, _span in lessons[1].entries)
    assert lessons[2].entries[0][1].text == "Vocabulary"


def test_looking_back_and_project_are_one_lesson() -> None:
    document = make_document(
        [("heading", "LOOKING BACK"), ("instruction", "1. Do it."), ("heading", "PROJECT")],
        [("instruction", "2. Make a poster.")],
    )
    lessons = split_lessons([document])
    assert list(lessons) == [7]
    assert [span.text for _ref, span in lessons[7].entries] == ["1. Do it.", "2. Make a poster."]


def test_each_instruction_starts_an_exercise(document: SourceDocument) -> None:
    chunks = chunk_lesson(split_lessons([document])[1])
    assert [chunk.number for chunk in chunks] == ["1", "2", "3", "4", "5", "6"]
    assert chunks[0].heading == "A day at the park."
    assert chunks[5].instruction == "QUIZ What is the place? Do the quiz."  # a continuation line
    assert [span.text for _ref, span in chunks[5].follow_ups] == ["What other places do you know?"]


def test_a_sub_heading_closes_the_exercise_and_titles_the_next() -> None:
    document = make_document(
        [
            ("heading", "A CLOSER LOOK 1"),
            ("heading", "Vocabulary"),
            ("instruction", "1. Match the words."),
            ("list_item", "1. cat"),
            ("heading", "Pronunciation"),
            ("instruction", "2. Listen and repeat."),
        ]
    )
    chunks = chunk_lesson(split_lessons([document])[2])
    assert [(c.number, c.heading) for c in chunks] == [("1", "Vocabulary"), ("2", "Pronunciation")]


# --- exercises ---------------------------------------------------------------------------------


def exercises_of(document: SourceDocument, curriculum: Curriculum, lesson_no: int = 1):
    return plan_lesson([document], curriculum, unit_id=UNIT, lesson_no=lesson_no).exercises


def test_exercise_kinds_follow_the_spans(document: SourceDocument, curriculum: Curriculum) -> None:
    exercises = exercises_of(document, curriculum)
    assert [e.kind for e in exercises] == [
        "dialogue",
        "gap_fill",
        "match",
        "choice",
        "true_false",
        "question_set",
        "gallery",  # the lone picture beside the follow-up question is shown with it
    ]
    assert [e.id for e in exercises] == ["e1", "e2", "e3", "e4", "e5", "e6", "e6a"]


def test_dialogue_keeps_the_wording_and_cites_the_lines(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    dialogue = exercises_of(document, curriculum)[0]
    assert dialogue.content["lines"][1] == {
        "speaker": "Nam",
        "text": "I'm going to the park near our school.",
    }
    assert [ref.span_id for ref in dialogue.refs] == ["p1.s6", "p1.s8", "p1.s9", "p1.s10", "p1.s11"]
    assert [span.text for _ref, span in dialogue.before] == ["Two children walking in a park"]
    assert dialogue.after == []


def test_gap_fill_match_choice_true_false_content(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    gap, match, choice, true_false = exercises_of(document, curriculum)[1:5]
    assert gap.instruction == "Read the conversation again. Fill in each blank with ONE word."
    assert [i["text"] for i in gap.content["items"]] == [
        "Nam is going to the ___ near his school.",
        "He plays ___ with his friends.",
    ]
    assert match.content["left"] == ["park", "lovely", "usually"]
    assert match.content["right"][1] == "very nice"
    assert choice.content["items"][0] == {
        "stem": "Where is Nam going?",
        "options": ["to school", "to the park", "home"],
        "answer": "to school",
    }
    assert choice.content["items"][1]["options"] == ["tennis", "football"]  # inline options
    assert [i["statement"] for i in true_false.content["items"]] == [
        "Nam is going home.",
        "Lan asks a question.",
    ]


def test_word_box_and_rule_box_and_activity(curriculum: Curriculum) -> None:
    document = make_document(
        [
            ("heading", "A CLOSER LOOK 2"),
            ("heading", "Remember!"),
            ("paragraph", "Use a question word before to + verb."),
            ("list_item", "I know where to go."),
            ("instruction", "1. Fill in each blank with a word from the box."),
            ("list_item", "bakers"),
            ("list_item", "drivers"),
            ("list_item", "nurses"),
            ("list_item", "1. The ___ make bread."),
            ("instruction", "2. Work in pairs. Talk about your street."),
        ]
    )
    rule, gap, activity = exercises_of(document, curriculum, lesson_no=3)
    assert rule.kind == "rule_box" and rule.phase is Phase.PRESENTATION
    assert rule.content["title"] == "Remember!"
    assert rule.content["points"] == ["Use a question word before to + verb."]
    assert rule.content["examples"] == ["I know where to go."]
    assert gap.content["word_box"] == ["bakers", "drivers", "nurses"]
    assert activity.kind == "activity" and activity.phase is Phase.PRODUCTION
    assert activity.content["instructions"] == "Work in pairs. Talk about your street."
    assert activity.content["interaction"] == "pairs"


def test_reading_text_with_a_title_and_a_picture(curriculum: Curriculum) -> None:
    document = make_document(
        [
            ("heading", "SKILLS 1"),
            ("instruction", "1. Read the text about a market."),
            ("heading", "A market on the river"),
            ("image", "A floating market"),
            (
                "paragraph",
                "Every morning small boats bring fruit and vegetables to the market. "
                "Buyers walk from boat to boat and choose what they need.",
            ),
        ]
    )
    (reading,) = exercises_of(document, curriculum, lesson_no=5)
    assert reading.kind == "reading"
    assert reading.content["title"] == "A market on the river"
    assert reading.content["image"]["span_id"] == "p1.s4"
    assert reading.content["paragraphs"][0]["image"]["span_id"] == "p1.s4"


def test_phases_never_go_backwards(document: SourceDocument, curriculum: Curriculum) -> None:
    plan = plan_lesson([document], curriculum, unit_id=UNIT, lesson_no=1)
    phases = [e.phase for e in plan.exercises]
    assert phases == [
        Phase.PRESENTATION,
        Phase.PRACTICE,
        Phase.PRACTICE,
        Phase.PRACTICE,
        Phase.PRACTICE,
        Phase.PRODUCTION,
        Phase.PRODUCTION,
    ]


def test_missing_banner_and_bad_lesson_number_are_reported(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    with pytest.raises(ValueError, match="COMMUNICATION banner"):
        plan_lesson([document], curriculum, unit_id=UNIT, lesson_no=4)
    with pytest.raises(ValueError, match="1-7"):
        plan_lesson([document], curriculum, unit_id=UNIT, lesson_no=8)
    with pytest.raises(KeyError):
        plan_lesson([document], curriculum, unit_id="ta9.u99", lesson_no=1)


def test_skeleton_adds_up_to_45_minutes(document: SourceDocument, curriculum: Curriculum) -> None:
    plan = plan_lesson([document], curriculum, unit_id=UNIT, lesson_no=1)
    minutes = plan.minutes()
    assert sum(minutes.values()) == 45
    assert minutes == {
        Phase.WARM_UP: 5,
        Phase.PRESENTATION: 12,
        Phase.PRACTICE: 15,
        Phase.PRODUCTION: 9,
        Phase.WRAP_UP: 4,
    }
    # A lesson with no production exercise shares those minutes out; it still adds up.
    short = make_document([("heading", "GETTING STARTED"), ("instruction", "1. Do this.")])
    only = plan_lesson([short], curriculum, unit_id=UNIT, lesson_no=1)
    assert sum(only.minutes(vocabulary=False).values()) == 45
    assert Phase.PRESENTATION not in only.minutes(vocabulary=False)


# --- the model reply ---------------------------------------------------------------------------


def good_reply() -> dict[str, Any]:
    return {
        "exercises": [
            {"exercise": "e1", "steps": ["Show the picture.", "Play the audio."], "minutes": 6},
            {"exercise": "e2", "gaps": [["park"], ["football"]], "steps": ["Fill in alone."]},
            {"exercise": "e3", "letters": ["a", "b", "c"], "minutes": 4},
            {"exercise": "e4", "letters": ["B", "B"]},
            {"exercise": "e5", "truths": [False, True]},
            {"exercise": "e6", "answers": ["a pool", "a library"], "minutes": 4},
            {"exercise": "e6a", "answers": [], "minutes": 3},
        ],
        "vocabulary": [
            {
                "word": "park",
                "ipa": "/park/",
                "pos": "n",
                "meaning_vi": "công viên",
                "example": "I'm going to the park near our school.",
                "span": "p1.s9",
            },
            {
                "word": "lovely",
                "ipa": "/luvli/",
                "pos": "adj",
                "meaning_vi": "đáng yêu",
                "span": "p1.s11",
            },
        ],
        "warm_up": {
            "title": "Park or no park",
            "instructions": "Say what you can do in a park.",
            "teacher_steps": ["Draw a tree on the board.", "Ask: Where do people play?"],
            "student_steps": ["Look at the board.", "Tell your partner one thing."],
            "interaction": "pairs",
            "skills": ["speaking"],
        },
        "wrap_up": {
            "title": "Say it together",
            "instructions": "Say the new words.",
            "teacher_steps": ["Show the words.", "Pupils repeat."],
            "student_steps": ["Repeat the words.", "Tell your partner one new word."],
        },
        "homework": "Write two sentences about a park.",
    }


def provider_with(*replies: dict[str, Any]) -> MockProvider:
    return MockProvider([json.dumps(reply) for reply in replies])


def test_model_reply_fills_answers_vocabulary_and_the_teacher_layer(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    provider = provider_with(good_reply())
    lesson, report = draft_secondary_lesson(
        [document], curriculum, unit_id=UNIT, lesson_no=1, provider=provider
    )
    assert len(provider.calls) == 1 and not report.reprompted and not report.problems
    assert [s.phase for s in lesson.sections] == list(Phase)
    assert sum(s.minutes for s in lesson.sections) == 45
    blocks = {b.kind: b for b in lesson.ordered_blocks()}
    gap = blocks["gap_fill"].content
    assert [i.answers for i in gap.items] == [["park"], ["football"]]
    assert gap.exercise_no == "2" and gap.instruction.startswith("Read the conversation again")
    assert blocks["match"].content.answer == {0: 0, 1: 1, 2: 2}
    assert [i.answer for i in blocks["choice"].content.items] == ["to the park", "football"]
    assert [i.answer for i in blocks["true_false"].content.items] == [False, True]
    assert "About" in lesson.blocks[lesson.sections[1].block_ids[1]].teacher_note
    vocabulary = blocks["vocabulary"]
    assert [i.word for i in vocabulary.content.items] == ["park", "lovely"]
    assert {c.span_id for c in vocabulary.sources} == {"p1.s9", "p1.s11"}
    # the vocabulary follows the dialogue inside the presentation
    presentation = lesson.sections[1]
    assert [lesson.blocks[b].kind for b in presentation.block_ids] == ["dialogue", "vocabulary"]
    # the picture of the page that no exercise uses is the picture prompt of the warm-up
    opener = [lesson.blocks[b].kind for b in lesson.sections[0].block_ids]
    assert opener[-2:] == ["gallery", "activity"]
    warm_up = lesson.blocks[lesson.sections[0].block_ids[-1]]
    assert isinstance(warm_up, ActivityBlock) and warm_up.provenance == "generated"
    assert warm_up.content.minutes == 5
    assert lesson.sections[-1].title == "Wrap-up and homework"
    assert lesson.blocks[lesson.sections[-1].block_ids[-1]].content.text.startswith("Homework:")
    assert lesson.curriculum.lesson_no == 1 and lesson.curriculum.period is None


def test_an_invalid_first_reply_is_reprompted_once(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    bad = good_reply()
    bad["exercises"][1]["gaps"] = [["park"]]  # one answer for two sentences
    provider = provider_with(bad, good_reply())
    lesson, report = draft_secondary_lesson(
        [document], curriculum, unit_id=UNIT, lesson_no=1, provider=provider
    )
    assert len(provider.calls) == 2 and report.reprompted and not report.problems
    prompt = provider.calls[1]["messages"][-1]["content"]
    assert "e2" in prompt and "2 entries" in prompt
    assert any(b.kind == "gap_fill" for b in lesson.blocks.values())


def test_a_reply_that_stays_wrong_falls_back_part_by_part(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    bad = good_reply()
    bad["exercises"][1]["gaps"] = [["park"]]
    bad["exercises"][2]["letters"] = ["a", "a", "b"]  # a right-hand item used twice
    bad["vocabulary"][1]["word"] = "castle"  # a word the text never writes
    bad["warm_up"]["teacher_steps"] = ["Only one step."]
    provider = provider_with(bad, bad)
    lesson, report = draft_secondary_lesson(
        [document], curriculum, unit_id=UNIT, lesson_no=1, provider=provider
    )
    assert len(provider.calls) == 2  # one call and one re-prompt, never more
    assert sorted(report.problems) == ["e2", "e3", "vocabulary:castle", "warm_up"]
    assert report.fallback_exercises == ["e2", "e3"]
    assert report.fallback_warm_up and not report.fallback_wrap_up
    kinds = [b.kind for b in lesson.ordered_blocks()]
    assert "gap_fill" not in kinds and "match" not in kinds
    # the exercise survives as a question list with its wording, without an answer key
    lists = [b for b in lesson.blocks.values() if b.kind == "question_set"]
    gap_list = next(b for b in lists if b.content.exercise_no == "2")
    assert gap_list.content.questions[0].question == "Nam is going to the ___ near his school."
    assert all(not q.answer for q in gap_list.content.questions)
    # the parts that were fine are kept
    assert [
        i.word
        for i in next(b for b in lesson.blocks.values() if b.kind == "vocabulary").content.items
    ] == ["park"]
    assert any(b.kind == "choice" for b in lesson.blocks.values())


def test_unparseable_replies_give_the_offline_lesson(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    provider = MockProvider(["not json", "still not json"])
    lesson, report = draft_secondary_lesson(
        [document], curriculum, unit_id=UNIT, lesson_no=1, provider=provider
    )
    assert report.error and report.fallback_warm_up and report.fallback_wrap_up
    assert report.fallback_exercises == ["e2", "e3", "e4", "e5"]
    assert not any(b.kind == "vocabulary" for b in lesson.blocks.values())
    assert sum(s.minutes for s in lesson.sections) == 45


def test_without_a_provider_the_lesson_is_complete_but_has_no_answers(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    lesson, report = draft_secondary_lesson(
        [document], curriculum, unit_id=UNIT, lesson_no=1, provider=None
    )
    assert not report.model_called
    assert lesson.sections[0].phase is Phase.WARM_UP and lesson.sections[-1].phase is Phase.WRAP_UP
    assert [b.kind for b in lesson.ordered_blocks()].count("dialogue") == 1


def test_reply_validation_names_each_problem(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    plan = plan_lesson([document], curriculum, unit_id=UNIT, lesson_no=1)
    reply = good_reply()
    reply["exercises"][3]["letters"] = ["B", "Z"]
    reply["exercises"][4]["truths"] = [True]
    reply["exercises"][5]["answers"] = ["only one"]
    del reply["exercises"][2]  # e3 has no entry
    reply["vocabulary"].append({"word": "castle", "span": "p9.s9"})
    problems = reply_problems(SecondaryDraft.model_validate(reply), plan)
    assert sorted(problems) == ["e3", "e4", "e5", "e6", "vocabulary:castle"]
    assert "no entry" in problems["e3"][0]


def test_word_occurrence_tolerates_inflection_and_placeholders() -> None:
    assert occurs_in("facility", "There are good facilities here.")
    assert occurs_in("remind sb of", "That reminds me of the time we moved.")
    assert occurs_in("get on with", "We will get on with them.")
    assert not occurs_in("castle", "I'm going to the park.")


def test_prompt_carries_exercises_spans_and_the_reference(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    plan = plan_lesson([document], curriculum, unit_id=UNIT, lesson_no=1)
    prompt = build_prompt(plan, curriculum, "Warm-up: ask about parks. 5 minutes.")
    assert "e2 | textbook exercise 2 | gap_fill" in prompt
    assert "[p1.s9] Nam: I'm going to the park near our school." in prompt
    assert (
        "REFERENCE PLAN (may inform timing and warm-up ideas; never overrides the textbook)"
        in prompt
    )
    assert "Warm-up: ask about parks." in prompt
    assert "45-minute" in prompt and "warm_up 5" in prompt


# --- checks and grounding ----------------------------------------------------------------------


def test_pipeline_grounds_the_exercises_and_keeps_the_book_wording(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    lesson, _report = build_secondary_lesson(
        {document.doc_id: document},
        curriculum,
        provider_with(good_reply()),
        unit_id=UNIT,
        lesson_no=1,
    )
    for block in lesson.ordered_blocks():
        if block.kind in ("dialogue", "gap_fill", "match", "choice", "true_false", "question_set"):
            check = next(c for c in block.checks if c.name == "grounding.citation_supported")
            assert check.status == "pass", (block.kind, check.detail)
            assert block.provenance == "extracted"
    names = {c.name for b in lesson.blocks.values() for c in b.checks}
    assert "english.words_from_unit" in names and "english.vocab_level" not in names


def test_words_from_unit_warns_on_words_the_unit_never_uses(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    reply = good_reply()
    reply["warm_up"]["student_steps"] = [
        "Look at the park.",
        "Describe the sophisticated skyscraper.",
    ]
    lesson, _ = draft_secondary_lesson(
        [document], curriculum, unit_id=UNIT, lesson_no=1, provider=provider_with(reply)
    )
    results = words_from_unit(lesson, curriculum, sources={document.doc_id: document})
    by_block = {block_id: check for block_id, check in results}
    warm_up_id = lesson.sections[0].block_ids[-1]
    assert by_block[warm_up_id].status == "warn"
    assert "skyscraper" in by_block[warm_up_id].detail and "park" not in by_block[warm_up_id].detail
    wrap_up = next(
        b
        for b in lesson.ordered_blocks()
        if isinstance(b, ActivityBlock) and b.content.title == "Say it together"
    )
    assert by_block[wrap_up.id].status == "pass"


def test_secondary_curriculum_replaces_the_word_list_check(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    lesson, _ = draft_secondary_lesson(
        [document], curriculum, unit_id=UNIT, lesson_no=1, provider=provider_with(good_reply())
    )
    checked = run_all(lesson, curriculum, sources={document.doc_id: document})
    names = {c.name for b in checked.blocks.values() for c in b.checks}
    assert "english.words_from_unit" in names and "english.vocab_level" not in names
    timing = next(c for c in checked.checks if c.name == "english.timing")
    assert timing.status == "pass" and timing.detail == "45 min"
    assert isinstance(checked, Lesson)


# --- pictures, citations and the deck ----------------------------------------------------------


def test_a_picture_with_printed_text_on_it_is_not_used(curriculum: Curriculum) -> None:
    """A photo behind a conversation is cropped with the conversation on it: it is left out."""
    spans = [
        Span(span_id="p1.s1", kind=SpanKind.HEADING, text="GETTING STARTED", order=1),
        Span(span_id="p1.s2", kind=SpanKind.INSTRUCTION, text="1. Listen and read.", order=2),
        Span(
            span_id="p1.s3",
            kind=SpanKind.IMAGE,
            text="A road",
            order=3,
            bbox=(0, 0, 400, 300),
            image_path="d/p1_img1.png",
        ),
        Span(
            span_id="p1.s4",
            kind=SpanKind.IMAGE,
            text="A river",
            order=4,
            bbox=(0, 320, 200, 500),
            image_path="d/p1_img2.png",
        ),
    ]
    for number, (name, text) in enumerate(
        (("Lan", "Hi, Nam. Where are you going?"), ("Nam", "To the park."), ("Lan", "Great!")),
        start=5,
    ):
        top = 20 + (number - 5) * 60
        spans.append(
            Span(
                span_id=f"p1.s{number}",
                kind=SpanKind.PARAGRAPH,
                text=f"{name}: {text}",
                order=number,
                bbox=(20, top, 380, top + 40),
            )
        )
    document = SourceDocument(
        doc_id="d_test0002",
        filename="unit.pdf",
        sha256="1" * 64,
        media_type="application/pdf",
        pages=[Page(number=1, spans=spans)],
    )
    (exercise,) = plan_lesson([document], curriculum, unit_id=UNIT, lesson_no=1).exercises
    assert exercise.kind == "dialogue"
    assert [span.text for _ref, span in exercise.before] == ["A river"]  # the free picture stays
    assert exercise.after == []


def test_a_word_cited_at_the_wrong_span_is_moved_to_the_span_that_has_it(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    reply = good_reply()
    reply["vocabulary"][0]["span"] = "p1.s8"  # "park" is in p1.s9, one span off
    provider = provider_with(reply)
    lesson, report = draft_secondary_lesson(
        [document], curriculum, unit_id=UNIT, lesson_no=1, provider=provider
    )
    assert len(provider.calls) == 1 and not report.reprompted
    vocabulary = next(b for b in lesson.blocks.values() if b.kind == "vocabulary")
    assert {c.span_id for c in vocabulary.sources} == {"p1.s9", "p1.s11"}


def test_a_book_that_marks_instructions_only_as_list_items_still_segments_primary_style() -> None:
    from classdeck.planning.segment import instruction_number

    span = Span(span_id="p1.s1", kind=SpanKind.INSTRUCTION, text="3. Look and say.", order=1)
    assert instruction_number(span) == 3
    item = Span(span_id="p1.s2", kind=SpanKind.LIST_ITEM, text="4. Listen.", order=2)
    assert instruction_number(item) == 4


def test_the_report_counts_the_paid_tokens_and_the_cost(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    from classdeck.llm import LLMResponse, Usage

    class Priced(MockProvider):
        def complete(self, messages, **kwargs):  # type: ignore[no-untyped-def]
            reply = super().complete(messages, **kwargs)
            return LLMResponse(
                text=reply.text,
                model="openai/gpt-6-luna",
                usage=Usage(input_tokens=2000, output_tokens=1000),
            )

    provider = Priced([json.dumps(good_reply())])
    _lesson, report = draft_secondary_lesson(
        [document], curriculum, unit_id=UNIT, lesson_no=1, provider=provider
    )
    assert (report.input_tokens, report.output_tokens) == (2000, 1000)
    assert report.cost_usd == pytest.approx(0.0007)


def test_a_long_conversation_is_cut_into_balanced_slides() -> None:
    from classdeck.export.html import DIALOGUE_BUDGET, _dialogue_height, _dialogue_parts
    from classdeck.lesson import DialogueLine

    lines = [DialogueLine(speaker="AB"[n % 2], text="word " * (4 + 5 * (n % 4))) for n in range(12)]
    assert _dialogue_height(lines, dense=False) > DIALOGUE_BUDGET
    parts = _dialogue_parts(lines, dense=True)
    assert len(parts) >= 2
    assert next(start for start, _ in parts) == 0
    assert sum(len(part) for _start, part in parts) == 12
    assert all(_dialogue_height(part, dense=True) <= DIALOGUE_BUDGET for _s, part in parts)
    assert min(len(part) for _s, part in parts) >= 2  # no orphan line
    short = _dialogue_parts(lines[:3], dense=False)
    assert len(short) == 1 and short[0][0] == 0


def test_the_deck_shows_the_answers_and_splits_the_conversation(
    document: SourceDocument, curriculum: Curriculum, tmp_path: Path
) -> None:
    from classdeck.export import export_html

    lesson, _ = build_secondary_lesson(
        {document.doc_id: document},
        curriculum,
        provider_with(good_reply()),
        unit_id=UNIT,
        lesson_no=1,
    )
    deck = export_html(lesson, tmp_path / "lesson.html", sources={document.doc_id: document})
    text = deck.read_text(encoding="utf-8")
    assert "Listen and read." in text and "Where are you going?" in text
    assert 'data-exercise="gap_fill"' in text and "Show answers" in text
    assert "About 5 min." in text or "About 6 min." in text or "About 7 min." in text
    assert "<ol><li>Show the picture.</li>" in text.replace("\n", "")
