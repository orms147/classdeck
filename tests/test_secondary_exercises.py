# ruff: noqa: RUF001
"""Secondary lessons: picture matching, sound sorting, phases, exact wording.

The pages are self-written (a made-up unit about a harbour town), laid out the way a scanned page
reads after the vision model: headings, ``instruction`` spans, numbered items, picture spans with
a description and numbered captions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from classdeck.curriculum import load_curriculum
from classdeck.curriculum.loader import Curriculum
from classdeck.english.validators import pattern_used
from classdeck.lesson import (
    ActivityBlock,
    BlockAdapter,
    Lesson,
    Page,
    Phase,
    SoundSortContent,
    SourceDocument,
    Span,
    SpanKind,
)
from classdeck.lesson.exercises import answer_key, exercise_lines, page_strings
from classdeck.llm import MockProvider
from classdeck.planning.secondary import (
    SecondaryDraft,
    draft_secondary_lesson,
    plan_lesson,
    reply_problems,
    sounds_in,
    written_word,
)
from classdeck.planning.spelling import britishise, variants

CURRICULUM_ROOT = Path(__file__).resolve().parent.parent / "curriculum"
UNIT = "ta9.u1"
DOC = "d_harbour1"

PAGE: list[tuple[str, str]] = [
    ("heading", "A CLOSER LOOK 1"),
    ("heading", "Vocabulary"),
    ("instruction", "1. Match the workers with what they do."),
    (
        "table",
        "1. fisherman | a. catches fish at sea\n"
        "2. ferry driver | b. takes people across the water\n"
        "3. lighthouse keeper | c. looks after the light on the coast",
    ),
    ("paragraph", "What other harbour workers can you add?"),
    ("instruction", "2. Write a word or phrase in the box under the correct picture."),
    ("list_item", "net"),
    ("list_item", "harbour"),
    ("list_item", "lighthouse"),
    ("image", "Small boats tied to a long wooden pier"),
    ("caption", "1. ______"),
    ("image", "A tall white tower on a rock by the sea"),
    ("caption", "2. ______"),
    ("image", "A green fishing net on the sand"),
    ("caption", "3. ______"),
    ("instruction", "3. Fill in each blank with a word from the box."),
    ("list_item", "colour"),
    ("list_item", "ferries"),
    ("list_item", "pier"),
    ("list_item", "1. The boats are painted a bright ______."),
    ("list_item", "2. Two ______ cross the bay every hour."),
    ("list_item", "3. We walked along the ______ at sunset."),
    ("heading", "Pronunciation"),
    ("heading", "Vowel revision: /ɪ/ and /iː/"),
    ("instruction", "4. Listen and number the words you hear. Then listen again and repeat."),
    ("list_item", "___ ship | ___ sheep"),
    ("list_item", "___ bit | ___ beat"),
    ("list_item", "___ live | ___ leave"),
    (
        "instruction",
        "5. Listen and practise the sentences. Underline the bold words with /ɪ/ and "
        "circle the bold words with /iː/.",
    ),
    ("list_item", "1. The sheep sit near the ship."),
    ("list_item", "2. We leave the village in the evening."),
]


def make_document(page: list[tuple[str, str]] = PAGE) -> SourceDocument:
    spans = [
        Span(
            span_id=f"p1.s{order}",
            kind=SpanKind(kind),
            text=text,
            order=order,
            image_path=f"{DOC}/p1_img{order}.png" if kind == "image" else None,
        )
        for order, (kind, text) in enumerate(page, start=1)
    ]
    return SourceDocument(
        doc_id=DOC,
        filename="unit.pdf",
        sha256="1" * 64,
        media_type="application/pdf",
        pages=[Page(number=1, spans=spans)],
    )


@pytest.fixture(scope="module")
def curriculum() -> Curriculum:
    return load_curriculum(CURRICULUM_ROOT, "gdpt-2018", 9)


@pytest.fixture
def document() -> SourceDocument:
    return make_document()


def reply(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "exercises": [
            {"exercise": "e1", "letters": ["a", "b", "c"], "steps": ["Match.", "Check."]},
            {"exercise": "e2", "letters": ["c", "b", "a"], "steps": ["Look.", "Say the words."]},
            {"exercise": "e3", "gaps": [["colour"], ["ferries"], ["pier"]]},
            {
                "exercise": "e4",
                "word_sounds": ["ɪ", "iː", "ɪ", "iː", "ɪ", "iː"],
                "steps": ["Say each word.", "Sort them."],
            },
            {
                "exercise": "e5",
                "targets": [
                    [{"word": "sheep", "sound": "iː"}, {"word": "sit", "sound": "/ɪ/"}],
                    [{"word": "leave", "sound": "i:"}],
                ],
            },
            {"exercise": "e1a", "answers": []},
        ],
        "vocabulary": [
            {"word": "fisherman", "ipa": "/ˈfɪʃəmən/", "meaning_vi": "ngư dân"},
            {"word": "ferry driver", "ipa": "/ˈferi/", "meaning_vi": "lái phà"},
            {"word": "lighthouse keeper", "ipa": "/x/", "meaning_vi": "người giữ đèn"},
            {"word": "net", "ipa": "/net/", "meaning_vi": "lưới"},
            {"word": "harbour", "ipa": "/ˈhɑːbə/", "meaning_vi": "bến cảng"},
            {"word": "lighthouse", "ipa": "/ˈlaɪthaʊs/", "meaning_vi": "hải đăng"},
            {"word": "colour", "ipa": "/ˈkʌlə/", "meaning_vi": "màu"},
            {"word": "ferries", "ipa": "/ˈferiz/", "meaning_vi": "phà"},
            {"word": "pier", "ipa": "/pɪə/", "meaning_vi": "cầu tàu"},
        ],
        "warm_up": {
            "title": "Harbour words",
            "instructions": "Name things at a harbour.",
            "teacher_steps": ["Draw a boat.", "Ask what is at a harbour."],
            "student_steps": ["Look at the board.", "Say one word."],
        },
        "production": {
            "title": "My harbour job",
            "instructions": "Tell your partner who you want to be.",
            "teacher_steps": ["Model one answer.", "Pairs talk for two minutes."],
            "student_steps": ["Choose a job.", "Tell your partner."],
            "interaction": "pairs",
        },
        "wrap_up": {
            "title": "Say it together",
            "instructions": "Say the new words.",
            "teacher_steps": ["Show the words.", "Pupils repeat."],
            "student_steps": ["Repeat the words.", "Tell a partner one word."],
        },
        "homework": "Write three sentences.",
    }
    base.update(changes)
    return base


def draft(
    document: SourceDocument, curriculum: Curriculum, *replies: dict[str, Any]
) -> tuple[Lesson, Any, MockProvider]:
    provider = MockProvider([json.dumps(r) for r in replies])
    lesson, report = draft_secondary_lesson(
        [document], curriculum, unit_id=UNIT, lesson_no=2, provider=provider
    )
    return lesson, report, provider


# --- the page ----------------------------------------------------------------------------------


def test_sounds_are_read_from_slashes() -> None:
    assert sounds_in("Vowel revision: /ɪ/ and /iː/") == ["ɪ", "iː"]
    assert sounds_in("Underline /e/, circle /e/ and tick /æ/.") == ["e", "æ"]
    assert sounds_in("and/or either/or") == []
    assert sounds_in("/i:/ and /ɪ/") == ["iː", "ɪ"]


def test_pictures_after_a_word_box_become_a_picture_match(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    plan = plan_lesson([document], curriculum, unit_id=UNIT, lesson_no=2)
    exercise = next(e for e in plan.exercises if e.id == "e2")
    assert exercise.kind == "match" and exercise.phase is Phase.PRACTICE
    content = exercise.content
    assert content["right"] == ["net", "harbour", "lighthouse"]
    assert content["left"][1] == "A tall white tower on a rock by the sea"
    assert [c["span_id"] for c in content["left_images"]] == ["p1.s10", "p1.s12", "p1.s14"]
    assert exercise.before == [] and exercise.after == []  # the pictures are not separate slides
    assert {ref.span_id for ref in exercise.refs} >= {"p1.s10", "p1.s7"}
    assert "[picture: Small boats tied to a long wooden pier]" in "\n".join(exercise.prompt_lines())


def test_a_prompt_line_under_the_items_is_kept_as_a_production_question(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    plan = plan_lesson([document], curriculum, unit_id=UNIT, lesson_no=2)
    follow = next(e for e in plan.exercises if e.id == "e1a")
    assert follow.kind == "question_set" and follow.phase is Phase.PRODUCTION
    assert follow.content["questions"] == [{"question": "What other harbour workers can you add?"}]
    # controlled exercises that the page prints after it stay in practice
    assert [e.id for e in plan.exercises] == ["e1", "e2", "e3", "e4", "e5", "e1a"]
    assert [e.phase for e in plan.exercises][-1] is Phase.PRODUCTION
    assert not plan.needs_production


def test_word_lists_and_sentences_become_sound_sorts(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    plan = plan_lesson([document], curriculum, unit_id=UNIT, lesson_no=2)
    words = next(e for e in plan.exercises if e.id == "e4")
    assert words.kind == "sound_sort" and words.phase is Phase.PRACTICE
    assert words.content["sounds"] == ["ɪ", "iː"]
    assert [w["word"] for w in words.content["words"]] == [
        "ship",
        "sheep",
        "bit",
        "beat",
        "live",
        "leave",
    ]
    sentences = next(e for e in plan.exercises if e.id == "e5")
    assert sentences.kind == "sound_sort" and sentences.content["words"] == []
    assert sentences.content["sentences"][0]["text"] == "The sheep sit near the ship."
    assert "Sentence 1: The sheep sit near the ship." in "\n".join(sentences.prompt_lines())


def test_phases_put_vocabulary_and_pronunciation_drills_in_practice(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    plan = plan_lesson([document], curriculum, unit_id=UNIT, lesson_no=2)
    by_id = {e.id: e.phase for e in plan.exercises}
    assert [by_id[i] for i in ("e1", "e2", "e3", "e4", "e5")] == [Phase.PRACTICE] * 5
    assert by_id["e1a"] is Phase.PRODUCTION


def test_the_words_of_the_vocabulary_exercises_are_key_words(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    plan = plan_lesson([document], curriculum, unit_id=UNIT, lesson_no=2)
    assert plan.key_words == [
        "fisherman",
        "ferry driver",
        "lighthouse keeper",
        "net",
        "harbour",
        "lighthouse",
        "colour",
        "ferries",
        "pier",
    ]


# --- the reply and the blocks ------------------------------------------------------------------


def test_the_lesson_carries_a_picture_match_and_two_sound_sorts(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    lesson, report, provider = draft(document, curriculum, reply())
    assert len(provider.calls) == 1 and not report.reprompted and not report.problems
    blocks = [b for b in lesson.ordered_blocks() if b.kind in ("match", "sound_sort")]
    picture, words, sentences = blocks[1], blocks[2], blocks[3]
    assert picture.kind == "match" and picture.content.answer == {0: 2, 1: 1, 2: 0}
    assert [c.span_id for c in picture.content.left_images if c] == ["p1.s10", "p1.s12", "p1.s14"]
    assert words.kind == "sound_sort"
    assert [(w.word, w.sound) for w in words.content.words][:2] == [("ship", "ɪ"), ("sheep", "iː")]
    targets = [(t.word, t.sound) for t in sentences.content.sentences[0].targets]
    assert targets == [("sheep", "iː"), ("sit", "ɪ")]
    assert sentences.content.sentences[1].targets[0].sound == "iː"  # "i:" is the long mark
    assert lesson.sections[2].phase is Phase.PRACTICE and len(lesson.sections[2].block_ids) == 5
    assert sum(s.minutes for s in lesson.sections) == 45
    # the picture match gives the new words their pictures
    vocabulary = next(b for b in lesson.blocks.values() if b.kind == "vocabulary")
    pictures = {i.word: i.image.span_id for i in vocabulary.content.items if i.image}
    assert pictures == {"lighthouse": "p1.s10", "harbour": "p1.s12", "net": "p1.s14"}


def test_a_sound_sort_reply_is_checked_against_the_page(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    plan = plan_lesson([document], curriculum, unit_id=UNIT, lesson_no=2)
    bad = reply()
    bad["exercises"][3]["word_sounds"] = ["ɪ", "iː", "ɪ"]
    bad["exercises"][4]["targets"] = [
        [{"word": "goat", "sound": "iː"}],
        [{"word": "leave", "sound": "ʌ"}],
    ]
    problems = reply_problems(SecondaryDraft.model_validate(bad), plan)
    assert "6 entries" in problems["e4"][0]
    assert any("'goat' is not a word of the sentence" in p for p in problems["e5"])
    assert any("must be /ɪ/, /iː/" in p for p in problems["e5"])
    assert not reply_problems(SecondaryDraft.model_validate(reply()), plan)


def test_a_sound_sort_without_answers_falls_back_to_a_question_list(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    bad = reply()
    bad["exercises"][3] = {"exercise": "e4", "word_sounds": []}
    lesson, report, _provider = draft(document, curriculum, bad, bad)
    assert report.reprompted and "e4" in report.fallback_exercises
    fallback = next(
        b
        for b in lesson.blocks.values()
        if b.kind == "question_set" and "Sounds" in b.content.prompt
    )
    assert fallback.content.prompt == "Sounds: /ɪ/  /iː/"
    assert [q.question for q in fallback.content.questions][:2] == ["ship", "sheep"]


def test_the_exercise_text_helpers_know_the_new_shapes(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    lesson, *_ = draft(document, curriculum, reply())
    picture, words, sentences = [
        b for b in lesson.ordered_blocks() if b.kind in ("match", "sound_sort")
    ][1:4]
    # a picture's description is not printed on the page, the words are
    assert page_strings(picture)[1:] == ["net", "harbour", "lighthouse"]
    assert exercise_lines(picture)[0] == "1. [picture: Small boats tied to a long wooden pier]"
    assert answer_key(picture)[0].startswith("1-C  [picture: Small boats")
    assert answer_key(words) == ["/ɪ/  ship, bit, live", "/iː/  sheep, beat, leave"]
    assert answer_key(sentences)[0] == "1. sheep /iː/, sit /ɪ/"
    assert exercise_lines(words)[0] == "Sounds: /ɪ/  /iː/"


def test_sound_sort_content_is_validated() -> None:
    SoundSortContent.model_validate(
        {"sounds": ["a", "e"], "words": [{"word": "cat", "sound": "a"}]}
    )
    with pytest.raises(ValueError, match="needs words or sentences"):
        SoundSortContent.model_validate({"sounds": ["a", "e"]})
    with pytest.raises(ValueError, match="not one of the sounds"):
        SoundSortContent.model_validate(
            {"sounds": ["a", "e"], "words": [{"word": "cat", "sound": "u"}]}
        )
    with pytest.raises(ValueError, match="does not occur"):
        SoundSortContent.model_validate(
            {
                "sounds": ["a", "e"],
                "sentences": [{"text": "A cat.", "targets": [{"word": "dog", "sound": "a"}]}],
            }
        )
    with pytest.raises(ValueError, match="one entry per left item"):
        BlockAdapter.validate_python(
            {
                "id": "b1",
                "kind": "match",
                "content": {
                    "left": ["a", "b"],
                    "right": ["c", "d"],
                    "left_images": [None],
                    "answer": {0: 0, 1: 1},
                },
            }
        )


# --- the deck ----------------------------------------------------------------------------------


def test_the_deck_runs_the_pictures_and_the_sounds(
    document: SourceDocument, curriculum: Curriculum, tmp_path: Path
) -> None:
    from classdeck.export import export_html

    for span in document.pages[0].spans:
        if span.image_path:
            path = tmp_path / span.image_path
            path.parent.mkdir(parents=True, exist_ok=True)
            pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 16, 16), False)
            pixmap.clear_with(180)
            pixmap.save(str(path))
    lesson, *_ = draft(document, curriculum, reply())
    deck = export_html(
        lesson, tmp_path / "deck.html", sources={DOC: document}, image_root=tmp_path
    ).read_text(encoding="utf-8")
    assert deck.count("match__item--picture") >= 3 and "match match--pictures" in deck
    assert 'class="sort sort--words"' in deck and 'data-act="chip"' in deck
    assert deck.count('class="sort__head"') == 2 and 'data-act="column"' in deck
    assert 'data-act="target"' in deck and 'data-answer="1"' in deck  # sheep: the second sound
    assert 'class="sort__legend"' in deck
    assert "sortShow" in deck and "sortCheck" in deck
    # the speech buttons of the word chips
    assert 'data-say="sheep"' in deck
    # the teacher's key
    assert "/ɪ/  ship, bit, live" in deck


# --- production --------------------------------------------------------------------------------


def test_without_a_free_task_the_model_writes_one(curriculum: Curriculum) -> None:
    document = make_document(
        [
            ("heading", "A CLOSER LOOK 1"),
            ("instruction", "1. Match the workers with what they do."),
            ("table", "1. fisherman | a. catches fish\n2. ferry driver | b. takes people across"),
        ]
    )
    plan = plan_lesson([document], curriculum, unit_id=UNIT, lesson_no=2)
    assert plan.needs_production
    assert Phase.PRODUCTION in plan.minutes()
    good = reply()
    good["exercises"] = [{"exercise": "e1", "letters": ["a", "b"]}]
    good["vocabulary"] = good["vocabulary"][:2]
    provider = MockProvider([json.dumps(good)])
    lesson, report = draft_secondary_lesson(
        [document], curriculum, unit_id=UNIT, lesson_no=2, provider=provider
    )
    assert "3b." in provider.calls[0]["messages"][-1]["content"]
    production = lesson.sections[3]
    assert production.phase is Phase.PRODUCTION and sum(s.minutes for s in lesson.sections) == 45
    task = lesson.blocks[production.block_ids[0]]
    assert isinstance(task, ActivityBlock) and task.provenance == "generated"
    assert task.content.title == "My harbour job" and not report.fallback_production
    # a reply without the task is re-prompted, then a template stands in for it
    del good["production"]
    provider = MockProvider([json.dumps(good), json.dumps(good)])
    lesson, report = draft_secondary_lesson(
        [document], curriculum, unit_id=UNIT, lesson_no=2, provider=provider
    )
    assert report.reprompted and report.fallback_production
    assert lesson.sections[3].block_ids and sum(s.minutes for s in lesson.sections) == 45


def test_the_prompt_does_not_ask_for_a_task_the_page_already_has(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    _lesson, _report, provider = draft(document, curriculum, reply())
    prompt = provider.calls[0]["messages"][-1]["content"]
    assert '"production" is not needed' in prompt
    assert "fisherman, ferry driver, lighthouse keeper" in prompt


# --- exact wording -----------------------------------------------------------------------------


def test_british_spelling_replaces_a_misread_american_form() -> None:
    assert britishise("Local specialty, Color and favorite neighbors.") == (
        "Local speciality, Colour and favourite neighbours."
    )
    assert "speciality" in variants("specialty")
    assert "color" in variants("colour")
    assert britishise("Practice makes perfect") == "Practice makes perfect"


def test_vocabulary_is_written_as_the_page_writes_it(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    plan = plan_lesson([document], curriculum, unit_id=UNIT, lesson_no=2)
    assert written_word("color", plan) == "colour"
    assert written_word("Harbor", plan) == "Harbour"
    assert written_word("harbour", plan) == "harbour"
    assert written_word("zebra", plan) == "zebra"
    assert written_word("ferry  driver", plan) == "ferry driver"


def test_a_misspelt_word_in_the_reply_is_corrected_and_listed_once(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    changed = reply()
    changed["vocabulary"][6] = {"word": "color", "ipa": "/x/", "meaning_vi": "màu"}
    changed["vocabulary"][8] = {"word": "Pier", "ipa": "/pɪə/", "meaning_vi": "cầu tàu"}
    lesson, report, provider = draft(document, curriculum, changed)
    assert len(provider.calls) == 1 and not report.problems
    vocabulary = next(b for b in lesson.blocks.values() if b.kind == "vocabulary")
    words = [i.word for i in vocabulary.content.items]
    assert "colour" in words and "color" not in words and "Pier" in words


def test_the_vocabulary_must_list_every_word_of_its_exercises(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    short = reply()
    short["vocabulary"] = short["vocabulary"][:3]
    plan = plan_lesson([document], curriculum, unit_id=UNIT, lesson_no=2)
    problems = reply_problems(SecondaryDraft.model_validate(short), plan)
    assert "net, harbour, lighthouse, colour, ferries, pier" in problems["vocabulary:missing"][0]
    # still short after the re-prompt: the page's words are added without a gloss
    lesson, report, _provider = draft(document, curriculum, short, short)
    assert report.reprompted
    vocabulary = next(b for b in lesson.blocks.values() if b.kind == "vocabulary")
    assert [i.word for i in vocabulary.content.items][-1] == "pier"
    assert vocabulary.content.items[-1].meaning_vi is None
    assert len(vocabulary.content.items) == 9


# --- checks ------------------------------------------------------------------------------------


def test_pattern_used_is_silent_for_a_lesson_without_patterns(
    document: SourceDocument, curriculum: Curriculum
) -> None:
    lesson, *_ = draft(document, curriculum, reply())
    assert pattern_used(lesson, curriculum) == []
