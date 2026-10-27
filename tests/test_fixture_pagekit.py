"""The public fixture renderer (eval/fixtures/public/pagekit.py): validation, output, ingest."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from classdeck.ingest import ingest
from classdeck.lesson import SpanKind
from classdeck.planning import lesson_runs, segment_document

FIXTURES = Path(__file__).resolve().parents[1] / "eval" / "fixtures" / "public"
UNITS = FIXTURES / "units"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _load_pagekit() -> ModuleType:
    spec = importlib.util.spec_from_file_location("pagekit", FIXTURES / "pagekit.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve postponed annotations via sys.modules
    spec.loader.exec_module(module)
    return module


pagekit = _load_pagekit()


def lesson_one_activities() -> list[dict]:
    return [
        {
            "no": 1,
            "instruction": "Look, listen and repeat.",
            "type": "look_listen_repeat",
            "picture": {"scene": "two children greeting at the school gate", "style": "scene"},
            "lines": ["Ha: Hello, I'm Ha.", "Tien: Hi, Ha. I'm Tien."],
        },
        {
            "no": 2,
            "instruction": "Listen, point and say.",
            "type": "listen_point_say",
            "pattern": "Hello, I'm ___.",
            "items": [
                {"word": "Ha", "picture": {"scene": "girl waving", "style": "card"}},
                {"word": "Tien"},
                {"word": "Duc"},
            ],
            "lines": ["Hello, I'm ___.", "Hi, ___. I'm ___."],
        },
        {
            "no": 3,
            "instruction": "Let's talk.",
            "type": "lets_talk",
            "lines": ["Hello. / Hi.", "I'm (name)."],
        },
        {
            "no": 4,
            "instruction": "Listen and tick.",
            "type": "listen_and_do",
            "options": ["a. Hello, I'm Lan.", "b. Hi, I'm Duc."],
        },
        {
            "no": 5,
            "instruction": "Look, complete and read.",
            "type": "look_complete_read",
            "lines": ["Hello, I'm _____.", "Hi, _____. I'm _____."],
        },
        {
            "no": 6,
            "instruction": "Let's sing.",
            "type": "song",
            "lines": ["Hello, hello! I'm Ha.", "Hello, hello! I'm Tien."],
        },
    ]


def lesson_three_activities() -> list[dict]:
    return [
        {
            "no": 1,
            "instruction": "Listen and repeat.",
            "type": "phonics",
            "sound": "/h/",
            "letters": "h",
            "words": ["hello", "hi"],
            "lines": ["Hello, Ha!", "Hi, Tien!"],
        },
        {
            "no": 2,
            "instruction": "Listen and circle.",
            "type": "listen_and_do",
            "options": ["a. hello", "b. hi"],
        },
        {
            "no": 3,
            "instruction": "Let's chant.",
            "type": "chant",
            "lines": ["Hello, hello, how are you?", "Hi, hi, I'm fine, thank you."],
        },
        {
            "no": 4,
            "instruction": "Read and match.",
            "type": "read_and_match",
            "pairs": [["Hello, I'm Ha.", "Hi, Ha. I'm Tien."], ["Goodbye, Tien.", "Bye, Ha."]],
        },
        {
            "no": 5,
            "instruction": "Let's write.",
            "type": "lets_write",
            "lines": ["Hello, I'm _____.", "Goodbye, _____."],
        },
        {
            "no": 6,
            "instruction": "Project.",
            "type": "project",
            "lines": ["Make a name card. Show it to your friends."],
        },
    ]


def unit_spec(lessons: list[dict]) -> dict:
    return {"id": "test-unit", "grade": 3, "unit": 7, "title": "Greetings", "lessons": lessons}


def sequence(path: Path) -> list[tuple[int, str, str]]:
    return [
        (page.number, span.kind.value, span.text)
        for page in ingest(path).pages
        for span in page.spans
    ]


@pytest.fixture(scope="module")
def one_lesson() -> dict:
    return unit_spec([{"no": 1, "activities": lesson_one_activities()}])


@pytest.fixture(scope="module")
def rendered(one_lesson: dict, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return pagekit.render_unit(one_lesson, tmp_path_factory.mktemp("render"))


# --- validation --------------------------------------------------------------------------------


def test_sample_spec_validates(one_lesson: dict) -> None:
    unit = pagekit.validate_spec(one_lesson)
    assert unit.heading == "Unit 7  Greetings"
    assert unit.layout.lesson_line is True
    assert [activity.type for activity in unit.lessons[0].activities][:2] == [
        "look_listen_repeat",
        "listen_point_say",
    ]


def test_rejects_five_activities(one_lesson: dict) -> None:
    spec = copy.deepcopy(one_lesson)
    spec["lessons"][0]["activities"].pop()
    with pytest.raises(ValueError, match="numbered 1 to 6"):
        pagekit.validate_spec(spec)


def test_rejects_duplicate_activity_numbers(one_lesson: dict) -> None:
    spec = copy.deepcopy(one_lesson)
    spec["lessons"][0]["activities"][5]["no"] = 5
    with pytest.raises(ValueError, match="numbered 1 to 6"):
        pagekit.validate_spec(spec)


def test_rejects_unknown_activity_type(one_lesson: dict) -> None:
    spec = copy.deepcopy(one_lesson)
    spec["lessons"][0]["activities"][0]["type"] = "watch_video"
    with pytest.raises(ValueError, match="type"):
        pagekit.validate_spec(spec)


def test_rejects_content_line_that_reads_as_an_instruction(one_lesson: dict) -> None:
    spec = copy.deepcopy(one_lesson)
    spec["lessons"][0]["activities"][2]["lines"] = ["1. Say hello to your partner."]
    with pytest.raises(ValueError, match="activity instruction"):
        pagekit.validate_spec(spec)


def test_rejects_text_outside_latin_1(one_lesson: dict) -> None:
    spec = copy.deepcopy(one_lesson)
    spec["lessons"][0]["activities"][2]["lines"] = ["Xin chào, Hà ơi."]
    with pytest.raises(ValueError, match="Latin-1"):
        pagekit.validate_spec(spec)


def test_rejects_lessons_out_of_order() -> None:
    spec = unit_spec([{"no": 2, "activities": lesson_one_activities()}])
    with pytest.raises(ValueError, match=r"numbered 1.. in order"):
        pagekit.validate_spec(spec)


def test_rejects_unknown_field(one_lesson: dict) -> None:
    spec = copy.deepcopy(one_lesson)
    spec["lessons"][0]["activities"][0]["tittle"] = "typo"
    with pytest.raises(ValueError, match="tittle"):
        pagekit.validate_spec(spec)


def test_instruction_gets_its_full_stop(one_lesson: dict) -> None:
    spec = copy.deepcopy(one_lesson)
    spec["lessons"][0]["activities"][2]["instruction"] = "Let's talk"
    unit = pagekit.validate_spec(spec)
    assert unit.lessons[0].activities[2].instruction == "Let's talk."
    instructions = [text for kind, text in pagekit.text_sequence(unit)[0] if kind == "list_item"]
    assert instructions[2] == "3. Let's talk."


def test_load_specs_reads_the_units_directory() -> None:
    specs = pagekit.load_specs(UNITS)
    ids = [spec["id"] for spec in specs]
    assert "mini-unit-hello" in ids
    assert ids == sorted(ids)


def test_load_specs_requires_the_id_to_match_the_file_stem(
    tmp_path: Path, one_lesson: dict
) -> None:
    (tmp_path / "other-name.json").write_text(json.dumps(one_lesson), encoding="utf-8")
    with pytest.raises(ValueError, match="file stem"):
        pagekit.load_specs(tmp_path)


def test_unknown_format_is_rejected(tmp_path: Path, one_lesson: dict) -> None:
    with pytest.raises(ValueError, match="unknown formats"):
        pagekit.render_unit(one_lesson, tmp_path, formats=("odt",))


def test_page_overflow_is_reported(tmp_path: Path, one_lesson: dict) -> None:
    spec = copy.deepcopy(one_lesson)
    for activity in spec["lessons"][0]["activities"][:3]:
        activity["lines"] = [f"Hello there, friend number {n}." for n in range(1, 13)]
    with pytest.raises(ValueError, match="overflows"):
        pagekit.render_unit(spec, tmp_path, formats=("pdf",))


# --- rendering and ingest ----------------------------------------------------------------------


def test_render_writes_every_format(rendered: dict[str, Path]) -> None:
    assert set(rendered) == {"pdf", "docx", "pptx"}
    for fmt, path in rendered.items():
        assert path.name == f"test-unit.{fmt}"
        assert path.stat().st_size > 0


def test_pdf_ingest_matches_the_plan(rendered: dict[str, Path], one_lesson: dict) -> None:
    document = ingest(rendered["pdf"])
    assert [page.number for page in document.pages] == [1, 2]
    assert [
        [(span.kind.value, span.text) for span in page.spans] for page in document.pages
    ] == pagekit.text_sequence(one_lesson)
    spans = [span for page in document.pages for span in page.spans]
    headings = [span.text for span in spans if span.kind is SpanKind.HEADING]
    assert headings == ["Unit 7 Greetings", "Lesson 1", "Unit 7 Greetings"]
    instructions = [span.text for span in spans if span.kind is SpanKind.LIST_ITEM]
    assert instructions == [
        "1. Look, listen and repeat.",
        "2. Listen, point and say.",
        "3. Let's talk.",
        "4. Listen and tick.",
        "5. Look, complete and read.",
        "6. Let's sing.",
    ]


def test_pdf_pictures_are_image_spans_with_a_bbox(rendered: dict[str, Path]) -> None:
    document = ingest(rendered["pdf"])
    images = [span for page in document.pages for span in page.spans if span.kind is SpanKind.IMAGE]
    assert len(images) == 4  # one scene and three cards
    for span in images:
        assert span.bbox is not None
        x0, y0, x1, y1 = span.bbox
        assert x1 > x0 and y1 > y0
    scene, *cards = images
    assert scene.bbox is not None and scene.bbox[2] - scene.bbox[0] == pytest.approx(240.0)
    tops = {round(card.bbox[1]) for card in cards if card.bbox is not None}
    assert len(tops) == 1  # one row
    lefts = [card.bbox[0] for card in cards if card.bbox is not None]
    assert lefts == sorted(lefts)


def test_docx_and_pptx_carry_the_same_text(rendered: dict[str, Path]) -> None:
    pdf, docx, pptx = (sequence(rendered[fmt]) for fmt in ("pdf", "docx", "pptx"))
    assert docx == pdf
    assert [text for _, _, text in pptx] == [text for _, _, text in pdf]
    # Only the title placeholder is a heading in PPTX, so its lesson line is a paragraph.
    differing = [(a, b) for a, b in zip(pptx, pdf, strict=True) if a != b]
    assert differing == [((1, "paragraph", "Lesson 1"), (1, "heading", "Lesson 1"))]


def test_segmentation_yields_six_numbered_chunks(rendered: dict[str, Path]) -> None:
    chunks = segment_document(ingest(rendered["pdf"]))
    assert [chunk.number for chunk in chunks] == [1, 2, 3, 4, 5, 6]
    texts = {chunk.number: chunk.text.splitlines() for chunk in chunks}
    assert texts[1] == [
        "Unit 7 Greetings",
        "Lesson 1",
        "1. Look, listen and repeat.",
        "Ha: Hello, I'm Ha.",
        "Tien: Hi, Ha. I'm Tien.",
    ]
    assert texts[2] == [
        "2. Listen, point and say.",
        "Hello, I'm ___.",
        "Ha Tien Duc",
        "Hello, I'm ___.",
        "Hi, ___. I'm ___.",
    ]
    assert texts[3] == ["3. Let's talk.", "Hello. / Hi.", "I'm (name)."]
    assert texts[4] == [
        "Unit 7 Greetings",
        "4. Listen and tick.",
        "a. Hello, I'm Lan. b. Hi, I'm Duc.",
    ]
    assert texts[6] == ["6. Let's sing.", "Hello, hello! I'm Ha.", "Hello, hello! I'm Tien."]


def test_three_lessons_give_six_pages_and_three_runs(tmp_path: Path) -> None:
    spec = unit_spec(
        [
            {"no": 1, "activities": lesson_one_activities()},
            {"no": 2, "activities": lesson_one_activities()},
            {"no": 3, "activities": lesson_three_activities()},
        ]
    )
    path = pagekit.render_unit(spec, tmp_path, formats=("pdf",))["pdf"]
    document = ingest(path)
    assert [page.number for page in document.pages] == [1, 2, 3, 4, 5, 6]
    assert [
        [(span.kind.value, span.text) for span in page.spans] for page in document.pages
    ] == pagekit.text_sequence(spec)
    runs = lesson_runs(segment_document(document))
    assert [[chunk.number for chunk in run] for run in runs] == [[1, 2, 3, 4, 5, 6]] * 3
    phonics = runs[2][0].text.splitlines()
    assert phonics[:5] == [
        "Unit 7 Greetings",
        "Lesson 3",
        "1. Listen and repeat.",
        "h /h/",
        "hello hi",
    ]
    matching = runs[2][3].text.splitlines()
    assert matching[:2] == ["Unit 7 Greetings", "4. Read and match."]
    assert matching[2].startswith("a. Hello, I'm Ha.") and matching[2].endswith("Bye, Ha.")
    assert matching[3].startswith("b. Goodbye, Tien.") and matching[3].endswith("I'm Tien.")


def test_renders_are_deterministic(tmp_path: Path, one_lesson: dict) -> None:
    first = pagekit.render_unit(one_lesson, tmp_path / "a")
    second = pagekit.render_unit(one_lesson, tmp_path / "b")
    for fmt in first:
        assert first[fmt].read_bytes() == second[fmt].read_bytes(), fmt
    assert sequence(first["pdf"]) == sequence(second["pdf"])


def test_pictures_are_png_and_deterministic() -> None:
    picture = pagekit.Picture(scene="two children greeting at the school gate", style="scene")
    png = pagekit.draw_picture(picture)
    assert png.startswith(PNG_MAGIC)
    assert png == pagekit.draw_picture(picture)
    assert pagekit.infer_shapes(picture.scene) == ["face", "face", "house"]
    assert pagekit.infer_shapes("a quiet lake") == ["face", "sun"]
    card = pagekit.Item(word="Tien").card
    assert card.style == "card"
    assert pagekit.draw_picture(card).startswith(PNG_MAGIC)


def test_mini_unit_hello_rerenders_to_the_committed_text(tmp_path: Path) -> None:
    spec = next(spec for spec in pagekit.load_specs(UNITS) if spec["id"] == "mini-unit-hello")
    for fmt, path in pagekit.render_unit(spec, tmp_path).items():
        assert sequence(path) == sequence(FIXTURES / f"mini-unit-hello.{fmt}"), fmt
