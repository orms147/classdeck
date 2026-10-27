from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from classdeck.curriculum import (
    Curriculum,
    CurriculumFile,
    CurriculumNotFound,
    GrammarRule,
    curriculum_path,
    list_available,
    load_curriculum,
)

ROOT = Path(__file__).resolve().parents[1] / "curriculum"

MUST_MATCH = {
    "perfect": ["I have been here", "She has eaten", "They haven't finished", "I've seen it"],
    "passive": ["it was eaten by the dog", "The cake is made by Mai", "It's drawn by Tony"],
    "past": ["we went home", "I was happy", "She didn't come", "They were in class"],
}
MUST_NOT_MATCH = [
    "She has a cat",
    "I have ten pens",
    "I have green eyes",
    "I am happy",
    "Open your book",
    "What's your name?",
    "This is my friend",
    "Do you like chess?",
]


@pytest.fixture(scope="module")
def curriculum() -> Curriculum:
    return load_curriculum(ROOT, "gdpt-2018", 3)


def _minimal(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "version": "test",
        "subject": "tieng-anh",
        "grade": 3,
        "vocabulary": {"level": "pre-A1", "words": ["hello"]},
        "units": [
            {"id": "u1", "title": "One", "outcomes": [{"id": "o1", "text": "Say hello."}]},
            {"id": "u2", "title": "Two", "outcomes": [{"id": "o2", "text": "Say bye."}]},
        ],
    }
    data.update(overrides)
    return data


def test_real_file_validates_and_is_seed(curriculum: Curriculum) -> None:
    file = curriculum.file
    assert isinstance(file, CurriculumFile)
    assert file.status == "seed"
    assert (file.version, file.subject, file.grade, file.language) == (
        "gdpt-2018",
        "tieng-anh",
        3,
        "en",
    )
    assert curriculum.period_minutes == 35
    assert [unit.id for unit in file.units] == [f"ta3.u{n}" for n in range(1, 6)]
    for number, unit in enumerate(file.units, start=1):
        assert [ref.unit for ref in unit.textbook_units] == [number]
        assert 2 <= len(unit.outcomes) <= 3
        assert 5 <= len(unit.vocabulary) <= 8
        assert unit.patterns
        assert unit.phonics


def test_grade_words_are_lower_case_and_unique(curriculum: Curriculum) -> None:
    words = curriculum.file.vocabulary.words
    assert 120 <= len(words) <= 160
    assert all(word == word.lower() for word in words)
    assert len(words) == len(set(words))


def test_unit_and_outcome_lookup(curriculum: Curriculum) -> None:
    unit = curriculum.unit("ta3.u1")
    assert unit.title == "Hello"
    outcome = curriculum.outcome("ta3.u1.o1")
    assert "Hello" in outcome.text
    assert curriculum.unit_of_outcome("ta3.u1.o1") is unit
    assert curriculum.unit_of_outcome("ta3.u4.o2").id == "ta3.u4"


def test_unknown_ids_raise_key_error(curriculum: Curriculum) -> None:
    with pytest.raises(KeyError, match=r"ta3\.u9"):
        curriculum.unit("ta3.u9")
    with pytest.raises(KeyError, match=r"ta3\.u1\.o9"):
        curriculum.outcome("ta3.u1.o9")
    with pytest.raises(KeyError, match=r"ta3\.u1\.o9"):
        curriculum.unit_of_outcome("ta3.u1.o9")
    with pytest.raises(KeyError, match=r"ta3\.u9"):
        curriculum.word_list("ta3.u9")


def test_word_list_extends_grade_words_with_unit_words(curriculum: Curriculum) -> None:
    base = curriculum.word_list()
    unit_words = curriculum.word_list("ta3.u4")
    assert base <= unit_words
    assert {word.lower() for word in curriculum.unit("ta3.u4").vocabulary} <= unit_words
    assert "hello" in base
    assert all(word == word.lower() for word in unit_words)


def test_word_list_lower_cases_mixed_case_input() -> None:
    data = _minimal(vocabulary={"level": "pre-A1", "words": ["Hello", "BYE"]})
    data["units"][0]["vocabulary"] = ["Apple"]  # type: ignore[index]
    curriculum = Curriculum(CurriculumFile.model_validate(data))
    assert curriculum.word_list() == frozenset({"hello", "bye"})
    assert curriculum.word_list("u1") == frozenset({"hello", "bye", "apple"})


def test_forbidden_rules_compile_and_behave(curriculum: Curriculum) -> None:
    rules = {rule.id: rule for rule in curriculum.forbidden_rules()}
    assert set(rules) == {"perfect", "passive", "past"}
    for rule in rules.values():
        assert rule.regex.search("") is None
        assert rule.note
    for rule_id, sentences in MUST_MATCH.items():
        for sentence in sentences:
            assert rules[rule_id].regex.search(sentence), (rule_id, sentence)
    # The rules the task singles out, checked against the rule that should catch each one.
    assert rules["perfect"].regex.search("I have been here")
    assert rules["perfect"].regex.search("She has eaten")
    assert rules["passive"].regex.search("it was eaten by the dog")
    assert rules["past"].regex.search("we went home")
    assert rules["past"].regex.search("I was happy")
    for sentence in MUST_NOT_MATCH:
        for rule in rules.values():
            assert rule.regex.search(sentence) is None, (rule.id, sentence)


