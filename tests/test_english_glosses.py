"""Vietnamese glosses: the bundled list, ``fill_glosses`` and ``english.gloss_present``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from classdeck.curriculum import load_curriculum
from classdeck.english import validators as v
from classdeck.ingest import ingest
from classdeck.lesson import CheckStatus, Lesson, VocabularyBlock
from classdeck.planning import build_skeleton, draft_lesson
from tests.test_english_validators import clean_lesson

ROOT = Path(__file__).resolve().parents[1]
CURRICULUM_ROOT = ROOT / "curriculum"
FIXTURES = ROOT / "eval" / "fixtures" / "public"


def grade_words(grade: int) -> set[str]:
    path = CURRICULUM_ROOT / "gdpt-2018" / "tieng-anh" / f"lop-{grade}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    words = {str(word).lower() for word in data["vocabulary"]["words"]}
    words |= {str(word).lower() for unit in data["units"] for word in unit["vocabulary"]}
    return {word for word in words if len(word) > 1}


@pytest.mark.parametrize("grade", [3, 4, 5])
def test_the_gloss_list_covers_every_word_of_the_grade(grade: int) -> None:
    dictionary = v.gloss_dictionary()
    assert sorted(grade_words(grade) - set(dictionary)) == []


def test_the_gloss_list_is_well_formed() -> None:
    path = Path(v.__file__).parent / "data" / "gloss_vi.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "_note" in raw and "_note" not in v.gloss_dictionary()
    dictionary = v.gloss_dictionary()
    assert all(key == key.lower() for key in dictionary)
    assert all(value.strip() and len(value.split()) <= 5 for value in dictionary.values())
    assert dictionary["hello"] == "xin chào"
    assert dictionary["ruler"] == "cái thước"


def test_lookup_gloss_handles_case_and_phrases() -> None:
    assert v.lookup_gloss("  Hello ") == "xin chào"
    assert v.lookup_gloss("good morning") == "tốt buổi sáng"  # the words' glosses, joined
    assert v.lookup_gloss("good zebra") is None
    assert v.lookup_gloss("zebra") is None


def test_fill_glosses_fills_only_empty_glosses_and_leaves_the_input_alone() -> None:
    lesson = clean_lesson()
    items = lesson.blocks["b_02"].content.items
    items[0].meaning_vi = ""
    items[1].meaning_vi = "chào bạn"  # a gloss someone wrote is kept
    filled = v.fill_glosses(lesson)
    assert filled is not lesson
    assert [i.meaning_vi for i in filled.blocks["b_02"].content.items] == ["xin chào", "chào bạn"]
    assert items[0].meaning_vi == ""
    items[0].meaning_vi = None
    assert v.fill_glosses(lesson).blocks["b_02"].content.items[0].meaning_vi == "xin chào"


def test_gloss_present_warns_for_words_without_a_gloss() -> None:
    lesson = clean_lesson()
    items = lesson.blocks["b_02"].content.items
    items[0].meaning_vi = ""
    items[1].word = "zebra"
    items[1].meaning_vi = "  "
    ((block_id, result),) = v.gloss_present(
        lesson, load_curriculum(CURRICULUM_ROOT, "gdpt-2018", 3)
    )
    assert block_id == "b_02" and result.name == "english.gloss_present"
    assert result.status is CheckStatus.WARN
    assert result.detail == "no Vietnamese gloss for: hello, zebra"
    filled = v.fill_glosses(lesson)
    ((_, after),) = v.gloss_present(filled, load_curriculum(CURRICULUM_ROOT, "gdpt-2018", 3))
    assert after.status is CheckStatus.WARN and after.detail.endswith("zebra")  # unknown stays


def test_run_all_fills_the_glosses_of_a_template_drafted_lesson() -> None:
    curriculum = load_curriculum(CURRICULUM_ROOT, "gdpt-2018", 3)
    document = ingest(FIXTURES / "mini-unit-hello.pdf")
    outline = build_skeleton(
        [document], curriculum, unit_id="ta3.u1", lesson_no=1, period=1, grade=3
    )
    drafted = draft_lesson(outline, [document], curriculum)
    block = next(b for b in drafted.blocks.values() if isinstance(b, VocabularyBlock))
    assert [item.meaning_vi for item in block.content.items] == ["", ""]  # templates leave it empty

    filled = v.fill_glosses(drafted)
    filled_block = filled.blocks[block.id]
    assert isinstance(filled_block, VocabularyBlock)
    assert [(i.word, i.meaning_vi) for i in filled_block.content.items] == [
        ("hello", "xin chào"),
        ("hi", "chào"),
    ]

    checked = v.run_all(drafted, curriculum)
    result = next(c for c in checked.blocks[block.id].checks if c.name == "english.gloss_present")
    assert result.status is CheckStatus.PASS
    assert isinstance(checked, Lesson)