def test_forbidden_regex_is_case_insensitive(curriculum: Curriculum) -> None:
    rules = {rule.id: rule for rule in curriculum.forbidden_rules()}
    assert rules["perfect"].regex.search("SHE HAS EATEN")
    assert rules["past"].regex.search("We Went home")


def test_invalid_regex_is_rejected() -> None:
    with pytest.raises(ValidationError, match="invalid regular expression"):
        GrammarRule(id="bad", pattern="(unclosed")


def test_search_outcomes_ranks_by_token_overlap(curriculum: Curriculum) -> None:
    hits = curriculum.search_outcomes("greet hello")
    assert hits
    assert hits[0].id == "ta3.u1.o1"
    assert curriculum.unit_of_outcome(hits[0].id).id == "ta3.u1"
    assert all(hit.id != "ta3.u1.o3" for hit in hits)


def test_search_outcomes_respects_unit_and_limit(curriculum: Curriculum) -> None:
    in_unit_one = curriculum.search_outcomes("name", unit_id="ta3.u1")
    assert [hit.id for hit in in_unit_one] == ["ta3.u1.o2"]
    assert curriculum.search_outcomes("greet hello", unit_id="ta3.u2") == []
    everywhere = curriculum.search_outcomes("name")
    assert len(everywhere) > 1
    assert len(curriculum.search_outcomes("name", limit=1)) == 1
    assert curriculum.search_outcomes("name", limit=0) == []
    assert curriculum.search_outcomes("zzz unknown") == []
    with pytest.raises(KeyError):
        curriculum.search_outcomes("name", unit_id="ta3.u9")


def test_duplicate_outcome_ids_are_rejected() -> None:
    units = [
        {"id": "u1", "title": "One", "outcomes": [{"id": "o1", "text": "A."}]},
        {"id": "u2", "title": "Two", "outcomes": [{"id": "o1", "text": "B."}]},
    ]
    with pytest.raises(ValidationError, match="duplicate outcome id: o1"):
        CurriculumFile.model_validate(_minimal(units=units))


def test_duplicate_unit_ids_are_rejected() -> None:
    units = [
        {"id": "u1", "title": "One", "outcomes": [{"id": "o1", "text": "A."}]},
        {"id": "u1", "title": "Two", "outcomes": [{"id": "o2", "text": "B."}]},
    ]
    with pytest.raises(ValidationError, match="duplicate unit id: u1"):
        CurriculumFile.model_validate(_minimal(units=units))


def test_unknown_fields_and_bad_values_are_rejected() -> None:
    with pytest.raises(ValidationError):
        CurriculumFile.model_validate(_minimal(surprise=1))
    with pytest.raises(ValidationError):
        CurriculumFile.model_validate(_minimal(grade=6))
    with pytest.raises(ValidationError):
        CurriculumFile.model_validate(_minimal(status="draft"))
    bad_skill = [
        {
            "id": "u1",
            "title": "One",
            "outcomes": [{"id": "o1", "text": "A.", "skills": ["dancing"]}],
        }
    ]
    with pytest.raises(ValidationError):
        CurriculumFile.model_validate(_minimal(units=bad_skill))


def test_defaults() -> None:
    file = CurriculumFile.model_validate(_minimal())
    assert file.status == "seed"
    assert file.language == "en"
    assert file.period_minutes == 35
    assert file.grammar_scope.allowed == []
    assert file.grammar_scope.forbidden == []
    assert file.units[0].lessons == 3


def test_missing_grade_raises_curriculum_not_found() -> None:
    with pytest.raises(CurriculumNotFound):
        load_curriculum(ROOT, "gdpt-2018", 6)
    with pytest.raises(FileNotFoundError):
        load_curriculum(ROOT, "no-such-version", 3)


def test_curriculum_path() -> None:
    path = curriculum_path(ROOT, "gdpt-2018", 3)
    assert path == ROOT / "gdpt-2018" / "tieng-anh" / "lop-3.yaml"
    assert path.is_file()


def test_list_available() -> None:
    assert list_available(ROOT) == [("gdpt-2018", grade) for grade in (3, 4, 5)]
    assert list_available(ROOT / "does-not-exist") == []


def test_loader_rejects_file_that_disagrees_with_its_path(tmp_path: Path) -> None:
    directory = tmp_path / "gdpt-2018" / "tieng-anh"
    directory.mkdir(parents=True)
    source = curriculum_path(ROOT, "gdpt-2018", 3).read_text(encoding="utf-8")
    (directory / "lop-4.yaml").write_text(source, encoding="utf-8")
    with pytest.raises(ValueError, match="expected version 'gdpt-2018', grade 4"):
        load_curriculum(tmp_path, "gdpt-2018", 4)
    assert list_available(tmp_path) == [("gdpt-2018", 4)]


def test_seed_patterns_do_not_trip_the_forbidden_rules(curriculum: Curriculum) -> None:
    for unit in curriculum.file.units:
        for item in unit.patterns:
            for rule in curriculum.forbidden_rules():
                assert rule.regex.search(item.pattern) is None, (unit.id, rule.id, item.pattern)


def test_word_list_includes_classroom_language(curriculum) -> None:
    words = curriculum.word_list("ta3.u1")
    assert {"listen", "partner", "picture", "swap"} <= words
    assert "classroom_language" not in words
